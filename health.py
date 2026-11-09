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
HOT_STALE = timedelta(minutes=60)     # a hot film with no reading (not even a heartbeat) for this long raises the alarm
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
    # Hot films (polled at least every 30 minutes) must never go quiet on a big day: each missed reading is lost for good.
    # Live check (this is the alarm: it opens the 'Tracker needs attention' issue, which emails the owner) + a 6-hour history line.
    try:
        movies = json.load(open(os.path.join(series.ROOT, 'movies.json'), encoding='utf-8'))
        movies = movies.values() if isinstance(movies, dict) else movies
        hot = [m for m in movies if not m.get('paused') and (not m.get('every') or m['every'] <= 1800)]
        silent, holes, since = [], [], now - timedelta(hours=6)
        for m in hot:
            ts = sorted(datetime.fromisoformat(r['timestamp'])
                        for r in csv.DictReader(open(os.path.join(series.ROOT, m['csv']), newline='', encoding='utf-8'))
                        if r['kind'] != 'error' and r['timestamp'] >= (since - timedelta(hours=40)).isoformat())
            if not ts or now - ts[-1] > timedelta(hours=36):
                continue                                    # a film that no longer shows a counter is not a hot film any more
            if now - ts[-1] > HOT_STALE:
                silent.append(f"{m['name'][:22]} silent {round((now - ts[-1]).total_seconds() / 60)} min")
            pts = [t for t in ts if t >= since]
            gap = max(((b_ - a_).total_seconds() / 60 for a_, b_ in zip(pts, pts[1:])), default=0)
            if gap > 75:
                holes.append(f"{m['name'][:22]} {round(gap)} min")
        add('Hot films: reading on time', not silent, ', '.join(silent) if silent else f'{len(hot)} hot films read within the last {round(HOT_STALE.total_seconds() / 60)} min')
        add('Hot films: gaps in the last 6 h (info)', True, ', '.join(holes) if holes else 'none over 75 min')
    except Exception as e:
        add('Hot films: reading on time', True, f'not checked ({str(e)[:60]})')
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
