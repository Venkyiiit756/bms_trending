#!/usr/bin/env python3
"""Print the booking-related fields BookMyShow returns for some movies (a diagnostic, run by hand).

    python probe.py ET00451914 ET00447840

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


if __name__ == '__main__':
    main(sys.argv[1:])
