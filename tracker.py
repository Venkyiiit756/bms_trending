import sys
import json
import os
import csv
import time
import signal
import argparse
import subprocess
from datetime import datetime, timedelta, timezone

# Movies to track live in movies.json; run one process per movie with --movie KEY.
MOVIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'movies.json')
MOVIE = {'key': 'drishyam', 'eventcode': 'ET00477911', 'name': 'Drishyam 3',
         'region_code': 'HYD', 'region_slug': 'hyderabad',
         'page_slug': 'drishyam-the-conclusion',
         'csv': 'bms_booking_log.csv', 'headers_csv': 'bms_api_headers.csv'}
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
MIN_GAP = 15               # seconds between any two requests (all movies share one IP)
MISSING_RETRY = 3600       # a movie whose response has no counter is retried this often
GIT_TIMEOUT = 120
# Each request runs in its own short-lived process so one stuck connection can
# never stall the polling loop.
LOG_HEADERS = os.environ.get('LOG_HEADERS', '1') != '0'
# Response headers that reveal how the figure is cached; nothing identifying.
KEEP_HEADERS = ('date', 'age', 'cache-control', 'expires', 'etag', 'last-modified',
                'cf-cache-status', 'x-cache', 'via', 'server-timing')




def load_movies():
    with open(MOVIES_FILE, encoding='utf-8') as f:
        return {m['key']: m for m in json.load(f)}


def select_movie(key):
    """Point this (child) process at one entry of movies.json."""
    global MOVIE
    movies = load_movies()
    if key not in movies:
        sys.exit(f"Unknown movie '{key}'. Known: {', '.join(movies)}")
    MOVIE = {**MOVIE, **movies[key]}


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



def init_csv(path):
    """Create the log, or upgrade a file written with the older 5-column layout."""
    if os.path.exists(path):
        with open(path, newline='', encoding='utf-8') as f:
            rows = list(csv.reader(f))
        if rows and rows[0] == CSV_HEADER:
            return
        old = rows[1:]
        write_rows(path, [CSV_HEADER] + [r + [''] * (len(CSV_HEADER) - len(r)) for r in old], 'w')
    else:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        write_rows(path, [CSV_HEADER], 'w')


def init_headers_file(path):
    if LOG_HEADERS and not os.path.exists(path):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        write_rows(path, [HEADERS_HEADER], 'w')


def last_logged(path):
    """(timestamp, (metric_type, metric_value)) of the newest data row, or None."""
    with open(path, newline='', encoding='utf-8') as f:
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
    page = f"{base}/movies/{MOVIE['region_slug']}/{MOVIE['page_slug']}/{MOVIE['eventcode']}"

    app_headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': page,
        'x-platform': 'AND',
        'x-platform-code': 'ANDROID',
        'x-app-code': 'MOBAND2',
        'x-region-code': MOVIE['region_code'],
        'x-region-slug': MOVIE['region_slug'],
        'x-app-version': '18.2.8',
        'x-app-version-code': '18281',
        'Origin': base
    }

    try:
        api_response = session.get(
            f"{base}/api/movies/v1/synopsis/secondary?eventcode={MOVIE['eventcode']}&channel=mobile",
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

    movie_name = MOVIE['name']
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



def fetch(key):
    """Run fetch_once() for one movie in a child process; always returns a dict."""
    cmd = [sys.executable, os.path.abspath(__file__), '--fetch-json', '--movie', key]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=FETCH_TIMEOUT)
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


def commit_and_push(files):
    git('add', *[p for p in files if os.path.exists(p)])
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



class State:
    """Polling state of one movie."""

    def __init__(self, movie, due):
        self.key = movie['key']
        self.csv = movie['csv']
        self.headers_csv = movie['headers_csv']
        init_csv(self.csv)
        init_headers_file(self.headers_csv)
        prev = last_logged(self.csv)
        self.last_ts, self.last_key = prev if prev else (None, None)
        self.prev_poll = None
        self.failures = 0      # consecutive access failures (429, 403, timeout...)
        self.missing = 0       # consecutive responses without a counter
        self.retries = 0
        self.targeted = False  # was this poll aimed at an expected cache expiry?
        self.due = due         # monotonic time of the next request
        self.every = movie.get('every', 0)   # >0: poll only this often (seconds), not at every cache expiry
        self.files = [self.csv] + ([self.headers_csv] if LOG_HEADERS else [])


def poll(st, shared):
    """Request one movie, log the outcome, schedule its next request.

    Returns True if a CSV row was written. `shared['block_until']` (monotonic) is
    raised after an access failure so that every movie backs off, not just this one.
    """
    result = fetch(st.key)
    t = now()
    mono = time.monotonic()
    logged = False
    if result.get('error'):
        no_counter = result['status'] == 200
        if no_counter:       # this movie only: no Trending widget (yet)
            st.missing += 1
            wait = MISSING_RETRY
            n = st.missing
        else:                # access problem: likely shared by all movies
            shared['failures'] += 1
            wait = backoff_wait(shared['failures'], result.get('retry_after', 0))
            shared['block_until'] = max(shared['block_until'], mono + wait)
            st.failures += 1
            n = st.failures
        st.targeted = False
        print(f"[{fmt(t)}] {st.key}: poll FAILED ({result['detail']}); #{n}, "
              f"next try in {wait}s", flush=True)
        if LOG_HEADERS:
            write_rows(st.headers_csv, [[fmt(t), result['status'], '',
                                         json.dumps({'error': result['detail']})]])
        if n == 1 or n % 4 == 0:
            write_rows(st.csv, [[fmt(t), '', 'error', result['status'],
                                 f"{result['detail']} (failure {n})", '', 'error', '']])
            logged = True
        st.due = mono + wait
        return logged

    if st.failures or shared['failures']:
        print(f"[{fmt(t)}] {st.key}: recovered", flush=True)
    st.failures = st.missing = shared['failures'] = 0
    headers = result['headers']
    hit = cache_hit(headers)
    key = (result['metric_type'], result['metric_value'])
    kind = None
    if st.last_key is None:
        kind = 'start'
    elif key != st.last_key:
        kind = 'change'
    elif (t - st.last_ts).total_seconds() >= HEARTBEAT_SECONDS:
        kind = 'heartbeat'
    print(f"[{fmt(t)}] {st.key}: ok {key[1]} ({headers.get('cf-cache-status', '?')}"
          f"{', age ' + str(cache_age(headers)) if hit else ''})", flush=True)
    if kind:
        changed = kind in ('start', 'change')
        write_rows(st.csv, [[
            fmt(t), result['movie_name'], key[0], key[1], result['raw_text'],
            fmt(st.prev_poll or st.last_ts) if kind == 'change' else '', kind,
            fmt(fresh_time(t, headers)) if changed else '']])
        print(f"[{fmt(t)}] {st.key}: saved {kind}: {key[0]} {key[1]}", flush=True)
        st.last_ts, st.last_key = t, key
        logged = True
    if LOG_HEADERS:
        write_rows(st.headers_csv, [[fmt(t), result['status'], key[1],
                                     json.dumps(headers, sort_keys=True)]])
    st.prev_poll = t
    # Next ask: when the cached copy should expire. Still cached after we aimed at
    # the expiry? Retry shortly, but give up the exact timing after a few tries.
    if hit and st.targeted:
        st.retries += 1
    elif not hit:
        st.retries = 0
    wait = max(CACHE_TTL - cache_age(headers) + EXPIRY_MARGIN, MIN_WAIT)
    if st.retries >= MAX_QUICK_RETRIES:
        wait = POLL_SECONDS
    st.targeted = st.retries < MAX_QUICK_RETRIES
    st.due = mono + (st.every or wait)
    return logged


def recently_polled(movie):
    """True if the movie's log has a row newer than its `every` interval (minus a margin)."""
    path = movie['csv']
    if not os.path.exists(path):
        return False
    last = None
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.reader(f):
            last = row
    try:
        stamp = datetime.fromisoformat(last[0])
    except (TypeError, ValueError, IndexError):
        return False
    return (now() - stamp).total_seconds() < movie['every'] - 900


def run(keys, minutes, commit):
    """Poll the given movies for `minutes`, one request at a time.

    Each movie is asked once at its expected cache expiry (new figures arrive every
    ~10 minutes). Requests are at least MIN_GAP seconds apart, and an access failure
    (429...) pauses all movies. A data row is written when a figure changes, every
    HEARTBEAT_SECONDS when it does not, and for failures.
    """
    movies = load_movies()
    end = time.monotonic() + minutes * 60
    start = time.monotonic()
    # Movies with an `every` interval (hourly sales are enough for them) are spread
    # evenly over that interval; the rest start MIN_GAP apart and follow the cache.
    keys = [k for k in keys if not movies[k].get('paused')]
    # Movies checked less than once an hour (every > 3600 s) are skipped by runs that start
    # too soon after their last logged row, so they are polled about once per interval.
    keys = [k for k in keys if not (movies[k].get('every', 0) > 3600 and recently_polled(movies[k]))]
    slow = [k for k in keys if movies[k].get('every')]
    fast = [k for k in keys if k not in slow]
    due = {k: start + i * MIN_GAP for i, k in enumerate(fast)}
    for i, k in enumerate(slow):
        every = min(movies[k]['every'], 3600)
        due[k] = start + len(fast) * MIN_GAP + i * (every - 300) / len(slow)
    states = [State(movies[k], due[k]) for k in keys]
    shared = {'failures': 0, 'block_until': 0.0}
    last_request = 0.0
    dirty = False

    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)

    files = [p for s in states for p in s.files]
    try:
        while True:
            st = min(states, key=lambda s: s.due)
            go = max(st.due, shared['block_until'], last_request + MIN_GAP)
            if go >= end:
                print('Run window finished.', flush=True)
                break
            if go > time.monotonic():
                if dirty and commit:       # save while idle rather than at the end
                    commit_and_push(files)
                    dirty = False
                sleep(max(0, go - time.monotonic()))
            last_request = time.monotonic()
            dirty = poll(st, shared) or dirty
    except KeyboardInterrupt:
        print('Interrupted; saving what we have.', flush=True)
    finally:
        if commit:
            commit_and_push(files)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=float, default=0,
                    help='keep polling for this long (default: one poll)')
    ap.add_argument('--commit', action='store_true', help='git commit and push new rows')
    ap.add_argument('--movie', action='append',
                    help='key from movies.json (repeatable); default: all movies')
    ap.add_argument('--fetch-json', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.fetch_json:
        select_movie(args.movie[0])
        print(json.dumps(fetch_once()))
    else:
        keys = args.movie or [k for k, m in load_movies().items() if not m.get('paused')]
        unknown = [k for k in keys if k not in load_movies()]
        if unknown:
            sys.exit(f"Unknown movie(s): {', '.join(unknown)}")
        run(keys, args.minutes, args.commit)
