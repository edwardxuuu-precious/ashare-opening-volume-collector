"""Small, price-free Actions summary: publishing and freshness are separate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def render(receipt):
    lines = ['## Long-cycle data check', '',
             f"Publication: **{receipt['state']}**",
             f"Data completeness: **{receipt['dataState']}**",
             f"Target trading date: {receipt['asOf']}",
             f"Checked at (UTC): {receipt['checkedAt']}", '',
             '| Source | Actual last date |', '| --- | --- |']
    lines.extend(f'| {code} | {day} |' for code, day in receipt['sourceDates'].items())
    lines += ['', receipt['message'], '',
              'The 22:40 Asia/Shanghai schedule is a target, not a guaranteed start time. '
              'A successful publication does not mean every source is current.']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = json.loads(args.receipt.read_text())
    with args.summary.open('a', encoding='utf-8') as stream:
        stream.write(render(receipt))
    if receipt['dataState'] != 'ready':
        # Fixed command text: do not interpolate upstream strings into Actions commands.
        print('::warning title=Cycle data incomplete::Publication completed but some data is pending. See the source dates in the job summary.')


if __name__ == '__main__':
    main()
