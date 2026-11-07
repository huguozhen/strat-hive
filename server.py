#!/usr/bin/env python3
import argparse
import json
import ipaddress
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlsplit
from strathive.core import Ledger, Error

ROOT = Path(__file__).resolve().parent

class Handler(BaseHTTPRequestHandler):
    def respond(self, code, obj):
        body=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    def allowed_host(self):
        # Numeric IPs work through NAT/port forwarding; explicit names are opt-in.
        value = self.headers.get('Host', '')
        try:
            parsed = urlsplit('//'+value)
            hostname = parsed.hostname
            if not hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return False
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                return False
            try:
                ipaddress.ip_address(hostname)
                return True
            except ValueError:
                return hostname.lower() in self.server.allowed_hosts
        except ValueError:
            return False
    def do_GET(self):
        if not self.allowed_host(): return self.respond(403,{'error':'无效的访问地址'})
        path=urlparse(self.path).path
        if path=='/api/state':
            return self.respond(200,self.server.ledger.snapshot())
        files={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if path not in files: return self.send_error(404)
        f=ROOT/'static'/files[path]; body=f.read_bytes(); mime={'html':'text/html','js':'text/javascript','css':'text/css'}[f.suffix[1:]]
        self.send_response(200); self.send_header('Content-Type',mime+'; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-cache'); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        # Reject DNS rebinding and cross-origin browser requests.
        if not self.allowed_host(): return self.respond(403,{'error':'无效的访问地址'})
        origin=self.headers.get('Origin')
        if origin and origin != 'http://'+self.headers.get('Host',''): return self.respond(403,{'error':'不允许跨来源请求'})
        if self.headers.get_content_type() != 'application/json': return self.respond(415,{'error':'需要 JSON 请求'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>1024*1024: raise Error('请求大小无效')
            data=json.loads(self.rfile.read(n))
            if not isinstance(data,dict): raise Error('请求须为 JSON 对象')
            parts=urlparse(self.path).path.strip('/').split('/')
            ledger=self.server.ledger; result=None
            if parts==['api','strategies']: result=ledger.strategy_create(data)
            elif parts==['api','accounts']: result=ledger.account_create(data)
            elif parts==['api','pools']: result=ledger.pool_create(data)
            elif parts==['api','runs']: result=ledger.run_create(data)
            elif len(parts)==4 and parts[:2]==['api','strategies'] and parts[3]=='update': ledger.strategy_update(int(parts[2]),data)
            elif len(parts)==4 and parts[:2]==['api','accounts'] and parts[3]=='update': ledger.account_update(int(parts[2]),data)
            elif len(parts)==4 and parts[:2]==['api','pools'] and parts[3]=='transfer': ledger.transfer(int(parts[2]),data)
            elif len(parts)==4 and parts[:2]==['api','runs'] and parts[3]=='status': ledger.transition(int(parts[2]),data)
            else: return self.respond(404,{'error':'接口不存在'})
            self.respond(200,{'ok':True,'id':result})
        except (Error,ValueError,TypeError,KeyError,json.JSONDecodeError) as e:
            self.respond(400,{'error':str(e) or '请求无效'})
        except Exception:
            import traceback; traceback.print_exc()
            self.respond(500,{'error':'服务器错误，请查看日志'})

def main():
    parser=argparse.ArgumentParser(description='策略蜂巢本地服务')
    parser.add_argument('--host', default='0.0.0.0', help='监听地址（默认 0.0.0.0）')
    parser.add_argument('--allowed-host', action='append', default=[], help='允许访问的域名，可重复指定；IP 地址和 localhost 默认允许')
    parser.add_argument('--port',type=int,default=8000); parser.add_argument('--db',default=os.environ.get('STRATHIVE_DB',str(ROOT/'data'/'strathive.db')))
    args=parser.parse_args(); Path(args.db).expanduser().resolve().parent.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer((args.host,args.port),Handler); server.ledger=Ledger(args.db)
    server.allowed_hosts={'localhost', *(h.lower() for h in args.allowed_host)}
    print(f'策略蜂巢：监听 {args.host}:{server.server_port}  本机访问：http://127.0.0.1:{server.server_port}  数据库：{args.db}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
if __name__=='__main__': main()
