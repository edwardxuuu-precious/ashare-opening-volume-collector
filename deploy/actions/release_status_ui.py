"""Release only the protected static status UI; never write market data."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('static_release', ROOT/'deploy/reconciliation_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def main(expected_job):
    # Keep an independent artifact and evidence trail; reuse the existing
    # account/job drift guard and presigned-in-memory Amplify uploader.
    release.OUT = ROOT/'outputs/fast-refresh/website'
    release.OUT.mkdir(parents=True, exist_ok=True)
    release.publish(expected_job)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-job', required=True)
    args = parser.parse_args()
    main(args.expected_job)
