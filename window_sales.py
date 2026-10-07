#!/usr/bin/env python3
"""Estimate tickets booked between two times from bms_booking_log.csv.

BookMyShow only exposes a rolling "tickets booked in the last 1 hour" figure
that refreshes every ~15 minutes, so log rows cannot simply be added up. This
script treats each reading as the sales of the 60 minutes before the moment the
figure refreshed, works out the value at the end of each hour in the window,
and adds those hours up. Times are IST.

Usage:
    python window_sales.py 10:00 12:00
    python window_sales.py "2026-10-07 10:00" "2026-10-07 12:30"
"""
import argparse
import csv
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
HOUR = timedelta(hours=1)


def parse_count(text):
    """'7.49K' -> (7490.0, 5.0): the value and its rounding error in tickets.

    BookMyShow shows two decimals but drops trailing zeros ('14K' is 14.00K,
    '12.1K' is 12.10K), so K and M figures are good to 0.01K / 0.01M.
    """
    text = text.strip().upper()
    mult = {'K': 1e3, 'M': 1e6}.get(text[-1], 1)
    if mult == 1:
        return float(text), 0.5
    return float(text[:-1]) * mult, 0.005 * mult


def parse_row_ts(text):
    ts = datetime.fromisoformat(text)
    # Rows written before the IST switch have no offset and are UTC.
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def parse_when(text, today):
    for fmt in ('%Y-%m-%d %H:%M', '%H:%M'):
        try:
            when = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == '%H:%M':
            when = when.replace(year=today.year, month=today.month, day=today.day)
        return when.replace(tzinfo=IST)
    sys.exit(f"Can't read time '{text}'. Use HH:MM or 'YYYY-MM-DD HH:MM' (IST).")


def load_refreshes(path, metric):
    """Return [(refresh_time, value, rounding)] for each time the figure changed.

    A change was seen somewhere between the previous poll and the row's own
    time, so the refresh time is taken as the midpoint. Newer logs record the
    previous poll (about a minute earlier); older ones only have the previous
    row, which can be several minutes earlier.
    """
    with open(path, newline='', encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if r['metric_type'] == metric]
    refreshes, prev_ts, prev_val = [], None, None
    for row in rows:
        ts = parse_row_ts(row['timestamp'])
        val, err = parse_count(row['metric_value'])
        if val != prev_val:
            before = row.get('prev_poll')
            lo = parse_row_ts(before) if before else prev_ts
            when = ts if lo is None else lo + (ts - lo) / 2
            refreshes.append((when, val, err))
        prev_ts, prev_val = ts, val
    return refreshes


def value_at(refreshes, when):
    """Trailing-hour sales as of `when`: (estimate, low, high, note) or None."""
    times = [r[0] for r in refreshes]
    i = bisect_right(times, when) - 1
    if i < 0:
        return None
    t0, v0, e0 = refreshes[i]
    if i + 1 == len(refreshes):
        step = v0 - refreshes[i - 1][1] if i else 0
        note = 'after latest refresh (value may be stale)'
        return v0, v0 - e0, v0 + max(step, 0) + e0, note
    t1, v1, e1 = refreshes[i + 1]
    frac = (when - t0) / (t1 - t0)
    est = v0 + frac * (v1 - v0)
    return est, min(v0, v1) - e0, max(v0, v1) + e1, ''


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('start', help="IST, 'HH:MM' (today) or 'YYYY-MM-DD HH:MM'")
    ap.add_argument('end')
    ap.add_argument('--csv', default='bms_booking_log.csv')
    ap.add_argument('--metric', default='1_hour')
    args = ap.parse_args()

    today = datetime.now(IST)
    start, end = parse_when(args.start, today), parse_when(args.end, today)
    if end <= start:
        sys.exit('End must be after start.')
    if end > today:
        sys.exit(f"End {end:%H:%M} is in the future (now {today:%H:%M} IST).")
    refreshes = load_refreshes(args.csv, args.metric)
    if not refreshes:
        sys.exit(f'No {args.metric} rows in {args.csv}.')

    whole = int((end - start) / HOUR)
    partial = (end - start) / HOUR - whole
    # Anchor on the end time, working backwards, so the newest data is used.
    parts = [(end - i * HOUR, 1.0) for i in range(whole)]
    if partial > 1e-9:
        parts.append((end - whole * HOUR, partial))

    total = [0.0, 0.0, 0.0]
    print(f"Tickets booked {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M} IST\n")
    print(f"{'hour ending':<18}{'weight':>7}{'estimate':>11}{'low':>9}{'high':>9}")
    for when, weight in sorted(parts):
        got = value_at(refreshes, when)
        if got is None:
            first = refreshes[0][0].astimezone(IST)
            sys.exit(f"No data at {when:%H:%M}; the log starts at {first:%Y-%m-%d %H:%M} IST.")
        est, low, high, note = got
        for k, v in enumerate((est, low, high)):
            total[k] += weight * v
        print(f"{when:%m-%d %H:%M:%S}   {weight:>6.2f}{est:>11,.0f}{low:>9,.0f}{high:>9,.0f}"
              + (f"  <- {note}" if note else ''))

    print(f"\nEstimated total: {total[0]:,.0f} tickets  (range {total[1]:,.0f} - {total[2]:,.0f})")
    if partial > 1e-9:
        print(f"Note: the last {partial * 60:.0f} min is scaled from the hourly rate, "
              "so it is rougher than whole hours.")
    print("Assumes each figure is the 60 minutes before the moment it refreshed.")


if __name__ == '__main__':
    main()
