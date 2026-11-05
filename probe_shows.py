#!/usr/bin/env python3
"""Diagnostic (run by hand in Actions): what does BookMyShow's public buytickets page say about shows and how full they are?
Fetches the page for a film in several cities, pulls the embedded JSON and prints venue / show counts, the availability codes and one
sample show, so a collector can be written.

    python probe_shows.py ET00451914 namit-malhotras-ramayana
"""
import json
import re
import sys
import time
from collections import Counter

import discover

CITIES = [('hyderabad', 'hyd'), ('mumbai', 'mumbai'), ('national-capital-region-ncr', 'ncr'), ('bengaluru', 'bang'), ('chennai', 'chen')]


def state(html):
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*', html)
    if not m:
        return None
    dec = json.JSONDecoder()
    try:
        obj, _ = dec.raw_decode(html[m.end():])
        return obj
    except ValueError:
        return None


def find_sessions(node, out):
    if isinstance(node, dict):
        if 'sessionId' in node and ('showTime' in node or 'availStatus' in node):
            out.append(node)
        for v in node.values():
            find_sessions(v, out)
    elif isinstance(node, list):
        for v in node:
            find_sessions(v, out)


def main():
    from curl_cffi import requests
    code, slug = sys.argv[1], sys.argv[2]
    day = time.strftime('%Y%m%d', time.gmtime(time.time() + 5.5 * 3600))
    for city, rc in CITIES:
        url = f'{discover.BASE}/buytickets/{slug}-{city.split("-")[-1] if city.startswith("national") else city}/movie-{rc}-{code}-MT/{day}'
        r = requests.get(url, headers={'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9'}, impersonate='chrome', timeout=30)
        print(f'=== {city} HTTP {r.status_code} {len(r.text)} bytes  {url}', flush=True)
        if r.status_code == 200:
            st = state(r.text)
            if st is None:
                print('  no initial state JSON; top-level script hints:', re.findall(r'window\.__\w+__', r.text)[:5])
            else:
                sessions = []
                find_sessions(st, sessions)
                print(f'  sessions found: {len(sessions)}; distinct venues: {len({s.get("venue_code") or s.get("venueCode") for s in sessions})}')
                print('  availStatus counts:', dict(Counter(str(s.get('availStatus')) for s in sessions)))
                print('  other keys of a session:', sorted(sessions[0].keys())[:40] if sessions else None)
                if sessions:
                    print('  sample session:', json.dumps(sessions[0], ensure_ascii=False)[:900])
                    print('  seat-category keys sample:', json.dumps({k: sessions[0][k] for k in sessions[0] if 'cat' in k.lower() or 'seat' in k.lower() or 'price' in k.lower()}, ensure_ascii=False)[:600])
                print('  top-level state keys:', list(st.keys())[:15] if isinstance(st, dict) else type(st))
        time.sleep(12)


if __name__ == '__main__':
    main()
