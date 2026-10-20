import _bootstrap
import io,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend/scripts'))
import collect
from test_baostock_reconciliation import pair,DAY,OLD,initial

class BaoCollectionTests(unittest.TestCase):
    def run_case(self,fail_publish=False):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);out=Path(folder.name)
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        (out/'calendar.json').write_text(json.dumps(dict(calendarAsOf=today,tradingDates=[OLD,DAY])))
        (out/'checkpoint').mkdir();rows=initial()
        rows[OLD]=dict(collect.evaluate('600519','test',OLD,*pair()),verifiedAt='prior-proof')
        (out/'checkpoint/600519.json').write_text(json.dumps(dict(code='600519',fetchedOn='2026-09-05',fetchStatus='ok',days=rows)))
        class Session:
            def __init__(self,**kwargs):self.closed=False;self.calls=[]
            def pair(self,code,dates):self.calls.append((code,dates));return pair()
            def close(self):self.closed=True
        session=Session()
        def forbidden(*args,**kwargs):raise AssertionError('Sina must not be fetched while only repairing an old checkpoint')
        args=SimpleNamespace(out=str(out),interval=.75,months=6,days=5,dates=None,symbols=None,full=True,limit=None,
                             max_stocks=None,resume=True,resume_attempted=True,efficient_sina=True,baostock_fallback=True,
                             reconcile_latest_first=True,publish_every=1,source_policy='legacy')
        patches=[patch.dict(sys.modules,{'akshare':SimpleNamespace(stock_info_a_code_name=forbidden),
                                        'baostock_source':SimpleNamespace(BaoStockSession=lambda **_:session)}),
                 patch.object(collect,'load_universe',return_value={'rows':[dict(code='600519',name='test')]}),
                 patch.object(collect.RequestBudget,'install'),patch.object(collect,'minute_unadjusted',side_effect=forbidden)]
        from contextlib import ExitStack
        with ExitStack() as stack:
            from contextlib import redirect_stdout
            output=stack.enter_context(redirect_stdout(io.StringIO()))
            for p in patches:stack.enter_context(p)
            if fail_publish:stack.enter_context(patch.object(collect,'publish',side_effect=RuntimeError('publish failed')))
            if fail_publish:
                with self.assertRaises(RuntimeError):collect.run_collection(args)
            else:collect.run_collection(args)
        self.progress=[json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]
        return out,session
    def test_old_checkpoint_repair_preserves_matched_and_closes_session(self):
        out,session=self.run_case()
        self.assertTrue(session.closed);self.assertEqual(session.calls,[('600519',[OLD,DAY])])
        record=json.loads((out/'checkpoint/600519.json').read_text())
        self.assertEqual(record['days'][OLD]['verifiedAt'],'prior-proof')
        self.assertEqual(record['days'][DAY]['sourceProvider'],'baostock')
        manifest=json.loads((out/'manifest.json').read_text())
        self.assertEqual(manifest['sourcePolicy'],['sina','eastmoney','baostock'])
        self.assertEqual([event['completedStocks'] for event in self.progress],[1,1])
        self.assertEqual(self.progress[-1]['completed'],1)
    def test_session_closes_even_when_publication_fails(self):
        _,session=self.run_case(fail_publish=True)
        self.assertTrue(session.closed)

if __name__=='__main__':unittest.main()
