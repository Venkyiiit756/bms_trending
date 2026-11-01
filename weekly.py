#!/usr/bin/env python3
"""Weekly report agent: last week's (Monday 4 AM to Monday 4 AM, IST) ticket story in a few clean lines.

Same pattern as the daily briefing: Python computes every figure (week totals from the same 4 AM-4 AM
day totals as the website), Claude (if ANTHROPIC_API_KEY is set) writes a headline and four bullets, each
number is checked against the facts, and a plain template is the fallback. Output: weekly.json (+ weeklies/).
Runs Monday morning (started by tracker.py); skipped on other days unless --force / --date.

    python weekly.py [--date YYYY-MM-DD] [--force] [--no-llm]     # date = the Monday the week ends on
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

import agentkit
import briefing
import briefing_facts as bf
import interest
import sales
import series

ROOT = series.ROOT
OUT = os.path.join(ROOT, 'weekly.json')
ARCHIVE = os.path.join(ROOT, 'weeklies')
MIN_WEEK = 60_000              # a film needs this many tickets in the week to be listed


def week_end(today):
    """The Monday (4 AM) that closes the latest finished week."""
    return today - timedelta(days=today.weekday())


def week_facts(end):
    t4 = datetime(end.year, end.month, end.day, 4, 0, tzinfo=series.IST)
    start = end - timedelta(days=7)
    films = []
    for m in sales.build()['movies']:
        i = sales.slot_index(m, t4)
        days = [sales.day24(m, i - 48 * k) for k in range(14)]          # 0 = Sunday just closed ... 13
        cur, prev = days[:7], days[7:]
        if sum(1 for d in cur if d) < 5:
            continue
        tot = sum(d['v'] for d in cur if d)
        if tot < MIN_WEEK:
            continue
        ptot = sum(d['v'] for d in prev if d) if sum(1 for d in prev if d) >= 5 else None
        best = max(((k, d) for k, d in enumerate(cur) if d), key=lambda kd: kd[1]['v'])
        bd = end - timedelta(days=best[0] + 1)
        films.append({'name': m['name'], 'short': series.short_name(m['name'], 26), 'week_total': bf.fmt(tot), 'week_value': round(tot),
                      'days_counted': sum(1 for d in cur if d), 'exact': all(d['exact'] for d in cur if d),
                      'vs_prior_week': bf.pct(tot, ptot) if ptot else None,
                      'best_day': bf.day_label(bd), 'best_day_tickets': bf.fmt(best[1]['v'])})
    films.sort(key=lambda f: -f['week_value'])
    seen, uniq = set(), []
    for f in films:                                   # two listings of one film can carry the same counter
        if f['week_value'] not in seen:
            seen.add(f['week_value'])
            uniq.append(f)
    for k, f in enumerate(uniq, 1):
        f['rank'] = k
    # interest gained over the week, one entry per film
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8'))
    cut = datetime(end.year, end.month, end.day, 8, 0, tzinfo=series.IST)
    best_i = {}
    for code, m in interest.series().items():
        pts = [p for p in m['points'] if p[0] <= cut]
        old = [p for p in pts if p[0] <= pts[-1][0] - timedelta(days=6)] if pts else []
        if not old:
            continue
        c = catalog.get(code, {})
        g = pts[-1][1] - old[-1][1]
        e = {'name': c.get('name') or m['name'], 'short': series.short_name(c.get('name') or m['name'], 26),
             'interested': bf.fmt(pts[-1][1]) + '+', 'gained_7d_value': g, 'gained_7d': ('+' if g >= 0 else '−') + bf.fmt(abs(g))}
        key = c.get('group') or code
        if key not in best_i or len(pts) > best_i[key][0]:
            best_i[key] = (len(pts), e)
    gain = sorted((e for _, e in best_i.values() if e['gained_7d_value'] > 0), key=lambda e: -e['gained_7d_value'])[:4]
    opened = bf._opened(end + timedelta(days=0))
    return {'week': f'{bf.day_label(start)} to {bf.day_label(end - timedelta(days=1))}', 'window': f'4 AM {bf.day_label(start)} to 4 AM {bf.day_label(end)} IST',
            'end': end.isoformat(), 'films': uniq[:8], 'film_count': len(uniq), 'interest_gainers': gain,
            'bookings_opened_since_yesterday': opened,
            'notes': ['Tickets are counted from BookMyShow counters (one listing per film, not revenue).',
                      'Week totals add the seven 4 AM to 4 AM days; a film needs at least 5 of 7 days counted.']}


def template(f):
    fl, gi = f['films'], f['interest_gainers']
    out = {'headline': 'No full-week ticket figures were available.', 'bullets': []}
    if fl:
        a = fl[0]
        out['headline'] = f"{series.short_name(a['name'], 26)} led the week of {f['week']} with {a['week_total']} tickets" + (
            f", {abs(a['vs_prior_week'])}% {'up' if a['vs_prior_week'] > 0 else 'down'} on the week before." if a['vs_prior_week'] is not None else '.')
        out['bullets'].append('Top films: ' + ' · '.join(f"{x['rank']}. {x['short']} {x['week_total']}" for x in fl[:3]))
        risers = [x for x in fl if x['vs_prior_week'] is not None and x['week_value'] >= 100_000]
        if risers:
            up = max(risers, key=lambda x: x['vs_prior_week'])
            dn = min(risers, key=lambda x: x['vs_prior_week'])
            out['bullets'].append(f"Biggest rise: {up['short']}, up {up['vs_prior_week']}% to {up['week_total']}." if up['vs_prior_week'] > 0 else '')
            if dn is not up and dn['vs_prior_week'] < 0:
                out['bullets'].append(f"Biggest drop: {dn['short']}, down {abs(dn['vs_prior_week'])}% to {dn['week_total']}.")
        out['bullets'].append(f"Best single day: {a['short']} on {a['best_day']} with {a['best_day_tickets']} tickets.")
    if gi:
        out['bullets'].append('Interest gained this week: ' + ' · '.join(f"{g['short']} {g['gained_7d']}" for g in gi[:3]))
    out['bullets'] = [b for b in out['bullets'] if b]
    return out


SYSTEM = """You write the weekly report for a BookMyShow ticket-tracking account (Hyderabad, Indian films).
You receive FACTS (JSON) for one week, Monday 4 AM to Monday 4 AM IST.

Hard rules:
- Use numbers ONLY exactly as they appear in the facts ("1.07M", "217K", "38"). Never add, subtract, round or convert.
- We count tickets, not revenue: never use collection, gross, crore, revenue or earnings.
- Do not explain causes unless the facts do. Say what the numbers show. Call films by their "short" name.
- Percentages carry a % sign and words like up or down, never a plus/minus sign.
- No links, "below", "thread" or promises of more content.
Reply with ONLY JSON: {"headline": "<one sentence, at most 150 characters>", "bullets": ["<exactly 4 short sentences>"]}"""


def write(f, key, log):
    messages = [{'role': 'user', 'content': 'FACTS:\n' + json.dumps(f, ensure_ascii=False, indent=1)}]
    tin = tout = 0
    draft = None
    for attempt in (1, 2):
        text, a, b = agentkit.ask_model(SYSTEM, messages, key)
        tin, tout = tin + a, tout + b
        draft = agentkit.json_in(text)
        bad = []
        if not isinstance(draft, dict):
            bad = ['reply is not a JSON object']
        else:
            parts = [('headline', draft.get('headline') or '')] + [(f'bullet {i + 1}', x) for i, x in enumerate(draft.get('bullets') or []) if isinstance(x, str)]
            bad = [f'{n}: {p}' for n, t in parts for p in briefing.verify(t, f)]
            if not isinstance(draft.get('headline'), str) or not 3 <= len(draft.get('bullets') or []) <= 5:
                bad.append('need a headline and 4 bullets')
        log.append({'attempt': attempt, 'problems': bad})
        if not bad:
            return draft, tin, tout
        messages += [{'role': 'assistant', 'content': text},
                     {'role': 'user', 'content': 'Fix these problems and reply with ONLY the corrected JSON:\n- ' + '\n- '.join(bad[:10])}]
    return draft, tin, tout


def run(end, use_llm=True, force=False):
    if not force and os.path.exists(OUT):
        try:
            if json.load(open(OUT, encoding='utf-8')).get('end') == end.isoformat():
                print(f'weekly report for {end} already exists')
                return None
        except ValueError:
            pass
    f = week_facts(end)
    body, mode, log, usage = template(f), 'template', [], None
    for t in [body['headline'], *body['bullets']]:
        assert not briefing.verify(t, f), (t, briefing.verify(t, f))
    key = os.environ.get('ANTHROPIC_API_KEY')
    if use_llm and key and f['films']:
        try:
            d, tin, tout = write(f, key, log)
            usage = {'input_tokens': tin, 'output_tokens': tout, 'cost_cents': agentkit.cost_cents(tin, tout)}
            if isinstance(d, dict):
                h = d.get('headline') if isinstance(d.get('headline'), str) and not briefing.verify(d['headline'], f) else None
                bl = [x.strip() for x in d.get('bullets') or [] if isinstance(x, str) and x.strip() and not briefing.verify(x, f)][:5]
                if h or len(bl) >= 3:
                    body = {'headline': h or body['headline'], 'bullets': bl if len(bl) >= 3 else body['bullets']}
                    mode = 'claude' if h and len(bl) >= 3 else 'claude+template'
        except Exception as e:
            log.append({'error': str(e)[:200]})
            print(f'model writer failed ({e}); using the template', flush=True)
    res = {'end': f['end'], 'week': f['week'], 'window': f['window'], 'generated_at': datetime.now(series.IST).isoformat(timespec='seconds'),
           'mode': mode, 'model': agentkit.MODEL if mode.startswith('claude') else None, 'headline': body['headline'],
           'bullets': body['bullets'], 'usage': usage, 'attempts': log,
           'films': [{k: x.get(k) for k in ('rank', 'name', 'week_total', 'vs_prior_week', 'best_day', 'best_day_tickets', 'exact')} for x in f['films']],
           'interest': [{k: g.get(k) for k in ('name', 'interested', 'gained_7d')} for g in f['interest_gainers']]}
    os.makedirs(ARCHIVE, exist_ok=True)
    for path in (OUT, os.path.join(ARCHIVE, f"{f['end']}.json")):
        json.dump(res, open(path, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    for fn in sorted(os.listdir(ARCHIVE))[:-26]:
        os.remove(os.path.join(ARCHIVE, fn))
    print(f"weekly report {f['week']}: mode {mode}; {len(res['bullets'])} bullets, {len(res['films'])} films", flush=True)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--no-llm', action='store_true')
    a = ap.parse_args()
    today = date.fromisoformat(a.date) if a.date else datetime.now(series.IST).date()
    if today.weekday() != 0 and not a.force and not a.date:
        print('not Monday; nothing to do')
        sys.exit(0)
    run(week_end(today), not a.no_llm, a.force or bool(a.date))
