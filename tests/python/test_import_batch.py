import _bootstrap
import fcntl, hashlib, importlib.util, io, json, sys, tarfile, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend/server'))
import import_checkpoint_batch as module
DAY='2026-09-04'

def record(code='000001',status='ok'):
    row=dict(code=code,name='test',market='SZ',verificationVersion=3,status=status,
             first15Volume=150,dailyVolume=1000,minuteDayVolume=1000,dayVolumeDifference=0,
             quality='matched',ratio=15)
    if status=='unverified':row.update(ratio=None,referenceRatio=15,minuteDayVolume=999,dayVolumeDifference=-1,quality='source_difference')
    if status=='missing':row.update(ratio=None,first15Volume=None,dailyVolume=None,minuteDayVolume=None)
    return dict(code=code,fetchedOn='2026-09-06',fetchStatus='ok',days={DAY:row})

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'root';self.data=self.root/'data';self.data.mkdir(parents=True)
        (self.root/'worker-state.json').write_text(json.dumps({'dates':[DAY],'status':'paused'}))
        (self.data/'universe.json').write_text(json.dumps({'total':2,'rows':[{'code':'000001'},{'code':'000002'}]}))
        self.marker=Path(self.tmp.name)/'maintenance';self.marker.touch()
    def tearDown(self):self.tmp.cleanup()
    def bundle(self,items):
        path=Path(self.tmp.name)/'batch.tar.gz'
        with tarfile.open(path,'w:gz') as tar:
            for name, value in items:
                entry=tarfile.TarInfo(name)
                if isinstance(value,tarfile.TarInfo):tar.addfile(value);continue
                raw=json.dumps(value).encode();entry.size=len(raw);tar.addfile(entry,io.BytesIO(raw))
        return path,hashlib.sha256(path.read_bytes()).hexdigest()
    def import_(self,items,**kwargs):
        path,sha=self.bundle(items)
        return module.import_batch(path,self.root,kwargs.get('sha',sha),maintenance_path=self.marker)
    def test_imports_valid_and_unverified_with_hash_markers(self):
        result=self.import_([('checkpoint/000001.json',record()),('checkpoint/000002.json',record('000002','unverified'))])
        self.assertEqual(result['importedCount'],2)
        row=json.loads((self.data/'checkpoint/000002.json').read_text())
        self.assertEqual(row['importedArchiveSha256'],result['archiveSha256'])
        self.assertIsNone(row['days'][DAY]['ratio']);self.assertEqual(row['days'][DAY]['referenceRatio'],15)
        self.assertFalse((self.data/'manifest.json').exists())
    def test_existing_never_overwritten(self):
        dest=self.data/'checkpoint';dest.mkdir();(dest/'000001.json').write_text('original')
        result=self.import_([('checkpoint/000001.json',record())])
        self.assertEqual(result['skippedCounts'],{'existing':1});self.assertEqual((dest/'000001.json').read_text(),'original')
    def test_skips_unknown_errors_and_missing_only(self):
        error=record('000002');error['fetchStatus']='error'
        result=self.import_([('checkpoint/000001.json',record(status='missing')),('checkpoint/000002.json',error),('checkpoint/999999.json',record('999999'))])
        self.assertEqual(result['importedCount'],0)
        self.assertEqual(result['skippedCounts'],{'missing_only':1,'error_or_pending':1,'unknown':1})
    def test_invalid_late_member_prevents_all_checkpoint_writes(self):
        bad=record('000002');bad['days'][DAY]['ratio']=17
        with self.assertRaises(ValueError):self.import_([('checkpoint/000001.json',record()),('checkpoint/000002.json',bad)])
        self.assertFalse((self.data/'checkpoint').exists());self.assertFalse((self.root/'imports').exists())
    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):self.import_([('../000001.json',record())])
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):self.import_([('checkpoint/000001.json',record()),('checkpoint/000001.json',record())])
    def test_link_rejected(self):
        item=tarfile.TarInfo('checkpoint/000001.json');item.type=tarfile.SYMTYPE;item.linkname='/etc/passwd'
        with self.assertRaises(ValueError):self.import_([('checkpoint/000001.json',item)])
    def test_directory_member_rejected(self):
        item=tarfile.TarInfo('checkpoint');item.type=tarfile.DIRTYPE
        with self.assertRaises(ValueError):self.import_([('checkpoint',item)])
    def test_hash_mismatch_rejected(self):
        with self.assertRaises(ValueError):self.import_([('checkpoint/000001.json',record())],sha='0'*64)
    def test_missing_maintenance_rejected(self):
        self.marker.unlink()
        with self.assertRaises(RuntimeError):self.import_([('checkpoint/000001.json',record())])
    def test_running_worker_lock_rejected(self):
        with (self.root/'.worker.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(RuntimeError):self.import_([('checkpoint/000001.json',record())])
    def test_running_collector_lock_rejected(self):
        with (self.data/'.collector.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(RuntimeError):self.import_([('checkpoint/000001.json',record())])
    def test_version_mismatch_rejected(self):
        for version in (1,2):
            with self.subTest(version=version):
                bad=record();bad['days'][DAY]['verificationVersion']=version
                with self.assertRaises(ValueError):self.import_([('checkpoint/000001.json',bad)])
    def test_code_mismatch_rejected(self):
        with self.assertRaises(ValueError):self.import_([('checkpoint/000001.json',record('000002'))])
    def test_incomplete_frozen_dates_skipped(self):
        (self.root/'worker-state.json').write_text(json.dumps({'dates':[DAY,'2026-09-03']}))
        result=self.import_([('checkpoint/000001.json',record())])
        self.assertEqual(result['skippedCounts'],{'incomplete_dates':1})
    def test_invalid_reference_rejected(self):
        bad=record(status='unverified');bad['days'][DAY]['referenceRatio']=16
        with self.assertRaises(ValueError):self.import_([('checkpoint/000001.json',bad)])
    def test_nan_json_rejected(self):
        bad=record();bad['days'][DAY]['ratio']=float('nan')
        with self.assertRaises(ValueError):self.import_([('checkpoint/000001.json',bad)])
    def test_json_duplicate_key_rejected(self):
        with self.assertRaises(ValueError):module.strict_json('{"code":"000001","code":"000002"}')

if __name__=='__main__':unittest.main()
