#!/usr/bin/env python3
"""Build the static website in site/ from snapshots/ and the movie logs.

site/ = index.html + manifest.json + snapshots/ (images, CSVs) + history/<movie>.csv
The hourly workflow copies site/ to the separate public site repository.
"""
import csv
import json
import os
import re
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(ROOT, 'site')
SNAP = os.path.join(ROOT, 'snapshots')
NAMES = {'drishyam': 'Drishyam', 'jailer2_hyd': 'Jailer 2 (Telugu)', 'jailer2': 'Jailer 2 (Hindi)'}
SKIP_HISTORY = {'jailer2_telugu'}


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def main():
    if os.path.exists(SITE):
        shutil.rmtree(SITE)
    os.makedirs(os.path.join(SITE, 'history'))
    shutil.copy(os.path.join(ROOT, 'site_src', 'index.html'), SITE)

    snaps, files = {}, {}
    for day in sorted(os.listdir(SNAP)):
        d = os.path.join(SNAP, day)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            m = re.match(r'(\d{4}-\d\d-\d\d)_(\d{4})(.*)\.(png|csv)$', fn)
            if not m:
                continue
            date, hhmm, suffix, ext = m.groups()
            if ext == 'csv':
                snaps.setdefault(date, []).append(hhmm)
            else:
                files.setdefault(f'{date}/{date}_{hhmm}', {})[suffix.lstrip('_')] = True
        shutil.copytree(d, os.path.join(SITE, 'snapshots', day))
    shutil.copy(os.path.join(SNAP, 'latest-last-1-hour.png'), SITE) if os.path.exists(
        os.path.join(SNAP, 'latest-last-1-hour.png')) else None

    history = []
    with open(os.path.join(ROOT, 'movies.json'), encoding='utf-8') as f:
        movies = json.load(f)
    for mv in movies:
        if mv['key'] in SKIP_HISTORY or not os.path.exists(os.path.join(ROOT, mv['csv'])):
            continue
        with open(os.path.join(ROOT, mv['csv']), newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        name = NAMES.get(mv['key']) or next((r['movie_name'] for r in rows if r['movie_name']), mv['name'])
        data = [(r['timestamp'], r['metric_type'], r['metric_value']) for r in rows
                if r['metric_type'] in ('1_hour', '24_hours') and r['kind'] in ('start', 'change')]
        if not data:
            continue
        rel = f'history/{slug(name)}.csv'
        with open(os.path.join(SITE, rel), 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['time_ist', 'figure', 'value'])
            w.writerows(data)
        history.append({'name': name, 'file': rel})
    history.sort(key=lambda h: h['name'])

    with open(os.path.join(SITE, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'snapshots': {d: sorted(set(h)) for d, h in snaps.items()}, 'files': files,
                   'history': history}, f)
    open(os.path.join(SITE, '.nojekyll'), 'w').close()
    print(f'site: {sum(len(set(v)) for v in snaps.values())} snapshots over {len(snaps)} days, {len(history)} history files')


if __name__ == '__main__':
    main()
