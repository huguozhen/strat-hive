import argparse
import json
import os
import sys
from pathlib import Path
from .ledger import Ledger, LedgerError

def main():
    parser = argparse.ArgumentParser(description='策略蜂巢本地账本；操作结果以 JSON 输出')
    parser.add_argument('--db', default=os.environ.get('STRATHIVE_DB', str(Path.home()/'.strathive'/'ledger.db')))
    parser.add_argument('action', help='操作名，或 serve 启动 Web')
    parser.add_argument('--json', default='{}', help='操作参数 JSON；使用 @文件名 读取文件，@- 从标准输入读取')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.action == 'serve':
        from .web import make_server
        server = make_server('127.0.0.1',args.port,args.db)
        print(f'策略蜂巢 http://127.0.0.1:{server.server_port}', flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()
        return
    try:
        text = args.json
        if text.startswith('@'):
            text = sys.stdin.read() if text == '@-' else Path(text[1:]).read_text(encoding='utf-8')
        data = json.loads(text)
        if not isinstance(data,dict): raise ValueError('参数须为 JSON 对象')
        ledger = Ledger(args.db)
        if args.action == 'catalog.sync':
            from .catalog import sync
            result = sync(ledger)
        else: result = ledger.execute(args.action,data)
        print(json.dumps(result,ensure_ascii=False))
    except (LedgerError, ValueError, OSError) as e:
        print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr)
        return 1
if __name__ == '__main__': sys.exit(main())
