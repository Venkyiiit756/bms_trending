#!/usr/bin/env python3
"""Daily briefing agent: yesterday's box-office story for the website and three X draft posts.

Workflow (runs each morning, started by tracker.py; see .github/workflows/daily_briefing.yml):

    1. briefing_facts.facts()     Python computes every figure. The model never does arithmetic.
    2. writer                     Claude (if ANTHROPIC_API_KEY is set) drafts headline, bullets and 3 posts
                                  as JSON from those facts. Without a key, or if the model fails, the
                                  plain template writer below is used, so a briefing is always produced.
    3. verify()                   every quantity in the text (217K, 1.07M, 130%) must appear in the facts,
                                  no revenue words, posts fit X's limit. Failures go back to the model once
                                  with the list of problems; anything still failing is replaced by the template.
    4. briefing.json              published on the website (Auto posts tab). Posts are DRAFTS: nothing is sent
                                  to X; the owner copies or opens them from the site.

    python briefing.py                  # today's briefing (IST), skipped if it already exists
    python briefing.py --date 2026-10-31 --force --no-llm
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

import briefing_facts
import series

ROOT = series.ROOT
OUT = os.path.join(ROOT, 'briefing.json')
ARCHIVE = os.path.join(ROOT, 'briefings')
KEEP_DAYS = 30
MODEL = os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-5-5')      # the cheapest current model; the verifier guards the quality
POST_LIMIT = 272                    # X allows 280; keep a margin
BANNED = re.compile(r'\b(collections?|gross(ed)?|crores?|revenue|box[- ]office collection|earn(ed|ings)?)\b', re.I)
QTY = re.compile(r'(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s?([KkMm](?![A-Za-z])|%)')
BIGINT = re.compile(r'(?<![\w.,])(\d{4,})(?![\w.,])')
NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')


def xlen(s):
    """X counts emoji as 2 characters."""
    return sum(2 if ord(c) > 0x2fff else 1 for c in s)


def tag(name):
    t = re.sub(r'[^A-Za-z0-9]', '', series.short_name(name, 30))
    return f'#{t}' if t and not t.isdigit() else ''


def short(name, n=22):
    return series.short_name(name, n)


# ---------- verification ----------
def allowed(facts):
    """Quantities the writer may use: every K/M figure in the facts verbatim, and every plain number."""
    text = json.dumps(facts, ensure_ascii=False)
    km = {f'{m.group(1).replace(",", "")}{m.group(2).upper()}' for m in QTY.finditer(text) if m.group(2) in 'KkMm'}
    nums = {m.group(0).replace(',', '') for m in NUM.finditer(text)}
    return km, nums


def verify(text, facts, post=False):
    """Problems found in one piece of text (empty list = fine)."""
    km, nums = allowed(facts)
    bad = []
    for m in QTY.finditer(text):
        val, unit = m.group(1).replace(',', ''), m.group(2)
        if unit == '%':
            if val not in nums and val.rstrip('0').rstrip('.') not in nums:
                bad.append(f'{m.group(0)} is not in the facts')
        elif f'{val}{unit.upper()}' not in km:
            bad.append(f'{m.group(0)} is not in the facts')
    for m in BIGINT.finditer(text):
        v = m.group(1)
        if not (1900 <= int(v) <= 2100) and v not in nums:
            bad.append(f'{v} is not in the facts')
    for m in BANNED.finditer(text):
        bad.append(f'"{m.group(0)}" is not allowed (we count tickets, not revenue)')
    if post and xlen(text) > POST_LIMIT:
        bad.append(f'post is {xlen(text)} characters (limit {POST_LIMIT})')
    return bad


# ---------- the template writer (also the fallback) ----------
def _pct(p):
    return '' if p is None else f'{"▲" if p > 0 else "▼"}{abs(p)}%'


def fit_post(head, lines, tags):
    L = list(lines)
    while True:
        txt = '\n'.join([head, '', *L, '', tags]) if L else '\n'.join([head, '', tags])
        if xlen(txt) <= POST_LIMIT or len(L) <= 1:
            return txt
        L.pop()


def template(f):
    films, ints = f['films'], f['interest_top']
    top = films[:3]
    parts = []
    out = {'headline': '', 'bullets': [], 'drafts': [], 'flags': list(f['flags'])}
    if top:
        a = top[0]
        out['headline'] = f"{short(a['name'], 26)} led {f['day']} with {a['yesterday']} tickets" + (
            f" ({_pct(a['vs_day_before'])} vs the day before)" if a['vs_day_before'] is not None else '') + (
            f"; {short(top[1]['name'], 26)} followed with {top[1]['yesterday']}." if len(top) > 1 else '.')
        out['bullets'].append('Top films, 4 AM to 4 AM: ' + ' · '.join(
            f"{i}. {short(x['name'], 26)} {x['yesterday']}" for i, x in enumerate(top, 1)))
        for x in films[:6]:
            if x['vs_day_before'] is not None and abs(x['vs_day_before']) >= briefing_facts.BIG_MOVE and x['yesterday_value'] >= briefing_facts.MIN_MOVE:
                out['bullets'].append(f"{short(x['name'], 26)} {'rose' if x['vs_day_before'] > 0 else 'fell'} {abs(x['vs_day_before'])}% "
                                      f"({x['day_before']} to {x['yesterday']}).")
        busy = [x for x in films[:5] if x.get('busiest_hour')]
        if busy:
            b = busy[0]
            out['bullets'].append(f"Busiest single hour: {short(b['name'], 26)} with {b['busiest_hour']} around {b['busiest_hour_at']}.")
    else:
        out['headline'] = f"No full-day ticket figures were available for {f['day']}."
    if ints:
        i0 = ints[0]
        out['bullets'].append(f"Most awaited: {short(i0['name'], 26)} with {i0['interested']} interested" + (
            f" ({i0['gained_24h']} in 24 hours" + (f", out {i0['release']}" if i0.get('release') else '') + ')' if i0.get('gained_24h') else ''))
    for g in f['interest_gainers'][:2]:
        if g['name'] != (ints[0]['name'] if ints else None) and g.get('gained_24h_value', 0) > 0:
            out['bullets'].append(f"Rising interest: {short(g['name'], 26)} {g['gained_24h']} to {g['interested']}.")
    for o in f['bookings_opened']:
        out['bullets'].append(f"Bookings opened for {short(o['name'], 26)}" + (f" (releases {o['release']})." if o.get('release') else '.'))

    # drafts
    drafts = []
    if top:
        lines = [f"{i}. {short(x['name'], 26)}: {x['yesterday']}" + (f" ({_pct(x['vs_day_before'])})" if x['vs_day_before'] is not None else '')
                 for i, x in enumerate(top, 1)]
        tags = ' '.join(dict.fromkeys([t for t in (tag(x['name']) for x in top[:2]) if t] + ['#BoxOffice']))
        drafts.append({'story': 'leaderboard', 'film': None,
                       'text': fit_post(f"🎟️ Tickets booked on BookMyShow, {f['day']} (4 AM to 4 AM)", lines, tags)})
    mover = f['biggest_move'] or (top[0] if top else None)
    if mover:
        up = (mover['vs_day_before'] or 0) >= 0
        head = (f"{'📈' if up else '📉'} {short(mover['name'], 26)}: {mover['yesterday']} tickets on {f['day']}" +
                (f", {_pct(mover['vs_day_before'])} vs the day before ({mover['day_before']})." if mover['vs_day_before'] is not None else '.'))
        extra = [f"Busiest hour: {mover['busiest_hour']} around {mover['busiest_hour_at']}."] if mover.get('busiest_hour') else []
        drafts.append({'story': 'mover', 'film': mover['name'],
                       'text': fit_post(head, extra, ' '.join(t for t in (tag(mover['name']), '#BoxOffice') if t))})
    if f['bookings_opened']:
        o = f['bookings_opened'][0]
        drafts.append({'story': 'bookings', 'film': o['name'],
                       'text': fit_post(f"🔔 Bookings are open for {short(o['name'], 26)}" + (f" (releases {o['release']})." if o.get('release') else '.'),
                                        [], ' '.join(t for t in (tag(o['name']), '#BoxOffice') if t))})
    elif ints:
        lines = [f"{i}. {short(e['name'], 26)}: {e['interested']}" + (f" ({e['gained_24h']} in 24h)" if e.get('gained_24h') else '')
                 for i, e in enumerate(ints[:3], 1)]
        tags = ' '.join(dict.fromkeys([t for t in (tag(e['name']) for e in ints[:2]) if t] + ['#BoxOffice']))
        drafts.append({'story': 'interest', 'film': None,
                       'text': fit_post('🍿 Most awaited on BookMyShow (people who marked "interested")', lines, tags)})
    out['drafts'] = drafts
    return out


# ---------- the model writer ----------
SYSTEM = """You are the morning analyst for a BookMyShow ticket-tracking account on X (Hyderabad, Indian films).
You receive FACTS (JSON) about yesterday, 4 AM to 4 AM IST. Write a short briefing and three X draft posts.

Hard rules:
- Use numbers ONLY exactly as they appear in the facts (for example "217K", "1.07M", "130"). Never add, subtract,
  round, convert or estimate a number yourself. If you need a number that is not in the facts, leave it out.
- We count tickets booked, not revenue. Never use the words collection, gross, crore, revenue or earnings.
- Do not explain why something happened unless the facts say so. Say what the numbers show.
- Figures that start with ≈ are estimates; keep the ≈.
- Posts: at most 272 characters, friendly and clear, 2-3 hashtags including #BoxOffice, emoji allowed.
- Do not invent films, dates, or hashtags for films not in the facts.
- Call each film by its "short" name from the facts (never the long "name").
- Never promise or mention anything outside the facts (links, a full list, a thread, an image being posted).
- Percentages always carry a % sign and no plus or minus sign next to words like up, down, rose or fell
  (write "up 130%" or "down 68%", never "up +130%", "fell -68" or "up 38").
- "flags" is only for real problems you notice in the facts (an inconsistent date, a figure that looks wrong, stale data
  when health.minutes_old_at_8am is above 90). Do not repeat anything already listed in the facts' own flags, and use
  an empty list when everything looks fine.

Reply with ONLY a JSON object:
{"headline": "<one sentence, at most 140 characters>",
 "bullets": ["<3 to 5 short sentences>"],
 "drafts": [{"story": "leaderboard|mover|interest|bookings", "film": "<film name or null>", "text": "<post>"}, ... exactly 3],
 "flags": ["<anything the owner should double-check, or an empty list>"]}"""


def _call(messages, key):
    """One request through the official SDK (it retries 429/5xx itself). Returns the reply text.

    No temperature / top_p: the API rejects sampling settings. Effort "low" keeps the model's own
    thinking (billed as output) small - the task is writing from verified facts, not reasoning.
    """
    import anthropic
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=120)
    r = client.messages.create(model=MODEL, max_tokens=4000, system=SYSTEM, messages=messages,
                               output_config={'effort': 'low'})
    if r.stop_reason == 'refusal':
        raise RuntimeError('the model declined this request')
    return ''.join(b.text for b in r.content if b.type == 'text')


def _json(text):
    m = re.search(r'\{.*\}', text, re.S)
    return json.loads(m.group(0)) if m else None


def problems(draft, f):
    """All problems in a model draft: structure plus every text field verified against the facts."""
    out = []
    if not isinstance(draft, dict):
        return ['reply is not a JSON object']
    if not isinstance(draft.get('headline'), str) or not draft['headline'].strip():
        out.append('headline is missing')
    bl = draft.get('bullets')
    if not isinstance(bl, list) or not 2 <= len(bl) <= 6 or not all(isinstance(b, str) for b in bl):
        out.append('bullets must be 3 to 5 strings')
    dr = draft.get('drafts')
    if not isinstance(dr, list) or len(dr) != 3 or not all(isinstance(d, dict) and isinstance(d.get('text'), str) for d in dr):
        out.append('drafts must be exactly 3 objects with a text')
    for field, txt in [('headline', draft.get('headline') or '')] + [(f'bullet {i + 1}', b) for i, b in enumerate(bl or []) if isinstance(b, str)]:
        out += [f'{field}: {p}' for p in verify(txt, f)]
    for i, d in enumerate(dr or [], 1):
        if isinstance(d, dict) and isinstance(d.get('text'), str):
            out += [f'post {i}: {p}' for p in verify(d['text'], f, post=True)]
    return out


def llm(f, key, log):
    messages = [{'role': 'user', 'content': 'FACTS:\n' + json.dumps(f, ensure_ascii=False, indent=1)}]
    for attempt in (1, 2):
        text = _call(messages, key)
        try:
            draft = _json(text)
        except ValueError:
            draft = None
        bad = problems(draft, f)
        log.append({'attempt': attempt, 'problems': bad})
        if not bad:
            return draft
        messages += [{'role': 'assistant', 'content': text},
                     {'role': 'user', 'content': 'Fix these problems and reply again with ONLY the corrected JSON:\n- ' + '\n- '.join(bad[:12])}]
    return draft if isinstance(draft, dict) else None


def merge(draft, base, f):
    """Use each model field that verifies; otherwise the template's version of that field."""
    out = {'headline': base['headline'], 'bullets': base['bullets'], 'drafts': base['drafts'], 'flags': list(base['flags'])}
    if not draft:
        return out, 'template'
    used = 0
    if isinstance(draft.get('headline'), str) and draft['headline'].strip() and not verify(draft['headline'], f):
        out['headline'], used = draft['headline'].strip(), used + 1
    bl = [b for b in (draft.get('bullets') or []) if isinstance(b, str) and b.strip() and not verify(b, f)]
    if len(bl) >= 2:
        out['bullets'], used = bl[:5], used + 1
    dr = [{'story': str(d.get('story') or ''), 'film': d.get('film') if isinstance(d.get('film'), str) else None, 'text': d['text'].strip()}
          for d in (draft.get('drafts') or []) if isinstance(d, dict) and isinstance(d.get('text'), str) and not verify(d['text'], f, post=True)]
    if dr:
        out['drafts'], used = (dr + base['drafts'])[:3], used + 1
    extra = [x for x in (draft.get('flags') or []) if isinstance(x, str) and x.strip() and not verify(x, f)]
    out['flags'] += [x for x in extra if x not in out['flags']][:3]
    whole = used == 3 and len(bl) == len(draft.get('bullets') or []) and len(dr) == 3     # nothing had to be replaced
    return out, ('claude' if whole else 'claude+template' if used else 'template')


# ---------- output ----------
def studio_for(d, f):
    day = f['date']
    if d.get('story') == 'leaderboard':
        return {'type': 'board24', 'd': day, 't': '0400', 'top': '5'}
    if d.get('story') == 'interest':
        return {'type': 'interest', 'top': '5'}
    return None


def run(today, use_llm=True, force=False, out_path=OUT):
    if not force and os.path.exists(out_path):
        try:
            if json.load(open(out_path, encoding='utf-8')).get('date') == today.isoformat():
                print(f'briefing for {today} already exists')
                return None
        except ValueError:
            pass
    f = briefing_facts.facts(today)
    base = template(f)
    for txt in [base['headline'], *base['bullets'], *[d['text'] for d in base['drafts']]]:
        assert not verify(txt, f), (txt, verify(txt, f))            # the template must always pass its own check
    key, log, mode, body = os.environ.get('ANTHROPIC_API_KEY'), [], 'template', base
    if use_llm and key and f['films']:
        try:
            body, mode = merge(llm(f, key, log), base, f)
        except Exception as e:
            log.append({'error': str(e)[:200]})
            print(f'model writer failed ({e}); using the template', flush=True)
    for d in body['drafts']:
        d['studio'] = studio_for(d, f)
        d['length'] = xlen(d['text'])
    res = {'date': f['date'], 'generated_at': datetime.now(series.IST).isoformat(timespec='seconds'), 'mode': mode,
           'model': MODEL if mode.startswith('claude') else None, 'window': f['window'], 'day': f['day'],
           'headline': body['headline'], 'bullets': body['bullets'], 'drafts': body['drafts'], 'flags': body['flags'],
           'notes': f['notes'], 'attempts': log,
           'tables': {'films': [{k: x.get(k) for k in ('rank', 'name', 'yesterday', 'vs_day_before', 'vs_week_ago', 'busiest_hour', 'busiest_hour_at')}
                                for x in f['films'][:8]],
                      'interest': [{k: e.get(k) for k in ('name', 'interested', 'gained_24h', 'release', 'days_to_release', 'bookings')}
                                   for e in f['interest_top']]}}
    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(res, fh, indent=1, ensure_ascii=False)
    if out_path == OUT:
        os.makedirs(ARCHIVE, exist_ok=True)
        with open(os.path.join(ARCHIVE, f"{f['date']}.json"), 'w', encoding='utf-8') as fh:
            json.dump(res, fh, indent=1, ensure_ascii=False)
        for fn in os.listdir(ARCHIVE):
            m = re.fullmatch(r'(\d{4}-\d\d-\d\d)\.json', fn)
            if m and date.fromisoformat(m.group(1)) < today - timedelta(days=KEEP_DAYS):
                os.remove(os.path.join(ARCHIVE, fn))
    print(f"briefing {f['date']}: mode {mode}; {len(res['bullets'])} bullets, {len(res['drafts'])} draft posts, {len(res['flags'])} flags", flush=True)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--no-llm', action='store_true')
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args()
    day = date.fromisoformat(a.date) if a.date else datetime.now(series.IST).date()
    sys.exit(0 if run(day, not a.no_llm, a.force or bool(a.date), a.out) is not False else 1)
