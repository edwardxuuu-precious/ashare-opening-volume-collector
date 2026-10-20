"""Prepare a concrete grid release using read-only AWS calls. Never deploys."""
from copy import deepcopy
import difflib, hashlib, io, json, subprocess, sys, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/golden-grid-20261020'
sys.path.insert(0, str(ROOT / 'deploy/auth'))
from chip_bundle import inline_handler
from aws_stock import verify_account
from release_site import package


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def main():
    verify_account()
    import boto3
    from botocore.config import Config
    session = boto3.Session(profile_name='stock-dev', region_name='us-east-1')
    config = Config(connect_timeout=7, read_timeout=20, retries={'total_max_attempts': 1})
    cf, fn = (session.client(service, config=config) for service in ('cloudformation', 'lambda'))
    before = cf.get_template(StackName='stock-private-access')['TemplateBody']
    if isinstance(before, str):
        before = json.loads(before)
    function = fn.get_function(FunctionName='stock-auth-api')
    settings = function['Configuration']
    if settings['CodeSha256'] != 'dEyruNdFkRVK1vLtcrCDO/CxIi72TKxDQTHGgrDSZ9w=':
        raise RuntimeError('AUTH_BASELINE_CHANGED_REVIEW_REQUIRED')
    with urllib.request.urlopen(function['Code']['Location'], timeout=25) as response:
        raw = response.read(1000001)
    if len(raw) > 1000000:
        raise RuntimeError('AUTH_PACKAGE_SIZE_BOUND_EXCEEDED')
    actual = zipfile.ZipFile(io.BytesIO(raw)).read('index.py').decode('utf-8')
    desired = inline_handler()
    old_route = r'(?:screening|\d{6})\.json))'
    new_route = r'(?:screening|golden|\d{6})\.json))'
    if actual.count(old_route) != 1 or actual.replace(old_route, new_route) != desired:
        raise RuntimeError('AUTH_DIFF_IS_NOT_THE_SINGLE_REVIEWED_ROUTE')
    candidate = deepcopy(before)
    # Actual source is authoritative if a prior code-only release left a
    # template representation different from the deployed package.
    before['Resources']['Function']['Properties']['Code'] = {'ZipFile': actual}
    candidate['Resources']['Function']['Properties']['Code'] = {'ZipFile': desired}
    changed = {key for key in before['Resources'] if before['Resources'][key] != candidate['Resources'][key]}
    if changed != {'Function'}:
        raise RuntimeError('NON_CODE_RESOURCE_CHANGE')
    candidate_raw = canonical(candidate)
    if len(candidate_raw) > 51200:
        raise RuntimeError('INLINE_TEMPLATE_SIZE_EXCEEDED')
    OUT.mkdir(parents=True, exist_ok=True)
    for name, value in [('auth-template-before.json', before), ('auth-template-candidate.json', candidate)]:
        (OUT / name).write_bytes(canonical(value))
    (OUT / 'auth-code.diff').write_text(''.join(difflib.unified_diff(actual.splitlines(True), desired.splitlines(True), fromfile='deployed/index.py', tofile='candidate/index.py')), encoding='utf-8')
    artifact = package(ROOT, OUT / 'site-publish.zip')
    data = OUT / 'golden.json'
    value = json.loads(data.read_text(encoding='utf-8'))
    paths = [
        'backend/chips/golden.py', 'backend/chips/runner.py', 'deploy/auth/handler.py', 'deploy/auth/template.json',
        'deploy/actions/package.py', 'src/app/golden/page.tsx', 'src/app/golden/golden.css',
        'src/lib/golden-grid.ts', 'src/components/stock-navigation.tsx', 'src/components/chip-trading-chart.tsx',
        'tooling/release_site.py', 'tooling/prepare_golden_grid.py', 'tooling/prepare_golden_release.py',
    ]
    proposal = {
        'proposal_id': 'STOCK-GOLDEN-GRID-DESCRIPTIVE-20261020-01',
        'status': 'PREPARED_NOT_DEPLOYED',
        'git_base_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
        'purpose': 'Publish the reviewed whole-current-market descriptive grid workbench at stock.iteready.com/golden',
        'scope': ['Commit and push reviewed website/runtime source to the existing public repository main',
                  'Publish reviewed protected static artifact to the existing Amplify production branch',
                  'Code-only update of existing stock-auth-api for the exact authenticated golden.json GET',
                  'Write new immutable golden.json for the pinned source batch to existing private S3; no existing chip data mutation',
                  'Use the existing daily workflow for subsequent all-stock grid calculation, with no new timer or AWS resource'],
        'source_files_sha256': {path: digest((ROOT / path).read_bytes()) for path in paths},
        'website_artifact': artifact,
        'auth': {'expected_code_sha256': settings['CodeSha256'], 'expected_revision_id': settings['RevisionId'],
                 'before_template_sha256': digest(canonical(before)), 'candidate_template_sha256': digest(candidate_raw),
                 'candidate_source_sha256': digest(desired.encode()), 'changed_resources': ['Function.Code'],
                 'memory_mb': settings['MemorySize'], 'timeout_seconds': settings['Timeout']},
        'initial_statistics': {'dataset_id': value['dataset_id'], 'as_of': value['as_of'], 'total': value['universe_total'],
                               'counts': value['counts'], 'sha256': digest(data.read_bytes())},
        'preserved': {'chip_coefficient': 2, 'chip_history_window': 210, 'unchanged_threshold': None,
                      'golden_policy_sha256': 'ba5ceafce205c8a674d74c36f1b5062835a3e1ba87073af3052a8d0e298a182b',
                      'old_filter_campaign_state_and_approvals': 'UNCHANGED', 'market_performance_or_order_execution': False},
        'gates': ['Verify current Amplify successful job before publishing',
                  'Verify deployed auth baseline still matches before creating/executing a code-only change set',
                  'Recheck the active data generation; do not expose mixed data or overwrite immutable objects',
                  'If a newer generation lacks grids, prepare its existing immutable frames or wait for the new daily implementation before declaring online ready'],
        'approval': None,
    }
    path = OUT / 'release-proposal.json'
    path.write_bytes(canonical(proposal))
    print(json.dumps({'proposal_id': proposal['proposal_id'], 'proposal_sha256': digest(path.read_bytes()),
                     'artifact_sha256': artifact['sha256'], 'initial_counts': value['counts'],
                     'auth_delta': 'one exact GET route', 'published': False}))


if __name__ == '__main__':
    main()
