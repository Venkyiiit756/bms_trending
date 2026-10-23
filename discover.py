#!/usr/bin/env python3
"""Find Hyderabad's movies by following the "other movies" list inside each movie's own response.

The explore page is blocked for GitHub runners, but every synopsis response we already read
for ticket counts carries a widget listing ~10 other movies (event codes and links). Starting
from the movies we track, this crawls those lists (a few gentle requests, spaced out) until
nothing new turns up, and writes catalog.json:

    {eventcode: {name, page_slug, first_seen, last_listed, last_checked, figure}}

`last_listed` is the last date the movie appeared in another movie's list. update_movies.py
uses the catalog to add new movies to movies.json and to retire ones that have disappeared.
Stops at the first 403/429 and keeps what it has.

Booking alerts: each movie's page says whether it can be booked (`meta.event.bookButton`) and
its release date. Each run also reads Hyderabad's "upcoming movies" page (about a week ahead).
Upcoming movies that cannot be booked yet, and watchlist.json entries (an event code, or name
words matched against listed movies, so a film is watched as soon as BookMyShow lists it), are
re-checked on each run; a watched film's first listing is reported too; when booking opens (or a movie releasing later is first found
already bookable: advance booking) it is written to build/booking_alerts.json, and the
workflow opens a GitHub issue for it.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(ROOT, 'catalog.json')
WATCHLIST = os.path.join(ROOT, 'watchlist.json')
ALERTS = os.path.join(ROOT, 'build', 'booking_alerts.json')
UPCOMING = '/explore/upcoming-movies-hyderabad'
BASE = 'https://in.bookmyshow.com'
HEADERS = {'Accept': 'application/json, text/plain, */*', 'x-platform': 'AND', 'x-platform-code': 'ANDROID',
           'x-app-code': 'MOBAND2', 'x-region-code': 'HYD', 'x-region-slug': 'hyderabad',
           'x-app-version': '18.2.8', 'x-app-version-code': '18281', 'Origin': BASE}
LINK = re.compile(r'/([a-z0-9][a-z0-9-]*)/(ET\d{8})')
MAX_FETCHES = 80
GAP = 12                     # seconds between requests


class Blocked(Exception):
    pass


def today():
    return datetime.now(IST).strftime('%Y-%m-%d')


def related(data, own):
    """{eventcode: slug} for the other movies listed inside a synopsis response."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            for slug, code in LINK.findall(node):
                if code != own:
                    found.setdefault(code, slug)
    widgets = data.get('widgets') if isinstance(data.get('widgets'), dict) else {}
    for w in widgets.values():
        if isinstance(w, dict) and isinstance(w.get('listData'), list):
            walk(w['listData'])
    return found


def trending(data):
    for w in (data.get('widgets') or {}).values():
        obj = (w or {}).get('objectData') or {}
        if (obj.get('action') or {}).get('label') == 'Trending':
            text = ''.join(c.get('text', '') for c in (obj.get('text') or {}).get('components', []))
            if 'last 1 hour' in text.lower():
                return '1_hour'
            if 'last 24 hours' in text.lower():
                return '24_hours'
    return 'none'


def fetch(code):
    from curl_cffi import requests
    r = requests.get(f'{BASE}/api/movies/v1/synopsis/secondary?eventcode={code}&channel=mobile',
                     headers=HEADERS, impersonate='chrome', timeout=30)
    if r.status_code in (403, 429):
        raise Blocked(f'HTTP {r.status_code}')
    if r.status_code != 200:
        print(f'{code}: HTTP {r.status_code}', flush=True)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def details(code):
    """Name, group, languages, release date and bookability from the init/dynamic response, or None."""
    from curl_cffi import requests
    r = requests.get(f'{BASE}/api/movies/v1/synopsis/init/dynamic?eventcode={code}&channel=mobile',
                     headers=HEADERS, impersonate='chrome', timeout=30)
    if r.status_code in (403, 429):
        raise Blocked(f'HTTP {r.status_code}')
    try:
        ev = (r.json().get('meta') or {}).get('event') or {}
    except ValueError:
        return None
    if not ev.get('eventName'):
        return None
    return {'name': ev['eventName'], 'group': ev.get('eventGroupCode'),
            'languages': [l.title() for l in ev.get('eventLanguage') or []], 'release': ev.get('releaseDate'),
            'book': bool(ev.get('bookButton'))}


def release_day(text):
    """'Nov 05, 2026' -> date, or None."""
    try:
        return datetime.strptime(text or '', '%b %d, %Y').date()
    except ValueError:
        return None


def needs_details(entry, day):
    """Never looked up, or not bookable yet and releasing soon or later (booking may open)."""
    if not entry.get('group'):
        return True
    r = release_day(entry.get('release'))
    return not entry.get('book') and r is not None and r >= day - timedelta(days=2)


def upcoming_page():
    """{eventcode: slug} from Hyderabad's upcoming-movies page."""
    from curl_cffi import requests
    r = requests.get(BASE + UPCOMING, headers=HEADERS, impersonate='chrome', timeout=30)
    if r.status_code in (403, 429):
        raise Blocked(f'HTTP {r.status_code}')
    return {code: slug for slug, code in LINK.findall(r.text)} if r.status_code == 200 else {}


def watched_codes(watch, catalog, day):
    """Watchlist codes, plus listed movies whose page slug or name contains a watchlist word."""
    out = [w['code'] for w in watch if w.get('code')]
    words = [m.lower() for w in watch for m in w.get('match', [])]
    for code, e in catalog.items():
        text = f"{e.get('page_slug', '')} {e.get('name', '')}".lower()
        r = release_day(e.get('release'))
        if code not in out and any(m in text for m in words) and (r is None or r >= day - timedelta(days=7)):
            out.append(code)
    # done once bookable (the opening is reported by then)
    return [c for c in out if not catalog.get(c, {}).get('book_opened') and catalog.get(c, {}).get('book') is not True]


def booking_alert(code, entry, before, day):
    """An alert if this lookup shows booking newly open, else None."""
    if not entry.get('book') or before is True or entry.get('book_opened'):
        return None
    r = release_day(entry.get('release'))
    if before is False and (r is None or r >= day - timedelta(days=2)):
        kind = 'Bookings opened'
    elif before is None and r is not None and r > day:
        kind = 'Advance bookings open'      # first time we see it, already bookable before release
    else:
        return None
    entry['book_opened'] = day.isoformat()
    return {'code': code, 'kind': kind, 'name': entry.get('name', code), 'release': entry.get('release'),
            'languages': entry.get('languages', []),
            'url': f"{BASE}/movies/hyderabad/{entry.get('page_slug', 'movie')}/{code}"}


MAX_DETAILS = 12


def known_codes():
    with open(os.path.join(ROOT, 'movies.json'), encoding='utf-8') as f:
        return {m['eventcode']: m for m in json.load(f)}


def main():
    catalog = json.load(open(CATALOG, encoding='utf-8')) if os.path.exists(CATALOG) else {}
    tracked = known_codes()
    day = today()
    for code, m in tracked.items():
        catalog.setdefault(code, {'name': m['name'], 'page_slug': m['page_slug'], 'first_seen': day,
                                  'last_listed': None, 'last_checked': None, 'figure': None})
    # 1. details (real name, listing group, languages, release date, bookable) come first, because
    # BookMyShow often stops the run after about ten requests: watchlist, then movies never looked
    # up, then upcoming movies that cannot be booked yet (movies found by this run's crawl: next run)
    blocked = False
    try:
        for code, slug in upcoming_page().items():
            e = catalog.setdefault(code, {'first_seen': day, 'last_listed': None, 'name': code, 'page_slug': slug,
                                          'last_checked': None, 'figure': None})
            e['upcoming'] = day
        time.sleep(GAP)
    except Blocked as e:
        print(f'blocked ({e}) on the upcoming page', flush=True)
        blocked = True
    except Exception as e:
        print(f'upcoming page error {e}', flush=True)
    watch = json.load(open(WATCHLIST, encoding='utf-8')) if os.path.exists(WATCHLIST) else []
    for w in watch:
        if w.get('code'):
            catalog.setdefault(w['code'], {'first_seen': day, 'last_listed': None, 'name': w.get('name', w['code']),
                                           'page_slug': w.get('page_slug', 'movie'), 'last_checked': None,
                                           'figure': None})
    d0 = datetime.strptime(day, '%Y-%m-%d').date()
    watched = watched_codes(watch, catalog, d0)
    pending = [c for c, e in catalog.items() if c not in watched and needs_details(e, d0)]
    pending.sort(key=lambda c: catalog[c].get('last_listed') or '', reverse=True)   # still listed first
    order = watched + pending
    lookups, alerts = 0, []
    for code in order:
        entry = catalog[code]
        if blocked or lookups >= MAX_DETAILS:
            break
        try:
            d = details(code)
        except Blocked as e:
            print(f'blocked ({e}) during details', flush=True)
            blocked = True
            break
        except Exception as e:
            print(f'{code}: details error {e}', flush=True)
            d = None
        lookups += 1
        time.sleep(GAP)
        if d:
            before = entry.get('book')
            entry.update(d)
            alert = booking_alert(code, entry, before, d0)
            same_film = [c for c, e in catalog.items()
                         if c != code and e.get('group') and e.get('group') == entry.get('group')
                         and (e.get('book_opened') or e.get('listed_alert'))]
            if alert and same_film:
                alert = None     # another listing (format/language) of a film already reported
            if not alert and not same_film and code in watched and not entry['book'] and not entry.get('listed_alert'):
                entry['listed_alert'] = day      # a watched film is now on BookMyShow, not bookable yet
                alert = {'code': code, 'kind': 'Listed on BookMyShow, bookings not open yet',
                         'name': entry.get('name', code), 'release': entry.get('release'),
                         'languages': entry.get('languages', []),
                         'url': f"{BASE}/movies/hyderabad/{entry.get('page_slug', 'movie')}/{code}"}
            if alert:
                alerts.append(alert)
                print(f"booking alert: {alert['kind']}: {alert['name']} (release {alert['release']})", flush=True)
    # 2. crawl the "other movies" lists for new movies
    queue = [] if blocked else list(tracked)
    seen, fetches = set(), 0
    while queue and fetches < MAX_FETCHES:
        code = queue.pop(0)
        if code in seen:
            continue
        seen.add(code)
        try:
            data = fetch(code)
        except Blocked as e:
            print(f'blocked ({e}); keeping what was found', flush=True)
            blocked = True
            break
        except Exception as e:
            print(f'{code}: request error {e}', flush=True)
            data = None
        fetches += 1
        time.sleep(GAP)
        if not data:
            continue
        entry = catalog.setdefault(code, {'first_seen': day, 'last_listed': None})
        meta = data.get('meta') if isinstance(data.get('meta'), dict) else {}
        name = (meta.get('event') or {}).get('eventName') if isinstance(meta.get('event'), dict) else None
        if name:
            entry['name'] = name
        entry['figure'] = trending(data)
        entry['last_checked'] = day
        for other, slug in related(data, code).items():
            o = catalog.setdefault(other, {'first_seen': day, 'last_listed': None, 'name': other,
                                           'page_slug': slug, 'last_checked': None, 'figure': None})
            o['last_listed'] = day
            o.setdefault('page_slug', slug)
            if other not in seen and other not in queue:
                queue.append(other)
    os.makedirs(os.path.dirname(ALERTS), exist_ok=True)
    with open(ALERTS, 'w', encoding='utf-8') as f:
        json.dump(alerts, f, indent=1)
    new = [c for c in catalog if c not in tracked]
    print(f'{fetches} + {lookups} requests; catalog {len(catalog)} movies; {len(new)} not tracked yet', flush=True)
    if fetches == 0 and lookups == 0 and blocked:
        sys.exit('Nothing fetched; leaving the catalog unchanged.')
    with open(CATALOG, 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(catalog.items())), f, indent=1)


def post_alerts():
    """Open a GitHub issue for each booking alert of this run (GitHub then notifies the repo owner)."""
    import subprocess
    alerts = json.load(open(ALERTS, encoding='utf-8')) if os.path.exists(ALERTS) else []
    for a in alerts:
        title = f"{a['kind']}: {a['name']}" + (f" (releases {a['release']})" if a.get('release') else '')
        what = ('is listed, but has no **Book tickets** button yet' if a['kind'].startswith('Listed')
                else 'shows a **Book tickets** button')
        body = (f"On BookMyShow, **{a['name']}** {what} "
                f"({', '.join(a['languages']) or 'languages unknown'}; release date {a.get('release') or 'unknown'}).\n\n"
                f"{a['url']}\n\nNoticed by movie discovery on {today()} (it runs three times a day). "
                f"New movies are added to tracking automatically; their ticket counts start at the next hourly run.")
        res = subprocess.run(['gh', 'issue', 'create', '--title', title, '--body', body],
                             capture_output=True, text=True)
        print(res.stdout.strip() or res.stderr.strip(), flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--post-alerts']:
        post_alerts()
    else:
        main()
