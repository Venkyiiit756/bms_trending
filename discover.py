#!/usr/bin/env python3
"""List the movies currently on BookMyShow's Hyderabad page -> hyderabad_movies.json.

First tries the plain HTML of the explore page (cheap). If that holds no movie
links (page rendered by JavaScript), falls back to a headless browser that
scrolls until the list stops growing. Entries are kept once seen (last_seen is
refreshed), so a movie that drops off the page is still tracked until pruned.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hyderabad_movies.json')
URL = 'https://in.bookmyshow.com/explore/movies-hyderabad'
LINK = re.compile(r'/movies/hyderabad/([a-z0-9-]+)/(ET\d{8})')
MIN_EXPECTED = 5


def from_html():
    from curl_cffi import requests
    r = requests.get(URL, impersonate='chrome', timeout=40)
    print(f'html fetch: HTTP {r.status_code}, {len(r.text)} bytes', flush=True)
    if r.status_code != 200:
        return {}
    return {code: slug for slug, code in LINK.findall(r.text)}


def from_browser():
    from playwright.sync_api import sync_playwright
    found = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(5000)
        stable = 0
        for _ in range(40):                      # finite scroll, stop when stable
            hrefs = page.eval_on_selector_all(
                "main a[href*='/movies/']", 'els => els.map(e => e.href)')
            before = len(found)
            for h in hrefs:
                m = LINK.search(h)
                if m:
                    found[m.group(2)] = m.group(1)
            stable = stable + 1 if len(found) == before else 0
            if stable >= 3:
                break
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(1500)
        print(f'browser title: {page.title()!r}', flush=True)
        browser.close()
    return found


def main():
    found, method = {}, 'html'
    try:
        found = from_html()
    except Exception as e:
        print(f'html discovery failed: {e}', flush=True)
    if len(found) < MIN_EXPECTED:
        method = 'browser'
        try:
            found = from_browser()
        except Exception as e:
            print(f'browser discovery failed: {e}', flush=True)
    print(f'{method}: {len(found)} movies', flush=True)
    if len(found) < MIN_EXPECTED:
        sys.exit('Discovery found too few movies; leaving the list unchanged.')

    now = datetime.now(IST).strftime('%Y-%m-%d')
    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as f:
            old = {m['eventcode']: m for m in json.load(f)}
    new = [c for c in found if c not in old]
    for code, slug in found.items():
        entry = old.get(code, {'eventcode': code, 'first_seen': now})
        entry.update(page_slug=slug, last_seen=now)
        old[code] = entry
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(sorted(old.values(), key=lambda m: m['eventcode']), f, indent=2)
    print(f'{len(old)} movies in list; new today: {len(new)}', flush=True)
    for code in found:
        print(f'  {code}  {found[code]}')


if __name__ == '__main__':
    main()
