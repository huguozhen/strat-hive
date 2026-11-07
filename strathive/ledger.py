"""Shared transactional application interface for the local ledger."""
import datetime
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

class LedgerError(ValueError):
    pass

def decimal(value, label, zero=False, places=None):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (not zero and not n) or n > Decimal('10000000000000') or (places is not None and n.as_tuple().exponent < -places):
            raise ValueError()
        return n
    except (ValueError, InvalidOperation):
        raise LedgerError(f'{label}须为有效的正数' + ('或零' if zero else '') + (f'，最多 {places} 位小数' if places is not None else ''))

def money(value, zero=False):
    return int(decimal(value, '金额', zero, 2) * 100)

def quantity(value):
    n = decimal(value, '数量')
    if n != n.to_integral_value() or n > 2**53: raise LedgerError('数量须为正整数')
    return int(n)

def fmt(value):
    return f'{value // 100}.{value % 100:02d}'

def required(data, name):
    value = str(data.get(name, '')).strip()
    if not value: raise LedgerError(f'请填写 {name}')
    return value

def get(db, table, ident, label):
    row = db.execute(f'SELECT * FROM {table} WHERE id=?', (ident,)).fetchone()
    if not row: raise LedgerError(f'{label}不存在')
    return row

class Ledger:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, commission_rate TEXT NOT NULL, unallocated INTEGER NOT NULL, initial_cash INTEGER NOT NULL, setup_complete INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS strategies(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pools(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), name TEXT NOT NULL, cash INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, strategy_id INTEGER NOT NULL REFERENCES strategies(id), pool_id INTEGER NOT NULL REFERENCES pools(id), name TEXT NOT NULL, status TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS active_pool ON runs(pool_id) WHERE status != 'ended';
            CREATE TABLE IF NOT EXISTS securities(code TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalog_meta(source TEXT NOT NULL, updated_at TEXT NOT NULL, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS holdings(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), pool_id INTEGER NOT NULL REFERENCES pools(id), code TEXT NOT NULL, name TEXT NOT NULL, quantity INTEGER NOT NULL, cost TEXT NOT NULL, date TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cash_events(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), pool_id INTEGER REFERENCES pools(id), direction TEXT NOT NULL, amount INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def execute(self, action, data=None):
        data = data or {}
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                return self._execute(db, action, data)
        except sqlite3.IntegrityError as e:
            raise LedgerError('操作冲突：请检查关联记录，资产池同一时间只能绑定一个运行') from e

    def _execute(self, db, action, data):
        if action == 'account.create':
            rate = str(decimal(data.get('commission_rate'), '佣金费率', True))
            if Decimal(rate) > 1: raise LedgerError('佣金费率不能大于 1')
            initial_cash = money(data.get('cash',0),True)
            cur = db.execute('INSERT INTO accounts(name, commission_rate, unallocated, initial_cash) VALUES(?,?,?,?)', (required(data,'name'),rate,initial_cash,initial_cash))
            return {'id':cur.lastrowid}
        if action == 'account.update':
            account = get(db,'accounts',data.get('id'),'账户')
            rate = str(decimal(data.get('commission_rate', account['commission_rate']), '佣金费率', True))
            if Decimal(rate) > 1: raise LedgerError('佣金费率不能大于 1')
            name = required({'name':data.get('name',account['name'])}, 'name')
            db.execute('UPDATE accounts SET name=?,commission_rate=? WHERE id=?',(name,rate,account['id']))
            return {'id':account['id']}
        if action == 'account.list':
            return [self.account(db, r['id']) for r in db.execute('SELECT id FROM accounts ORDER BY id').fetchall()]
        if action == 'account.get': return self.account(db, data.get('id'))
        if action == 'strategy.create':
            cur = db.execute('INSERT INTO strategies(name,description) VALUES(?,?)',(required(data,'name'),str(data.get('description','')).strip()))
            return {'id':cur.lastrowid}
        if action == 'strategy.update':
            row = get(db,'strategies',data.get('id'),'策略')
            db.execute('UPDATE strategies SET name=?,description=? WHERE id=?',(required({'name':data.get('name',row['name'])},'name'),str(data.get('description',row['description'])).strip(),row['id']))
            return {'id':row['id']}
        if action == 'strategy.list': return [dict(x) for x in db.execute('SELECT * FROM strategies ORDER BY id')]
        if action == 'pool.create':
            get(db,'accounts',data.get('account_id'),'账户')
            cur = db.execute('INSERT INTO pools(account_id,name) VALUES(?,?)',(data['account_id'],required(data,'name')))
            return {'id':cur.lastrowid}
        if action == 'run.create':
            get(db,'strategies',data.get('strategy_id'),'策略'); get(db,'pools',data.get('pool_id'),'资产池')
            cur = db.execute("INSERT INTO runs(strategy_id,pool_id,name,status) VALUES(?,?,?,'paused')",(data['strategy_id'],data['pool_id'],required(data,'name')))
            return {'id':cur.lastrowid}
        if action == 'run.status':
            run = get(db,'runs',data.get('id'),'策略运行')
            status = data.get('status')
            if status not in ('running','paused','ended'): raise LedgerError('运行状态无效')
            if run['status'] == 'ended' and status != 'ended': raise LedgerError('已结束的运行不能重新启动，请创建新运行')
            db.execute('UPDATE runs SET status=? WHERE id=?',(status,run['id']))
            return {'id':run['id'],'status':status}
        if action in ('run.list','run.get'):
            rows = db.execute('SELECT runs.*, strategies.name AS strategy_name, pools.name AS pool_name, pools.account_id FROM runs JOIN strategies ON strategies.id=runs.strategy_id JOIN pools ON pools.id=runs.pool_id ORDER BY runs.id').fetchall()
            results = []
            for row in rows:
                if action == 'run.get' and str(row['id']) != str(data.get('id')): continue
                item = dict(row)
                item['pool'] = next(p for p in self.account(db, row['account_id'])['pools'] if p['id'] == row['pool_id'])
                results.append(item)
            if action == 'run.get':
                if not results: raise LedgerError('策略运行不存在')
                return results[0]
            return results
        if action == 'catalog.import':
            entries = data.get('securities')
            if not isinstance(entries,list) or not entries: raise LedgerError('证券目录为空，原有目录未更改')
            values = []
            seen = set()
            for item in entries:
                code, name, kind = required(item,'code'), required(item,'name'), required(item,'type')
                if not re.fullmatch(r'\d{6}',code) or kind not in ('stock','etf') or code in seen: raise LedgerError('目录包含无效或重复证券代码、类型，原有目录未更改')
                seen.add(code); values.append((code,name,kind))
            source = required(data,'source')
            db.execute('DELETE FROM securities'); db.executemany('INSERT INTO securities VALUES(?,?,?)',values)
            db.execute('DELETE FROM catalog_meta'); db.execute('INSERT INTO catalog_meta VALUES(?,?,?)',(source,datetime.datetime.now(datetime.timezone.utc).isoformat(),len(values)))
            return {'source':source,'count':len(values)}
        if action == 'catalog.search':
            q = str(data.get('query','')).strip()
            rows = db.execute('SELECT * FROM securities WHERE code LIKE ? OR name LIKE ? ORDER BY code LIMIT 100',(f'%{q}%',f'%{q}%')).fetchall()
            meta = db.execute('SELECT * FROM catalog_meta').fetchone()
            return {'metadata':dict(meta) if meta else None,'securities':[dict(r) for r in rows]}
        if action == 'initial.close':
            account = get(db,'accounts',data.get('account_id'),'账户')
            if account['setup_complete']: raise LedgerError('初始建账已完成')
            db.execute('UPDATE accounts SET setup_complete=1 WHERE id=?',(account['id'],))
            return {'id':account['id']}
        if action == 'initial.record':
            account = get(db,'accounts',data.get('account_id'),'账户')
            if account['setup_complete']: raise LedgerError('初始建账已完成，不能再次录入初始持仓')
            items = data.get('holdings')
            if not isinstance(items,list) or not items: raise LedgerError('请填写初始持仓')
            for item in items:
                sec = db.execute('SELECT * FROM securities WHERE code=? OR name=?',(item.get('security'),item.get('security'))).fetchall()
                if len(sec)!=1: raise LedgerError('本地目录未收录或名称不唯一，请检查代码或更新证券资料')
                n = quantity(item.get('quantity')); cost = str(decimal(item.get('cost'),'成本价',True,4))
                date = required(item,'date')
                try: datetime.date.fromisoformat(date)
                except ValueError: raise LedgerError('持仓日期须为 YYYY-MM-DD')
                allocations = item.get('allocations')
                if not isinstance(allocations,list) or not allocations: raise LedgerError('每只证券须完整分配至资产池')
                if sum(quantity(x.get('quantity')) for x in allocations) != n: raise LedgerError('分配数量之和必须等于持仓数量')
                seen = set()
                for alloc in allocations:
                    pool = get(db,'pools',alloc.get('pool_id'),'资产池')
                    if pool['account_id'] != account['id'] or pool['id'] in seen: raise LedgerError('分配资产池不属于账户或重复')
                    if not db.execute("SELECT 1 FROM runs WHERE pool_id=? AND status != 'ended'",(pool['id'],)).fetchone(): raise LedgerError('请先为资产池创建承接策略运行')
                    seen.add(pool['id'])
                    db.execute('INSERT INTO holdings(account_id,pool_id,code,name,quantity,cost,date) VALUES(?,?,?,?,?,?,?)',(account['id'],pool['id'],sec[0]['code'],sec[0]['name'],quantity(alloc['quantity']),cost,date))
            db.execute('UPDATE accounts SET setup_complete=1 WHERE id=?',(account['id'],))
            return {'recorded':len(items)}
        if action == 'cash.list':
            get(db,'accounts',data.get('account_id'),'账户')
            return [{**dict(r), 'amount':fmt(r['amount'])} for r in db.execute('SELECT * FROM cash_events WHERE account_id=? ORDER BY id',(data['account_id'],))]
        if action == 'cash.move':
            a = get(db,'accounts',data.get('account_id'),'账户'); direction = data.get('direction'); amount = money(data.get('amount'))
            pool = get(db,'pools',data['pool_id'],'资产池') if data.get('pool_id') else None
            if pool and pool['account_id'] != a['id']: raise LedgerError('资产池不属于该账户')
            if direction not in ('in','out'): raise LedgerError('方向须为 in 或 out')
            if pool:
                if direction == 'in' and amount > a['unallocated']: raise LedgerError('未分配现金不足')
                if direction == 'out' and amount > pool['cash']: raise LedgerError('资产池现金不足')
                delta = amount if direction == 'in' else -amount
                db.execute('UPDATE pools SET cash=cash+? WHERE id=?',(delta,pool['id']))
                db.execute('UPDATE accounts SET unallocated=unallocated-? WHERE id=?',(delta,a['id']))
            else:
                if direction == 'out' and amount > a['unallocated']: raise LedgerError('未分配现金不足')
                db.execute('UPDATE accounts SET unallocated=unallocated+? WHERE id=?',(amount if direction == 'in' else -amount,a['id']))
            db.execute('UPDATE accounts SET setup_complete=1 WHERE id=?',(a['id'],))
            db.execute('INSERT INTO cash_events(account_id,pool_id,direction,amount) VALUES(?,?,?,?)',(a['id'],pool['id'] if pool else None,direction,amount))
            return self.account(db,a['id'])
        raise LedgerError('未知操作')

    def account(self, db, ident):
        a = get(db,'accounts',ident,'账户')
        pools = []
        for p in db.execute('SELECT * FROM pools WHERE account_id=? ORDER BY id',(a['id'],)).fetchall():
            runs = [dict(r) for r in db.execute('SELECT * FROM runs WHERE pool_id=? ORDER BY id',(p['id'],))]
            holdings = [dict(r) for r in db.execute('SELECT code,name,quantity,cost,date FROM holdings WHERE pool_id=? ORDER BY id',(p['id'],))]
            pools.append({'id':p['id'],'name':p['name'],'cash':fmt(p['cash']),'runs':runs,'holdings':holdings})
        grouped = {}
        for h in db.execute('SELECT code,name,quantity,cost FROM holdings WHERE account_id=? ORDER BY code',(a['id'],)):
            entry = grouped.setdefault(h['code'], {'code':h['code'], 'name':h['name'], 'quantity':0, 'total':Decimal(0)})
            entry['quantity'] += h['quantity']
            entry['total'] += Decimal(h['cost']) * h['quantity']
        holdings = []
        for h in grouped.values():
            h['cost'] = format(h.pop('total') / h['quantity'], '.4f')
            holdings.append(h)
        return {'id':a['id'],'name':a['name'],'commission_rate':a['commission_rate'],'setup_complete':bool(a['setup_complete']),'initial_cash':fmt(a['initial_cash']),'cash':fmt(a['unallocated']+sum(int(Decimal(p['cash'])*100) for p in pools)),'unallocated_cash':fmt(a['unallocated']),'pools':pools,'holdings':holdings}
