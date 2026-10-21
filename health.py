#!/usr/bin/env python3
"""Is data still coming in? Writes build/health.json for the website, and opens a GitHub issue
(which emails the repository owner) when the main movies have had no new reading for STALE
minutes; closes it again once data flows. Needs GH_TOKEN with issues: write for the issue part.
"""
import json
import os
import subprocess
from datetime import datetime, timedelta

import series
from series import IST

STALE = timedelta(minutes=45)
TITLE = 'Data collection looks stalled'


def gh(*args):
    try:
        return subprocess.run(['gh', *args], capture_output=True, text=True, timeout=60)
    except Exception as e:
        print(f'gh failed: {e}')
        return None


def main():
    now = datetime.now(IST)
    status = {}
    for key, name, path, entry in series.movies():
        if not entry.get('manual'):
            continue
        rows = series.load(path)
        status[name] = rows[-1].time.astimezone(IST).strftime('%Y-%m-%d %H:%M') if rows else None
    newest = max((datetime.strptime(t, '%Y-%m-%d %H:%M').replace(tzinfo=IST) for t in status.values() if t),
                 default=None)
    ok = newest is not None and now - newest <= STALE
    os.makedirs(os.path.join(series.ROOT, 'build'), exist_ok=True)
    with open(os.path.join(series.ROOT, 'build', 'health.json'), 'w', encoding='utf-8') as f:
        json.dump({'checked': now.strftime('%Y-%m-%d %H:%M'), 'ok': ok,
                   'newest': newest.strftime('%Y-%m-%d %H:%M') if newest else None, 'movies': status}, f)
    print(f"health: {'ok' if ok else 'STALE'}; newest reading {newest}")
    if not os.environ.get('GH_TOKEN') or not os.environ.get('GITHUB_REPOSITORY'):
        return
    repo = os.environ['GITHUB_REPOSITORY']
    found = gh('issue', 'list', '--repo', repo, '--state', 'open', '--search', f'"{TITLE}" in:title',
               '--json', 'number', '--jq', '.[0].number')
    number = found.stdout.strip() if found and found.returncode == 0 else ''
    if not ok and not number:
        gh('issue', 'create', '--repo', repo, '--title', TITLE, '--body',
           f'No new reading for the main movies since {newest:%Y-%m-%d %H:%M} IST (checked {now:%H:%M}). '
           'Hourly posts are paused until data flows again. Check the "BMS Trend Tracker" workflow runs.\n\n'
           '_Opened automatically by health.py; it closes itself when data is back._')
    elif ok and number:
        gh('issue', 'close', number, '--repo', repo, '--comment', f'Data is flowing again (newest reading {newest:%H:%M} IST).')


if __name__ == '__main__':
    main()
