#!/usr/bin/env python3
"""Release radar: films coming in the next 3 weeks, how awaited they are, and whether bookings are open.

No language model needed: it is a table made from catalog.json (release date, bookings open) and the hourly
interest history (data/interest.csv). build_site.py writes it to data/radar.json for the Reports tab.
"""
import json
import os
from datetime import datetime, timedelta

import briefing_facts as bf
import interest
import series

ROOT = series.ROOT
AHEAD_DAYS = 21
BACK_DAYS = 1          # keep a film for a day after release


def build(today=None):
    today = today or datetime.now(series.IST).date()
    now = datetime.now(series.IST)
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8'))
    best = {}
    for code, m in interest.series().items():
        c = catalog.get(code, {})
        rel = interest.release_day(c.get('release') or m['release'])
        if rel is None or not today - timedelta(days=BACK_DAYS) <= rel <= today + timedelta(days=AHEAD_DAYS) or not m['points']:
            continue
        t, v = m['points'][-1]
        day_ago = [p for p in m['points'] if p[0] <= t - timedelta(hours=22)]
        week_ago = [p for p in m['points'] if p[0] <= t - timedelta(days=6)]
        name = series.readable(c.get('name') or m['name'], c.get('page_slug'))
        e = {'name': name, 'release': f'{rel:%a} {rel.day} {rel:%b}' + (f' {rel.year}' if rel.year != today.year else ''), 'release_date': rel.isoformat(), 'days': (rel - today).days,
             'interested': bf.fmt(v) + '+', 'interested_value': v,
             'gain_24h': (('+' if v >= day_ago[-1][1] else '−') + bf.fmt(abs(v - day_ago[-1][1]))) if day_ago else None,
             'gain_7d': (('+' if v >= week_ago[-1][1] else '−') + bf.fmt(abs(v - week_ago[-1][1]))) if week_ago else None,
             'bookings': 'open' if c.get('book') else 'not open yet' if c.get('book') is False else 'unknown',
             'languages': c.get('languages', []), 'updated': f'{t.astimezone(series.IST):%a %-d %b %-I:%M %p}'}
        key = c.get('group') or code
        if key not in best or len(m['points']) > best[key][0]:
            best[key] = (len(m['points']), e)
    rows = sorted((e for _, e in best.values()), key=lambda e: (e['release_date'], -e['interested_value']))
    return {'generated': now.isoformat(timespec='minutes'), 'ahead_days': AHEAD_DAYS, 'films': rows}


if __name__ == '__main__':
    print(json.dumps(build(), indent=1, ensure_ascii=False))
