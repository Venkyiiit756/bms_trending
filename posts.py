#!/usr/bin/env python3
"""Hourly Twitter/X post kit: square images, caption and alt text for each full hour, 08:00-23:00 IST.

Every post has the "last hour" leaderboard (with change against the same time yesterday). A second
image rotates by hour:
    08:00               yesterday's peak hour for each movie
    12, 15, 18, 21:00   same time over the last four days (the trend table)
    14, 20, 23:00       the top movie's hourly line, today against yesterday
Output: build/posts/<date>/<HHMM>/{1,2}.png and an entry in posts_log.json (caption, alt text,
status). If the X_* secrets are set the post is also published (see xpost.py); otherwise it is
left as a kit to post by hand from the website.

Guard: nothing is made from stale data. Each figure must be at most MAX_AGE old at the post time,
and a post is skipped if fewer than MIN_MOVIES movies qualify.

    python posts.py                          # the current hour, if it is a posting hour
    python posts.py --slot "2026-10-21 18:00" --no-post
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta

import cards
import series
from series import IST, HANDLE

ROOT = series.ROOT
OUT = os.path.join(ROOT, 'build', 'posts')
LOG = os.path.join(ROOT, 'posts_log.json')
HOURS = range(8, 24)            # 16 posts a day; nothing between midnight and 8 AM
MAX_AGE = timedelta(minutes=30)
LATE = timedelta(minutes=35)    # don't publish a slot this long after it
MIN_MOVIES = 2
TOP = 7
TREND_HOURS, LINE_HOURS, PEAK_HOURS = {12, 15, 18, 21}, {14, 20, 23}, {8}
FOOTER = f'Source: BookMyShow app · {HANDLE}'


def pct(cur, prev):
    if not prev:
        return '—'
    d = round((cur - prev) / prev * 100)
    return f'▲{d}%' if d > 0 else (f'▼{-d}%' if d < 0 else '0%')


def hourly(readings, at):
    r = series.latest(readings, at, MAX_AGE)
    return r if r and r.kind == '1_hour' else None


def tag(name):
    t = re.sub(r'[^A-Za-z0-9]', '', name.split('(')[0])
    return f'#{t}' if t and not t.isdigit() else ''


def build(slot, data, names):
    """Make the images and caption for one slot. Returns a log entry, or None if data is too old."""
    now_rows = []
    for key, name in names.items():
        r = hourly(data[key], slot)
        if r:
            y = hourly(data[key], slot - timedelta(days=1))
            now_rows.append((name, r, y, key))
    now_rows.sort(key=lambda x: -x[1].value)
    board = len(now_rows) >= MIN_MOVIES
    if not board and slot.hour not in PEAK_HOURS:
        return None
    top = now_rows[:TOP]
    day = f'{slot:%a %d %b}'
    win = series.window_label(slot)
    folder = os.path.join(OUT, f'{slot:%Y-%m-%d}', f'{slot:%H%M}')
    os.makedirs(folder, exist_ok=True)
    images, alts = [], []

    # 1. leaderboard
    if not board:
        top = []
    rows = [[series.short_name(n, 22), r.text, pct(r.value, y.value if y else None)] for n, r, y, _ in top]
    colors = [[None, cards.INK, cards.change_color(r.value, y.value if y else None)] for _, r, y, _ in top]
    p1 = os.path.join(folder, '1.png')
    if board:
        cards.square_table(p1, 'Tickets booked in the last hour', f'{day} · {win} IST', FOOTER,
                           ['Movie', 'Last hour', 'vs yesterday'], rows, colors, widths=[.48, .26, .26])
        images.append(p1)
        alts.append(f'Table of tickets booked on BookMyShow in the last hour ({win}, {day}), with change against '
                    'the same hour yesterday: ' + '; '.join(
                        f'{n} {r.text}' + (f', {pct(r.value, y.value)} vs yesterday' if y else '') for n, r, y, _ in top))

    # 2. rotating second image
    h = slot.hour
    p2 = os.path.join(folder, '2.png')
    if h in TREND_HOURS and board:
        days = [slot - timedelta(days=i) for i in (3, 2, 1, 0)]
        rows, colors = [], []
        for n, _, _, key in top:
            vals = [hourly(data[key], d) for d in days]
            rows.append([series.short_name(n, 18)] + [v.text if v else '—' for v in vals])
            colors.append([None] + [cards.INK if i == 0 else
                                    cards.change_color(v.value if v else None, vals[i - 1].value if vals[i - 1] else None)
                                    for i, v in enumerate(vals)])
        cards.square_table(p2, 'Same time, last four days', f'Tickets in the last hour · {win} IST', FOOTER,
                           ['Movie'] + [f'{d:%a %d}' for d in days], rows, colors, widths=[.32, .17, .17, .17, .17])
        alts.append(f'Table of tickets booked in the hour {win} on each of the last four days: ' + '; '.join(
            f'{r[0]} ' + ', '.join(f'{d:%a} {v}' for d, v in zip(days, r[1:])) for r in rows))
        images.append(p2)
    elif h in LINE_HOURS and board:
        name, _, _, key = top[0]
        def pts(day0, end):
            out, prev = [], None
            for r in data[key]:
                if r.kind != '1_hour' or not day0 <= r.time <= end:
                    continue
                if prev and r.time - prev > timedelta(minutes=40):
                    out.append((float('nan'), float('nan')))       # break the line over gaps
                out.append(((r.time.astimezone(IST) - day0).total_seconds() / 3600, r.value / 1000))
                prev = r.time
            return out
        today0 = slot.replace(hour=0, minute=0)
        yday0 = today0 - timedelta(days=1)
        cards.square_lines(p2, f'{name}: tickets booked per hour', f'Today vs yesterday · until {slot:%-I %p} IST',
                           FOOTER, [(f'Yesterday ({yday0:%a})', pts(yday0, today0), '#9aa6b5', 2.0),
                                    (f'Today ({today0:%a})', pts(today0, slot), cards.NAVY, 3.0)])
        alts.append(f'Line chart of tickets booked in the last hour for {name} through the day, today against yesterday.')
        images.append(p2)
    elif h in PEAK_HOURS:
        y0 = slot.replace(hour=0, minute=0) - timedelta(days=1)
        y1 = y0 + timedelta(days=1)
        peaks = []
        for key, n in names.items():
            day_rows = [r for r in data[key] if r.kind == '1_hour' and y0 <= r.time < y1]
            if day_rows:
                best = max(day_rows, key=lambda r: r.value)
                t = best.time.astimezone(IST)
                t = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
                peaks.append((n, best, series.window_label(t)))
        peaks.sort(key=lambda x: -x[1].value)
        if peaks:
            peaks = peaks[:TOP]
            cards.square_table(p2, "Yesterday's busiest hour", f'{y0:%a %d %b} · tickets in the peak hour', FOOTER,
                               ['Movie', 'Peak hour', 'When'], [[series.short_name(n, 22), b.text, w] for n, b, w in peaks],
                               widths=[.44, .22, .34])
            alts.append(f"Table of each movie's busiest hour on {y0:%a %d %b}: " + '; '.join(
                f'{n} {b.text} ({w})' for n, b, w in peaks))
            images.append(p2)

    if not images:
        return None
    # caption (X counts 280 characters; keep a margin)
    if not board:          # morning recap only
        text = f"Yesterday's busiest hour on BookMyShow ({y0:%a %d %b}):\n\n" + '\n'.join(
            f'{i}. {n}: {b.text} ({w})' for i, (n, b, w) in enumerate(peaks[:5], 1)) + '\n\n#BoxOffice'
        rel = lambda p: os.path.relpath(p, os.path.join(ROOT, 'build'))
        return {'slot': f'{slot:%Y-%m-%d %H:%M}', 'text': text[:280], 'images': [rel(p) for p in images],
                'alt': [a[:1000] for a in alts], 'status': 'kit', 'tweet': None}
    lines = [f'{i}. {n}: {r.text}' + (f' ({pct(r.value, y.value)})' if y else '') for i, (n, r, y, _) in enumerate(top[:5], 1)]
    tags = ' '.join(t for t in (tag(top[0][0]), '#BoxOffice') if t)
    head = f'Tickets booked on BookMyShow in the last hour ({win}, {slot:%d %b}), change vs same time yesterday:'
    while True:
        text = head + '\n\n' + '\n'.join(lines) + '\n\n' + tags
        if len(text) <= 270 or len(lines) <= 2:
            break
        lines.pop()
    rel = lambda p: os.path.relpath(p, os.path.join(ROOT, 'build'))
    return {'slot': f'{slot:%Y-%m-%d %H:%M}', 'text': text, 'images': [rel(p) for p in images],
            'alt': [a[:1000] for a in alts], 'status': 'kit', 'tweet': None}


def load_log():
    return json.load(open(LOG, encoding='utf-8')) if os.path.exists(LOG) else []


def save_log(log):
    cutoff = (datetime.now(IST) - timedelta(days=30)).strftime('%Y-%m-%d')
    log = [e for e in log if e['slot'] >= cutoff]
    with open(LOG, 'w', encoding='utf-8') as f:
        json.dump(log, f, indent=1, ensure_ascii=False)
        f.write('\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--slot', help="IST hour 'YYYY-MM-DD HH:00' (default: the current hour)")
    ap.add_argument('--no-post', action='store_true', help='make the kit only')
    args = ap.parse_args()
    now = datetime.now(IST)
    slot = (datetime.strptime(args.slot, '%Y-%m-%d %H:%M').replace(tzinfo=IST) if args.slot
            else now.replace(minute=0, second=0, microsecond=0))
    if slot.hour not in HOURS:
        print(f'{slot:%H:%M} is not a posting hour')
        return
    log = load_log()
    key = f'{slot:%Y-%m-%d %H:%M}'
    if any(e['slot'] == key and e['status'] == 'posted' for e in log):
        print(f'{key} already posted')
        return
    names = {k: n for k, n, _, _ in series.movies()}
    data = {k: series.load(p) for k, _, p, _ in series.movies()}
    entry = build(slot, data, names)
    if entry is None:
        print(f'{key}: too few up-to-date figures; no post (see health check)')
        return
    publish = not args.no_post and now - slot <= LATE and os.environ.get('X_API_KEY')
    if publish:
        import xpost
        try:
            entry['tweet'] = xpost.post(entry['text'], [os.path.join(ROOT, 'build', p) for p in entry['images']],
                                        entry['alt'])
            entry['status'] = 'posted'
        except Exception as e:  # keep the kit for manual posting
            entry['status'] = f'error: {str(e)[:300]}'
    log = [e for e in log if e['slot'] != key] + [entry]
    save_log(log)
    print(f"{key}: {entry['status']} ({len(entry['images'])} images)\n{entry['text']}")


if __name__ == '__main__':
    main()
