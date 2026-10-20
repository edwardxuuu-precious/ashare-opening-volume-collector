"""Required delivery evidence; not a general task-progress recorder."""
import hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
root=Path(__file__).resolve().parents[1];out=root/'outputs/chips-daily-20261018'
def read(name):return json.loads((out/name).read_bytes())
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
from backend.chips.runner import implementation_identity
value={'recorded_at':datetime.now(timezone.utc).isoformat(),'proposal_id':'STOCK_CHIPS_DAILY_20261018',
    'authorization_basis':'User requested daily private website screening, OHLCV/right-side chip chart, coefficient2, no chip stability threshold, four comparison stocks and Windows AWS/GitHub migration.',
    'website':'https://stock.iteready.com/chips','sample_site':'https://stock.iteready.com/chips/calibration',
    'amplify_job':34,'amplify_state':'SUCCEED','build_host':'Windows','bootstrap_aws_auth_host':'Mac',
    'windows_github':'VERIFIED_BY_PUBLIC_RUNTIME_PUSH','windows_aws_sso':'CONFIGURED_BUT_OWNER_FIRST_LOGIN_NOT_COMPLETED',
    'public_runtime_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root.parent/'ashare-opening-volume-collector',text=True).strip(),
    'execution_identity':implementation_identity(),'coefficient':2,'unchanged_threshold':None,
    'calibration':{'dataset_id':read('calibration-four/manifest.json')['dataset_id'],'stocks':4,'dates':read('calibration-four/manifest.json')['dates'],
        'previous_three_snapshots_recalculated':False,'fourth_stock':'000001','s3_payload_roundtrip_sha256_verified':True},
    'new_author_checks':[
        {'command':'python -m unittest test_chip_worker_v3.WorkerV3.test_v4_warm_changed_session_matches_full_recurrence_and_keeps_state_private test_chip_worker_v3.WorkerV3.test_v4_incompatible_batch_rejected_even_when_empty_or_partially_successful','exit_code':0,'scope':'invented software inputs'},
        {'command':'python -m unittest test_chip_heartbeat_v4','exit_code':0,'scope':'invented stalled-worker clock'},
        {'command':'python -m unittest test_chip_adapter_identity_v5','exit_code':0,'scope':'invented incompatible cache prefix'},
        {'command':'python -m unittest test_chip_bao_suspension_v6','exit_code':0,'scope':'invented no-trade fields and bounded transport'},
        {'command':'python -m unittest test_chip_transport_receipt_v7','exit_code':0,'scope':'invented terminal failures retained'},
        {'command':'npm run typecheck','exit_code':0}, {'command':'npm run build','exit_code':0,'protected_data_guard':True},
        {'command':'python tooling/release_site.py --dry-run','exit_code':0,'published':False},
        {'command':'node actual-calibration-consumer-validation','exit_code':0,'stocks':4},
        {'command':'node actual-cloud-stock-consumer-validation','exit_code':0,'code':'688001','history_rows':500,'candles':120,'manifest_wrapper':'LOCAL_CHECK_ONLY_NOT_PUBLISHED'}],
    'retained_failures':[
        'WorkerV3 literal zero-delta assertion failed on -1E-48 Decimal normalization. V4 uses changed daily allocation and compares to the complete recurrence; no algorithm clipping or threshold added.',
        'Observed 688005 suspended SDK rows with empty volume/amount failed conversion; original SDK rows retained and repaired normalization produced READY. No zero filling.',
        'Cloud run39094938513 cancelled only after identifying the adapter defect. 151 saved rows and immutable artifacts preserved; next generation archives old batch.',
        'Windows stock-dev STS still fails because no local SSO token exists. No Mac credential cache copied.'
    ],
    'independent_review':'STATIC_ONLY: heartbeat, batch/cache identities, overlap coverage, no-trade normalization, bounded transport and terminal error receipts closed; no independent execution claimed.',
    'browser_checks':{'shared_navigation':True,'daily_progress':True,'four_sample_options':True,'candles_volume_price_axis':True,
        'switch_to_600812':'Peak6.49, 120 candles and120 matching volume bars observed',
        'anonymous_daily_status_http':401,'anonymous_calibration_manifest_http':401,'viewport':'319x884; horizontal chart scroll also checked'},
    'not_complete':['Initial whole-universe historical bootstrap still running; no committed daily screening manifest yet.',
        'BSE full historical turnover source missing; unverified limit events remain UNKNOWN.',
        'Fallback site current-list completeness not certified against the unavailable primary full list.',
        'Vendor screenshot equivalence and chip stability threshold await user screenshots.',
        'Windows AWS account owner must complete first SSO authentication; Windows release authentication not yet verified.'],
    'artifacts':{name:digest(out/name) for name in ('stock-daily-site-windows.zip','calibration-four/manifest.json','000001-baostock-full.raw.json','688005-bao-diagnostic-sdk.json','real-daily-688001.json.gz','live-chart-detail.jpg')},
    'fixture_source_sha256':{p.name:digest(p) for p in (root/'tests/python').glob('test_chip_*v[3-7].py')}}
if (out/'live-market-progress.json').exists():value['last_observed_market']=read('live-market-progress.json')
if (out/'RECOVERY_DISPATCH.json').exists():value['cloud_recovery_run']=read('RECOVERY_DISPATCH.json')
(out/'DELIVERY_CHECKS.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'receipt':str(out/'DELIVERY_CHECKS.json'),'receipt_sha256':digest(out/'DELIVERY_CHECKS.json')}))
