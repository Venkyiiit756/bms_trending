#!/usr/bin/env python3
"""Print the booking-related fields BookMyShow returns for some movies (a diagnostic, run by hand).

    python probe.py ET00451914 ET00447840
    python probe.py --search fauzi king100      # try endpoints that list or search movies
    python probe.py --upcoming 10               # movies on the upcoming pages, with details for 10

For each movie: both synopsis responses' top-level keys, meta.event, and every field whose key
or text mentions booking, buttons, release, sale or status. Used to find what marks a movie
whose bookings have just opened.
"""
import json
import re
import sys
import time

import discover

PATTERN = re.compile(r'book|cta|button|interest|coming|release|open|sale|advance|status|avail|notify|soon', re.I)
URLS = {'init': '/api/movies/v1/synopsis/init/dynamic?eventcode={}&channel=mobile',
        'secondary': '/api/movies/v1/synopsis/secondary?eventcode={}&channel=mobile'}


def walk(node, path, out):
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, f'{path}.{k}', out)
    elif isinstance(node, list):
        for i, v in enumerate(node[:30]):
            walk(v, f'{path}[{i}]', out)
    elif node not in (None, ''):
        text = str(node)
        if PATTERN.search(path.rsplit('.', 1)[-1]) or (isinstance(node, str) and len(text) < 120 and PATTERN.search(text)):
            out.append(f'{path} = {text[:120]}')


def main(codes):
    from curl_cffi import requests
    for code in codes:
        for kind, url in URLS.items():
            r = requests.get(discover.BASE + url.format(code), headers=discover.HEADERS,
                             impersonate='chrome', timeout=30)
            print(f'===== {code} {kind}: HTTP {r.status_code}')
            if r.status_code == 200:
                data = r.json()
                print('keys:', list(data)[:20])
                ev = (data.get('meta') or {}).get('event') if isinstance(data.get('meta'), dict) else None
                if ev:
                    print('meta.event:', json.dumps(ev, ensure_ascii=False)[:1500])
                hits = []
                walk(data, '', hits)
                for h in hits[:80]:
                    print('  ', h)
            time.sleep(discover.GAP)


SEARCH = ['/api/explore/v1/discover/upcoming-movies-hyderabad',
          '/api/explore/v1/discover/movies-hyderabad?categories=upcoming',
          '/api/explore/v1/discover/movies-hyderabad',
          '/explore/upcoming-movies-hyderabad',
          '/api/le/search?q={q}&region=HYD',
          '/quickbook-search.bms?cat=MT&q={q}',
          '/serv/getData?cmd=QUICKSEARCH&q={q}&region=HYD']


PAGES = ['/explore/upcoming-movies-hyderabad?languages=telugu', '/explore/upcoming-movies-hyderabad',
         '/explore/movies-hyderabad?languages=telugu']


def upcoming(limit):
    """Every movie code on the upcoming-movies pages, then name / languages / release / bookable for each."""
    from curl_cffi import requests
    codes = {}
    for path in PAGES:
        r = requests.get(discover.BASE + path, headers=discover.HEADERS, impersonate='chrome', timeout=30)
        found = re.findall(r'ET\d{8}', r.text)
        print(f'===== {path}: HTTP {r.status_code}, {len(set(found))} codes', flush=True)
        for c in found:
            codes.setdefault(c, path)
        if r.status_code in (403, 429):
            break
        time.sleep(discover.GAP)
    known = json.load(open(discover.CATALOG, encoding='utf-8'))
    todo = [c for c in codes if not known.get(c, {}).get('group')]
    print(f'{len(codes)} codes, {len(todo)} not in the catalog with details; looking up {min(limit, len(todo))}')
    for c in todo[:limit]:
        try:
            d = discover.details(c)
        except discover.Blocked as e:
            print(f'blocked ({e})')
            break
        print(f"   {c} {json.dumps(d, ensure_ascii=False) if d else 'no details'}", flush=True)
        time.sleep(discover.GAP)


def search(queries):
    """Try endpoints that might list upcoming movies or search by name; print the movies found."""
    from curl_cffi import requests
    link = re.compile(r'([a-z0-9-]{3,80})/(ET\d{8})')
    for path in SEARCH:
        for q in (queries if '{q}' in path else ['']):
            url = discover.BASE + path.format(q=q)
            r = requests.get(url, headers=discover.HEADERS, impersonate='chrome', timeout=30)
            found = sorted(set(link.findall(r.text)))
            print(f'===== {url}: HTTP {r.status_code}, {len(r.text)} bytes, {len(found)} movie links')
            for slug, code in found[:60]:
                print(f'   {code} {slug}')
            if r.status_code in (403, 429):
                return
            time.sleep(discover.GAP)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--upcoming']:
        upcoming(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
    elif sys.argv[1:2] == ['--search']:
        search(sys.argv[2:])
    else:
        main(sys.argv[1:])
