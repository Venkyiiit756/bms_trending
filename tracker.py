import sys
import json
import os
import csv
import time
import signal
import argparse
import subprocess
from datetime import datetime, timedelta, timezone

CSV_FILE = 'bms_booking_log.csv'
HEADERS_FILE = 'bms_api_headers.csv'
CSV_HEADER = ['timestamp', 'movie_name', 'metric_type', 'metric_value', 'raw_text',
              'prev_poll', 'kind', 'fresh_at']
HEADERS_HEADER = ['timestamp', 'status', 'metric_value', 'headers']
IST = timezone(timedelta(hours=5, minutes=30))

# Cloudflare caches the response for 10 minutes (its Age header counts up to 599),
# so the figure can only change once per cache lifetime. We ask once when the cached
# copy should expire instead of polling constantly, which is both lighter on the
# server and avoids being rate-limited (HTTP 429).
CACHE_TTL = 600
EXPIRY_MARGIN = 2          # ask this many seconds after the expected expiry
MIN_WAIT = 5
MAX_QUICK_RETRIES = 4      # still cached after expiry this many times -> poll gently
POLL_SECONDS = 60          # gentle fallback interval
BACKOFF = (120, 240, 480, 900)   # waits after consecutive failures (429, 403, ...)
HEARTBEAT_SECONDS = 15 * 60
FETCH_TIMEOUT = 60
GIT_TIMEOUT = 120
# Each request runs in its own short-lived process so one stuck connection can
# never stall the polling loop.
FETCH_CMD = [sys.executable, os.path.abspath(__file__), '--fetch-json']
LOG_HEADERS = os.environ.get('LOG_HEADERS', '1') != '0'
# Response headers that reveal how the figure is cached; nothing identifying.
KEEP_HEADERS = ('date', 'age', 'cache-control', 'expires', 'etag', 'last-modified',
                'cf-cache-status', 'x-cache', 'via', 'server-timing')


def now():
    return datetime.now(IST)


def sleep(seconds):
    time.sleep(seconds)


def fmt(ts):
    text = ts.strftime('%Y-%m-%d %H:%M:%S%z')
    return text[:-2] + ':' + text[-2:]  # +0530 -> +05:30


def write_rows(path, rows, mode='a'):
    with open(path, mode=mode, newline='', encoding='utf-8') as f:
        csv.writer(f).writerows(rows)


def init_csv():
    """Create the log, or upgrade a file written with the older 5-column layout."""
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, newline='', encoding='utf-8') as f:
            rows = list(csv.reader(f))
        if rows and rows[0] == CSV_HEADER:
            return
        old = rows[1:]
        write_rows(CSV_FILE, [CSV_HEADER] + [r + [''] * (len(CSV_HEADER) - len(r)) for r in old], 'w')
    else:
        write_rows(CSV_FILE, [CSV_HEADER], 'w')


def init_headers_file():
    if LOG_HEADERS and not os.path.exists(HEADERS_FILE):
        write_rows(HEADERS_FILE, [HEADERS_HEADER], 'w')


def last_logged():
    """(timestamp, (metric_type, metric_value)) of the newest data row, or None."""
    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if r['metric_type'] != 'error']
    if not rows:
        return None
    row = rows[-1]
    ts = datetime.fromisoformat(row['timestamp'])
    if ts.tzinfo is None:  # rows from before the IST switch are UTC
        ts = ts.replace(tzinfo=timezone.utc)
    return ts, (row['metric_type'], row['metric_value'])


def fetch_once():
    """One request to BookMyShow. Returns a result dict, or {'error': True, ...}."""
    try:
        from curl_cffi import requests as b_requests
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "curl_cffi"])
        from curl_cffi import requests as b_requests
    session = b_requests.Session()
    base = 'https://in.bookmyshow.com'
    page = base + '/movies/hyderabad/drishyam-the-conclusion/ET00477911'

    app_headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': page,
        'x-platform': 'AND',
        'x-platform-code': 'ANDROID',
        'x-app-code': 'MOBAND2',
        'x-region-code': 'HYD',
        'x-region-slug': 'hyderabad',
        'x-app-version': '18.2.8',
        'x-app-version-code': '18281',
        'Origin': base
    }

    try:
        api_response = session.get(
            base + '/api/movies/v1/synopsis/secondary?eventcode=ET00477911&channel=mobile',
            headers=app_headers,
            impersonate="chrome",
            timeout=25
        )
        status = api_response.status_code
        body = api_response.text
    except Exception as e:
        return {'error': True, 'status': 0, 'detail': f'request error: {e}'}

    if status != 200:
        retry_after = str(api_response.headers.get('retry-after') or '')
        return {'error': True, 'status': status, 'detail': f'HTTP {status}',
                'retry_after': int(retry_after) if retry_after.isdigit() else 0}

    try:
        data = json.loads(body)
    except ValueError:
        return {'error': True, 'status': status, 'detail': 'response is not JSON'}

    movie_name = 'Drishyam 3'
    meta = data.get('meta', {})
    if isinstance(meta, dict) and 'event' in meta:
        movie_name = meta['event'].get('eventName', movie_name)

    if not isinstance(data.get('widgets'), dict):
        return {'error': True, 'status': status, 'detail': 'unexpected response: widgets missing'}

    headers = {k.lower(): v for k, v in api_response.headers.items()}
    headers = {k: headers[k] for k in KEEP_HEADERS if k in headers}

    for widget in data['widgets'].values():
        obj = widget.get('objectData') or {}
        if (obj.get('action') or {}).get('label') == 'Trending':
            text = ''.join(c.get('text', '') for c in
                           (obj.get('text') or {}).get('components', []))
            if text:
                metric_type = 'unknown'
                metric_value = text
                if 'last 1 hour' in text.lower():
                    metric_type = '1_hour'
                    metric_value = text.split(' ')[0]
                elif 'last 24 hours' in text.lower():
                    metric_type = '24_hours'
                    metric_value = text.split(' ')[0]
                return {'movie_name': movie_name, 'metric_type': metric_type,
                        'metric_value': metric_value, 'raw_text': text,
                        'status': status, 'headers': headers}

    return {'error': True, 'status': status, 'detail': 'no trending widget in response'}


def fetch():
    """Run fetch_once() in a child process; always returns a dict (see 'error')."""
    try:
        proc = subprocess.run(FETCH_CMD, capture_output=True, text=True, timeout=FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {'error': True, 'status': 0, 'detail': f'timed out after {FETCH_TIMEOUT}s'}
    if proc.stderr.strip():
        print(proc.stderr.strip(), flush=True)
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        return {'error': True, 'status': 0, 'detail': f'request process failed (exit {proc.returncode})'}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {'error': True, 'status': 0, 'detail': 'unreadable reply from request process'}


def git(*args):
    try:
        return subprocess.run(['git', *args], capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f'git {args[0]} timed out after {GIT_TIMEOUT}s', flush=True)
        return subprocess.CompletedProcess(args, 124, '', 'timeout')


def commit_and_push():
    files = [p for p in (CSV_FILE, HEADERS_FILE) if os.path.exists(p)]
    git('add', *files)
    if git('diff', '--cached', '--quiet').returncode == 0:
        return
    git('commit', '-m', 'Update booking logs [skip ci]')
    for attempt in (1, 2, 3):
        if (git('pull', '--rebase', '--autostash', 'origin', 'main').returncode == 0
                and git('push', 'origin', 'HEAD:main').returncode == 0):
            return
        print(f'Push failed (attempt {attempt}); retrying', flush=True)
        sleep(attempt * 5)
    print('Giving up on this push; the next one will retry.', flush=True)


def cache_hit(headers):
    return headers.get('cf-cache-status') == 'HIT'


def cache_age(headers):
    age = str(headers.get('age', ''))
    return int(age) if age.isdigit() else 0


def fresh_time(t, headers):
    """When BookMyShow's server produced this response: now for a fresh fetch
    (EXPIRED/MISS), or now minus Age for a copy served from Cloudflare's cache."""
    if cache_hit(headers):
        return t - timedelta(seconds=cache_age(headers))
    return t


def backoff_wait(failures, retry_after):
    wait = BACKOFF[min(failures, len(BACKOFF)) - 1]
    return max(wait, min(retry_after, 3600))


def run(minutes, commit):
    """Poll for `minutes`, once per cache expiry; log only changes plus a heartbeat.

    A data row is written when the figure changes (fresh_at is the moment
    BookMyShow's server produced it) and every HEARTBEAT_SECONDS when it has not.
    After a failure (429, 403, timeout...) we back off instead of retrying at once,
    and write an 'error' row so a block shows up in the log.
    """
    init_csv()
    init_headers_file()
    end = now() + timedelta(minutes=minutes)
    prev = last_logged()
    last_ts, last_key = prev if prev else (None, None)
    prev_poll = None
    failures = 0
    retries = 0
    targeted = False   # was this poll aimed at an expected cache expiry?

    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)

    try:
        while True:
            result = fetch()
            t = now()
            logged = False
            if result.get('error'):
                failures += 1
                wait = backoff_wait(failures, result.get('retry_after', 0))
                targeted = False
                print(f"[{fmt(t)}] poll FAILED ({result['detail']}); failure {failures}, "
                      f"waiting {wait}s", flush=True)
                if LOG_HEADERS:
                    write_rows(HEADERS_FILE, [[fmt(t), result['status'], '',
                                               json.dumps({'error': result['detail']})]])
                if failures == 1 or failures % 4 == 0:
                    write_rows(CSV_FILE, [[fmt(t), '', 'error', result['status'],
                                           f"{result['detail']} (failure {failures})",
                                           '', 'error', '']])
                    logged = True
            else:
                if failures:
                    print(f"[{fmt(t)}] recovered after {failures} failed polls", flush=True)
                failures = 0
                headers = result['headers']
                hit = cache_hit(headers)
                key = (result['metric_type'], result['metric_value'])
                kind = None
                if last_key is None:
                    kind = 'start'
                elif key != last_key:
                    kind = 'change'
                elif (t - last_ts).total_seconds() >= HEARTBEAT_SECONDS:
                    kind = 'heartbeat'
                print(f"[{fmt(t)}] poll ok {key[1]} "
                      f"({headers.get('cf-cache-status', '?')}"
                      f"{', age ' + str(cache_age(headers)) if hit else ''})", flush=True)
                if kind:
                    changed = kind in ('start', 'change')
                    write_rows(CSV_FILE, [[
                        fmt(t), result['movie_name'], key[0], key[1], result['raw_text'],
                        fmt(prev_poll or last_ts) if kind == 'change' else '', kind,
                        fmt(fresh_time(t, headers)) if changed else '']])
                    print(f"[{fmt(t)}] saved {kind}: {key[0]} {key[1]}", flush=True)
                    last_ts, last_key = t, key
                    logged = True
                if LOG_HEADERS:
                    write_rows(HEADERS_FILE, [[fmt(t), result['status'], key[1],
                                               json.dumps(headers, sort_keys=True)]])
                prev_poll = t
                # Next ask: when the cached copy should expire. Still cached after we
                # aimed at the expiry? Retry shortly, but give up the exact timing
                # after a few tries and fall back to gentle polling.
                if hit and targeted:
                    retries += 1
                elif not hit:
                    retries = 0
                wait = max(CACHE_TTL - cache_age(headers) + EXPIRY_MARGIN, MIN_WAIT)
                if retries >= MAX_QUICK_RETRIES:
                    wait = POLL_SECONDS
                targeted = retries < MAX_QUICK_RETRIES
            if logged and commit:
                commit_and_push()
            if now() + timedelta(seconds=wait) >= end:
                print('Run window finished.', flush=True)
                break
            sleep(wait)
    except KeyboardInterrupt:
        print('Interrupted; saving what we have.', flush=True)
    finally:
        if commit:
            commit_and_push()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=float, default=0,
                    help='keep polling for this long (default: one poll)')
    ap.add_argument('--commit', action='store_true', help='git commit and push new rows')
    ap.add_argument('--fetch-json', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.fetch_json:
        print(json.dumps(fetch_once()))
    else:
        run(args.minutes, args.commit)
