"""ALL_VALUES_INVENTED: incompatible normalized prefixes must not be reused."""
import hashlib,json,time,unittest
from backend.chips.runner import _calculate,implementation_identity
from backend.chips.calendar import last_ten
from test_chip_daily_v1 import Memory

class AdapterV5(unittest.TestCase):
    def test_old_adapter_prefix_is_not_sent_to_source_or_model(self):
        store=Memory();owner='INVENTED';identity=implementation_identity()
        store.put('collector/chips/lease.json',{'owner':owner,'expires':time.time()+600})
        store.put('collector/chips/model/000001.json',{'_history_adapter_sha256':'OLD_INVENTED_ADAPTER',
            '_model_code_sha256':identity['files']['model.py'],'_source_history':['INVALID_OLD_PREFIX'],'last_date':'2026-10-15'})
        class Source:
            def history(self,stock,as_of,cached=None):
                if cached is not None:raise AssertionError('Incompatible historical prefix was reused')
                lines=[f'{d},5.00,5.00,5.00,5.00,10,5000,,0,0,2.5' for d in last_ten(as_of)]
                raw=json.dumps({'rc':0,'data':{'code':'000001','klines':lines}}).encode()
                return json.loads(raw),{'sha256':hashlib.sha256(raw).hexdigest(),'provider':'EASTMONEY'},raw
        ctx={'slot':'5555555555555555','dataset':'5555555555555555','days':last_ten('2026-10-16'),
            'as_of':'2026-10-16','owner':owner,'pools':{},'actual_limits':{},'execution_identity':identity}
        result=_calculate(store,Source(),{'code':'000001','name':'INVENTED'},ctx)
        self.assertEqual(result['chip_status'],'READY')
        cache=store.get('collector/chips/model/000001.json')[0]
        self.assertEqual(cache['_history_adapter_sha256'],identity['history_adapter_sha256'])
