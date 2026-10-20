#!/usr/bin/env python3
"""Build a full-market local snapshot from existing checkpoints without any upstream requests."""
from __future__ import annotations
import argparse, fcntl, json, os, re, sys
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from collect import load_checkpoint, safe_saved_row, make_snapshot, publish
from reconciliation import VERIFICATION_VERSION
from retention import six_month_start, validate_dates
from universe_cache import validate_universe
try:
    from .cloud_worker import validate_day
except ImportError:
    from cloud_worker import validate_day


def build_cached_snapshot(root, *, baostock_fallback=False, latest_first=False,
                          maintenance_path=Path('/etc/stock-collector.maintenance')):
    root=Path(root);out=root/'data';marker=Path(maintenance_path)
    if not marker.is_file() or marker.is_symlink():raise RuntimeError('Maintenance marker is required')
    if root.is_symlink() or out.is_symlink() or not out.is_dir():raise ValueError('Existing collector root/data is required')
    with ExitStack() as stack:
        owner=out.stat()
        for path in (root/'.worker.lock',out/'.collector.lock'):
            fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            lock=stack.enter_context(os.fdopen(fd,'a'))
            if os.geteuid()==0:os.fchown(lock.fileno(),owner.st_uid,owner.st_gid)
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Writer is running; stop it before building a cached snapshot')
        catalog=validate_universe(json.loads((out/'universe.json').read_text()))
        calendar=json.loads((out/'calendar.json').read_text())
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        asof=calendar.get('calendarAsOf')
        if not isinstance(asof,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',asof) or asof>today:
            raise ValueError('Invalid cached calendar provenance')
        datetime.fromisoformat(asof)
        trading=calendar.get('tradingDates')
        if not isinstance(trading,list) or len(trading)!=len(set(trading)):
            raise ValueError('Invalid cached trading calendar')
        for day in trading:
            if not isinstance(day,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):raise ValueError('Invalid trading date')
            datetime.fromisoformat(day)
        state=json.loads((root/'worker-state.json').read_text())
        frozen=state.get('dates')
        if not isinstance(frozen,list) or not frozen:raise ValueError('Frozen requested dates are required')
        start=six_month_start(today)
        dates=validate_dates([day for day in frozen if isinstance(day,str) and start<=day<=today],today,trading)
        checkpoint=out/'checkpoint'
        if checkpoint.is_symlink():raise ValueError('Checkpoint directory cannot be a symlink')
        records={};incomplete=[]
        for item in catalog['rows']:
            code=item['code'];path=checkpoint/(code+'.json')
            if path.is_symlink():raise ValueError('Checkpoint cannot be a symlink')
            if not path.exists():continue
            record=load_checkpoint(path)
            if not record or record.get('code')!=code or record.get('fetchStatus') not in ('ok','error'):
                raise ValueError('Invalid checkpoint identity or fetch status')
            json.dumps(record,allow_nan=False)
            if not all(day in record['days'] for day in dates):
                incomplete.append(code);continue
            normalized={}
            for day in dates:
                row=safe_saved_row(record['days'][day])
                if row.get('code')!=code or row.get('verificationVersion')!=VERIFICATION_VERSION:
                    raise ValueError('Stock-day identity or verification version mismatch')
                validate_day({'date':day,'rows':[row]},day)
                if row.get('status') not in ('ok','unverified','missing','suspended') or row.get('status')=='ok' and row.get('ratio') is None:
                    raise ValueError('Invalid cached row status/ratio')
                normalized[day]=row
            records[code]=dict(record,days=normalized)
        # No aggregates are written until all candidate checkpoints pass validation.
        results,scope,attempted=make_snapshot(catalog['rows'],records,dates,True)
        publish(results,dates,out,scope,catalog['total'],attempted_count=attempted,
                reconcile_latest_first=latest_first,baostock_fallback=baostock_fallback)
        return dict(scope=scope,totalStocks=catalog['total'],attemptedStocks=attempted,
                    pendingStocks=catalog['total']-attempted,dates=dates,incompleteCheckpointCount=len(incomplete),
                    upstreamRequests=0,publishedToS3=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True);p.add_argument('--baostock-fallback',action='store_true')
    p.add_argument('--reconcile-latest-first',action='store_true');a=p.parse_args()
    print(json.dumps(build_cached_snapshot(a.root,baostock_fallback=a.baostock_fallback,latest_first=a.reconcile_latest_first),ensure_ascii=False))

if __name__=='__main__':main()
