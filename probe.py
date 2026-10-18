"""One-off probe (run on the GitHub runner): which BookMyShow paths can list Hyderabad's movies?
Writes probe_output.txt. A few gentle GET requests, spaced out; stops at the first 403/429."""
import json
import re
import time
from curl_cffi import requests

BASE = 'https://in.bookmyshow.com'
H = {'Accept': 'application/json, text/plain, */*', 'x-platform': 'AND', 'x-platform-code': 'ANDROID',
     'x-app-code': 'MOBAND2', 'x-region-code': 'HYD', 'x-region-slug': 'hyderabad',
     'x-app-version': '18.2.8', 'x-app-version-code': '18281', 'Origin': BASE}
ET = re.compile(r'ET\d{8}')
out = []


def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line, flush=True)
    out.append(line)


def get(path, headers=H):
    r = requests.get(BASE + path, headers=headers, impersonate='chrome', timeout=30)
    return r


def walk(node, path=''):
    """Yield (path, ET code) for every event code found in a JSON structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f'{path}/{k}')
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f'{path}[{i}]')
    elif isinstance(node, str):
        for m in ET.findall(node):
            yield path, m


stop = False
# 1) event codes inside known movies' own responses (related / similar movies widgets)
for code in ('ET00477911', 'ET00518674', 'ET00483565'):
    r = get(f'/api/movies/v1/synopsis/secondary?eventcode={code}&channel=mobile')
    log(f'== synopsis/secondary {code}: HTTP {r.status_code}, {len(r.text)} bytes')
    if r.status_code in (403, 429):
        stop = True
        break
    try:
        found = list(walk(r.json()))
    except ValueError:
        found = []
    others = {}
    for p, c in found:
        if c != code:
            others.setdefault(c, set()).add(re.sub(r'\[\d+\]', '[]', p))
    log(f'   other event codes in response: {len(others)}')
    for c, ps in sorted(others.items())[:30]:
        log('   ', c, sorted(ps)[:2])
    time.sleep(8)

# 2) candidate listing endpoints (guesses; the app's real ones are not documented)
CANDS = ['/api/explore/v1/discover/movies-hyderabad', '/api/explore/v1/discover/movies',
         '/api/explore/v2/discover/movies-hyderabad', '/api/movies/v1/listing', '/api/movies/v1/nowshowing',
         '/api/movies/v1/synopsis/init/dynamic?eventcode=ET00477911&channel=mobile',
         '/robots.txt', '/sitemap.xml', '/sitemap-movies-hyderabad.xml']
for p in CANDS:
    if stop:
        break
    try:
        r = get(p)
    except Exception as e:
        log(f'== {p}: error {e}')
        continue
    codes = set(ET.findall(r.text))
    log(f'== {p}: HTTP {r.status_code}, {len(r.text)} bytes, ET codes: {len(codes)}, '
        f'type: {r.headers.get("content-type", "")[:40]}')
    if r.status_code in (403, 429):
        stop = True
    elif r.status_code == 200 and (p.endswith('.txt') or p.endswith('.xml')):
        log('   ' + r.text[:400].replace('\n', ' | '))
    time.sleep(8)
log('stopped early on 403/429' if stop else 'finished all probes')
open('probe_output.txt', 'w').write('\n'.join(out) + '\n')
