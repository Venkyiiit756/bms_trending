#!/usr/bin/env python3
"""BookMyShow's own audience rating, vote count and review count for the films the forecast cares about -> ratings.json.

Read from the two movie responses the app uses (found with probe_ratings.py):
  init/static     the rating widget: text " 9.4/10" (styleId overall-rating) and " (10.6K+ Votes)" (styleId review-count)
  secondary       meta.ratings.aggregatedRating (0-100) and the "Top reviews" header ("7K reviews")
Runs in Actions (BookMyShow answers runners, not this sandbox), a few requests spaced out; a film that fails keeps its previous values.

    python ratings.py            # the films intel.targets() picks
    python ratings.py ET00451914 # specific codes
"""
import json
import os
import re
import sys
import time
from datetime import datetime

import intel
import series

IST = series.IST
ROOT = series.ROOT
OUT = os.path.join(ROOT, 'ratings.json')
GAP = 12


def count(text):
    """'10.6K+ Votes' -> 10600, '4.8K reviews' -> 4800."""
    m = re.search(r'([\d.,]+)\s*([KkMm]?)', text or '')
    if not m:
        return None
    try:
        return round(float(m.group(1).replace(',', '')) * {'K': 1e3, 'M': 1e6}.get(m.group(2).upper(), 1))
    except ValueError:
        return None


def find(node, pred, out):
    if isinstance(node, dict):
        if pred(node):
            out.append(node)
        for v in node.values():
            find(v, pred, out)
    elif isinstance(node, list):
        for v in node:
            find(v, pred, out)


def parse_static(data):
    got = []
    find(data, lambda d: d.get('styleId') in ('overall-rating', 'review-count') and 'text' in d, got)
    res = {}
    for d in got:
        if d['styleId'] == 'overall-rating':
            m = re.search(r'([\d.]+)\s*/\s*10', d['text'])
            if m and 'bms_rating' not in res:
                res['bms_rating'] = float(m.group(1))
        elif 'bms_votes' not in res:
            res['bms_votes'] = count(d['text'])
    return res


def parse_secondary(data):
    res = {}
    agg = ((data.get('meta') or {}).get('ratings') or {}).get('aggregatedRating')
    if isinstance(agg, (int, float)) and agg:
        res['aggregated'] = agg / 10
    got = []
    find(data, lambda d: (d.get('header') or {}).get('title') == 'Top reviews', got)
    if got:
        res['reviews'] = count((got[0]['header'] or {}).get('seeMoreText'))
        sample = [int(m.group(1)) for r in ((got[0].get('objectData') or {}).get('reviewsList') or []) for m in [re.match(r'(\d+)\s*/\s*10', str(r.get('rating') or ''))] if m]
        if sample:
            res['sample_reviews'] = sample
    return res


def fetch(code):
    from curl_cffi import requests
    import discover
    out = {}
    for path, parse in (('/api/movies/v1/synopsis/init/static?eventcode={}&channel=mobile', parse_static),
                        ('/api/movies/v1/synopsis/secondary?eventcode={}&channel=mobile', parse_secondary)):
        r = requests.get(discover.BASE + path.format(code), headers=discover.HEADERS, impersonate='chrome', timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f'HTTP {r.status_code}')
        out.update(parse(r.json()))
        time.sleep(GAP)
    return out


def main():
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8'))
    by_name = {}
    for code, c in catalog.items():
        if c.get('name') and not c.get('group') == 'dup':
            by_name.setdefault(c['name'], code)
    old = {}
    try:
        old = json.load(open(OUT, encoding='utf-8')).get('films', {})
    except (OSError, ValueError):
        pass
    wanted = sys.argv[1:] or [by_name[n] for n, _, _ in intel.targets() if n in by_name]
    names = {code: c.get('name') for code, c in catalog.items()}
    films = dict(old)
    for code in wanted:
        name = names.get(code, code)
        try:
            r = fetch(code)
        except Exception as e:
            print(f'{name}: failed ({str(e)[:80]})', flush=True)
            break                                  # most likely a 429: stop asking
        if r.get('bms_rating') or r.get('aggregated'):
            r['bms_rating'] = r.get('bms_rating') or r.get('aggregated')
            r['updated'] = datetime.now(IST).isoformat(timespec='minutes')
            films[name] = r
            print(f"{name}: {r['bms_rating']}/10, {r.get('bms_votes')} votes, {r.get('reviews')} reviews", flush=True)
        else:
            print(f'{name}: no rating shown yet', flush=True)
    json.dump({'generated': datetime.now(IST).isoformat(timespec='minutes'), 'films': films}, open(OUT, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)


if __name__ == '__main__':
    main()
