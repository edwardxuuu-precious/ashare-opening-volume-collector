#!/usr/bin/env python3
"""Root-only fixed shutdown action after the current service invocation safely finishes."""
import json
import os
from pathlib import Path
import subprocess

STATE = Path('/var/lib/stock-collector/worker-state.json')
MAINTENANCE = Path('/etc/stock-collector.maintenance')

def should_stop(state, invocation_id, maintenance):
    return (not maintenance and bool(invocation_id)
            and state.get('serviceInvocationId') == invocation_id
            and state.get('shutdownReady') is True
            and state.get('status') in ('completed', 'paused'))

def main():
    try:
        # Bounded, data-only parsing. State cannot supply commands or paths.
        if STATE.stat().st_size > 128 * 1024:
            return
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return
    if should_stop(state, os.environ.get('INVOCATION_ID', ''), MAINTENANCE.exists()):
        subprocess.run(['/usr/sbin/shutdown', '-h', '+1'], check=True)

if __name__ == '__main__': main()
