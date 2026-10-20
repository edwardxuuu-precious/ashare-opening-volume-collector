import _bootstrap
import importlib.util,json,sys,tempfile,unittest,fcntl
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend/server'))
from publish_cached import build_cached_snapshot
DAY='2026-09-04'

class CachedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.data=self.root/'data';self.data.mkdir()
        self.marker=self.root/'maintenance';self.marker.touch()
        rows=[dict(code=f'{i:06}',name='股票'+str(i)) for i in range(1,1001)]
        (self.data/'universe.json').write_text(json.dumps(dict(asOf='2026-09-06',total=1000,historicalMembership=False,rows=rows)))
        (self.data/'calendar.json').write_text(json.dumps(dict(calendarAsOf='2026-09-06',tradingDates=[DAY])))
        (self.root/'worker-state.json').write_text(json.dumps({'dates':[DAY]}))
        (self.data/'checkpoint').mkdir()
        self.row=dict(code='000001',name='股票1',market='SZ',verificationVersion=3,status='ok',ratio=15,first15Volume=150,dailyVolume=1000,minuteDayVolume=1000,dayVolumeDifference=0,quality='matched',sourceProvider='baostock')
        self.save(self.row)
    def tearDown(self):self.tmp.cleanup()
    def save(self,row):
        (self.data/'checkpoint/000001.json').write_text(json.dumps(dict(code='000001',fetchStatus='ok',fetchedOn='2026-09-06',days={DAY:row})))
    def run_(self):return build_cached_snapshot(self.root,baostock_fallback=True,maintenance_path=self.marker)
    def test_builds_full_placeholders_without_network(self):
        with patch('socket.create_connection',side_effect=AssertionError('network forbidden')), \
             patch('socket.socket.connect',side_effect=AssertionError('socket forbidden')), \
             patch('requests.sessions.Session.request',side_effect=AssertionError('HTTP forbidden')):
            result=self.run_()
        self.assertEqual(result['attemptedStocks'],1);self.assertEqual(result['pendingStocks'],999)
        self.assertEqual(result['upstreamRequests'],0);self.assertFalse(result['publishedToS3'])
        data=json.loads((self.data/(DAY+'.json')).read_text());self.assertEqual(len(data['rows']),1000)
        self.assertEqual(data['rows'][0]['sourceProvider'],'baostock');self.assertEqual(data['rows'][0]['ratio'],15)
    def test_invalid_ratio_rejects_before_any_aggregate_write(self):
        self.row['ratio']=90;self.save(self.row)
        with self.assertRaises(ValueError):self.run_()
        self.assertFalse((self.data/'manifest.json').exists())
    def test_old_version_rejected(self):
        self.row['verificationVersion']=1;self.save(self.row)
        with self.assertRaises(ValueError):self.run_()
    def test_missing_maintenance_rejected(self):
        self.marker.unlink()
        with self.assertRaises(RuntimeError):self.run_()
    def test_running_writer_rejected(self):
        with (self.root/'.worker.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(RuntimeError):self.run_()

if __name__=='__main__':unittest.main()
