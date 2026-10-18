#!/usr/bin/env python3
"""Save a table image of the Hyderabad movies' hourly ticket figures at each full hour (IST).

For every hour boundary (11:00, 12:00, ...) it takes each tracked movie's latest reading
at or before that time (no older than MAX_AGE) and writes, under snapshots/<date>/:
    <date>_<HHMM>_last-1-hour.png   movies showing "tickets booked in last 1 hour"
    <date>_<HHMM>_last-24-hours.png movies showing the 24-hour figure instead (night, small films)
    <date>_<HHMM>.csv               the same numbers as data
and refreshes snapshots/latest-last-1-hour.png. Missing hours of the last LOOKBACK
hours are filled in, so a late or skipped run is made up by the next one.

    python snapshot.py                    # catch up on all missing hours
    python snapshot.py --at "2026-10-18 11:00"
"""
import argparse
import csv
import json
import os
import shutil
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'snapshots')
MAX_AGE = timedelta(minutes=75)     # a reading older than this is not shown
LOOKBACK = 6                        # hours of catch-up
# Friendlier names where several events share a title
NAMES = {'drishyam': 'Drishyam', 'jailer2_hyd': 'Jailer 2 (Telugu)', 'jailer2': 'Jailer 2 (Hindi)'}


def movies():
    with open(os.path.join(ROOT, 'movies.json'), encoding='utf-8') as f:
        return json.load(f)


def readings(path):
    """[(time, metric_type, value, movie_name)] of data rows, oldest first."""
    if not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf-8') as f:
        return [(datetime.fromisoformat(r['timestamp']), r['metric_type'], r['metric_value'], r['movie_name'])
                for r in csv.DictReader(f) if r['metric_type'] in ('1_hour', '24_hours')]


def value_k(text):
    t = text.upper()
    return float(t[:-1]) * (1000 if t[-1] == 'K' else 1e6 if t[-1] == 'M' else 1) if t[-1] in 'KM' else float(t)


def collect(at, data):
    """{'1_hour': [(name, value, reading_time)], '24_hours': [...]} as of `at`."""
    out = {'1_hour': [], '24_hours': []}
    for m in movies():
        rows = [r for r in data[m['key']] if r[0] <= at]
        if not rows:
            continue
        t, kind, val, title = rows[-1]
        if at - t > MAX_AGE:
            continue
        name = NAMES.get(m['key']) or title or m['name']
        out[kind].append((name, val, t))
    for k in out:
        out[k].sort(key=lambda r: -value_k(r[1]))
    return out


def draw(rows, head, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(2.9, 0.3 * (len(rows) + 1) + 0.1), dpi=300)
    ax.axis('off')
    tb = ax.table(cellText=[[n, v] for n, v, _ in rows], colLabels=['Movie', head],
                  cellLoc='center', loc='center', colWidths=[.64, .36])
    tb.auto_set_font_size(False)
    tb.set_fontsize(8.5)
    pal = ['#c0392b', '#1f3a5f', '#0a7d33', '#8e44ad', '#b9770e', '#117a8b', '#555']
    for (r, c), x in tb.get_celld().items():
        x.set_edgecolor('#222')
        x.set_linewidth(0.8)
        x.set_height(1 / (len(rows) + 1))
        t = x.get_text()
        t.set_fontweight('bold')
        if r == 0:
            x.set_facecolor('#1f3a5f')
            t.set_color('white')
            continue
        x.set_facecolor('#f6f8fb' if r % 2 else 'white')
        t.set_color('#0a7d33' if c else pal[(r - 1) % len(pal)])
    plt.savefig(path, bbox_inches='tight', pad_inches=0.03)
    plt.close(fig)


def snapshot(at, data):
    day = os.path.join(OUT, f'{at:%Y-%m-%d}')
    stem = os.path.join(day, f'{at:%Y-%m-%d_%H%M}')
    os.makedirs(day, exist_ok=True)
    got = collect(at, data)
    names = {'1_hour': ('last-1-hour', 'Last 1 hr'), '24_hours': ('last-24-hours', 'Last 24 hrs')}
    for kind, (suffix, head) in names.items():
        if got[kind]:
            draw(got[kind], head, f'{stem}_{suffix}.png')
    with open(stem + '.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['snapshot_time_ist', 'movie', 'metric', 'value', 'reading_time_ist'])
        for kind in got:
            for name, val, t in got[kind]:
                w.writerow([f'{at:%Y-%m-%d %H:%M}', name, kind, val, f'{t:%Y-%m-%d %H:%M}'])
    return stem, got


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--at', help="IST hour, 'YYYY-MM-DD HH:00'; default: all missing recent hours")
    args = ap.parse_args()
    data = {m['key']: readings(os.path.join(ROOT, m['csv'])) for m in movies()}
    now = datetime.now(IST)
    if args.at:
        hours = [datetime.strptime(args.at, '%Y-%m-%d %H:%M').replace(tzinfo=IST)]
    else:
        top = now.replace(minute=0, second=0, microsecond=0)
        hours = [top - timedelta(hours=i) for i in range(LOOKBACK - 1, -1, -1)]
    latest = None
    for at in hours:
        csv_path = os.path.join(OUT, f'{at:%Y-%m-%d}', f'{at:%Y-%m-%d_%H%M}.csv')
        if not args.at and os.path.exists(csv_path):
            continue
        stem, got = snapshot(at, data)
        print(f'{at:%Y-%m-%d %H:%M}: {len(got["1_hour"])} hourly, {len(got["24_hours"])} 24-hour movies')
        if got['1_hour']:
            latest = stem + '_last-1-hour.png'
    if latest:
        shutil.copyfile(latest, os.path.join(OUT, 'latest-last-1-hour.png'))


if __name__ == '__main__':
    main()
