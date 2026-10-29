"""Loopback-only local portfolio application. Python standard library only."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit
from engine import Engine, Problem

ROOT=Path(__file__).resolve().parent

def make_server(engine, port=8787):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            return self.headers.get('Host') in hosts and (not self.headers.get('Origin') or self.headers['Origin'] in {'http://'+x for x in hosts})

        def send(self, status, data, content_type='application/json'):
            body=json.dumps(data,ensure_ascii=False).encode() if content_type=='application/json' else data.encode()
            self.send_response(status);self.send_header('Content-Type',content_type+'; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():return self.send(403,{'error':'Local same-origin access only'})
            path=urlsplit(self.path).path
            if path=='/api/workspace':return self.send(200,engine.workspace())
            if path=='/api/report':return self.send(200,engine.report())
            if path=='/api/export.csv':
                return self.send(200,engine.csv(),'text/csv')
            assets={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
            if path in assets:
                name,mime=assets[path];return self.send(200,(ROOT/'web'/name).read_text(encoding='utf8'),mime)
            return self.send(404,{'error':'Not found'})

        def do_POST(self):
            if not self.allowed():return self.send(403,{'error':'Local same-origin access only'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send(415,{'error':'Use application/json'})
            action=urlsplit(self.path).path.removeprefix('/api/')
            if action not in {'accounts','contacts','opportunities','update','note','issue','complete'}:return self.send(404,{'error':'Unknown operation'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=16384:raise Problem('Request body must be 1-16384 bytes',413)
                payload=json.loads(self.rfile.read(length))
                result=engine.mutate(action,payload,self.headers.get('Idempotency-Key'))
                return self.send(200,result)
            except (json.JSONDecodeError,ValueError,UnicodeDecodeError):return self.send(400,{'error':'Invalid JSON body or content length'})
            except Problem as exc:return self.send(exc.status,{'error':str(exc)})
            except Exception:return self.send(500,{'error':'Unexpected error; no change committed'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,default=ROOT/'relay.sqlite3');p.add_argument('--port',type=int,default=8787);p.add_argument('--seed',action='store_true');args=p.parse_args()
    engine=Engine(args.db)
    if args.seed:engine.seed()
    server=make_server(engine,args.port);print(f'Relay CRM ready: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:server.server_close()
