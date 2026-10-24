#!/usr/bin/env python3
"""Build the website files in site/ for the public site repository.

site/ = index.html + manifest.json + snapshots/<day>/*.csv + history/<movie>.csv + data/*.json
        + the images made in this run (build/snapshots, build/posts, latest image, health.json).
Older images are not rebuilt: the site repository keeps them (the publish step only adds files
and prunes folders older than KEEP_DAYS).
"""
import csv
import json
import os
import re
import shutil

import interest
import sales
import series

ROOT = series.ROOT
SITE = os.path.join(ROOT, 'site')
SNAP = os.path.join(ROOT, 'snapshots')
BUILD = os.path.join(ROOT, 'build')
DATA_DAYS = 21


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def main():
    if os.path.exists(SITE):
        shutil.rmtree(SITE)
    os.makedirs(os.path.join(SITE, 'history'))
    shutil.copy(os.path.join(ROOT, 'site_src', 'index.html'), SITE)

    # snapshot list and which images each one has, from the CSVs kept in this repository
    snaps, files = {}, {}
    for day in sorted(os.listdir(SNAP)):
        d = os.path.join(SNAP, day)
        if not os.path.isdir(d):
            continue
        os.makedirs(os.path.join(SITE, 'snapshots', day))
        for fn in sorted(os.listdir(d)):
            m = re.match(r'(\d{4}-\d\d-\d\d)_(\d{4})\.csv$', fn)
            if not m:
                continue
            date, hhmm = m.groups()
            shutil.copy(os.path.join(d, fn), os.path.join(SITE, 'snapshots', day, fn))
            snaps.setdefault(date, []).append(hhmm)
            with open(os.path.join(d, fn), newline='', encoding='utf-8') as f:
                kinds = {r['metric'] for r in csv.DictReader(f)}
            files[f'{date}/{date}_{hhmm}'] = {'last-1-hour': '1_hour' in kinds, 'last-24-hours': '24_hours' in kinds}

    # images made in this run
    for sub in ('snapshots', 'posts'):
        if os.path.isdir(os.path.join(BUILD, sub)):
            shutil.copytree(os.path.join(BUILD, sub), os.path.join(SITE, sub), dirs_exist_ok=True)
    for fn in ('latest-last-1-hour.png', 'health.json'):
        if os.path.exists(os.path.join(BUILD, fn)):
            shutil.copy(os.path.join(BUILD, fn), SITE)

    # clean full history per movie (stale copies removed)
    history = []
    for key, name, path, _ in series.movies():
        rows = series.load(path)
        if not rows:
            continue
        rel = f'history/{slug(name)}.csv'
        with open(os.path.join(SITE, rel), 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['time_ist', 'figure', 'value', 'tickets'])
            prev = None
            for r in rows:
                if (r.kind, r.text) != prev:
                    w.writerow([r.time.astimezone(series.IST).strftime('%Y-%m-%d %H:%M:%S'),
                                'last_1_hour' if r.kind == '1_hour' else 'last_24_hours', r.text, int(r.value)])
                    prev = (r.kind, r.text)
        history.append({'name': name, 'file': rel})
    history.sort(key=lambda h: h['name'])

    # compact half-hour data for the in-browser views and image studio (last DATA_DAYS days)
    names, rows_by_day = [], {}
    keep = sorted(snaps)[-DATA_DAYS:]
    for date in keep:
        for hhmm in sorted(set(snaps[date])):
            with open(os.path.join(SNAP, date, f'{date}_{hhmm}.csv'), newline='', encoding='utf-8') as f:
                for r in csv.DictReader(f):
                    r['movie'] = series.ALIASES.get(r['movie'], r['movie'])
                    if r['movie'] not in names:
                        names.append(r['movie'])
                    try:
                        num = round(series.value(r['value']))
                    except ValueError:
                        continue
                    rows_by_day.setdefault(date, {}).setdefault(hhmm, []).append(
                        [names.index(r['movie']), 1 if r['metric'] == '1_hour' else 24, num, r['value'],
                         r['reading_time_ist'][11:16]])
    os.makedirs(os.path.join(SITE, 'data'), exist_ok=True)
    with open(os.path.join(SITE, 'data', 'snapshots.json'), 'w', encoding='utf-8') as f:
        json.dump({'movies': names, 'days': rows_by_day}, f, separators=(',', ':'), ensure_ascii=False)
    # "interested" counts of upcoming films (see interest.py), with release / bookable from the catalog
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8'))
    films = []
    for code, m in interest.series().items():
        c = catalog.get(code, {})
        films.append({'code': code, 'name': c.get('name') or m['name'], 'release': c.get('release') or m['release'],
                      'book': c.get('book'), 'languages': c.get('languages', []),
                      'url': f"https://in.bookmyshow.com/movies/hyderabad/{c.get('page_slug', 'movie')}/{code}",
                      'points': [[t.astimezone(series.IST).strftime('%Y-%m-%d %H:%M'), v] for t, v in m['points']]})
    # one entry per BookMyShow film: other listings of it (formats / languages) show the same count
    best = {}
    for fl in films:
        key = catalog.get(fl['code'], {}).get('group') or fl['code']
        if key not in best or len(fl['points']) > len(best[key]['points']):
            best[key] = fl
    films = list(best.values())
    with open(os.path.join(SITE, 'data', 'interest.json'), 'w', encoding='utf-8') as f:
        json.dump({'films': films}, f, separators=(',', ':'), ensure_ascii=False)
    if os.path.exists(interest.CSV):
        shutil.copy(interest.CSV, os.path.join(SITE, 'data', 'interest.csv'))
    # tickets per half hour since tracking began, for daily totals (see sales.py)
    with open(os.path.join(SITE, 'data', 'sales.json'), 'w', encoding='utf-8') as f:
        json.dump(sales.build(), f, separators=(',', ':'), ensure_ascii=False)

    posts_path = os.path.join(ROOT, 'posts_log.json')
    posts = json.load(open(posts_path, encoding='utf-8')) if os.path.exists(posts_path) else []
    posts = sorted(posts, key=lambda p: p['slot'], reverse=True)[:48]

    with open(os.path.join(SITE, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'snapshots': {d: sorted(set(h)) for d, h in snaps.items()}, 'files': files,
                   'history': history, 'posts': posts, 'handle': series.HANDLE}, f, ensure_ascii=False)
    open(os.path.join(SITE, '.nojekyll'), 'w').close()
    print(f'site: {sum(len(set(v)) for v in snaps.values())} snapshots over {len(snaps)} days, '
          f'{len(history)} history files, {len(posts)} posts')


if __name__ == '__main__':
    main()
