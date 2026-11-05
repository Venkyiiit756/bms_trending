#!/usr/bin/env python3
"""Forecast agent, version 1: what the next hours and the rest of the day may look like.

Everything numeric is plain statistics on the tracker's own data (nothing a language model made up):

  next hours      hourly counter now x the way the same hours moved yesterday and a week ago (average of the two).
                  Tested on the past: median error ~6% for the next hour (just repeating the last hour is ~10%).
  today's total   tickets so far since 4 AM + what is left of yesterday / last week, scaled by today's pace.
  tomorrow        today's projected total x the usual change from this weekday to the next (all films, past weeks).
  range           from how far the same method was off in the past (10th-90th percentile), never a guess.

What it does not know yet (version 2): review scores and word of mouth, show counts, screens, the weather.
Put what you know in signals.json ([{"film": "Scene", "talk": "good"|"mixed"|"bad", "note": "..."}]); it is shown next to
the numbers and nudges the tomorrow figure a little (good x1.10, bad x0.85). Holidays come from holidays.json.

    python forecast.py                 # write build/forecast.json (also done by build_site.py)
    python forecast.py --outlook       # Claude (or a template) writes the short outlook text -> outlook.json
"""
import argparse
import bisect
import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta

import briefing_facts as bf
import sales
import series

IST = series.IST
ROOT = series.ROOT
H = timedelta(hours=1)
MIN_1H = 240                 # a film needs this many hourly readings before it is forecast
STALE = timedelta(minutes=90)
OUT = os.path.join(ROOT, 'build', 'forecast.json')
OUTLOOK = os.path.join(ROOT, 'outlook.json')
SIGNALS = os.path.join(ROOT, 'signals.json')
HOLIDAYS = os.path.join(ROOT, 'holidays.json')
TALK = {'good': 1.10, 'mixed': 1.0, 'bad': 0.85}
HORIZON = 3
MAXK = 16                    # hours ahead for the whole-day table
DAY_HOURS = range(8, 24)     # the hours the site reports: 7-8 AM ... 11 PM-midnight, labelled by the hour they end


# ---------- hourly series ----------
def make_hv(rs):
    """hv(t): the 'tickets in the last hour' figure at time t, interpolated between readings within 50 minutes."""
    pts = [(r.time, r.value) for r in rs if r.kind == '1_hour']
    times = [t for t, _ in pts]

    def hv(t):
        i = bisect.bisect_left(times, t)
        lo = pts[i - 1] if i > 0 else None
        hi = pts[i] if i < len(pts) else None
        if lo and hi and (t - lo[0]) <= timedelta(minutes=50) and (hi[0] - t) <= timedelta(minutes=50):
            if hi[0] == lo[0]:
                return lo[1]
            return lo[1] + (hi[1] - lo[1]) * ((t - lo[0]) / (hi[0] - lo[0]))
        for p in (lo, hi):
            if p and abs((p[0] - t).total_seconds()) <= 40 * 60:      # only one side available (the newest reading): use it
                return p[1]
        return None
    return hv, (times[-1] if times else None)


def shape(hv, T, k):
    """Forecast of the hourly figure k hours after T: the latest figure x how the same hours moved 1 and 7 days ago."""
    last = hv(T)
    if last is None:
        return None
    preds = []
    for d in (1, 7):
        yk, y0 = hv(T + k * H - timedelta(days=d)), hv(T - timedelta(days=d))
        if yk and y0 and y0 > 0:
            preds.append(last * yk / y0)
    return sum(preds) / len(preds) if preds else None


def films():
    """[(name, readings)] for films with enough hourly readings (one entry per name)."""
    out, seen = [], set()
    for _, name, path, _ in series.movies():
        rs = series.load(path)
        if sum(1 for r in rs if r.kind == '1_hour') >= MIN_1H and name not in seen:
            seen.add(name)
            out.append((name, rs))
    return out


def backtest_hours(fl):
    """Ratios actual / forecast for hours 1..HORIZON ahead, from every past daytime hour that had the data."""
    ratios = {k: [] for k in range(1, MAXK + 1)}
    for _, rs in fl:
        hv, _ = make_hv(rs)
        h1 = [r.time for r in rs if r.kind == '1_hour']
        T = h1[0].astimezone(IST).replace(minute=0, second=0, microsecond=0) + timedelta(days=8)
        end = h1[-1]
        while T + 2 * H < end:
            if 7 <= T.hour <= 22:
                for k in range(1, MAXK + 1):
                    f, a = shape(hv, T, k), hv(T + k * H)
                    if f and a:
                        ratios[k].append(a / f)
            T += H
    return ratios


def band(ratios, k):
    """10th / 50th / 90th percentile of actual/forecast for k hours ahead (neighbouring horizons are pooled when few cases)."""
    vals = list(ratios.get(k, []))
    j = 1
    while len(vals) < 30 and j < 4:
        vals += ratios.get(k - j, []) + ratios.get(k + j, [])
        j += 1
    return quantiles(vals) if vals else (1.0, 1.0, 1.0)


def quantiles(vals, qs=(0.1, 0.5, 0.9)):
    s = sorted(vals)
    return [s[min(len(s) - 1, int(q * len(s)))] for q in qs]


# ---------- day totals ----------
def slot_sum(m, a, b):
    s = miss = 0
    for i in range(a + 1, b + 1):
        v = m['slots'][i] if 0 <= i < len(m['slots']) else -1
        if v < 0:
            miss += 1
        else:
            s += v
    return None if (b - a) <= 0 or miss > (b - a) * 0.1 else s * (b - a) / (b - a - miss)


def project_day(m, hv, T, i_now, i_4am):
    """(so_far, projected total) for the day that started at slot i_4am, seen at hour T (slots: half-hour tickets, 48 = a day).
    So far = slots since 4 AM. The rest of the evening = the hourly forecasts for the remaining hours (counter units x the film's
    counter-to-ticket factor); the night after 11 PM = yesterday's night, scaled by how today's evening compares with yesterday's."""
    so_far = slot_sum(m, i_4am, i_now)
    if not so_far:
        return None
    left = list(range(1, 24 - T.hour + 1)) if T.hour < 24 else []
    fc, yest = 0.0, 0.0
    got = 0
    for k in left:
        f = shape(hv, T, k)
        y = hv(T + k * H - timedelta(days=1))
        if f is not None and y:
            fc, yest, got = fc + f, yest + y, got + 1
    if left and got < 0.8 * len(left):
        return None
    ratio = fc / yest if yest else 1.0
    factor = m.get('factor') or 1.2
    i_23 = i_4am + 38
    night = slot_sum(m, i_23 - 48, i_4am)           # yesterday 11 PM -> 4 AM
    proj = so_far + fc * factor * (len(left) / got if got else 1) + (night or 0) * min(max(ratio, 0.3), 3.0)
    return so_far, proj


def backtest_days(data, hvs):
    """Ratios truth / projection at 3 PM, 6 PM and 9 PM, for days with a BookMyShow 24-hour figure."""
    out = {15: [], 18: [], 21: []}
    for m in data['movies']:
        hv = hvs.get(m['name'])
        if hv is None:
            continue
        s0 = datetime.strptime(m['start'], '%Y-%m-%d %H%M').replace(tzinfo=IST)
        L = len(m['slots']) - 1
        i0 = next((i for i in range(L) if (s0 + timedelta(minutes=30 * i)).strftime('%H%M') == '0400'), None)
        if i0 is None:
            continue
        for i_end in range(i0 + 48 * 9, L + 1, 48):      # skip the first days: no week-ago reference yet
            truth = sales.day24(m, i_end)
            if not truth or not truth['exact'] or truth['v'] < 15000:
                continue
            i_4 = i_end - 48
            day0 = (s0 + timedelta(minutes=30 * i_4)).replace(hour=0, minute=0)
            for hr in out:
                i_now = i_4 + (hr - 4) * 2
                p = project_day(m, hv, day0 + timedelta(hours=hr), i_now, i_4)
                if p and p[1] > 0:
                    out[hr].append(truth['v'] / p[1])
    return out


def weekday_change(data):
    """{weekday: [ratio of next day's total to that day's]} from exact day totals of all films (decay included)."""
    tr = {}
    for m in data['movies']:
        s0 = datetime.strptime(m['start'], '%Y-%m-%d %H%M').replace(tzinfo=IST)
        L = len(m['slots']) - 1
        i0 = next((i for i in range(L) if (s0 + timedelta(minutes=30 * i)).strftime('%H%M') == '0400'), None)
        if i0 is None:
            continue
        vals = [(s, sales.day24(m, s)) for s in range(i0 + 48, L + 1, 48)]
        for (s1, r1), (s2, r2) in zip(vals, vals[1:]):
            if r1 and r2 and r1['exact'] and r2['exact'] and r1['v'] >= 15000:
                wd = (s0 + timedelta(minutes=30 * s1) - timedelta(days=1)).strftime('%a')
                tr.setdefault(wd, []).append(r2['v'] / r1['v'])
    return tr


# ---------- context ----------
def load_json(path, default):
    try:
        return json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        return default


def context(today, film_name, catalog_by_name):
    tomorrow = today + timedelta(days=1)
    ctx = {'today': f'{today:%a %-d %b}', 'tomorrow': f'{tomorrow:%a %-d %b}', 'tomorrow_strong_day': tomorrow.weekday() >= 4}
    ctx['tomorrow_note'] = {4: 'Friday: new releases open, usually a stronger day', 5: 'Saturday: weekend', 6: 'Sunday: weekend'}.get(tomorrow.weekday(), 'weekday')
    hol = [h for h in load_json(HOLIDAYS, []) if isinstance(h, dict) and h.get('date')]
    near = [f"{h['name']} ({h['date']})" for h in hol if 0 <= (date.fromisoformat(h['date']) - today).days <= 4]
    if near:
        ctx['holiday_soon'] = near
    c = catalog_by_name.get(film_name) or {}
    try:
        rel = datetime.strptime(c.get('release') or '', '%b %d, %Y').date()
        d = (today - rel).days
        ctx['release'] = f'{rel:%a %-d %b}'
        ctx['released_days_ago'] = d
        ctx['phase'] = 'opening weekend' if 0 <= d <= 3 else 'first week' if 0 <= d <= 7 else 'releases soon' if d < 0 else 'later weeks'
    except ValueError:
        pass
    return ctx


# ---------- the forecast ----------
def build(now=None):
    now = now or datetime.now(IST)
    today = (now - timedelta(hours=4)).date()               # the day runs from 4 AM
    fl = films()
    ratios = backtest_hours(fl)
    data = sales.build()
    hvs = {name: make_hv(rs)[0] for name, rs in fl}
    dr = backtest_days(data, hvs)
    wd = weekday_change(data)
    catalog = load_json(os.path.join(ROOT, 'catalog.json'), {})
    by_name = {}
    for c in catalog.values():
        if c.get('name') and c.get('release'):
            by_name.setdefault(c['name'], c)
    signals = {s.get('film'): s for s in load_json(SIGNALS, []) if isinstance(s, dict)}
    by_slots = {m['name']: m for m in data['movies']}
    T = now.replace(minute=0, second=0, microsecond=0)
    out = []
    for name, rs in fl:
        hv, newest = make_hv(rs)
        if newest is None or now - newest > STALE or hv(T) is None:
            continue
        cur = hv(T)
        item = {'name': name, 'short': series.short_name(name, 28), 'as_of': f'{T:%-I:%M %p}', 'now': bf.fmt(cur), 'now_value': round(cur)}
        nxt = []
        for k in range(1, HORIZON + 1):
            f = shape(hv, T, k)
            if not f:
                continue
            lo, _, hi = band(ratios, k)
            nxt.append({'hour': series.window_label(T + k * H), 'value': round(f), 'text': bf.fmt(f), 'low': bf.fmt(f * lo), 'high': bf.fmt(f * hi),
                        'low_value': round(f * lo), 'high_value': round(f * hi)})
        if not nxt:
            continue
        item['next'] = nxt
        hours = []
        for hh in DAY_HOURS:
            end = datetime(today.year, today.month, today.day, 0, 0, tzinfo=IST) + timedelta(hours=hh) if hh < 24 else None
            if end is None:
                continue
            row = {'hour': series.window_label(end), 'end': f'{hh:02d}00'}
            yv = hv(end - timedelta(days=1))
            if yv:
                row['yesterday'] = bf.fmt(yv)
                row['yesterday_value'] = round(yv)
            if end <= T:
                a = hv(end)
                if a is None:
                    continue
                row.update({'kind': 'actual', 'value': round(a), 'text': bf.fmt(a)})
            else:
                k = int(round((end - T) / H))
                f = shape(hv, T, k) if k <= MAXK else None
                if not f:
                    continue
                lo, _, hi = band(ratios, k)
                row.update({'kind': 'forecast', 'value': round(f), 'text': bf.fmt(f), 'low': bf.fmt(f * lo), 'high': bf.fmt(f * hi),
                            'low_value': round(f * lo), 'high_value': round(f * hi)})
            hours.append(row)
        item['hours'] = hours
        fut = [x for x in hours if x['kind'] == 'forecast']
        if fut:
            pk = max(fut, key=lambda x: x['value'])
            item['peak_ahead'] = {'hour': pk['hour'], 'text': pk['text'], 'low': pk['low'], 'high': pk['high']}
        y = hv(T - timedelta(days=1))
        if y:
            item['vs_yesterday_pct'] = round((cur - y) / y * 100)
        m = by_slots.get(name)
        if m:
            i_now = min(sales.slot_index(m, T), len(m['slots']) - 1)
            while i_now > 0 and m['slots'][i_now] < 0:        # the newest slot may not be filled yet
                i_now -= 1
            i_4 = sales.slot_index(m, datetime(today.year, today.month, today.day, 4, 0, tzinfo=IST))
            p = project_day(m, hv, T, i_now, i_4) if 0 <= i_4 < i_now <= len(m['slots']) - 1 else None
            if p and len(dr[18]) >= 8:
                hr = min((15, 18, 21), key=lambda h: abs(h - (T.hour if T.hour >= 4 else T.hour + 24)))
                lo, med, hi = quantiles(dr[hr])
                so_far, proj = p
                item['today'] = {'so_far': bf.fmt(so_far), 'so_far_value': round(so_far), 'projected': bf.fmt(proj * med), 'projected_value': round(proj * med),
                                 'low': bf.fmt(proj * lo), 'high': bf.fmt(proj * hi), 'low_value': round(proj * lo), 'high_value': round(proj * hi)}
                trs = wd.get(today.strftime('%a'), [])
                if len(trs) >= 8:
                    q1, qm, q3 = quantiles(trs, (0.25, 0.5, 0.75))
                    sig = signals.get(name, {})
                    adj = TALK.get(sig.get('talk'), 1.0)
                    item['tomorrow'] = {'projected': bf.fmt(proj * med * qm * adj), 'projected_value': round(proj * med * qm * adj),
                                        'low': bf.fmt(proj * lo * q1 * adj), 'high': bf.fmt(proj * hi * q3 * adj),
                                        'low_value': round(proj * lo * q1 * adj), 'high_value': round(proj * hi * q3 * adj),
                                        'basis': f"usual {today:%a}-to-{(today + timedelta(days=1)):%a} change across films: x{qm:.2f} (middle half x{q1:.2f} to x{q3:.2f}), {len(trs)} past cases"}
        item['context'] = context(today, name, by_name)
        if name in signals:
            item['signal'] = {k: signals[name].get(k) for k in ('talk', 'note')}
        out.append(item)
    out.sort(key=lambda x: -x['now_value'])
    q = quantiles(ratios[1])
    errs = [abs(r - 1) for r in ratios[1]]
    return {'generated': now.isoformat(timespec='minutes'), 'as_of': f'{T:%a %-d %b %-I:%M %p}', 'films': out,
            'accuracy': {'by_hours_ahead': {str(k): round(statistics.median(abs(r - 1) for r in ratios[k]) * 100) for k in (1, 2, 4, 8, 12) if len(ratios.get(k, [])) >= 20},
                         'day_error_pct': {str(h): round(statistics.median(abs(r - 1) for r in v) * 100) for h, v in dr.items() if len(v) >= 10},
                         'cases': len(ratios[1]), 'median_error_pct': round(statistics.median(errs) * 100) if errs else None,
                         'next_hour_range': f'x{q[0]:.2f} to x{q[2]:.2f}', 'day_cases': len(dr[18])},
            'not_included': ['review scores and word of mouth (only what is in signals.json)', 'show and screen counts', 'weather', 'release-day surges of films with no history']}


# ---------- outlook text (Claude, with a template fallback) ----------
SYSTEM = """You write a short outlook for a BookMyShow ticket-tracking account (Hyderabad), from FORECAST facts (JSON).
The numbers were computed by a statistical model; you only explain them.

Hard rules:
- Use numbers ONLY exactly as they appear in the facts ("12.5K", "70"). Never calculate, round or convert a number yourself.
- Call every figure a forecast or an estimate ("expected", "could reach"), never a certainty. Mention the range when you give a figure.
- Tickets, not revenue: never use collection, gross, crore, revenue or earnings.
- Do not invent reviews, ratings, collections, show counts or causes. The only causes you may mention are the ones in the facts:
  the weekday of tomorrow, a holiday listed in the facts, the film's release phase, and a "signal" when present. If there is no signal
  say plainly that review and word-of-mouth information is not included yet.
- Write every percentage with a % sign. Films by their "short" name. No links, no promises of more content.
- Cover the whole day, not only the next hour: when the busiest hour still ahead is, roughly how big the day could get, and what tomorrow looks like.
Reply with ONLY JSON: {"headline": "<one sentence, at most 140 characters>", "bullets": ["<3 to 4 short sentences>"], "watch": ["<2 short things that could change these numbers>"]}"""


def template(f):
    films_ = f['films']
    out = {'headline': 'No hourly forecast is available right now.', 'bullets': [], 'watch': []}
    if not films_:
        return out
    a = films_[0]
    n = a['next'][0]
    pk = a.get('peak_ahead')
    out['headline'] = (f"{a['short']}: busiest hour still ahead is {pk['hour']}, about {pk['text']} expected (range {pk['low']} to {pk['high']})." if pk
                       else f"{a['short']} is expected to see about {n['text']} tickets in {n['hour']} (range {n['low']} to {n['high']}).")
    for x in films_[:3]:
        t = x.get('today')
        line = f"{x['short']}: {x['next'][0]['text']} expected in {x['next'][0]['hour']}"
        if t:
            line += f", about {t['projected']} for the whole day (range {t['low']} to {t['high']})"
        out['bullets'].append(line + '.')
    ctx = a.get('context', {})
    out['watch'] = [f"Tomorrow is {ctx.get('tomorrow_note', 'a new day')}.", 'Review and word-of-mouth information is not included yet.']
    return out


def run_outlook(use_llm=True):
    import agentkit
    import briefing
    f = build()
    base = template(f)
    facts = {'as_of': f['as_of'], 'films': [{k: x[k] for k in x if k not in ('now_value', 'hours')} for x in f['films'][:6]], 'accuracy': f['accuracy'],
             'not_included': f['not_included']}
    body, mode, usage = base, 'template', None
    key = os.environ.get('ANTHROPIC_API_KEY')
    if use_llm and key and f['films']:
        msgs = [{'role': 'user', 'content': 'FORECAST:\n' + json.dumps(facts, ensure_ascii=False, indent=1)}]
        tin = tout = 0
        draft = None
        try:
            for _ in (1, 2):
                text, a, b = agentkit.ask_model(SYSTEM, msgs, key)
                tin, tout = tin + a, tout + b
                draft = agentkit.json_in(text)
                bad = []
                if isinstance(draft, dict):
                    parts = [draft.get('headline') or ''] + [x for x in (draft.get('bullets') or []) + (draft.get('watch') or []) if isinstance(x, str)]
                    bad = [p for t in parts for p in briefing.verify(t, facts)]
                    if not isinstance(draft.get('headline'), str) or not draft.get('bullets'):
                        bad.append('need a headline and bullets')
                else:
                    bad = ['reply is not a JSON object']
                if not bad:
                    break
                msgs += [{'role': 'assistant', 'content': text}, {'role': 'user', 'content': 'Fix these problems and reply with ONLY the corrected JSON:\n- ' + '\n- '.join(bad[:10])}]
            if isinstance(draft, dict) and not bad:
                body = {'headline': draft['headline'].strip(), 'bullets': [x for x in draft['bullets'] if isinstance(x, str)][:4], 'watch': [x for x in (draft.get('watch') or []) if isinstance(x, str)][:3]}
                mode = 'claude'
            usage = {'input_tokens': tin, 'output_tokens': tout, 'cost_cents': agentkit.cost_cents(tin, tout)}
        except Exception as e:
            print(f'model writer failed ({e}); using the template', flush=True)
    res = {'generated_at': datetime.now(IST).isoformat(timespec='minutes'), 'as_of': f['as_of'], 'mode': mode, 'model': agentkit.MODEL if mode == 'claude' else None,
           **body, 'usage': usage}
    json.dump(res, open(OUTLOOK, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    print(f"outlook written ({mode}): {body['headline']}", flush=True)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--outlook', action='store_true')
    ap.add_argument('--no-llm', action='store_true')
    a = ap.parse_args()
    if a.outlook:
        run_outlook(not a.no_llm)
    else:
        res = build()
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(res, open(OUT, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        print(json.dumps(res, indent=1, ensure_ascii=False)[:3500])
        sys.exit(0)
