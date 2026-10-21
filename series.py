"""Clean readings from the movie logs, shared by snapshots, posts, health checks and the website.

Drops error rows and stale cached copies: a row whose `fresh_at` (when BookMyShow produced the figure)
is older than an earlier row's was an out-of-date copy served late, which otherwise shows up as a
false dip followed by a jump back.
"""
import csv
import json
import os
from collections import namedtuple
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = os.path.dirname(os.path.abspath(__file__))
HANDLE = '@GeT_RiGhT_A1'
# Display names where several BookMyShow listings share a title.
NAMES = {'drishyam': 'Drishyam', 'jailer2_hyd': 'Jailer 2', 'jailer2': 'Jailer 2 (Hindi)'}
# Older names found in saved snapshot files -> current display name
ALIASES = {'Jailer 2 (Telugu)': 'Jailer 2', 'Rajini: The Jailer 2': 'Jailer 2 (Hindi)', 'Drishyam 3': 'Drishyam'}

Reading = namedtuple('Reading', 'time kind text value title')


def parse_time(text):
    t = datetime.fromisoformat(text)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def value(text):
    """'7.49K' -> 7490.0 (tickets)."""
    t = text.strip().upper()
    mult = {'K': 1e3, 'M': 1e6}.get(t[-1:], 1)
    return float(t[:-1] if mult != 1 else t) * mult


def load(path):
    """Readings (1_hour / 24_hours) of one movie log, oldest first, stale copies removed."""
    out, newest = [], None
    if not os.path.exists(path):
        return out
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r['metric_type'] not in ('1_hour', '24_hours'):
                continue
            try:
                t = parse_time(r['timestamp'])
                v = value(r['metric_value'])
            except (ValueError, KeyError):
                continue
            fresh = r.get('fresh_at') or ''
            if fresh:
                ft = parse_time(fresh)
                if newest and ft < newest - timedelta(seconds=60):
                    continue
                newest = max(newest, ft) if newest else ft
            out.append(Reading(t, r['metric_type'], r['metric_value'], v, r.get('movie_name') or ''))
    return out


def latest(readings, when, max_age):
    """The reading in force at `when` (newest at or before it), or None if older than max_age."""
    best = None
    for r in readings:
        if r.time <= when:
            best = r
        else:
            break
    if best is None or when - best.time > max_age:
        return None
    return best


def movies():
    """Tracked movies from movies.json: [(key, display name, csv path, entry)], paused ones excluded."""
    with open(os.path.join(ROOT, 'movies.json'), encoding='utf-8') as f:
        entries = json.load(f)
    out = []
    for m in entries:
        if m.get('paused'):
            continue
        out.append((m['key'], NAMES.get(m['key']) or m['name'], os.path.join(ROOT, m['csv']), m))
    return out


def window_label(end):
    """Label for the hour ending at `end`: '4–5 PM', '11 AM–12 PM', '11:30 AM–12:30 PM'."""
    start = end - timedelta(hours=1)

    def part(t):
        h = t.hour % 12 or 12
        return (f'{h}' if t.minute == 0 else f'{h}:{t.minute:02d}'), ('AM' if t.hour < 12 else 'PM')
    (a, am), (b, bm) = part(start), part(end)
    return f'{a}–{b} {bm}' if am == bm else f'{a} {am}–{b} {bm}'


def short_name(name, limit=20):
    """Fit a title in a narrow cell: drop a subtitle after ':' or cut at a word boundary."""
    if len(name) <= limit:
        return name
    if ':' in name and len(name.split(':')[0]) >= 4:
        name = name.split(':')[0]
        if len(name) <= limit:
            return name
    cut = name[:limit - 1].rsplit(' ', 1)[0]
    return cut + '…'
