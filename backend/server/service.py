#!/usr/bin/env python3
"""One durable, bounded AKShare job queue. Run behind HTTPS on a Python cloud host."""
from __future__ import annotations
import argparse,hashlib,json,os,queue,re,shutil,subprocess,sys,threading,time,uuid
from collections import defaultdict,deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from retention import six_month_start,validate_dates,accumulated_manifest

class JobStore:
    def __init__(self,out,seed=None,runner=None):
        self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True)
        if seed and not (self.out/'manifest.json').exists():
            for path in Path(seed).glob('*.json'):shutil.copy2(path,self.out/path.name)
            if (self.out/'stocks.json').exists():shutil.copy2(self.out/'stocks.json',self.out/'universe.json')
            m=self.manifest();self.atomic(self.out/'calendar.json',{k:m[k] for k in ('tradingDates','calendarAsOf','retentionStart','retentionMonths') if k in m})
        self.jobs_dir=self.out/'.jobs';self.jobs_dir.mkdir(exist_ok=True)
        self.lock=threading.RLock();self.jobs={};self.pending=queue.Queue(maxsize=8);self.runner=runner or self.collect
        self.requests=defaultdict(deque);self.last_maintenance=None;self.last_maintenance_attempt=0
        for path in self.jobs_dir.glob('*.json'):
            try:job=json.loads(path.read_text())
            except (ValueError,OSError):continue
            if time.time()-job.get('createdAt',0)>86400:path.unlink();continue
            if job['status'] in ('queued','running'):job.update(status='failed',message='服务重启，已保存进度保留，请重新查询。');self.atomic(path,job)
            self.jobs[job['id']]=job
    @staticmethod
    def atomic(path,value):
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False));tmp.replace(path)
    def manifest(self):
        return accumulated_manifest(json.loads((self.out/'manifest.json').read_text()))
    def update(self,job,**values):
        with self.lock:job.update(values,updatedAt=time.time());self.atomic(self.jobs_dir/(job['id']+'.json'),job)
    def get(self,id):
        with self.lock:return dict(self.jobs[id])
    def validate(self,payload):
        if not isinstance(payload,dict) or set(payload)-{'dates','scope','symbols'}:raise ValueError('查询参数无效')
        dates=payload.get('dates');symbols=payload.get('symbols',[]);scope=payload.get('scope')
        if not isinstance(dates,list) or not all(isinstance(d,str) for d in dates):raise ValueError('日期参数无效')
        if not isinstance(symbols,list) or len(symbols)>6000 or not all(isinstance(s,str) and re.fullmatch(r'\d{6}',s) for s in symbols):raise ValueError('股票代码必须为6位字符串')
        if scope not in ('all','single','multiple'):raise ValueError('股票范围无效')
        if scope!='all' and (not symbols or scope=='single' and len(set(symbols))!=1):raise ValueError('请选择股票')
        now=datetime.now(ZoneInfo('Asia/Shanghai'));today=now.date().isoformat();m=self.manifest()
        dates=validate_dates(dates,today,[d for d in m.get('tradingDates',[x['date'] for x in m['dates']]) if d<today or now.hour>=17])
        universe=json.loads((self.out/'universe.json').read_text());known={r['code'] for r in universe['rows']}
        if scope!='all' and not set(symbols)<=known:raise ValueError('股票代码不在当前A股名单中')
        return {'dates':dates,'scope':scope,'symbols':[] if scope=='all' else sorted(set(symbols))},len(known) if scope=='all' else len(set(symbols))
    def cached(self,request):
        for date in request['dates']:
            path=self.out/(date+'.json')
            if not path.exists():return False
            data=json.loads(path.read_text())
            if request['scope']=='all' and (data.get('scope')!='full' or data.get('pendingCount',0)>0):return False
            rows={r['code']:r for r in data['rows']}
            if request['scope']!='all' and not set(request['symbols'])<=set(rows):return False
            # Source-unavailable observations already fetched are terminal records, not endless retries.
            relevant=rows.values() if request['scope']=='all' else [rows[c] for c in request['symbols']]
            if any(r.get('reason')=='尚未采集' or r.get('reason','').startswith('采集失败') for r in relevant):return False
        return True
    def submit(self,payload,client='local'):
        request,total=self.validate(payload);signature=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        with self.lock:
            for job in self.jobs.values():
                if job['signature']==signature and job['status'] in ('queued','running'):return dict(job)
            cached=self.cached(request)
            if cached:
                return {'id':signature[:32],'status':'completed','completedStocks':total,'totalStocks':total,'message':'已命中保存的数据','resultDates':request['dates']}
            if not cached:
                recent=self.requests[client];now=time.time()
                while recent and recent[0]<now-60:recent.popleft()
                if len(recent)>=5 or self.pending.full():raise OverflowError('下载队列繁忙，请稍后再试。')
                recent.append(now)
            job={'id':uuid.uuid4().hex,'signature':signature,'request':request,'status':'completed' if cached else 'queued','createdAt':time.time(),'completedStocks':total if cached else 0,'totalStocks':total,'message':'已命中保存的数据' if cached else '等待采集器处理','resultDates':request['dates']}
            self.jobs[job['id']]=job;self.update(job)
            if not cached:self.pending.put_nowait(job['id'])
            return dict(job)
    def collect(self,job):
        request=job['request'];cmd=[sys.executable,str(ROOT/'scripts/collect.py'),'--dates',','.join(request['dates']),'--out',str(self.out),'--resume','--publish-every','1']
        cmd+=['--full'] if request['scope']=='all' else ['--symbols',','.join(request['symbols'])]
        log=self.jobs_dir/(job['id']+'.log')
        with log.open('w') as err:
            proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=err,text=True,bufsize=1)
            timer=threading.Timer(5.5*3600,proc.terminate);timer.start()
            try:
                for line in proc.stdout:
                    try:progress=json.loads(line)
                    except ValueError:continue
                    if 'completedStocks' in progress:
                        done=progress['completedStocks'];total=progress['totalStocks']
                        self.update(job,completedStocks=done,totalStocks=total,message=f'已处理 {done} / {total} 只股票')
                code=proc.wait()
                if code:raise RuntimeError('行情获取中断，已保存完成部分，请稍后重试。')
            finally:
                timer.cancel()
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=25)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
        log.unlink(missing_ok=True)
    def work_once(self,id=None):
        id=id or self.pending.get();job=self.jobs[id]
        try:
            self.update(job,status='running',message='正在连接行情来源…');self.runner(job)
            if not self.cached(job['request']):raise RuntimeError('部分请求未完成，已保存可用记录，请重试。')
            self.update(job,status='completed',completedStocks=job['totalStocks'],message='下载完成，正在显示结果')
        except Exception as exc:self.update(job,status='failed',message=str(exc) if isinstance(exc,RuntimeError) else '采集服务暂时不可用，请稍后重试。')
        finally:self.pending.task_done()
    def maintain(self):
        now=datetime.now(ZoneInfo('Asia/Shanghai'));slot=(now.date().isoformat(),now.hour>=17)
        if self.last_maintenance==slot or time.time()-self.last_maintenance_attempt<900:return
        self.last_maintenance_attempt=time.time()
        result=subprocess.run([sys.executable,str(ROOT/'scripts/maintain.py'),'--out',str(self.out)],capture_output=True,timeout=60)
        if result.returncode==0:self.last_maintenance=slot
        with self.lock:
            for id,job in list(self.jobs.items()):
                if job['status'] not in ('running','queued') and time.time()-job['createdAt']>86400:
                    del self.jobs[id];(self.jobs_dir/(id+'.json')).unlink(missing_ok=True);(self.jobs_dir/(id+'.log')).unlink(missing_ok=True)
            for ip,times in list(self.requests.items()):
                if not times or times[-1]<time.time()-60:del self.requests[ip]
    def worker(self):
        while True:
            try:self.maintain()
            except (OSError,subprocess.TimeoutExpired):pass
            try:id=self.pending.get(timeout=60)
            except queue.Empty:continue
            self.work_once(id)

def make_handler(store,origins):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value):
            raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode();self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)))
            origin=self.headers.get('Origin')
            if origin in origins:self.send_header('Access-Control-Allow-Origin',origin);self.send_header('Vary','Origin')
            self.end_headers();self.wfile.write(raw)
        def do_OPTIONS(self):
            if self.headers.get('Origin') not in origins:return self.reply(403,{'error':'来源不允许'})
            self.send_response(204);self.send_header('Access-Control-Allow-Origin',self.headers['Origin']);self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS');self.send_header('Access-Control-Allow-Headers','Content-Type');self.end_headers()
        def do_GET(self):
            path=urlsplit(self.path).path
            try:
                if path=='/api/health':return self.reply(200,{'status':'ok','retentionPolicy':'accumulate','collectionWindowMonths':6})
                if re.fullmatch(r'/api/query-jobs/[a-f0-9]{32}',path):return self.reply(200,store.get(path.rsplit('/',1)[-1]))
                if path=='/data/manifest.json':return self.reply(200,store.manifest())
                if path=='/data/stocks.json':return self.reply(200,json.loads((store.out/'universe.json').read_text()))
                if re.fullmatch(r'/data/\d{4}-\d{2}-\d{2}\.json',path):
                    day=path[-15:-5]
                    return self.reply(200,json.loads((store.out/(day+'.json')).read_text()))
            except (KeyError,FileNotFoundError):return self.reply(404,{'error':'数据尚未下载'})
            except (ValueError,OSError):return self.reply(503,{'error':'数据暂时不可用'})
            return self.reply(404,{'error':'Not found'})
        def do_POST(self):
            if urlsplit(self.path).path!='/api/query-jobs':return self.reply(404,{'error':'Not found'})
            origin=self.headers.get('Origin')
            if origin and origin not in origins:return self.reply(403,{'error':'来源不允许'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=65536:raise ValueError('请求体过大或为空')
                payload=json.loads(self.rfile.read(size));return self.reply(202,store.submit(payload,self.client_address[0]))
            except (ValueError,TypeError):return self.reply(400,{'error':'请选择最近6个月的已收盘交易日及有效股票'})
            except OverflowError as exc:return self.reply(429,{'error':str(exc)})
    return Handler

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',default='var/work/service-data');parser.add_argument('--seed',default='public/data');parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8787);args=parser.parse_args()
    store=JobStore(args.out,args.seed);threading.Thread(target=store.worker,daemon=True).start()
    origins=set(os.environ.get('ALLOWED_ORIGINS','https://ashare-opening-volume.bene-team.chatgpt.site,http://localhost:3000').split(','))
    server=ThreadingHTTPServer((args.host,args.port),make_handler(store,origins));print(f'AKShare query service: http://{args.host}:{args.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:server.server_close()
