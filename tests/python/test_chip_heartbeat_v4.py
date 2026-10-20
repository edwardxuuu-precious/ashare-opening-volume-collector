"""ALL_VALUES_INVENTED: a worker with no completion must not block the lease."""
import concurrent.futures,unittest
from unittest.mock import patch
from backend.chips.runner import run
from test_chip_daily_v1 import Memory,FakeSource,DAYS

class HeartbeatV4(unittest.TestCase):
    def test_parent_renews_while_no_worker_completes(self):
        store=Memory();clock=[1000.0];calls=[];real_wait=concurrent.futures.wait
        def bounded_wait(active,*,timeout,return_when):
            calls.append(timeout)
            if len(calls)==1:
                clock[0]+=61
                return set(),set(active)
            self.assertGreaterEqual(store.writes.count('collector/chips/lease.json'),2)
            return real_wait(active,timeout=timeout,return_when=return_when)
        with patch('backend.chips.runner.Source',FakeSource),patch('backend.chips.runner.time.monotonic',lambda:clock[0]),patch('backend.chips.runner.concurrent.futures.wait',bounded_wait):
            value=run(store,as_of=DAYS[-1],max_minutes=3,workers=1)
        self.assertEqual(value['outcome'],'COMMITTED')
        self.assertEqual(calls,[30,30])
