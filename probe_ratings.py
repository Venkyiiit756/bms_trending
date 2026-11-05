#!/usr/bin/env python3
"""Diagnostic (run by hand in Actions): which fields of BookMyShow's movie responses carry the audience rating, votes,
review counts, review hashtags and any show/screen information? Prints a compact list per film.

    python probe_ratings.py ET00524017 ET00451914
"""
import json
import re
import sys
import time

import discover

PAT = re.compile(r'rating|vote|review|critic|hashtag|sentiment|summary|screen|show|cinema|venue|trending|popular|score|star|tag', re.I)
URLS = ['/api/movies/v1/synopsis/init/static?eventcode={}&channel=mobile', '/api/movies/v1/synopsis/init/dynamic?eventcode={}&channel=mobile',
        '/api/movies/v1/synopsis/secondary?eventcode={}&channel=mobile']


def walk(node, path, out):
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, f'{path}.{k}', out)
    elif isinstance(node, list):
        for i, v in enumerate(node[:40]):
            walk(v, f'{path}[{i}]', out)
    elif isinstance(node, (str, int, float)) and not isinstance(node, bool):
        text = str(node)
        if PAT.search(path.split('.')[-1]) or re.search(r'\d(\.\d)?/10|\bVotes?\b|\breviews?\b|\bRatings?\b', text, re.I):
            out.append((path, text[:160]))


def main():
    from curl_cffi import requests
    for code in sys.argv[1:]:
        for u in URLS:
            r = requests.get(discover.BASE + u.format(code), headers=discover.HEADERS, impersonate='chrome', timeout=30)
            print(f'=== {code} {u.split("?")[0].split("/")[-1]} HTTP {r.status_code} {len(r.text)} bytes', flush=True)
            if r.status_code != 200:
                time.sleep(15)
                continue
            try:
                data = r.json()
            except ValueError:
                print('not JSON; first 300 chars:', r.text[:300])
                continue
            out = []
            walk(data, '', out)
            seen = set()
            for p, v in out[:120]:
                key = (p.split('.')[-1], v)
                if key in seen:
                    continue
                seen.add(key)
                print(f'  {p[-70:]} = {v}')
            time.sleep(12)


if __name__ == '__main__':
    main()
