#!/usr/bin/env python3
"""Find Hyderabad's movies by following the "other movies" list inside each movie's own response.

The explore page is blocked for GitHub runners, but every synopsis response we already read
for ticket counts carries a widget listing ~10 other movies (event codes and links). Starting
from the movies we track, this crawls those lists (a few gentle requests, spaced out) until
nothing new turns up, and writes catalog.json:

    {eventcode: {name, page_slug, first_seen, last_listed, last_checked, figure}}

`last_listed` is the last date the movie appeared in another movie's list. update_movies.py
uses the catalog to add new movies to movies.json and to retire ones that have disappeared.
Stops at the first 403/429 and keeps what it has.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(ROOT, 'catalog.json')
BASE = 'https://in.bookmyshow.com'
HEADERS = {'Accept': 'application/json, text/plain, */*', 'x-platform': 'AND', 'x-platform-code': 'ANDROID',
           'x-app-code': 'MOBAND2', 'x-region-code': 'HYD', 'x-region-slug': 'hyderabad',
           'x-app-version': '18.2.8', 'x-app-version-code': '18281', 'Origin': BASE}
LINK = re.compile(r'/([a-z0-9][a-z0-9-]*)/(ET\d{8})')
MAX_FETCHES = 80
GAP = 8                      # seconds between requests


class Blocked(Exception):
    pass


def today():
    return datetime.now(IST).strftime('%Y-%m-%d')


def related(data, own):
    """{eventcode: slug} for the other movies listed inside a synopsis response."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            for slug, code in LINK.findall(node):
                if code != own:
                    found.setdefault(code, slug)
    widgets = data.get('widgets') if isinstance(data.get('widgets'), dict) else {}
    for w in widgets.values():
        if isinstance(w, dict) and isinstance(w.get('listData'), list):
            walk(w['listData'])
    return found


def trending(data):
    for w in (data.get('widgets') or {}).values():
        obj = (w or {}).get('objectData') or {}
        if (obj.get('action') or {}).get('label') == 'Trending':
            text = ''.join(c.get('text', '') for c in (obj.get('text') or {}).get('components', []))
            if 'last 1 hour' in text.lower():
                return '1_hour'
            if 'last 24 hours' in text.lower():
                return '24_hours'
    return 'none'


def fetch(code):
    from curl_cffi import requests
    r = requests.get(f'{BASE}/api/movies/v1/synopsis/secondary?eventcode={code}&channel=mobile',
                     headers=HEADERS, impersonate='chrome', timeout=30)
    if r.status_code in (403, 429):
        raise Blocked(f'HTTP {r.status_code}')
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def known_codes():
    with open(os.path.join(ROOT, 'movies.json'), encoding='utf-8') as f:
        return {m['eventcode']: m for m in json.load(f)}


def main():
    catalog = json.load(open(CATALOG, encoding='utf-8')) if os.path.exists(CATALOG) else {}
    tracked = known_codes()
    day = today()
    for code, m in tracked.items():
        catalog.setdefault(code, {'name': m['name'], 'page_slug': m['page_slug'], 'first_seen': day,
                                  'last_listed': None, 'last_checked': None, 'figure': None})
    queue = list(tracked)
    seen, fetches, blocked = set(), 0, False
    while queue and fetches < MAX_FETCHES:
        code = queue.pop(0)
        if code in seen:
            continue
        seen.add(code)
        try:
            data = fetch(code)
        except Blocked as e:
            print(f'blocked ({e}); keeping what was found', flush=True)
            blocked = True
            break
        except Exception as e:
            print(f'{code}: request error {e}', flush=True)
            data = None
        fetches += 1
        time.sleep(GAP)
        if not data:
            continue
        entry = catalog.setdefault(code, {'first_seen': day, 'last_listed': None})
        meta = data.get('meta') if isinstance(data.get('meta'), dict) else {}
        name = (meta.get('event') or {}).get('eventName') if isinstance(meta.get('event'), dict) else None
        if name:
            entry['name'] = name
        entry['figure'] = trending(data)
        entry['last_checked'] = day
        for other, slug in related(data, code).items():
            o = catalog.setdefault(other, {'first_seen': day, 'last_listed': None, 'name': other,
                                           'page_slug': slug, 'last_checked': None, 'figure': None})
            o['last_listed'] = day
            o.setdefault('page_slug', slug)
            if other not in seen and other not in queue:
                queue.append(other)
    new = [c for c in catalog if c not in tracked]
    print(f'{fetches} requests; catalog {len(catalog)} movies; {len(new)} not tracked yet', flush=True)
    if fetches == 0 and blocked:
        sys.exit('Nothing fetched; leaving the catalog unchanged.')
    with open(CATALOG, 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(catalog.items())), f, indent=1)


if __name__ == '__main__':
    main()
