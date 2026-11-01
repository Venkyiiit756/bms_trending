#!/usr/bin/env python3
"""Ask-the-data agent: answer a question about the tracked films from a GitHub issue.

You open an issue whose title starts with "?" (or has the label "ask"); ask.yml runs this, posts the answer as a
comment and closes the issue. Only the repository owner's issues are answered (see ask.yml), so strangers cannot
spend the API budget.

The model never sees raw files: Python builds a compact DATA digest (per film: the last 14 days of 4 AM-4 AM tickets,
week totals, interest and its change) and every number in the answer is checked against that digest. If the check
fails twice, no answer is posted, only a note saying so.

    python ask.py "Which film grew the most this week?"      # prints the answer
"""
import json
import os
import sys
from datetime import datetime, timedelta

import agentkit
import briefing
import briefing_facts as bf
import interest
import sales
import series

ROOT = series.ROOT
DAYS = 14


def digest(today=None):
    today = today or datetime.now(series.IST).date()
    t4 = datetime(today.year, today.month, today.day, 4, 0, tzinfo=series.IST)
    films, seen = [], set()
    for m in sales.build()['movies']:
        i = sales.slot_index(m, t4)
        got = [(k, d) for k in range(DAYS) for d in [sales.day24(m, i - 48 * k)] if d]
        if len(got) < 2:
            continue
        key = tuple(round(d['v']) for _, d in got[:3])
        if key in seen:                       # two listings of one film can carry the same counter
            continue
        seen.add(key)
        last7 = [d['v'] for k, d in got if k < 7]
        films.append((sum(d['v'] for _, d in got),
                      {'film': series.short_name(m['name'], 28), 'week_total': bf.fmt(sum(last7)) if len(last7) >= 5 else None,
                       'tickets_by_day': {bf.day_label(today - timedelta(days=k + 1)): ('' if d['exact'] else '≈') + bf.fmt(d['v']) for k, d in got}}))
    films = [f for _, f in sorted(films, key=lambda x: -x[0])]
    catalog = json.load(open(os.path.join(ROOT, 'catalog.json'), encoding='utf-8'))
    ints = {}
    for code, m in interest.series().items():
        if not m['points']:
            continue
        c = catalog.get(code, {})
        t, v = m['points'][-1]
        wk = [p for p in m['points'] if p[0] <= t - timedelta(days=6)]
        e = {'film': series.short_name(series.readable(c.get('name') or m['name'], c.get('page_slug')), 28), 'interested': bf.fmt(v) + '+',
             'gained_in_7_days': (('+' if v >= wk[-1][1] else '−') + bf.fmt(abs(v - wk[-1][1]))) if wk else None,
             'release': c.get('release') or m['release'] or None,
             'bookings': 'open' if c.get('book') else 'not open yet' if c.get('book') is False else None}
        key = c.get('group') or code
        if key not in ints or len(m['points']) > ints[key][0]:
            ints[key] = (len(m['points']), v, e)
    top = [e for _, _, e in sorted(ints.values(), key=lambda x: -x[1])][:20]
    return {'today': today.isoformat(), 'ticket_days': '4 AM to 4 AM IST; each date is the day that started at 4 AM',
            'films_tickets': films[:14], 'films_interest_before_release': top}


SYSTEM = """You answer questions about BookMyShow ticket counts for films in Hyderabad, using ONLY the DATA given.

Rules:
- Use numbers exactly as they appear in DATA ("217K", "1.07M", "38"). Never add, subtract, round, average or convert.
  If the question needs arithmetic the data does not already contain, give the numbers side by side and say you cannot total them.
- We count tickets booked, not revenue. If asked about collections or money, say the tracker does not measure it.
- If DATA does not answer the question, say so in one sentence. Do not guess or use outside knowledge.
- Keep it short: a one-sentence answer first, then at most 6 bullet points or one small markdown table. Call films by the names in DATA.
- Figures starting with ≈ are estimates; keep the ≈. No links, no promises of later reports."""


def answer(question, key):
    data = digest()
    msgs = [{'role': 'user', 'content': f'DATA:\n{json.dumps(data, ensure_ascii=False, indent=1)}\n\nQUESTION: {question[:1500]}'}]
    tin = tout = 0
    for _ in (1, 2):
        text, a, b = agentkit.ask_model(SYSTEM, msgs, key, 1500)
        tin, tout = tin + a, tout + b
        bad = briefing.verify(text, data)
        if not bad:
            return text.strip(), tin, tout
        msgs += [{'role': 'assistant', 'content': text},
                 {'role': 'user', 'content': 'These problems were found; reply again with the corrected answer only:\n- ' + '\n- '.join(bad[:10])}]
    return None, tin, tout


def main():
    q = ' '.join(sys.argv[1:]).strip() or (os.environ.get('ISSUE_TITLE', '').lstrip('? ').strip() + '\n' + os.environ.get('ISSUE_BODY', '')).strip()
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not q:
        print('No question given.')
        return
    if not key:
        print('The ANTHROPIC_API_KEY secret is not set, so I cannot answer questions yet.')
        return
    try:
        text, tin, tout = answer(q, key)
    except Exception as e:
        print(f'I could not reach the model ({str(e)[:120]}). Try again later.')
        return
    if text is None:
        print('I could not produce an answer whose numbers all match the data, so I am not posting one. Try rephrasing the question.')
        return
    print(f'{text}\n\n<sub>Answered from the tracker data (last {DAYS} days of ticket totals and the latest interest counts) by {agentkit.MODEL}; '
          f'about {agentkit.cost_cents(tin, tout)}¢. Every number was checked against the data.</sub>')


if __name__ == '__main__':
    main()
