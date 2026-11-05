#!/usr/bin/env python3
"""Diagnostic (run by hand in Actions): (A) is the trending counter specific to a city (x-region-code header)? (B) BookMyShow ratings of
the films we compare with; (C) the showtime route in the dynamic response; (D) does the public buytickets page expose venues / shows
for a city?  Prints compact results.

    python probe_scope.py ET00451914 "ET00518674 ET00483565 ET00495822 ET00502504 ET00475942 ET00516631"
"""
import json
import re
import sys
import time

import discover
import ratings

BASE = discover.BASE
REGIONS = [('HYD', 'hyderabad'), ('MUMBAI', 'mumbai'), ('NCR', 'national-capital-region-ncr'), ('BANG', 'bengaluru'), ('CHEN', 'chennai')]


def headers(code, region, slug):
    h = dict(discover.HEADERS)
    h.update({'x-region-code': region, 'x-region-slug': slug, 'x-platform': 'AND', 'x-platform-code': 'ANDROID', 'x-app-code': 'MOBAND2',
              'x-app-version': '18.2.8', 'x-app-version-code': '18281', 'Referer': f'{BASE}/movies/{slug}/x/{code}'})
    return h


def trending(data):
    found = []

    def walk(n):
        if isinstance(n, dict):
            obj = n.get('objectData') if isinstance(n.get('objectData'), dict) else None
            if obj and (obj.get('action') or {}).get('label') == 'Trending':
                found.append(''.join(c.get('text', '') for c in (obj.get('text') or {}).get('components', [])))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(data)
    return found


def main():
    from curl_cffi import requests
    main_code = sys.argv[1]
    others = sys.argv[2].split() if len(sys.argv) > 2 else []
    print('=== A. trending counter by region header, film', main_code, flush=True)
    for region, slug in REGIONS:
        r = requests.get(f'{BASE}/api/movies/v1/synopsis/secondary?eventcode={main_code}&channel=mobile', headers=headers(main_code, region, slug),
                         impersonate='chrome', timeout=30)
        txt = trending(r.json()) if r.status_code == 200 else f'HTTP {r.status_code}'
        print(f'  {region:7} {txt}', flush=True)
        time.sleep(10)
    print('=== B. ratings', flush=True)
    for code in [main_code] + others:
        try:
            print(f'  {code}', ratings.fetch(code), flush=True)
        except Exception as e:
            print(f'  {code} failed {str(e)[:60]}', flush=True)
            break
    print('=== C. showtime route in the dynamic response', flush=True)
    r = requests.get(f'{BASE}/api/movies/v1/synopsis/init/dynamic?eventcode={main_code}&channel=mobile', headers=discover.HEADERS, impersonate='chrome', timeout=30)
    if r.status_code == 200:
        d = r.json()
        print('  pageCta:', json.dumps(d.get('pageCta'), ensure_ascii=False)[:1800], flush=True)
    time.sleep(10)
    print('=== D. buytickets page', flush=True)
    day = time.strftime('%Y%m%d', time.gmtime(time.time() + 5.5 * 3600))
    for slug, name in (('hyderabad', 'namit-malhotras-ramayana'),):
        url = f'{BASE}/buytickets/{name}-{slug}/movie-hyd-{main_code}-MT/{day}'
        r = requests.get(url, headers={'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9'}, impersonate='chrome', timeout=30)
        t = r.text
        print(f'  {url} HTTP {r.status_code} {len(t)} bytes', flush=True)
        for pat in ('"venueCode"', '"VenueCode"', '"venue_code"', 'SessionId', '"sessionId"', 'showTime', 'ShowTime', 'availStatus', 'AvailStatus', '"cinemas"'):
            print(f'    {pat}: {t.count(pat)}')
        m = re.search(r'window\.__INITIAL_STATE__\s*=\s*', t)
        print('    initial state present:', bool(m), '| first 300 chars of body:', re.sub(r'\s+', ' ', t[:300]))


if __name__ == '__main__':
    main()
