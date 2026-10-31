#!/usr/bin/env python3
"""Everything the daily briefing may say, computed here in Python (never by the language model).

facts(today) covers "yesterday" = 4 AM of the day before `today` to 4 AM of `today` (IST), the same day
the website's Day-by-day tables use. Every figure that can appear in the briefing is in the returned
dict as ready-to-print text next to its raw value; briefing.py only lets the writer use numbers found here.

    python briefing_facts.py [YYYY-MM-DD]      # print the facts for that morning (default: today, IST)
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

import interest
import sales
import series

IST = series.IST
ROOT = series.ROOT
MIN_MOVE = 20_000          # a film needs this many tickets on both days before its change counts as news
BIG_MOVE = 40              # percent change worth a flag
STALE_MIN = 90


def fmt(v):
    """Tickets as the website prints them: 119K, 86.6K, 1.07M."""
    if v >= 1e6:
        return f'{v / 1e6:.2f}M'
    if v >= 1e5:
        return f'{round(v / 1e3)}K'
    if v >= 1e3:
        return f'{v / 1e3:.1f}'.rstrip('0').rstrip('.') + 'K'
    return str(round(v))


def pct(cur, prev):
    return round((cur - prev) / prev * 100) if prev else None


def day_label(d):
    return f'{d:%a} {d.day} {d:%b}'


def _films(today):
    """Per film: yesterday's total, change vs the day before and vs a week earlier."""
    t4 = datetime(today.year, today.month, today.day, 4, 0, tzinfo=IST)
    out = []
    paths = {name: path for _, name, path, _ in series.movies()}
    for m in sales.build()['movies']:
        i = sales.slot_index(m, t4)
        y, b, w = sales.day24(m, i), sales.day24(m, i - 48), sales.day24(m, i - 48 * 7)
        if not y or y['v'] <= 0:
            continue
        readings = [r for r in series.load(paths.get(m['name'], '')) if r.kind == '1_hour'
                    and t4 - timedelta(days=1) < r.time <= t4]
        peak = max(readings, key=lambda r: r.value) if readings else None
        f = {'name': m['name'], 'yesterday': ('' if y['exact'] else '≈') + fmt(y['v']), 'yesterday_value': round(y['v']),
             'exact': y['exact'], 'vs_day_before': pct(y['v'], b['v']) if b else None,
             'vs_week_ago': pct(y['v'], w['v']) if w else None}
        if b:
            f['day_before'] = ('' if b['exact'] else '≈') + fmt(b['v'])
        if peak:
            pt = peak.time.astimezone(IST)
            f['busiest_hour'] = peak.text
            f['busiest_hour_at'] = f'{pt:%-I:%M %p}'.replace(':00 ', ' ')
        out.append(f)
    out.sort(key=lambda f: -f['yesterday_value'])
    seen, uniq = set(), []                 # two listings of one film can show the same counter: keep the first
    for f in out:
        key = (f['yesterday_value'], f.get('busiest_hour'))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    out = uniq
    for k, f in enumerate(out, 1):
        f['rank'] = k
    return out


def _interest(today):
    now = datetime(today.year, today.month, today.day, 8, 0, tzinfo=IST)
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8'))
    rows, best = [], {}
    for code, m in interest.series().items():
        pts = [p for p in m['points'] if p[0] <= now + timedelta(hours=14)]      # a late run still uses its own day
        if not pts:
            continue
        t, v = pts[-1]
        ago = [p for p in pts if p[0] <= t - timedelta(hours=22)]
        c = catalog.get(code, {})
        name = c.get('name') or m['name']
        if re.fullmatch(r'ET\d+', name):
            continue
        rel = interest.release_day(c.get('release') or m['release'])
        e = {'name': name, 'interested': f'{fmt(v)}+', 'interested_value': v,
             'bookings': 'open' if c.get('book') else 'not open yet' if c.get('book') is False else None}
        if ago:
            g = v - ago[-1][1]
            e['gained_24h'] = ('+' if g >= 0 else '−') + fmt(abs(g))
            e['gained_24h_value'] = g
        if rel:
            e['release'] = f'{rel:%a} {rel.day} {rel:%b}'
            e['days_to_release'] = (rel - today).days
        key = c.get('group') or code             # other listings of one film show the same count: keep the longest history
        if key not in best or len(pts) > best[key][0]:
            best[key] = (len(pts), e)
    rows = [e for _, e in best.values()]
    rows.sort(key=lambda e: -e['interested_value'])
    return rows


def _opened(today):
    """Films whose bookings opened (or were first seen open ahead of release) since yesterday."""
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8'))
    since = (today - timedelta(days=1)).isoformat()
    return [{'name': c.get('name') or code, 'release': c.get('release'), 'languages': c.get('languages', [])}
            for code, c in catalog.items() if (c.get('book_opened') or '') >= since and not re.fullmatch(r'ET\d+', c.get('name', 'ET0'))]


def _health(today):
    newest = None
    for _, name, path, _ in series.movies():
        rs = series.load(path)
        if rs:
            newest = max(newest, rs[-1].time) if newest else rs[-1].time
    cutoff = datetime(today.year, today.month, today.day, 8, 0, tzinfo=IST)
    age = round((cutoff - newest).total_seconds() / 60) if newest else None
    return {'newest_reading': f'{newest.astimezone(IST):%a %-d %b %-I:%M %p}' if newest else None,
            'minutes_old_at_8am': age if age and age > 0 else 0}


def facts(today=None):
    today = today or datetime.now(IST).date()
    y0 = today - timedelta(days=1)
    films = _films(today)
    rising = [f for f in films if f['vs_day_before'] is not None and f['yesterday_value'] >= MIN_MOVE
              and f.get('day_before') and f['vs_day_before'] >= BIG_MOVE]
    falling = [f for f in films if f['vs_day_before'] is not None and f['yesterday_value'] >= MIN_MOVE
               and f['vs_day_before'] <= -BIG_MOVE]
    movers = sorted(films, key=lambda f: -abs(f['vs_day_before'] or 0))
    movers = [f for f in movers if f['vs_day_before'] is not None and f['yesterday_value'] >= MIN_MOVE][:1]
    ints = _interest(today)
    gain = sorted([e for e in ints if e.get('gained_24h_value')], key=lambda e: -e['gained_24h_value'])
    flags = []
    for f in rising + falling:
        flags.append(f"{series.short_name(f['name'], 30)}: {f['yesterday']} yesterday, {f['vs_day_before']:+d}% vs the day before")
    h = _health(today)
    if h['minutes_old_at_8am'] > STALE_MIN:
        flags.append(f"Data was {h['minutes_old_at_8am']} minutes old at 8 AM (newest reading {h['newest_reading']})")
    return {
        'date': today.isoformat(),
        'day': day_label(y0),
        'window': f'4 AM {day_label(y0)} to 4 AM {day_label(today)} IST',
        'films': films[:12],
        'film_count': len(films),
        'biggest_move': movers[0] if movers else None,
        'rising': rising[:3],
        'falling': falling[:3],
        'interest_top': ints[:6],
        'interest_gainers': gain[:3],
        'bookings_opened': _opened(today),
        'flags': flags,
        'health': h,
        'notes': ['Tickets are counted from BookMyShow counters (one listing per film, not revenue).',
                  'Figures marked ≈ are estimates from hourly figures (about ±3%).',
                  'Interest counts are rounded by BookMyShow.'],
    }


if __name__ == '__main__':
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
    print(json.dumps(facts(d), indent=1, ensure_ascii=False))
