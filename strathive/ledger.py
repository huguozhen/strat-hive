"""Shared transactional application interface for the local ledger."""
import datetime
import re
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
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
    return format(Decimal(str(value)) / 100, '.2f') if Decimal(str(value)) % 1 == 0 else format(Decimal(str(value)) / 100, 'f')

def timestamp(value):
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is not None: dt = dt.astimezone().replace(tzinfo=None)
        return dt.isoformat(timespec='microseconds')
    except (ValueError, TypeError):
        raise LedgerError('时间须为 ISO 日期及时间，例如 2026-01-02T10:00:00')

def cash_timestamp(value):
    # Legacy SQLite CURRENT_TIMESTAMP values use UTC with a space separator.
    if ' ' in value and 'T' not in value: value = value.replace(' ','T') + '+00:00'
    return timestamp(value)

def cents(value):
    return str(value * 100)

def rounded(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def required(data, name):
    raw = data.get(name) if isinstance(data,dict) else None
    if not isinstance(raw,str): raise LedgerError(f'请填写有效的 {name}')
    value = raw.strip()
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
        with closing(self.connect()) as db, db:
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
            CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), code TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL, direction TEXT NOT NULL, time TEXT NOT NULL, price TEXT NOT NULL, quantity INTEGER NOT NULL, amount TEXT NOT NULL, commission TEXT NOT NULL, stamp_tax TEXT NOT NULL, transfer_fee TEXT NOT NULL, reason TEXT NOT NULL, note TEXT NOT NULL, fee_basis TEXT NOT NULL);
            ''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(accounts)')}
            if 'opened_at' not in columns: db.execute('ALTER TABLE accounts ADD COLUMN opened_at TEXT')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def execute(self, action, data=None):
        data = data or {}
        try:
            with localcontext() as context, closing(self.connect()) as db, db:
                context.prec = 60
                db.execute('BEGIN IMMEDIATE')
                return self._execute(db, action, data)
        except sqlite3.IntegrityError as e:
            raise LedgerError('操作冲突：请检查关联记录，资产池同一时间只能绑定一个运行') from e

    def _execute(self, db, action, data):
        if action == 'account.create':
            rate = str(decimal(data.get('commission_rate'), '佣金费率', True))
            if Decimal(rate) > 1: raise LedgerError('佣金费率不能大于 1')
            initial_cash = money(data.get('cash',0),True)
            opened_at=timestamp(data.get('opened_at') or datetime.datetime.now().isoformat())
            cur = db.execute('INSERT INTO accounts(name, commission_rate, unallocated, initial_cash, opened_at) VALUES(?,?,?,?,?)', (required(data,'name'),rate,initial_cash,initial_cash,opened_at))
            return {'id':cur.lastrowid}
        if action == 'account.update':
            account = get(db,'accounts',data.get('id'),'账户')
            rate = str(decimal(data.get('commission_rate', account['commission_rate']), '佣金费率', True))
            if Decimal(rate) > 1: raise LedgerError('佣金费率不能大于 1')
            name = required({'name':data.get('name',account['name'])}, 'name')
            opened_at=timestamp(data['opened_at']) if data.get('opened_at') else account['opened_at']
            if data.get('opened_at') and any(datetime.date.fromisoformat(h['date']).isoformat() > opened_at[:10] for h in db.execute('SELECT date FROM holdings WHERE account_id=?',(account['id'],))): raise LedgerError('持仓日期不能晚于账户建账日期')
            if opened_at and db.execute('SELECT 1 FROM trades JOIN runs ON runs.id=trades.run_id JOIN pools ON pools.id=runs.pool_id WHERE pools.account_id=? AND trades.time<? LIMIT 1',(account['id'],opened_at)).fetchone(): raise LedgerError('建账时点晚于已有成交，请检查输入')
            if opened_at and any(cash_timestamp(r['created_at']) < opened_at for r in db.execute('SELECT created_at FROM cash_events WHERE account_id=?',(account['id'],))): raise LedgerError('建账时点晚于已有资金操作，请检查输入')
            db.execute('UPDATE accounts SET name=?,commission_rate=?,opened_at=? WHERE id=?',(name,rate,opened_at,account['id']))
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
                rebound = row['status']=='ended' and db.execute('SELECT 1 FROM runs WHERE pool_id=? AND id>? LIMIT 1',(row['pool_id'],row['id'])).fetchone()
                if rebound: item['pool'] = {**item['pool'], 'cash':None, 'holdings':[], 'historical':True}
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
            if not account['opened_at']: raise LedgerError('该旧账户的建账时点未知，请先在账户信息中确认建账时点')
            items = data.get('holdings')
            if not isinstance(items,list) or not items: raise LedgerError('请填写初始持仓')
            for item in items:
                sec = db.execute('SELECT * FROM securities WHERE code=? OR name=?',(item.get('security'),item.get('security'))).fetchall()
                if len(sec)!=1: raise LedgerError('本地目录未收录或名称不唯一，请检查代码或更新证券资料')
                n = quantity(item.get('quantity')); cost = str(decimal(item.get('cost'),'成本价',True,4))
                date = required(item,'date')
                try: date = datetime.date.fromisoformat(date).isoformat()
                except ValueError: raise LedgerError('持仓日期须为 YYYY-MM-DD')
                if date > account['opened_at'][:10]: raise LedgerError('持仓日期不能晚于账户建账日期')
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
            when = timestamp(data.get('time') or datetime.datetime.now().isoformat())
            if not a['opened_at']: raise LedgerError('该旧账户的建账时点未知，请先在账户信息中确认建账时点')
            if when < a['opened_at']: raise LedgerError('资金操作时间早于账户建账时点')
            db.execute('UPDATE accounts SET setup_complete=1 WHERE id=?',(a['id'],))
            db.execute('INSERT INTO cash_events(account_id,pool_id,direction,amount,created_at) VALUES(?,?,?,?,?)',(a['id'],pool['id'] if pool else None,direction,amount,when))
            self.replay(db,a['id'])
            return self.account(db,a['id'])
        if action in ('trade.create','trade.update'):
            old = get(db,'trades',data.get('id'),'交易') if action == 'trade.update' else None
            row = dict(old) if old else {}
            row.update(data)
            run = get(db,'runs',row.get('run_id'),'策略运行')
            pool = get(db,'pools',run['pool_id'],'资产池')
            account = get(db,'accounts',pool['account_id'],'账户')
            old_account_id = None
            if old:
                old_run = get(db,'runs',old['run_id'],'策略运行')
                old_account_id = get(db,'pools',old_run['pool_id'],'资产池')['account_id']
            sec_value = data.get('security',row.get('code'))
            sec = db.execute('SELECT * FROM securities WHERE code=? OR name=?',(sec_value,sec_value)).fetchall()
            if len(sec) != 1:
                if old and sec_value in (old['code'],old['name']): sec = [old]
                else: raise LedgerError('本地目录未收录或名称不唯一，请检查代码或更新证券资料')
            direction = row.get('direction')
            if direction not in ('buy','sell'): raise LedgerError('交易方向须为 buy 或 sell')
            when = timestamp(row.get('time'))
            if not account['opened_at']: raise LedgerError('该旧账户的建账时点未知，请先在账户信息中确认建账时点')
            if when < account['opened_at']: raise LedgerError('成交时间早于账户建账时点，不能录入建账前交易')
            price = decimal(row.get('price'),'成交价格',places=4)
            n = quantity(row.get('quantity')); amount = price * n
            # These are clearly labelled estimates; the receipt values can override each fee.
            day=when[:10]
            stock=sec[0]['type']=='stock'
            if day < '2002-05-01': commission=None
            else: commission=max(Decimal('5'),rounded(amount * Decimal(account['commission_rate'])))
            if day < '2022-07-01': stamp=None
            elif stock and direction=='sell': stamp=rounded(amount * (Decimal('0.0005') if day >= '2023-08-28' else Decimal('0.001')))
            else: stamp=Decimal('0.00')
            transfer=rounded(amount * Decimal('0.00001')) if stock and day >= '2026-01-01' else (Decimal('0.00') if not stock and day >= '2022-07-01' else None)
            estimate = {'commission':commission,'stamp_tax':stamp,'transfer_fee':transfer}
            import json
            previous_basis = json.loads(old['fee_basis']) if old else {}
            changed = bool(old and (old_account_id != account['id'] or old['code'] != sec[0]['code'] or old['direction'] != direction or old['time'] != when or Decimal(old['price']) != price or old['quantity'] != n))
            fees = {}; basis = {}
            for key in estimate:
                raw = data.get(key)
                if raw not in (None,''):
                    fees[key] = decimal(raw,key,True,2); basis[key] = 'manual'
                elif old and key not in data and (previous_basis.get(key) == 'manual' or not changed):
                    fees[key] = Decimal(old[key]); basis[key] = previous_basis.get(key,'manual')
                else:
                    if estimate[key] is None: raise LedgerError(f'该日期的{ {"commission":"佣金","stamp_tax":"印花税","transfer_fee":"过户费"}[key]}规则尚未核实，请按交割单手动输入')
                    fees[key] = estimate[key]; basis[key] = 'estimate'
            fields = (run['id'],sec[0]['code'],sec[0]['name'],sec[0]['type'],direction,when,str(price),n,str(amount),*(format(fees[k],'.2f') for k in ('commission','stamp_tax','transfer_fee')),str(row.get('reason','')).strip(),str(row.get('note','')).strip(),json.dumps(basis))
            if old:
                db.execute('UPDATE trades SET run_id=?,code=?,name=?,type=?,direction=?,time=?,price=?,quantity=?,amount=?,commission=?,stamp_tax=?,transfer_fee=?,reason=?,note=?,fee_basis=? WHERE id=?',(*fields,old['id']))
                ident=old['id']
            else:
                ident=db.execute('INSERT INTO trades(run_id,code,name,type,direction,time,price,quantity,amount,commission,stamp_tax,transfer_fee,reason,note,fee_basis) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',fields).lastrowid
            db.execute('UPDATE accounts SET setup_complete=1 WHERE id=?',(account['id'],))
            if old_account_id and old_account_id != account['id']: self.replay(db,old_account_id)
            self.replay(db,account['id'])
            return {'id':ident,'warning': ('该运行当前已暂停，已记录补记成交' if run['status']=='paused' else '该运行当前已结束，已记录补记成交' if run['status']=='ended' else '')}
        if action == 'trade.delete':
            trade=get(db,'trades',data.get('id'),'交易')
            run=get(db,'runs',trade['run_id'],'策略运行'); pool=get(db,'pools',run['pool_id'],'资产池')
            db.execute('DELETE FROM trades WHERE id=?',(trade['id'],))
            self.replay(db,pool['account_id'])
            return {'id':trade['id']}
        if action in ('trade.list','trade.get'):
            import json
            rows=db.execute('SELECT trades.*,runs.pool_id,pools.account_id,runs.name AS run_name FROM trades JOIN runs ON runs.id=trades.run_id JOIN pools ON pools.id=runs.pool_id ORDER BY trades.time,trades.id').fetchall()
            result=[]
            for r in rows:
                t=dict(r)
                if action=='trade.get' and str(t['id'])!=str(data.get('id')): continue
                if data.get('run_id') and str(t['run_id'])!=str(data['run_id']): continue
                if data.get('security') and data['security'] not in (t['code'],t['name']): continue
                if data.get('from') and t['time'] < timestamp(data['from']): continue
                if data.get('to') and t['time'] > timestamp(data['to']): continue
                t['fees']=format(sum(Decimal(t[k]) for k in ('commission','stamp_tax','transfer_fee')),'.2f')
                t['fee_basis']=json.loads(t['fee_basis']); result.append(t)
            if action=='trade.get':
                if not result: raise LedgerError('交易不存在')
                return result[0]
            return result
        raise LedgerError('未知操作')

    def replay(self, db, ident):
        a=get(db,'accounts',ident,'账户')
        pools={p['id']:{'cash':Decimal(0),'holdings':{}} for p in db.execute('SELECT * FROM pools WHERE account_id=?',(ident,))}
        unallocated=Decimal(a['initial_cash']) / 100
        account_holdings={}
        events=[]
        for h in db.execute('SELECT * FROM holdings WHERE account_id=?',(ident,)):
            events.append((a['opened_at'] or '0001-01-01T00:00:00.000000',0,h['id'],'holding',h))
        for c in db.execute('SELECT * FROM cash_events WHERE account_id=?',(ident,)):
            events.append((cash_timestamp(c['created_at']),1,c['id'],'cash',c))
        for t in db.execute('SELECT trades.*,runs.pool_id FROM trades JOIN runs ON runs.id=trades.run_id JOIN pools ON pools.id=runs.pool_id WHERE pools.account_id=?',(ident,)):
            events.append((timestamp(t['time']),2,t['id'],'trade',t))
        for when,_,__,kind,row in sorted(events,key=lambda x:x[:3]):
            if kind=='cash':
                delta=Decimal(row['amount'])/100 * (1 if row['direction']=='in' else -1)
                if row['pool_id']:
                    pools[row['pool_id']]['cash']+=delta; unallocated-=delta
                else: unallocated+=delta
                if unallocated < 0: raise LedgerError(f'{when} 的资金操作导致未分配现金不足，请检查后续记录')
                if row['pool_id'] and pools[row['pool_id']]['cash'] < 0: raise LedgerError(f'{when} 的资金操作导致资产池现金不足，请检查后续记录')
            else:
                p=pools[row['pool_id']]; hs=p['holdings']; h=hs.setdefault(row['code'],{'code':row['code'],'name':row['name'],'quantity':0,'total':Decimal(0),'date':row['date'] if kind=='holding' else when[:10]})
                ah=account_holdings.setdefault(row['code'],{'code':row['code'],'name':row['name'],'quantity':0,'total':Decimal(0)})
                if kind=='holding':
                    h['quantity']+=row['quantity']; h['total']+=Decimal(row['cost'])*row['quantity']
                    ah['quantity']+=row['quantity']; ah['total']+=Decimal(row['cost'])*row['quantity']
                else:
                    fees=sum(Decimal(row[k]) for k in ('commission','stamp_tax','transfer_fee')); amount=Decimal(row['amount']); n=row['quantity']
                    if row['direction']=='buy':
                        p['cash']-=amount+fees; h['quantity']+=n; h['total']+=amount+fees
                        ah['quantity']+=n; ah['total']+=amount+fees
                        if p['cash']<0: raise LedgerError(f'{when} 的买入导致资产池现金不足，请检查交易和后续资金记录')
                    else:
                        if h['quantity']<n: raise LedgerError(f'{when} 的卖出超过资产池持仓，请检查交易和后续记录')
                        p['cash']+=amount-fees; h['quantity']-=n; h['total']-=amount-fees
                        ah['quantity']-=n; ah['total']-=amount-fees
                        if not ah['quantity']: ah['total']=Decimal(0)
                        if p['cash']<0: raise LedgerError(f'{when} 的卖出费用导致资产池现金不足')
                        if not h['quantity']: h['total']=Decimal(0)
        return unallocated, pools, account_holdings

    def account(self, db, ident):
        a=get(db,'accounts',ident,'账户')
        unallocated,state,grouped=self.replay(db,a['id'])
        pools=[]
        for p in db.execute('SELECT * FROM pools WHERE account_id=? ORDER BY id',(a['id'],)).fetchall():
            runs=[dict(r) for r in db.execute('SELECT * FROM runs WHERE pool_id=? ORDER BY id',(p['id'],))]
            holdings=[]
            for h in state[p['id']]['holdings'].values():
                if not h['quantity']: continue
                holdings.append({'code':h['code'],'name':h['name'],'quantity':h['quantity'],'cost':format(h['total']/h['quantity'],'.4f'),'date':h['date']})
            pools.append({'id':p['id'],'name':p['name'],'cash':fmt(cents(state[p['id']]['cash'])),'runs':runs,'holdings':holdings})
        holdings=[]
        for h in grouped.values():
            if not h['quantity']: continue
            h['cost']=format(h.pop('total')/h['quantity'],'.4f'); holdings.append(h)
        holdings.sort(key=lambda h:h['code'])
        return {'id':a['id'],'name':a['name'],'commission_rate':a['commission_rate'],'opened_at':a['opened_at'],'setup_complete':bool(a['setup_complete']),'initial_cash':fmt(a['initial_cash']),'cash':fmt(cents(unallocated+sum((p['cash'] for p in state.values()),Decimal(0)))),'unallocated_cash':fmt(cents(unallocated)),'pools':pools,'holdings':holdings}
