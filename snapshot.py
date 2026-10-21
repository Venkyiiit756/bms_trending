#!/usr/bin/env python3
"""Snapshot of every tracked movie's ticket figures each half hour (:00 and :30, IST).

For each half-hour boundary it takes each movie's latest clean reading at or before that time
(no older than MAX_AGE; stale cached copies are dropped, see series.py) and writes:
    snapshots/<date>/<date>_<HHMM>.csv                 the numbers (kept in this repo)
    build/snapshots/<date>/<date>_<HHMM>_last-1-hour.png    table image, movies showing the 1-hour figure
    build/snapshots/<date>/<date>_<HHMM>_last-24-hours.png  table image, movies showing the 24-hour figure
Images go to build/ only; the website repository keeps them. Missing half-hours of the last
LOOKBACK steps are filled in, so a late or skipped run is made up by the next one.

    python snapshot.py                    # catch up on all missing half-hours
    python snapshot.py --at "2026-10-18 11:00"
"""
import argparse
import csv
import os
import shutil
from datetime import datetime, timedelta

import cards
import series
from series import IST

ROOT = series.ROOT
OUT = os.path.join(ROOT, 'snapshots')
IMG = os.path.join(ROOT, 'build', 'snapshots')
MAX_AGE = timedelta(minutes=75)     # small films are polled hourly
LOOKBACK = 12                       # half-hour steps of catch-up (= 6 hours)


def collect(at, data):
    """{'1_hour': [(name, text, reading_time)], '24_hours': [...]} as of `at`, biggest first."""
    out = {'1_hour': [], '24_hours': []}
    for key, name, _, _ in series.movies():
        r = series.latest(data.get(key, []), at, MAX_AGE)
        if r:
            out[r.kind].append((name, r.text, r.time, r.value))
    for k in out:
        out[k].sort(key=lambda x: -x[3])
    return out


def snapshot(at, data):
    stem = f'{at:%Y-%m-%d}/{at:%Y-%m-%d_%H%M}'
    os.makedirs(os.path.join(OUT, f'{at:%Y-%m-%d}'), exist_ok=True)
    os.makedirs(os.path.join(IMG, f'{at:%Y-%m-%d}'), exist_ok=True)
    got = collect(at, data)
    when = f'{at:%a %d %b}'
    titles = {'1_hour': ('last-1-hour', 'Last 1 hr', f'{when} · {series.window_label(at)}'),
              '24_hours': ('last-24-hours', 'Last 24 hrs', f'{when} · up to {at:%-I:%M %p}')}
    images = []
    for kind, (suffix, head, title) in titles.items():
        if got[kind]:
            path = os.path.join(IMG, f'{stem}_{suffix}.png')
            cards.compact_table(path, title, ['Movie', head], [[series.short_name(n), v] for n, v, _, _ in got[kind]])
            images.append(path)
    with open(os.path.join(OUT, stem + '.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['snapshot_time_ist', 'movie', 'metric', 'value', 'reading_time_ist'])
        for kind in got:
            for name, val, t, _ in got[kind]:
                w.writerow([f'{at:%Y-%m-%d %H:%M}', name, kind, val, f'{t.astimezone(IST):%Y-%m-%d %H:%M}'])
    return got, images


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--at', help="IST time, 'YYYY-MM-DD HH:00' or 'HH:30'; default: all missing recent half-hours")
    args = ap.parse_args()
    data = {key: series.load(path) for key, _, path, _ in series.movies()}
    now = datetime.now(IST)
    if args.at:
        steps = [datetime.strptime(args.at, '%Y-%m-%d %H:%M').replace(tzinfo=IST)]
    else:
        top = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
        steps = [top - timedelta(minutes=30 * i) for i in range(LOOKBACK - 1, -1, -1)]
    latest = None
    for at in steps:
        if not args.at and os.path.exists(os.path.join(OUT, f'{at:%Y-%m-%d}', f'{at:%Y-%m-%d_%H%M}.csv')):
            continue
        got, images = snapshot(at, data)
        print(f'{at:%Y-%m-%d %H:%M}: {len(got["1_hour"])} hourly, {len(got["24_hours"])} 24-hour movies')
        latest = next((p for p in images if p.endswith('last-1-hour.png')), latest)
    if latest:
        shutil.copyfile(latest, os.path.join(ROOT, 'build', 'latest-last-1-hour.png'))


if __name__ == '__main__':
    main()
