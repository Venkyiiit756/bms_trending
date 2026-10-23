#!/usr/bin/env python3
"""How many people marked a movie "interested" on BookMyShow, for upcoming and just-released films.

The count is in the app's synopsis "static" response (" 35.5K+ are interested "), rounded by
BookMyShow like the ticket counters. tracker.py asks for it every INTEREST_EVERY seconds for each
film from targets(), inside its own paced request loop, and appends rows to data/interest.csv:

    timestamp, eventcode, movie_name, text, interested, release

Replaces the separate bms-interest-tracker repository (a headless-browser scraper of the web page
with a fixed 2025 movie list).

    python interest.py --fetch ET00475942     # one request, JSON on stdout (used by tracker.py)
    python interest.py                        # list today's targets
"""
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(ROOT, 'data', 'interest.csv')
FIELDS = ['timestamp', 'eventcode', 'movie_name', 'text', 'interested', 'release']
INTEREST_EVERY = 3 * 3600
AFTER_RELEASE = 3            # days a film stays a target after its release date
MAX_TARGETS = 30
COUNT = re.compile(r'([\d.]+)\s*([KkMm]?)\+?\s+(?:are\s+)?interested')


def parse(text):
    """'398K+' style text and value from a response body, or (None, None)."""
    m = COUNT.search(text)
    if not m:
        return None, None
    num, unit = float(m.group(1)), m.group(2).upper()
    return f"{m.group(1)}{unit}+", round(num * {'K': 1e3, 'M': 1e6}.get(unit, 1))


def fetch(code):
    """One request for the film's interested count; always a dict (error / status / detail on failure)."""
    import discover
    from curl_cffi import requests
    try:
        r = requests.get(f'{discover.BASE}/api/movies/v1/synopsis/init/static?eventcode={code}&channel=mobile',
                         headers=discover.HEADERS, impersonate='chrome', timeout=30)
    except Exception as e:
        return {'error': True, 'status': 0, 'detail': f'request error {e}'}
    if r.status_code != 200:
        return {'error': True, 'status': r.status_code, 'detail': f'HTTP {r.status_code}',
                'retry_after': int(r.headers.get('retry-after') or 0) if str(r.headers.get('retry-after') or '').isdigit() else 0}
    text, value = parse(r.text)
    if value is None:
        return {'error': True, 'status': 200, 'detail': 'no interested count'}
    return {'text': text, 'value': value}


def release_day(text):
    try:
        return datetime.strptime(text or '', '%b %d, %Y').date()
    except ValueError:
        return None


def targets(day=None):
    """[(code, name, release)] to follow: catalog films releasing from AFTER_RELEASE days ago onwards,
    plus watchlist codes, nearest release first."""
    day = day or datetime.now(IST).date()
    catalog_path = os.path.join(ROOT, 'catalog.json')
    catalog = json.load(open(catalog_path, encoding='utf-8')) if os.path.exists(catalog_path) else {}
    watch_path = os.path.join(ROOT, 'watchlist.json')
    watch = json.load(open(watch_path, encoding='utf-8')) if os.path.exists(watch_path) else []
    out, groups = {}, set()
    for code, e in sorted(catalog.items(), key=lambda kv: release_day(kv[1].get('release')) or day):
        r = release_day(e.get('release'))
        if r is None or r < day - timedelta(days=AFTER_RELEASE):
            continue
        if e.get('group') in groups:        # one listing per film (same count)
            continue
        groups.add(e.get('group'))
        out[code] = (code, e.get('name', code), e.get('release') or '')
    for w in watch:
        if w.get('code') and w['code'] not in out and catalog.get(w['code'], {}).get('group') not in groups:
            out[w['code']] = (w['code'], w.get('name', w['code']), catalog.get(w['code'], {}).get('release') or '')
    return list(out.values())[:MAX_TARGETS]


def last_polled():
    """{eventcode: datetime of its last row}."""
    out = {}
    if os.path.exists(CSV):
        with open(CSV, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                try:
                    out[r['eventcode']] = datetime.fromisoformat(r['timestamp'])
                except ValueError:
                    pass
    return out


def append(t, code, name, text, value, release):
    new = not os.path.exists(CSV)
    os.makedirs(os.path.dirname(CSV), exist_ok=True)
    with open(CSV, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if new:
            w.writerow(FIELDS)
        w.writerow([t.isoformat(timespec='seconds'), code, name, text, value, release])


def series():
    """{eventcode: {'name', 'release', 'points': [(datetime, value)]}} from the CSV."""
    out = {}
    if os.path.exists(CSV):
        with open(CSV, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f):
                try:
                    t, v = datetime.fromisoformat(r['timestamp']), int(r['interested'])
                except ValueError:
                    continue
                m = out.setdefault(r['eventcode'], {'name': r['movie_name'], 'release': r['release'], 'points': []})
                m['name'], m['release'] = r['movie_name'], r['release'] or m['release']
                m['points'].append((t, v))
    return out


if __name__ == '__main__':
    if sys.argv[1:2] == ['--fetch'] and len(sys.argv) == 3:
        print(json.dumps(fetch(sys.argv[2])))
    else:
        for code, name, release in targets():
            print(code, release, name)
