"""One-off: what languages/versions does each Jailer 2 event cover? Writes probe_scope_output.txt."""
import json, re, time
from curl_cffi import requests
BASE = 'https://in.bookmyshow.com'
H = {'Accept': 'application/json', 'x-platform': 'AND', 'x-platform-code': 'ANDROID', 'x-app-code': 'MOBAND2',
     'x-region-code': 'HYD', 'x-region-slug': 'hyderabad', 'x-app-version': '18.2.8', 'x-app-version-code': '18281', 'Origin': BASE}
out = []
def log(s):
    print(s, flush=True); out.append(s)
def strings(node, path=''):
    if isinstance(node, dict):
        for k, v in node.items(): yield from strings(v, f'{path}/{k}')
    elif isinstance(node, list):
        for i, v in enumerate(node): yield from strings(v, f'{path}[{i}]')
    elif isinstance(node, (str, int, float)) and not isinstance(node, bool):
        yield path, str(node)
LANG = re.compile(r'\b(Tamil|Telugu|Hindi|Kannada|Malayalam|English|Bengali|Marathi)\b', re.I)
for ev in ('ET00518674', 'ET00518675', 'ET00429213'):
    for api in ('synopsis/secondary', 'synopsis/init/dynamic', 'synopsis/init/static'):
        r = requests.get(f'{BASE}/api/movies/v1/{api}?eventcode={ev}&channel=mobile', headers=H, impersonate='chrome', timeout=30)
        log(f'== {ev} {api}: HTTP {r.status_code}, {len(r.text)} bytes')
        if r.status_code in (403, 429):
            break
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError:
                data = None
            if data is not None:
                meta = data.get('meta') if isinstance(data, dict) else None
                if isinstance(meta, dict) and isinstance(meta.get('event'), dict):
                    log('   meta.event: ' + json.dumps(meta['event'])[:600])
                hits = {}
                for p, s in strings(data):
                    for m in LANG.findall(s):
                        hits.setdefault(m.title(), set()).add(re.sub(r'\[\d+\]', '[]', p))
                for lang, paths in sorted(hits.items()):
                    log(f'   {lang}: ' + ', '.join(sorted(paths)[:4]))
                # also print short strings mentioning "2D"/"language"/codes of child events
                for p, s in strings(data):
                    if re.search(r'language|format', p, re.I) and len(s) < 120:
                        log(f'   {p} = {s}')
        time.sleep(8)
open('probe_scope_output.txt', 'w').write('\n'.join(out) + '\n')
