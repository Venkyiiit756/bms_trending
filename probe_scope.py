"""One-off: does the 'tickets booked' counter depend on the region header? Writes probe_scope_output.txt."""
import json, time
from curl_cffi import requests
BASE = 'https://in.bookmyshow.com'
def hdr(code, slug):
    return {'Accept': 'application/json', 'x-platform': 'AND', 'x-platform-code': 'ANDROID', 'x-app-code': 'MOBAND2',
            'x-region-code': code, 'x-region-slug': slug, 'x-app-version': '18.2.8', 'x-app-version-code': '18281', 'Origin': BASE}
def counter(data):
    for w in (data.get('widgets') or {}).values():
        obj = (w or {}).get('objectData') or {}
        if (obj.get('action') or {}).get('label') == 'Trending':
            return ''.join(c.get('text', '') for c in (obj.get('text') or {}).get('components', []))
    return None
out = []
for ev in ('ET00518674', 'ET00477911', 'ET00483565'):
    for code, slug in (('HYD', 'hyderabad'), ('MUMBAI', 'mumbai'), ('BANG', 'bengaluru')):
        r = requests.get(f'{BASE}/api/movies/v1/synopsis/secondary?eventcode={ev}&channel=mobile',
                         headers=hdr(code, slug), impersonate='chrome', timeout=30)
        text = None
        if r.status_code == 200:
            try: text = counter(r.json())
            except ValueError: pass
        line = f'{ev} {code}: HTTP {r.status_code}, counter={text!r}'
        print(line, flush=True); out.append(line)
        if r.status_code in (403, 429): out.append('stopped'); break
        time.sleep(10)
open('probe_scope_output.txt', 'w').write('\n'.join(out) + '\n')
