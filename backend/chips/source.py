"""Bounded source reads, explicit units, and raw-byte provenance."""
import hashlib,json,re,threading,time,urllib.parse,urllib.request
from datetime import datetime,timezone

class Source:
    def __init__(self,interval=.8): self.interval=interval;self.lock=threading.Lock();self.next=0
    def read(self,url):
        with self.lock:
            delay=max(0,self.next-time.monotonic())
            if delay: time.sleep(delay)
            self.next=time.monotonic()+self.interval
        req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'})
        with urllib.request.urlopen(req,timeout=22) as r:
            raw=r.read(18000000)
        value=json.loads(raw)
        return value,{'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'ingested_at':datetime.now(timezone.utc).isoformat()},raw

    def universe(self):
        rows=[];total=None;receipts=[]
        for pn in range(1,81):
            q=urllib.parse.urlencode({'pn':pn,'pz':100,'po':1,'np':1,'fltt':2,'invt':2,'fid':'f12',
              'fs':'m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048','fields':'f2,f8,f12,f13,f14,f26,f124'})
            v,r,_=self.read('https://push2.eastmoney.com/api/qt/clist/get?'+q);d=v.get('data') or {}
            if total is None: total=d.get('total')
            if not isinstance(total,int) or not 1000<total<15000 or d.get('total')!=total: raise ValueError('UNIVERSE_TOTAL_INVALID')
            part=d.get('diff');part=list(part.values()) if isinstance(part,dict) else part
            if not isinstance(part,list) or not part: raise ValueError('UNIVERSE_PAGE_MISSING')
            rows+=part;receipts.append(r)
            if len(rows)>=total: break
        stocks=[];seen=set()
        for x in rows:
            code=str(x['f12']); listed=str(x.get('f26',''))
            if len(code)!=6 or not code.isdigit() or code in seen: raise ValueError('UNIVERSE_DUPLICATE_OR_INVALID')
            seen.add(code)
            stocks.append({'code':code,'name':x['f14'],'secid':str(x['f13'])+'.'+code,
                'listed':listed[:4]+'-'+listed[4:6]+'-'+listed[6:] if len(listed)==8 and listed.isdigit() else None})
        if len(stocks)!=total or not all(any(s['code'].startswith(p) for s in stocks) for p in ('60','00','30','688','9')): raise ValueError('UNIVERSE_COVERAGE_INCOMPLETE')
        return stocks,receipts

    def history(self,stock,as_of):
        q=urllib.parse.urlencode({'secid':stock['secid'],'klt':101,'fqt':0,'beg':'19900101','end':as_of.replace('-',''),'lmt':10000,
            'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'})
        value,receipt,raw=self.read('https://push2his.eastmoney.com/api/qt/stock/kline/get?'+q)
        data=value.get('data') or {}
        if data.get('code')!=stock['code'] or not data.get('klines') or len(data['klines'])>=10000:
            raise ValueError('FULL_HISTORY_MISSING_OR_CAPPED')
        return value,receipt,raw

    def pools(self,days):
        result={};receipts=[]
        for day in days:
            q=urllib.parse.urlencode({'ut':'7eea3edcaed734bea9cbfc24409ed989','dpt':'wz.ztzt','Pageindex':0,'pagesize':10000,'sort':'fbt:asc','date':day.replace('-','')})
            try:
                value,receipt,_=self.read('https://push2ex.eastmoney.com/getTopicZTPool?'+q);d=value.get('data') or {};pool=d.get('pool')
                if not isinstance(pool,list) or d.get('tc')!=len(pool): raise ValueError('LIMIT_POOL_INCOMPLETE')
                if len({p['c'] for p in pool})!=len(pool): raise ValueError('LIMIT_POOL_DUPLICATE')
                result[day]={p['c']:p for p in pool};receipt['requested_session']=day;receipt['provider_qdate']=d.get('qdate');receipts.append(receipt)
            except Exception as exc: result[day]=None;receipts.append({'requested_session':day,'error':type(exc).__name__})
        return result,receipts

    def actual_limits(self,stocks,as_of):
        """Today's vendor actual upper price, never backfilled into older days."""
        result={};receipts=[]
        for offset in range(0,len(stocks),60):
            part=stocks[offset:offset+60]
            symbols=[('bj' if s['code'].startswith(('4','8','9')) else 'sh' if s['code'].startswith('6') else 'sz')+s['code'] for s in part]
            url='https://qt.gtimg.cn/q='+','.join(symbols)
            try:
                with self.lock:
                    delay=max(0,self.next-time.monotonic())
                    if delay: time.sleep(delay)
                    self.next=time.monotonic()+self.interval
                raw=urllib.request.urlopen(url,timeout=22).read(2000000)
                for symbol,body in re.findall(r'v_([a-z]{2}\d{6})="([^"]*)"',raw.decode('gb18030')):
                    f=body.split('~')
                    if len(f)>48 and f[2]==symbol[2:] and len(f[30])==14 and f[30][:8]==as_of.replace('-','') and f[30][8:12]>='1500':
                        result[f[2]]={'date':as_of,'close':f[3],'reference_close':f[4],'upper_limit':f[47],'provider':'TENCENT_ACTUAL_DAILY_QUOTE'}
                receipts.append({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'ingested_at':datetime.now(timezone.utc).isoformat()})
            except Exception as e:receipts.append({'offset':offset,'error':type(e).__name__})
        return result,receipts
