import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from .ledger import Ledger, LedgerError

STATIC = Path(__file__).resolve().parent.parent / 'static'

def make_server(host, port, path):
    ledger = Ledger(path)
    class Handler(BaseHTTPRequestHandler):
        def reply(self,status,body,content_type):
            self.send_response(status); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(body))); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(body)
        def json_reply(self,status,data):
            self.reply(status,json.dumps(data,ensure_ascii=False).encode(),'application/json; charset=utf-8')
        def valid_host(self):
            if self.headers.get('Host', '').lower() not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                self.json_reply(403,{'error':'请求主机不被允许'}); return False
            return True
        def do_GET(self):
            if not self.valid_host(): return
            path = urlsplit(self.path).path
            if path.startswith('/api/'):
                try: self.json_reply(200,ledger.execute(unquote(path[5:])))
                except LedgerError as e: self.json_reply(400,{'error':str(e)})
                return
            files = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            if path not in files: self.reply(404,b'Not found','text/plain'); return
            target=STATIC/files[path]
            self.reply(200,target.read_bytes(),{'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}[path])
        def do_POST(self):
            if not self.valid_host(): return
            path = urlsplit(self.path).path
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'):
                self.json_reply(403,{'error':'来源不被允许'}); return
            if not path.startswith('/api/'): self.json_reply(404,{'error':'未知接口'}); return
            try:
                length = int(self.headers.get('Content-Length','0'))
                if length > 10_000_000: raise ValueError('请求过大')
                data = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(data,dict): raise ValueError('参数须为 JSON 对象')
                action=unquote(path[5:])
                if action == 'catalog.sync':
                    from .catalog import sync
                    result=sync(ledger)
                else: result=ledger.execute(action,data)
                self.json_reply(200,result)
            except (ValueError,OSError) as e: self.json_reply(400,{'error':str(e)})
    return ThreadingHTTPServer((host,port),Handler)
