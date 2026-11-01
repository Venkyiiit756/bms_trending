"""Shared pieces of the small Claude agents (weekly report, ask-the-data): one cheap model call and the
number check. Every figure the model may use is computed in Python first; anything it writes that is
not in those facts is rejected (briefing.verify), so a wrong number never reaches the site.
"""
import json
import os
import re

import briefing

MODEL = os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-5-5')
PRICES = briefing.PRICES


def ask_model(system, messages, key, max_tokens=2500):
    """(reply text, input tokens, output tokens) through the official SDK, effort low (cheapest)."""
    import anthropic
    r = anthropic.Anthropic(api_key=key, max_retries=3, timeout=120).messages.create(
        model=MODEL, max_tokens=max_tokens, system=system, messages=messages, output_config={'effort': 'low'})
    if r.stop_reason == 'refusal':
        raise RuntimeError('the model declined this request')
    return ''.join(b.text for b in r.content if b.type == 'text'), r.usage.input_tokens, r.usage.output_tokens


def cost_cents(tin, tout):
    p = PRICES.get(MODEL)
    return round((tin * p[0] + tout * p[1]) / 1e4, 3) if p else None


def json_in(text):
    m = re.search(r'\{.*\}', text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None
