#!/usr/bin/env python3
"""Shows and how full they are: a snapshot of BookMyShow's public buytickets page for a film, per city and per day -> data/shows.csv.

The page embeds JSON (window.__INITIAL_STATE__ -> showtimesByEvent) with every venue and show and an availability code per show:
  1 available, 2 fast filling, 3 almost full, 0 sold out.
That is the capacity side of the forecast: shows and screens are what the counter's bookings have to fit into.
Cloudflare answers some city pages with HTTP 403 now and then; those are retried once and otherwise skipped.

    python shows.py ET00451914 namit-malhotras-ramayana           # today and the next 3 days, 8 cities
    python shows.py ET00451914 namit-malhotras-ramayana --days 1 --cities hyd,ncr
"""
import argparse
import csv
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import discover
import series

IST = series.IST
CSV = os.path.join(series.ROOT, 'data', 'shows.csv')
FIELDS = ['time_ist', 'code', 'city', 'date', 'venues', 'shows', 'sold_out', 'almost_full', 'fast_filling', 'available', 'shows_before_6pm', 'shows_6_to_9pm', 'shows_after_9pm']
CITIES = {'hyd': ('hyderabad', 'hyd'), 'mumbai': ('mumbai', 'mumbai'), 'ncr': ('ncr', 'ncr'), 'bang': ('bengaluru', 'bang'), 'chen': ('chennai', 'chen'),
          'kolk': ('kolkata', 'kolk'), 'pune': ('pune', 'pune'), 'ahd': ('ahmedabad', 'ahd')}


def state(html):
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*', html)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(html[m.end():])[0]
    except ValueError:
        return None


def venues(node, out, venue=None):
    """Collect every dict that is a session (carries sessionId and showTime/availStatus) with its nearest enclosing venue id."""
    if isinstance(node, dict):
        if 'sessionId' in node and ('showTime' in node or 'availStatus' in node):
            out.append((venue, node))
            return
        for k in ('venueCode', 'venue_code', 'VenueCode', 'venueName', 'name'):
            if k.lower().startswith('venue') and isinstance(node.get(k), str):
                venue = node[k]
                break
        for v in node.values():
            venues(v, out, venue)
    elif isinstance(node, list):
        for v in node:
            venues(v, out, venue)


def summarize(st):
    found = []
    venues((st or {}).get('showtimesByEvent') or st, found)
    seen, sessions, names = set(), [], set()
    for venue, s in found:
        if s['sessionId'] in seen:
            continue
        seen.add(s['sessionId'])
        sessions.append(s)
        names.add(venue or s.get('venueCode') or s.get('venue_code'))
    names.discard(None)
    c = Counter(str(s.get('availStatus')) for s in sessions)
    t = [int(str(s.get('showTimeCode') or '0000')[:2]) for s in sessions]
    return {'venues': len(names), 'shows': len(sessions), 'sold_out': c['0'], 'almost_full': c['3'], 'fast_filling': c['2'], 'available': c['1'],
            'shows_before_6pm': sum(1 for h in t if h < 18), 'shows_6_to_9pm': sum(1 for h in t if 18 <= h < 21), 'shows_after_9pm': sum(1 for h in t if h >= 21)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('code')
    ap.add_argument('slug')
    ap.add_argument('--days', type=int, default=4)
    ap.add_argument('--cities', default=','.join(CITIES))
    a = ap.parse_args()
    from curl_cffi import requests
    now = datetime.now(IST)
    rows = []
    for city in a.cities.split(','):
        cslug, rc = CITIES[city]
        for d in range(a.days):
            day = (now + timedelta(days=d)).strftime('%Y%m%d')
            url = f'{discover.BASE}/buytickets/{a.slug}-{cslug}/movie-{rc}-{a.code}-MT/{day}'
            for attempt in (1, 2):
                r = requests.get(url, headers={'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9'}, impersonate='chrome', timeout=40)
                if r.status_code == 200:
                    break
                time.sleep(15)
            if r.status_code != 200:
                print(f'{city} {day}: HTTP {r.status_code}', flush=True)
                time.sleep(8)
                continue
            s = summarize(state(r.text))
            row = {'time_ist': now.strftime('%Y-%m-%d %H:%M'), 'code': a.code, 'city': city, 'date': day, **s}
            rows.append(row)
            print(f"{city:6} {day}: {s['shows']:4} shows, {s['venues']:3} venues | sold out {s['sold_out']}, almost full {s['almost_full']}, fast filling {s['fast_filling']}, available {s['available']}", flush=True)
            time.sleep(10)
    os.makedirs(os.path.dirname(CSV), exist_ok=True)
    new = not os.path.exists(CSV)
    with open(CSV, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


if __name__ == '__main__':
    main()
