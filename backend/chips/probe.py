"""Small provider health check; aggregate-only output, no AWS writes."""
import json,time
from .source import Source
from .runner import target_day,frame
from .calendar import last_ten

def main():
    s=Source();as_of=target_day();results=[]
    for code,name,market in [('000573','粤宏远A','0'),('600812','华北制药','1'),('300001','特锐德','0'),('688001','华兴源创','1'),('920000','安徽凤凰','0')]:
        started=time.monotonic()
        try:
            _,receipt,raw=s.history({'code':code,'secid':market+'.'+code},as_of)
            row,_=frame({'code':code,'name':name,'listed':None},raw,receipt,last_ten(as_of),{},'0000000000000000')
            results.append({'code':code,'source_ok':True,'provider':receipt.get('provider'),'model_ready':row['chip_status']=='READY','seconds':round(time.monotonic()-started,2)})
        except Exception as e:results.append({'code':code,'source_ok':False,'error':type(e).__name__,'seconds':round(time.monotonic()-started,2)})
        print(json.dumps(results[-1]),flush=True)
    s.close()
    print(json.dumps({'as_of':as_of,'provider_probe':results,'BSE_history':'PUBLIC_UNADJUSTED_THS_WITH_HISTORICAL_TURNOVER'}),flush=True)
    if not all(r.get('model_ready') for r in results): raise SystemExit(1)
if __name__=='__main__':main()
