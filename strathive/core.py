"""StratHive local ledger domain. All mutations are serialized SQLite transactions."""
import sqlite3
from contextlib import contextmanager, closing
from datetime import date
from decimal import Decimal, InvalidOperation

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS strategies(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, commission_rate TEXT NOT NULL, cash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS account_positions(account_id INTEGER NOT NULL REFERENCES accounts(id), code TEXT NOT NULL, name TEXT NOT NULL, quantity INTEGER NOT NULL, cost TEXT NOT NULL, acquired_date TEXT NOT NULL, PRIMARY KEY(account_id,code));
CREATE TABLE IF NOT EXISTS pools(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), name TEXT NOT NULL, allocated_cash TEXT NOT NULL, cash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS pool_positions(pool_id INTEGER NOT NULL REFERENCES pools(id), code TEXT NOT NULL, name TEXT NOT NULL, quantity INTEGER NOT NULL, cost TEXT NOT NULL, acquired_date TEXT NOT NULL, PRIMARY KEY(pool_id,code));
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, strategy_id INTEGER NOT NULL REFERENCES strategies(id), pool_id INTEGER NOT NULL UNIQUE REFERENCES pools(id), name TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('created','running','paused','ended')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
'''

class Error(ValueError): pass

def required(value, label, limit=100):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise Error(f'{label}不能为空且不能超过 {limit} 个字符')
    return value.strip()

def decimal(value, label, places, maximum='999999999999'):
    try:
        if isinstance(value, bool): raise ValueError()
        d = Decimal(str(value))
        if not d.is_finite() or d < 0 or d > Decimal(maximum) or d.as_tuple().exponent < -places: raise ValueError()
        return d
    except (InvalidOperation, ValueError, TypeError):
        raise Error(f'{label}须为非负数，最多 {places} 位小数')

def amount(value, label='金额'): return decimal(value, label, 2)
def money(value): return f'{value:.2f}'
def cost(value): return f'{value:.4f}'

def positions(raw):
    if not isinstance(raw, list): raise Error('持仓须为数组')
    result, seen = [], set()
    for p in raw:
        if not isinstance(p, dict): raise Error('持仓格式无效')
        code = required(p.get('code'), '证券代码', 6)
        # 沪深主板股票及沪深 ETF 常见代码段；不覆盖科创板、创业板、北交所。
        if len(code) != 6 or not code.isascii() or not code.isdigit() or not code.startswith(('600','601','603','605','000','001','002','003','510','511','512','513','515','516','517','518','519','520','560','561','562','563','588','159')):
            raise Error('仅支持沪深主板股票和沪深 ETF 的六位证券代码')
        if code in seen: raise Error('同一账户内证券代码不能重复')
        seen.add(code)
        name = required(p.get('name'), '证券名称')
        q = p.get('quantity')
        if isinstance(q, bool) or not str(q).isdigit() or not 0 < int(q) <= 1000000000000: raise Error('持仓数量须为正整数')
        c = decimal(p.get('cost'), '成本价', 4)
        day = p.get('acquired_date')
        try:
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day: raise ValueError()
        except ValueError: raise Error('持仓日期须为 YYYY-MM-DD')
        result.append((code, name, int(q), cost(c), day))
    return result

class Ledger:
    def __init__(self, path):
        self.path = str(path)
        with closing(self.connect()) as c: c.executescript(SCHEMA)
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        return c
    @contextmanager
    def tx(self):
        c = self.connect()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally: c.close()
    def get(self, c, table, id):
        row = c.execute(f'SELECT * FROM {table} WHERE id=?', (id,)).fetchone()
        if row is None: raise Error('记录不存在')
        return row
    def strategy_create(self, data):
        name = required(data.get('name'), '策略名称')
        desc = data.get('description', '')
        if not isinstance(desc, str) or len(desc) > 2000: raise Error('描述不能超过 2000 个字符')
        with self.tx() as c: return c.execute('INSERT INTO strategies(name,description) VALUES(?,?)', (name,desc.strip())).lastrowid
    def strategy_update(self, id, data):
        name = required(data.get('name'), '策略名称'); desc = data.get('description', '')
        if not isinstance(desc,str) or len(desc)>2000: raise Error('描述不能超过 2000 个字符')
        with self.tx() as c:
            self.get(c,'strategies',id)
            c.execute('UPDATE strategies SET name=?,description=? WHERE id=?',(name,desc.strip(),id))
    def account_create(self, data):
        name=required(data.get('name'),'账户名称'); rate=decimal(data.get('commission_rate'),'佣金费率',6,'1'); cash=amount(data.get('cash'),'账户现金'); ps=positions(data.get('positions', []))
        opening=data.get('opening')
        if ps and not isinstance(opening,dict): raise Error('已有持仓时必须创建承接策略、资产池和运行')
        if ps:
            sn=required(opening.get('strategy_name'),'承接策略名称'); pn=required(opening.get('pool_name'),'承接资产池名称'); rn=required(opening.get('run_name'),'承接运行名称')
            allocated=amount(opening.get('cash', '0'),'分配现金')
            if allocated>cash: raise Error('分配现金不能超过账户现金')
        with self.tx() as c:
            aid=c.execute('INSERT INTO accounts(name,commission_rate,cash) VALUES(?,?,?)',(name,str(rate),money(cash))).lastrowid
            c.executemany('INSERT INTO account_positions VALUES(?,?,?,?,?,?)',[(aid,*p) for p in ps])
            if ps:
                sid=c.execute('INSERT INTO strategies(name,description) VALUES(?,?)',(sn,'存量持仓承接')).lastrowid
                pid=c.execute('INSERT INTO pools(account_id,name,allocated_cash,cash) VALUES(?,?,?,?)',(aid,pn,money(allocated),money(allocated))).lastrowid
                c.executemany('INSERT INTO pool_positions VALUES(?,?,?,?,?,?)',[(pid,*p) for p in ps])
                c.execute('INSERT INTO runs(strategy_id,pool_id,name,status) VALUES(?,?,?,?)',(sid,pid,rn,'created'))
            return aid
    def account_update(self,id,data):
        name=required(data.get('name'),'账户名称'); rate=decimal(data.get('commission_rate'),'佣金费率',6,'1'); cash=amount(data.get('cash'),'账户现金'); ps=positions(data.get('positions',[]))
        with self.tx() as c:
            self.get(c,'accounts',id)
            pools=c.execute('SELECT * FROM pools WHERE account_id=?',(id,)).fetchall()
            allocated=sum((Decimal(p['cash']) for p in pools),Decimal(0))
            if cash<allocated: raise Error('账户现金不能低于已分配至资产池的现金')
            old={r['code']:r for r in c.execute('SELECT * FROM account_positions WHERE account_id=?',(id,))}
            new={p[0]:p for p in ps}
            added=[p for p in ps if p[0] not in old]
            opening=data.get('opening')
            if added:
                if not isinstance(opening,dict): raise Error('新增持仓必须创建承接策略、资产池和运行')
                sn=required(opening.get('strategy_name'),'承接策略名称'); pn=required(opening.get('pool_name'),'承接资产池名称'); rn=required(opening.get('run_name'),'承接运行名称')
                opening_cash=amount(opening.get('cash','0'),'分配现金')
                if opening_cash>cash-allocated: raise Error('承接资产池现金超过未分配现金')
            for code, previous in old.items():
                assigned=c.execute('SELECT pp.* FROM pool_positions pp JOIN pools p ON p.id=pp.pool_id WHERE p.account_id=? AND pp.code=?',(id,code)).fetchall()
                updated=new.get(code)
                if assigned and (not updated or (updated[1],updated[2],updated[3],updated[4]) != (previous['name'],previous['quantity'],previous['cost'],previous['acquired_date'])):
                    # A fully allocated position owned by one pool can be corrected as one atomic operation.
                    if len(assigned)!=1 or assigned[0]['quantity']!=previous['quantity']:
                        raise Error('该持仓被多个资产池或部分分配，无法自动同步修改')
                    c.execute('DELETE FROM pool_positions WHERE pool_id=? AND code=?',(assigned[0]['pool_id'],code))
                    if updated: c.execute('INSERT INTO pool_positions VALUES(?,?,?,?,?,?)',(assigned[0]['pool_id'],*updated))
            c.execute('UPDATE accounts SET name=?,commission_rate=?,cash=? WHERE id=?',(name,str(rate),money(cash),id))
            c.execute('DELETE FROM account_positions WHERE account_id=?',(id,))
            c.executemany('INSERT INTO account_positions VALUES(?,?,?,?,?,?)',[(id,*p) for p in ps])
            if added:
                sid=c.execute('INSERT INTO strategies(name,description) VALUES(?,?)',(sn,'存量持仓承接')).lastrowid
                pid=c.execute('INSERT INTO pools(account_id,name,allocated_cash,cash) VALUES(?,?,?,?)',(id,pn,money(opening_cash),money(opening_cash))).lastrowid
                c.executemany('INSERT INTO pool_positions VALUES(?,?,?,?,?,?)',[(pid,*p) for p in added])
                c.execute('INSERT INTO runs(strategy_id,pool_id,name,status) VALUES(?,?,?,?)',(sid,pid,rn,'created'))
    def pool_create(self,data):
        name=required(data.get('name'),'资产池名称'); cash=amount(data.get('cash','0'),'初始现金'); aid=data.get('account_id')
        with self.tx() as c:
            account=self.get(c,'accounts',aid)
            allocated=sum((Decimal(r[0]) for r in c.execute('SELECT cash FROM pools WHERE account_id=?',(aid,))),Decimal(0))
            if cash>Decimal(account['cash'])-allocated: raise Error('初始现金不能超过账户未分配现金')
            return c.execute('INSERT INTO pools(account_id,name,allocated_cash,cash) VALUES(?,?,?,?)',(aid,name,money(cash),money(cash))).lastrowid
    def transfer(self,id,data):
        direction=data.get('direction'); value=amount(data.get('amount'),'划转金额')
        if direction not in ('in','out') or value<=0: raise Error('划转方向或金额无效')
        with self.tx() as c:
            p=self.get(c,'pools',id); account=self.get(c,'accounts',p['account_id'])
            allocated=sum((Decimal(r[0]) for r in c.execute('SELECT cash FROM pools WHERE account_id=?',(p['account_id'],))),Decimal(0))
            if direction=='in' and value>Decimal(account['cash'])-allocated: raise Error('划入金额超过账户未分配现金')
            if direction=='out' and value>Decimal(p['cash']): raise Error('划出金额超过资产池可用现金')
            delta=value if direction=='in' else -value
            c.execute('UPDATE pools SET cash=?,allocated_cash=? WHERE id=?',(money(Decimal(p['cash'])+delta),money(Decimal(p['allocated_cash'])+delta),id))
    def run_create(self,data):
        name=required(data.get('name'),'运行名称')
        with self.tx() as c:
            self.get(c,'strategies',data.get('strategy_id')); self.get(c,'pools',data.get('pool_id'))
            if c.execute('SELECT 1 FROM runs WHERE pool_id=?',(data.get('pool_id'),)).fetchone(): raise Error('该资产池已绑定运行实例')
            return c.execute('INSERT INTO runs(strategy_id,pool_id,name,status) VALUES(?,?,?,?)',(data['strategy_id'],data['pool_id'],name,'created')).lastrowid
    def transition(self,id,data):
        target=data.get('status'); allowed={'created':('running','ended'),'running':('paused','ended'),'paused':('running','ended'),'ended':()}
        with self.tx() as c:
            r=self.get(c,'runs',id)
            if target not in allowed[r['status']]: raise Error('不允许此状态变更')
            c.execute('UPDATE runs SET status=? WHERE id=?',(target,id))
    def snapshot(self):
        with closing(self.connect()) as c:
            c.execute('BEGIN')
            strategies=[dict(x) for x in c.execute('SELECT * FROM strategies ORDER BY id DESC')]
            accounts=[dict(x) for x in c.execute('SELECT * FROM accounts ORDER BY id DESC')]
            pools=[dict(x) for x in c.execute('SELECT p.*,a.name AS account_name FROM pools p JOIN accounts a ON a.id=p.account_id ORDER BY p.id DESC')]
            runs=[dict(x) for x in c.execute('SELECT r.*,s.name AS strategy_name,p.name AS pool_name,p.account_id FROM runs r JOIN strategies s ON s.id=r.strategy_id JOIN pools p ON p.id=r.pool_id ORDER BY r.id DESC')]
            for p in pools:
                p['positions']=[dict(x) for x in c.execute('SELECT code,name,quantity,cost,acquired_date FROM pool_positions WHERE pool_id=? ORDER BY code',(p['id'],))]
                p['cash_occupied']='0.00' # No orders/trades in modules 3.1–3.3.
            for a in accounts:
                a['positions']=[dict(x) for x in c.execute('SELECT code,name,quantity,cost,acquired_date FROM account_positions WHERE account_id=? ORDER BY code',(a['id'],))]
                ap=[p for p in pools if p['account_id']==a['id']]
                allocated=sum((Decimal(p['cash']) for p in ap),Decimal(0)); a['allocated_cash']=money(allocated); a['unallocated_cash']=money(Decimal(a['cash'])-allocated)
                aggregate={}
                for p in ap:
                    for pos in p['positions']:
                        x=aggregate.setdefault(pos['code'],{'quantity':0,'costs':set()}); x['quantity']+=pos['quantity']; x['costs'].add(pos['cost'])
                a['position_summary']=[{'code':p['code'],'name':p['name'],'account_quantity':p['quantity'],'allocated_quantity':aggregate.get(p['code'],{}).get('quantity',0),'unallocated_quantity':p['quantity']-aggregate.get(p['code'],{}).get('quantity',0),'cost_match':not aggregate.get(p['code']) or aggregate[p['code']]['costs']=={p['cost']}} for p in a['positions']]
            return {'strategies':strategies,'accounts':accounts,'pools':pools,'runs':runs}
