#!/usr/bin/env python3
"""Tickets sold per half hour for each movie, for daily totals on the website.

BookMyShow shows either "booked in the last 1 hour" or "booked in the last 24 hours" for a
movie, never both. Big films show the hourly count by day and the 24-hour count late at
night (about 2-7 AM); small films show the 24-hour count all the time.

- slots[i]: estimated tickets in the 30 minutes ending at start + i*30 min. Half of the hourly
  figure at each half hour adds up to the true total (every ticket sits in two overlapping
  hourly windows). Short data gaps are filled linearly. Night hours, when only the 24-hour
  figure shows, are filled with a dip towards zero between the last and first hourly figures,
  scaled by NIGHT_SHARE: fitted against BookMyShow's 24-hour figures, real night sales are
  about 0.3 of that dip.
- Summed hourly figures come out below BookMyShow's own 24-hour figure (about 15-20% for big
  films), so daytime slots are scaled by `factor`: the median over recent nights of
  (24-hour figure - night estimate) / daytime estimate for the 48 slots before it.
- Checked by leaving each night out in turn (Oct 2026, 45 nights): typical error 0.7%, 90% within
  3.7%. Films that rarely show an hourly figure get no estimates (MIN_NIGHTS / MIN_HOURLY_SHARE).
- d24[i]: BookMyShow's 24-hour figure in force at that half hour (exact), when shown.
"""
import statistics
from datetime import datetime, timedelta

import series

STEP = timedelta(minutes=30)
MAX_AGE = timedelta(minutes=20)    # a reading older than this does not describe the half hour
MAX_AGE_24 = timedelta(minutes=60) # the 24-hour figure barely moves at night
NEAR = 10                           # use BookMyShow's 24-hour figure up to 5 hours (10 slots) from 4 AM
MAX_LINEAR = 8                      # fill data gaps up to 4 hours
MAX_NIGHT = 28                      # night gaps up to 14 hours
DEFAULT_FACTOR = 1.15
FACTOR_RANGE = (0.9, 1.5)
NIGHT_SHARE = 0.3
NIGHT_START, NIGHT_END = 22, 12      # IST hours a 24-hour-only gap may count as night
NIGHTS = 7
MIN_NIGHTS = 3
MIN_HOURLY_SHARE = 0.4


def _grid(readings):
    """In-force reading at every half hour from the first reading to the last."""
    first = readings[0].time.astimezone(series.IST)
    t = first.replace(minute=first.minute // 30 * 30, second=0, microsecond=0) + STEP
    out, i, cur = [], 0, None
    while t <= readings[-1].time:
        while i < len(readings) and readings[i].time <= t:
            cur = readings[i]
            i += 1
        ok = cur and t - cur.time <= (MAX_AGE_24 if cur.kind == '24_hours' else MAX_AGE)
        out.append((t, cur if ok else None))
        t += STEP
    return out


def _rates(grid):
    """Hourly rate at each half hour (known, filled, or None), and which ones are night fills."""
    rate = [r.value if r and r.kind == '1_hour' else None for _, r in grid]
    known = [i for i, v in enumerate(rate) if v is not None]
    out, night_fill = rate[:], [False] * len(rate)
    for a, b in zip(known, known[1:]):
        if b - a < 2:
            continue
        shown24 = [grid[j][0].astimezone(series.IST).hour for j in range(a + 1, b)
                   if grid[j][1] and grid[j][1].kind == '24_hours']
        night = bool(shown24)
        if night and any(NIGHT_END <= h < NIGHT_START for h in shown24):
            continue    # a small film showing its 24-hour figure by day: hourly sales unknown
        if (night and b - a > MAX_NIGHT) or (not night and b - a > MAX_LINEAR):
            continue
        for j in range(a + 1, b):
            x = (j - a) / (b - a)
            lin = rate[a] + (rate[b] - rate[a]) * x
            out[j] = lin * abs(2 * x - 1) * NIGHT_SHARE if night else lin
            night_fill[j] = night
    return out, night_fill


def _ratios(grid, raw, night_fill):
    """(BMS 24-hour figure - night estimate) / daytime estimate, one per night, oldest first."""
    per_night = {}
    for i in range(47, len(grid)):
        t, r = grid[i]
        if not r or r.kind != '24_hours':
            continue
        win = raw[i - 47:i + 1]
        if any(v is None for v in win) or sum(win) < 1000:
            continue
        night = (t - timedelta(hours=12)).date()
        nights = sum(v for v, n in zip(win, night_fill[i - 47:i + 1]) if n)
        per_night.setdefault(night, []).append((r.value - nights) / (sum(win) - nights))
    return [statistics.median(v) for _, v in sorted(per_night.items())]


def build():
    """{'step': 30, 'movies': [...]} for the website."""
    found = []
    for key, name, path, _ in series.movies():
        readings = series.load(path)
        if len(readings) < 2:
            continue
        grid = _grid(readings)
        rates, night_fill = _rates(grid)
        raw = [None if v is None else v / 2 for v in rates]
        found.append((name, grid, raw, night_fill, _ratios(grid, raw, night_fill)))
    pooled = [r for *_, ratios in found for r in ratios[-NIGHTS:]]
    default = statistics.median(pooled) if len(pooled) >= 3 else DEFAULT_FACTOR
    movies = []
    for name, grid, raw, night_fill, ratios in found:
        recent = ratios[-NIGHTS:]
        factor = statistics.median(recent) if len(recent) >= 2 else default
        factor = min(max(factor, FACTOR_RANGE[0]), FACTOR_RANGE[1])
        # films that rarely show an hourly figure and cannot be checked against enough nights:
        # no estimates (measured 18-25% off); the site then uses BookMyShow's 24-hour figures only
        hourly = sum(1 for _, r in grid[-336:] if r and r.kind == '1_hour') / len(grid[-336:])
        if len(recent) < MIN_NIGHTS and hourly < MIN_HOURLY_SHARE:
            raw = [None] * len(raw)
        movies.append({
            'name': name,
            'start': grid[0][0].strftime('%Y-%m-%d %H%M'),
            'factor': round(factor, 3),
            'nights': len(recent),
            'slots': [-1 if v is None else round(v if n else v * factor) for v, n in zip(raw, night_fill)],
            'd24': {i: round(r.value) for i, (_, r) in enumerate(grid) if r and r.kind == '24_hours'},
        })
    movies.sort(key=lambda m: -sum(v for v in m['slots'][-96:] if v > 0) - sum(list(m['d24'].values())[-1:]))
    return {'step': 30, 'movies': movies}


# --- day totals (the same rules as the website's Totals tab) ---
def slot_index(m, when):
    """Index of the half-hour slot ending at `when` (IST datetime) in a movie from build()."""
    start = datetime.strptime(m['start'], '%Y-%m-%d %H%M').replace(tzinfo=series.IST)
    return round((when - start).total_seconds() / 1800)


def slsum(m, a, b):
    """Estimated tickets in slots (a, b]; None when more than 10% of them have no data."""
    s = miss = 0
    for i in range(a + 1, b + 1):
        v = m['slots'][i] if 0 <= i < len(m['slots']) else -1
        if v < 0:
            miss += 1
        else:
            s += v
    n = b - a
    if not n or miss > n * 0.1:
        return None
    return {'v': s * n / (n - miss), 'exact': False}


def day24(m, i):
    """Tickets in the 24 hours ending at slot i: BookMyShow's own figure when it showed one (also within two
    slots of i, corrected by the estimated slots in between), else the estimate; None when unknown."""
    e = m['d24'].get(i)
    if e is not None:
        return {'v': e, 'exact': True}

    def raw(a, b):
        t = 0
        for j in range(a + 1, b + 1):
            v = m['slots'][j] if 0 <= j < len(m['slots']) else None
            if v is None or v < 0:
                return None
            t += v
        return t
    # BookMyShow's own figure on both sides within an hour: interpolate (the counter barely moves at night)
    lo = next(((k, m['d24'][i - k]) for k in (1, 2) if m['d24'].get(i - k) is not None), None)
    hi = next(((k, m['d24'][i + k]) for k in (1, 2) if m['d24'].get(i + k) is not None), None)
    if lo and hi:
        return {'v': (lo[1] * hi[0] + hi[1] * lo[0]) / (lo[0] + hi[0]), 'exact': True}
    for k in (1, -1, 2, -2):
        f = m['d24'].get(i + k)
        if f is None:
            continue
        lo, hi = min(i, i + k), max(i, i + k)
        now, then = raw(lo, hi), raw(lo - 48, hi - 48)
        if now is None or then is None:
            continue
        return {'v': f - now + then if k > 0 else f + now - then, 'exact': True}
    # No reading close to 4 AM (the tracker did not ask then): BookMyShow's nearest 24-hour figure within
    # four hours, blended between both sides when there is one on each side. Night sales are small and the
    # window moves by about as much as it drops off, so this is within about 1% (tested against days that
    # have a reading at 4 AM: median 0.3% off at 3 hours, 1% at 4 hours).
    lo = next(((k, m['d24'][i - k]) for k in range(1, NEAR + 1) if m['d24'].get(i - k) is not None), None)
    hi = next(((k, m['d24'][i + k]) for k in range(1, NEAR + 1) if m['d24'].get(i + k) is not None), None)
    if lo and hi:
        return {'v': (lo[1] * hi[0] + hi[1] * lo[0]) / (lo[0] + hi[0]), 'exact': True, 'near': min(lo[0], hi[0])}
    if lo or hi:
        k, v = lo or hi
        return {'v': v, 'exact': True, 'near': k}
    return slsum(m, i - 48, i)


if __name__ == '__main__':
    data = build()
    for m in data['movies']:
        known = sum(v >= 0 for v in m['slots'])
        print(f"{m['name'][:28]:28} from {m['start']}  slots {len(m['slots'])} known {known} "
              f"24h {len(m['d24'])} factor {m['factor']} ({m['nights']} nights)")
