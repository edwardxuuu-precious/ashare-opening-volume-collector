"""Loopback-only QA of the built site. Reads reviewed local examples; all
annotation writes go to a local QA folder, never AWS. Does not modify the build.
"""
import argparse,gzip,json,mimetypes,sys
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse,unquote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.golden.annotations import empty,apply
from backend.golden.watchlist import SYMBOLS


def main():
    p=argparse.ArgumentParser();p.add_argument('--prepared',type=Path,required=True);p.add_argument('--port',type=int,default=8798);a=p.parse_args()
    root=Path(__file__).resolve().parents[1];static=root/'dist/client';annotations=a.prepared/'qa-annotations';annotations.mkdir(exist_ok=True)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,status,value,mime='application/json'):
            raw=json.dumps(value,ensure_ascii=False).encode() if isinstance(value,(dict,list)) else value
            self.send_response(status);self.send_header('Content-Type',mime+'; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            path=unquote(urlparse(self.path).path)
            if path=='/auth/me': return self.send(200,dict(username='local-qa'))
            if path.startswith('/data/golden-workbench/annotations/'):
                symbol=path.rsplit('/',1)[-1][:-5]
                if symbol not in SYMBOLS:return self.send(404,{})
                file=annotations/(symbol+'.json');return self.send(200,json.loads(file.read_text()) if file.exists() else empty(symbol))
            if '..' in Path(path).parts:return self.send(404,{})
            if path.startswith('/data/golden-workbench/'):
                file=a.prepared/path.lstrip('/')
                if not file.is_file():return self.send(404,{})
                raw=file.read_bytes();return self.send(200,gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw)
            file=static/path.lstrip('/')
            if not file.is_file():file=static/(path.strip('/')+'.html' if path!='/' else 'index.html')
            if not file.is_file():return self.send(404,{})
            raw=file.read_bytes()
            if file.suffix in ('.js','.html'):raw=raw.replace(b'https://api.stock.iteready.com',f'http://127.0.0.1:{a.port}'.encode())
            self.send(200,raw,mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        def do_POST(self):
            path=urlparse(self.path).path
            if self.headers.get('Origin')!=f'http://127.0.0.1:{a.port}':return self.send(403,{})
            if not path.startswith('/data/golden-workbench/annotations/'):return self.send(404,{})
            symbol=path.rsplit('/',1)[-1][:-5]
            if symbol not in SYMBOLS:return self.send(404,{})
            file=annotations/(symbol+'.json');document=json.loads(file.read_text()) if file.exists() else empty(symbol)
            try:
                payload=json.loads(self.rfile.read(min(int(self.headers['Content-Length']),16001)))
                value=apply(document,payload,'local-qa',symbol);file.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');self.send(200,value)
            except ValueError as e:self.send(400,dict(error=str(e)))
    print(f'Local QA only: http://127.0.0.1:{a.port}/golden',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()


if __name__=='__main__':main()
