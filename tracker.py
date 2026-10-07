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
              'prev_poll', 'kind']
HEADERS_HEADER = ['timestamp', 'status', 'metric_value', 'headers']
IST = timezone(timedelta(hours=5, minutes=30))

POLL_SECONDS = 60
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
    """(timestamp, (metric_type, metric_value)) of the newest row, or None."""
    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    row = rows[-1]
    ts = datetime.fromisoformat(row['timestamp'])
    if ts.tzinfo is None:  # rows from before the IST switch are UTC
        ts = ts.replace(tzinfo=timezone.utc)
    return ts, (row['metric_type'], row['metric_value'])


def fetch_once():
    """One request to BookMyShow. Returns a dict, or None if nothing usable came back."""
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
        print('API request error:', e, file=sys.stderr)
        return None

    if status != 200:
        print(f'API failed with status {status}', file=sys.stderr)
        return None

    try:
        data = json.loads(body)
    except ValueError:
        print('Response is not JSON.', file=sys.stderr)
        return None

    movie_name = 'Drishyam 3'
    meta = data.get('meta', {})
    if isinstance(meta, dict) and 'event' in meta:
        movie_name = meta['event'].get('eventName', movie_name)

    if not isinstance(data.get('widgets'), dict):
        print('Unexpected response: widgets missing.', file=sys.stderr)
        return None

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

    print('No trending widget or stats returned.', file=sys.stderr)
    return None


def fetch():
    """Run fetch_once() in a child process; None on any failure or timeout."""
    try:
        proc = subprocess.run(FETCH_CMD, capture_output=True, text=True, timeout=FETCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f'Request timed out after {FETCH_TIMEOUT}s; skipping this poll', flush=True)
        return None
    if proc.stderr.strip():
        print(proc.stderr.strip(), flush=True)
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        print(f'Request process failed (exit {proc.returncode})', flush=True)
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return None


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


def next_boundary(t):
    return datetime.fromtimestamp((t.timestamp() // POLL_SECONDS + 1) * POLL_SECONDS, IST)


def run(minutes, commit):
    """Poll every POLL_SECONDS for `minutes`; log only changes plus a heartbeat.

    A row is written when the figure changes (with the time of the previous
    successful poll, so the change is known to within one poll) and every
    HEARTBEAT_SECONDS when it has not, which shows the tracker is alive.
    """
    init_csv()
    init_headers_file()
    end = now() + timedelta(minutes=minutes)
    prev = last_logged()
    last_ts, last_key = prev if prev else (None, None)
    prev_poll = None

    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)

    try:
        while True:
            result = fetch()
            t = now()
            print(f"[{fmt(t)}] poll " + (f"ok {result['metric_value']}" if result else 'FAILED'),
                  flush=True)
            if result:
                key = (result['metric_type'], result['metric_value'])
                kind = None
                if last_key is None:
                    kind = 'start'
                elif key != last_key:
                    kind = 'change'
                elif (t - last_ts).total_seconds() >= HEARTBEAT_SECONDS:
                    kind = 'heartbeat'
                if kind:
                    before = fmt(prev_poll or last_ts) if kind == 'change' else ''
                    write_rows(CSV_FILE, [[fmt(t), result['movie_name'], key[0], key[1],
                                           result['raw_text'], before, kind]])
                    print(f"[{fmt(t)}] saved {kind}: {key[0]} {key[1]}", flush=True)
                    last_ts, last_key = t, key
                if LOG_HEADERS:
                    write_rows(HEADERS_FILE, [[fmt(t), result['status'], key[1],
                                               json.dumps(result['headers'], sort_keys=True)]])
                prev_poll = t
                if kind and commit:
                    commit_and_push()
            nxt = next_boundary(now())
            if nxt >= end:
                print('Run window finished.', flush=True)
                break
            sleep(max((nxt - now()).total_seconds(), 0))
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
