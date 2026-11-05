#!/usr/bin/env python3
"""Intelligence agent: what is being said about a film right now, with sources.

For the films the forecast cares about (released in the last 2 weeks, or releasing in the next 5 days) Claude Haiku 5.5 searches the web
(Anthropic's web search tool) for critic reviews and ratings, audience ratings, word of mouth, reported screen / show counts and the
other releases it competes with. Nothing it says is trusted blindly:

  - every item must carry a URL that really appeared in the search results of that run (anything else is dropped),
  - ratings must be on a sensible scale, counts must be whole numbers,
  - the result is stored with its sources in intel.json and shown on the site, and forecast.py turns it into a SMALL, capped nudge.

    python intel.py                 # research the films that matter now (needs ANTHROPIC_API_KEY), write intel.json
    python intel.py --film "Scene"  # one film
Cost: web search is $10 per 1,000 searches (about 5 per film) plus a few thousand tokens at Haiku 5.5 prices: roughly 5-6 cents per film.
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

import series

IST = series.IST
ROOT = series.ROOT
OUT = os.path.join(ROOT, 'intel.json')
MODEL = os.environ.get('INTEL_MODEL', 'claude-haiku-5-5')
MAX_FILMS = 6
MAX_SEARCHES = 5
KEEP_HOURS = 30          # a film's intel is reused for this long unless it is in its opening days

SYSTEM = """You are a film-industry research assistant for an Indian box-office tracking account (Hyderabad; Telugu, Hindi, Tamil and English films).
Research ONE film with web search and report only what you find, with a source URL for every item.

Find, for this exact film (check the release date and language so you do not mix it up with another film of the same name):
 1. critics: up to 5 published reviews or critic ratings (outlet, rating and its scale if given, verdict positive / mixed / negative).
 2. audience: BookMyShow rating and vote count, IMDb rating and votes, Google users score, if shown.
 3. word_of_mouth: the general audience talk (good / mixed / bad / unknown) with one sentence of evidence.
 4. screens: any reported number of screens or shows (say where: Telangana, India, worldwide), or null.
 5. competition: films released within 2 days of this one in the same market, or already running strongly, that take away screens or audience.
If you cannot find something, use null or an empty list. Never guess a number. Never report collections or revenue.

Reply with ONLY a JSON object:
{"film": "<name>", "identified": true|false, "release_date": "YYYY-MM-DD or null",
 "critics": [{"outlet": "...", "rating": <number or null>, "scale": <number or null>, "verdict": "positive|mixed|negative", "url": "https://..."}],
 "audience": {"bms_rating": <number or null>, "bms_votes": <integer or null>, "imdb_rating": <number or null>, "imdb_votes": <integer or null>, "google_score_percent": <number or null>, "url": "https://... or null"},
 "word_of_mouth": {"verdict": "good|mixed|bad|unknown", "evidence": "<one sentence>", "url": "https://... or null"},
 "screens": {"count": <integer or null>, "where": "<text or null>", "url": "https://... or null"},
 "competition": [{"film": "...", "note": "<one short sentence>", "url": "https://..."}],
 "confidence": "high|medium|low"}"""


def search_agent(client, film, hint):
    """One web-search conversation for one film. Returns (parsed JSON or None, set of source URLs seen, usage dict)."""
    msgs = [{'role': 'user', 'content': f'Film: {film}\n{hint}\nToday is {datetime.now(IST):%A %-d %B %Y}.'}]
    tools = [{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': MAX_SEARCHES,
              'user_location': {'type': 'approximate', 'city': 'Hyderabad', 'region': 'Telangana', 'country': 'IN', 'timezone': 'Asia/Kolkata'}}]
    urls, tin, tout, searches = set(), 0, 0, 0
    text = ''
    for _ in range(4):                                   # pause_turn: send the paused answer back to continue
        r = client.messages.create(model=MODEL, max_tokens=6000, system=SYSTEM, tools=tools, messages=msgs, output_config={'effort': 'low'})
        tin, tout = tin + r.usage.input_tokens, tout + r.usage.output_tokens
        searches += getattr(getattr(r.usage, 'server_tool_use', None), 'web_search_requests', 0) or 0
        for b in r.content:
            if b.type == 'web_search_tool_result' and isinstance(b.content, list):
                urls.update(x.url for x in b.content if getattr(x, 'url', None))
        text = ''.join(b.text for b in r.content if b.type == 'text')
        if r.stop_reason == 'pause_turn':
            msgs.append({'role': 'assistant', 'content': r.content})
            continue
        if r.stop_reason == 'refusal':
            return None, urls, {'in': tin, 'out': tout, 'searches': searches}
        break
    m = re.search(r'\{.*\}', text, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    return data, urls, {'in': tin, 'out': tout, 'searches': searches}


def num(v, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def whole(v, lo=0, hi=10 ** 9):
    try:
        x = int(round(float(str(v).replace(',', '').replace('+', ''))))
    except (TypeError, ValueError):
        return None
    return x if lo <= x <= hi else None


def clean(data, urls):
    """Keep only what is well formed and backed by a URL that appeared in this run's search results."""
    if not isinstance(data, dict) or data.get('identified') is False:
        return None
    ok = lambda u: isinstance(u, str) and u in urls
    out = {'identified': True, 'release_date': data.get('release_date') if re.fullmatch(r'\d{4}-\d\d-\d\d', str(data.get('release_date'))) else None,
           'confidence': data.get('confidence') if data.get('confidence') in ('high', 'medium', 'low') else 'low'}
    crit = []
    for c in (data.get('critics') or [])[:6]:
        if not isinstance(c, dict) or not ok(c.get('url')) or c.get('verdict') not in ('positive', 'mixed', 'negative'):
            continue
        scale = num(c.get('scale'), 1, 100)
        rating = num(c.get('rating'), 0, scale or 100)
        crit.append({'outlet': str(c.get('outlet') or '')[:40], 'rating': rating, 'scale': scale, 'verdict': c['verdict'], 'url': c['url']})
    out['critics'] = crit
    a = data.get('audience') if isinstance(data.get('audience'), dict) else {}
    out['audience'] = {'bms_rating': num(a.get('bms_rating'), 0, 10), 'bms_votes': whole(a.get('bms_votes')), 'imdb_rating': num(a.get('imdb_rating'), 0, 10),
                       'imdb_votes': whole(a.get('imdb_votes')), 'google_score_percent': num(a.get('google_score_percent'), 0, 100), 'url': a.get('url') if ok(a.get('url')) else None}
    if not out['audience']['url']:                        # numbers without a source are dropped
        out['audience'] = {k: None for k in out['audience']}
    w = data.get('word_of_mouth') if isinstance(data.get('word_of_mouth'), dict) else {}
    verdict = w.get('verdict') if w.get('verdict') in ('good', 'mixed', 'bad') and ok(w.get('url')) else 'unknown'
    out['word_of_mouth'] = {'verdict': verdict, 'evidence': str(w.get('evidence') or '')[:220] if verdict != 'unknown' else '', 'url': w.get('url') if verdict != 'unknown' else None}
    s = data.get('screens') if isinstance(data.get('screens'), dict) else {}
    out['screens'] = {'count': whole(s.get('count'), 1, 20000) if ok(s.get('url')) else None, 'where': str(s.get('where') or '')[:60] if ok(s.get('url')) else None,
                      'url': s.get('url') if ok(s.get('url')) else None}
    out['competition'] = [{'film': str(c.get('film') or '')[:40], 'note': str(c.get('note') or '')[:140], 'url': c['url']}
                          for c in (data.get('competition') or [])[:4] if isinstance(c, dict) and ok(c.get('url')) and c.get('film')]
    return out


def targets(today=None):
    """Films worth researching now: released within 14 days or releasing within 5 days, and either in the forecast (they show an hourly
    counter) or awaited by 60K+ people. Small films are skipped: they cost as much to research and move nothing."""
    import forecast
    import interest
    today = today or datetime.now(IST).date()
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8'))
    focus = {n for n, _ in forecast.films()}
    awaited = {}
    for code, m in interest.series().items():
        if m['points']:
            awaited[(catalog.get(code, {}).get('name') or m['name'])] = m['points'][-1][1]
    seen, out = set(), []
    for code, c in catalog.items():
        try:
            rel = datetime.strptime(c.get('release') or '', '%b %d, %Y').date()
        except ValueError:
            continue
        d = (today - rel).days
        name = c.get('name')
        if not name or name in seen or not -5 <= d <= 14 or not (name in focus or awaited.get(name, 0) >= 60000):
            continue
        seen.add(name)
        out.append((abs(d), name, rel, c.get('languages') or []))
    out.sort()
    return [(n, r, l) for _, n, r, l in out][:MAX_FILMS]


def run(only=None):
    import anthropic
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        print('no ANTHROPIC_API_KEY; nothing to research')
        return None
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=300)
    old = {}
    try:
        old = json.load(open(OUT, encoding='utf-8')).get('films', {})
    except (OSError, ValueError):
        pass
    now = datetime.now(IST)
    res = {'generated': now.isoformat(timespec='minutes'), 'model': MODEL, 'films': {}, 'usage': {'in': 0, 'out': 0, 'searches': 0}}
    for name, rel, langs in ([(only, None, [])] if only else targets()):
        prev = old.get(name)
        opening = rel is not None and abs((now.date() - rel).days) <= 2
        if prev and not opening and not only:
            age = now - datetime.fromisoformat(prev.get('researched', '2000-01-01T00:00+05:30'))
            if age < timedelta(hours=KEEP_HOURS):
                res['films'][name] = prev
                continue
        hint = (f"Release date (BookMyShow): {rel:%Y-%m-%d}. Languages: {', '.join(langs) or 'unknown'}." if rel else '')
        try:
            data, urls, usage = search_agent(client, name, hint)
        except Exception as e:
            print(f'{name}: research failed ({str(e)[:120]})', flush=True)
            if prev:
                res['films'][name] = prev
            continue
        for k in usage:
            res['usage'][k] += usage[k]
        c = clean(data, urls)
        if c:
            c['researched'] = now.isoformat(timespec='minutes')
            res['films'][name] = c
        print(f"{name}: {'ok' if c else 'nothing reliable found'}; {usage['searches']} searches, {len(urls)} sources", flush=True)
    price = (0.10, 0.50)
    u = res['usage']
    u['cost_cents'] = round(u['searches'] * 1.0 + (u['in'] * price[0] + u['out'] * price[1]) / 1e4, 2)
    json.dump(res, open(OUT, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--film')
    a = ap.parse_args()
    r = run(a.film)
    sys.exit(0)
