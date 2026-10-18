#!/usr/bin/env python3
"""Decide which movies to poll, and how often, from catalog.json and the data collected so far.

Adds newly discovered movies to movies.json, then sets each automatic movie's polling tier:

  fast   (no `every`)  polled at every cache expiry (~10 min): sales >= FAST_MIN an hour, at most FAST_MAX movies
  1800                 every 30 min: sales >= HALF_MIN an hour
  3600                 hourly: has a figure, or is new
  21600                every 6 h: no counter for NO_COUNTER_DAYS days, still listed
  paused               not listed anywhere for UNLISTED_DAYS days and no counter

Entries with "manual": true are never changed. A total request budget (BUDGET per hour) demotes the
smallest movies first. Every change is appended to MOVIE_CHANGES.md.
"""
import csv
import json
import os
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
FAST_MIN, FAST_MAX, HALF_MIN = 8000, 4, 3000
NO_COUNTER_DAYS, UNLISTED_DAYS, NEW_DAYS = 3, 7, 3
BUDGET = 80
COST = {None: 6.0, 1800: 2.0, 3600: 1.0, 21600: 1 / 6}


def value(text):
    t = text.strip().upper()
    mult = {'K': 1e3, 'M': 1e6}.get(t[-1], 1)
    return float(t[:-1] if mult != 1 else t) * mult


def stats(path, now):
    """(best 1-hour value in the last 24 h, last data-row time, last any-row time)."""
    best, last_data, last_any = 0.0, None, None
    if os.path.exists(path):
        with open(path, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                try:
                    t = datetime.fromisoformat(r['timestamp'])
                except ValueError:
                    continue
                last_any = t
                if r['metric_type'] in ('1_hour', '24_hours'):
                    last_data = t
                    if r['metric_type'] == '1_hour' and now - t <= timedelta(hours=24):
                        best = max(best, value(r['metric_value']))
    return best, last_data, last_any


def days_since(date_text, today):
    if not date_text:
        return None
    return (today - datetime.strptime(date_text, '%Y-%m-%d').date()).days


def main():
    now = datetime.now(IST)
    today = now.date()
    with open(os.path.join(ROOT, 'movies.json'), encoding='utf-8') as f:
        movies = json.load(f)
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8')) \
        if os.path.exists(os.path.join(ROOT, 'catalog.json')) else {}
    log = []
    have = {m['eventcode'] for m in movies}

    for code, c in sorted(catalog.items()):
        if code in have:
            continue
        name = c.get('name') if c.get('name') and c['name'] != code else code
        movies.append({'key': code.lower(), 'name': name, 'eventcode': code, 'region_code': 'HYD',
                       'region_slug': 'hyderabad', 'page_slug': c.get('page_slug') or 'movie',
                       'csv': f'data/{code}.csv', 'headers_csv': f'data/{code}_headers.csv',
                       'every': 3600, 'added': today.isoformat()})
        log.append(f'added **{name}** ({code}), polled hourly')

    auto = []
    for m in movies:
        if m.get('manual'):
            continue
        c = catalog.get(m['eventcode'], {})
        name = c.get('name') or m['name']
        best, last_data, last_any = stats(os.path.join(ROOT, m['csv']), now)
        added_days = days_since(m.get('added'), today)
        listed_days = days_since(c.get('last_listed'), today)
        quiet_days = (now - last_data).days if last_data else (days_since(m.get('added'), today) or 0)
        if m.get('added') and added_days is not None and added_days < NEW_DAYS and not last_data:
            tier = 3600
        elif best >= HALF_MIN:
            tier = 1800
        elif last_data and quiet_days < NO_COUNTER_DAYS:
            tier = 3600
        elif listed_days is not None and listed_days >= UNLISTED_DAYS and quiet_days >= NO_COUNTER_DAYS:
            tier = 'paused'
        else:
            tier = 21600
        auto.append([m, tier, best])

    # fastest tier: the biggest few, counting manual fast movies against the cap
    manual_fast = sum(1 for m in movies if m.get('manual') and m.get('every') is None and not m.get('paused'))
    room = max(FAST_MAX - manual_fast, 0)
    for m, tier, best in sorted(auto, key=lambda a: -a[2]):
        if tier == 1800 and best >= FAST_MIN and room > 0:
            a = next(x for x in auto if x[0] is m)
            a[1] = None
            room -= 1

    def spend():
        s = sum(0 if m.get('paused') else COST[m.get('every')] for m in movies if m.get('manual'))
        return s + sum(COST[t] for _, t, _ in auto if t != 'paused')
    for a in sorted(auto, key=lambda a: a[2]):        # smallest first
        if spend() <= BUDGET:
            break
        if a[1] is None:
            a[1] = 1800
        elif a[1] == 1800:
            a[1] = 3600
    over = spend() > BUDGET

    for m, tier, best in auto:
        old = 'paused' if m.get('paused') else m.get('every')
        new = tier
        name = (catalog.get(m['eventcode']) or {}).get('name') or m['name']
        if tier == 'paused':
            m['paused'] = True
        else:
            m.pop('paused', None)
            if tier is None:
                m.pop('every', None)
            else:
                m['every'] = tier
        if name and name != m['eventcode']:
            m['name'] = name
        if old != new and not (old == 3600 and new == 3600):
            label = {None: 'every ~10 min', 1800: 'every 30 min', 3600: 'hourly', 21600: 'every 6 h', 'paused': 'paused'}
            log.append(f'**{m["name"]}** ({m["eventcode"]}): {label.get(old, old)} -> {label[new]}'
                       f' (best hourly figure in 24 h: {best / 1000:.1f}K)')

    with open(os.path.join(ROOT, 'movies.json'), 'w', encoding='utf-8') as f:
        json.dump(movies, f, indent=2)
        f.write('\n')
    if over:
        log.append(f'note: still above the {BUDGET} requests/hour budget after demotions')
    if log:
        path = os.path.join(ROOT, 'MOVIE_CHANGES.md')
        head = '# Movie tracking changes\n\nWritten automatically by update_movies.py (daily).\n\n'
        old_text = open(path, encoding='utf-8').read().split('\n\n', 2)[-1] if os.path.exists(path) else ''
        entry = f'## {today.isoformat()}\n' + ''.join(f'- {l}\n' for l in log) + '\n'
        with open(path, 'w', encoding='utf-8') as f:
            f.write(head + entry + old_text)
    print('\n'.join(log) if log else 'no changes')
    print(f'estimated requests/hour: {spend():.0f}')


if __name__ == '__main__':
    main()
