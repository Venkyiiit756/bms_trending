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
TITLE = 'Tracker needs attention'


def gh(*args):
    try:
        return subprocess.run(['gh', *args], capture_output=True, text=True, timeout=60)
    except Exception as e:
        print(f'gh failed: {e}')
        return None


def checks(now, newest):
    """The watchdog's checklist: [{name, ok, detail}]. Thresholds are generous so a late run is not an alarm."""
    import csv
    out = []

    def add(name, ok, detail):
        out.append({'name': name, 'ok': bool(ok), 'detail': detail})
    age = (now - newest) if newest else None
    add('Ticket readings', age is not None and age <= STALE,
        f"newest {newest:%a %-d %b %-I:%M %p}" if newest else 'no readings')
    last = None
    path = os.path.join(series.ROOT, 'data', 'interest.csv')
    if os.path.exists(path):
        with open(path, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                last = r['timestamp']
    t = datetime.fromisoformat(last) if last else None
    add('Interest counts', t is not None and now - t <= timedelta(hours=5),
        f"newest {t:%a %-d %b %-I:%M %p}" if t else 'no rows')
    try:
        cat = json.load(open(os.path.join(series.ROOT, 'catalog.json'), encoding='utf-8'))
        seen = max((e.get('upcoming') or e.get('last_listed') or '' for e in cat.values()), default='')
        d = datetime.strptime(seen, '%Y-%m-%d').date() if seen else None
    except Exception:
        d = None
    add('Movie discovery', d is not None and (now.date() - d).days <= 2, f"upcoming pages last read {d:%a %-d %b}" if d else 'never')
    try:
        bd = json.load(open(os.path.join(series.ROOT, 'briefing.json'), encoding='utf-8')).get('date')
    except Exception:
        bd = None
    due = now.hour * 60 + now.minute >= 9 * 60 + 45
    add('Daily briefing', bd == now.date().isoformat() or not due, f'written for {bd}' if bd else 'none yet')
    return out


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
    cks = checks(now, newest)
    os.makedirs(os.path.join(series.ROOT, 'build'), exist_ok=True)
    with open(os.path.join(series.ROOT, 'build', 'health.json'), 'w', encoding='utf-8') as f:
        json.dump({'checked': now.strftime('%Y-%m-%d %H:%M'), 'ok': ok,
                   'newest': newest.strftime('%Y-%m-%d %H:%M') if newest else None, 'movies': status, 'checks': cks}, f)
    print(f"health: {'ok' if ok else 'STALE'}; newest reading {newest}")
    if not os.environ.get('GH_TOKEN') or not os.environ.get('GITHUB_REPOSITORY'):
        return
    repo = os.environ['GITHUB_REPOSITORY']
    bad = [c for c in cks if not c['ok']]
    found = gh('issue', 'list', '--repo', repo, '--state', 'open', '--search', f'"{TITLE}" in:title',
               '--json', 'number', '--jq', '.[0].number')
    number = found.stdout.strip() if found and found.returncode == 0 else ''
    if bad and not number:
        lines = '\n'.join(f"- **{c['name']}**: {c['detail']}" for c in bad)
        gh('issue', 'create', '--repo', repo, '--title', TITLE, '--body',
           f'The watchdog (checked {now:%H:%M} IST) found:\n\n{lines}\n\n'
           'Check the "BMS Trend Tracker" and "Discover Hyderabad Movies" workflow runs.\n\n'
           '_Opened automatically by health.py; it closes itself when everything is back._')
    elif not bad and number:
        gh('issue', 'close', number, '--repo', repo, '--comment', 'All checks are green again.')


if __name__ == '__main__':
    main()
