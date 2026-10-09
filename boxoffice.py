#!/usr/bin/env python3
"""Polite, robots.txt-respecting box-office snapshot collector (Playwright, rendered-page extraction).

    python boxoffice.py run                       # one hourly collection pass (all movies / sites in movies.json)
    python boxoffice.py run --sites sacnilk       # subset
    python boxoffice.py discover --site moviemint # record what a page loads (URLs only; nothing is replayed)

Design rules (see README.md):
  * robots.txt is fetched and enforced (longest-match). Disallowed pages are not opened; disallowed first-party
    sub-requests (e.g. /api/) made by the page are aborted, not replayed.
  * No CAPTCHA / bot-challenge bypass, no logins, no paywall circumvention: those states are logged and the site is skipped.
  * Advance bookings, tracked completed-show collections and estimated box office are kept as separate data_type values.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from extract import TABLES_JS, analyse, detect_page_status, looks_premium_gated
from normalize import Robots, now_ist

HERE = Path(__file__).parent
DATA_FIELDS = [
    "collected_at_ist", "source", "data_type", "movie_key", "movie_title", "language",
    "show_date", "show_date_raw", "metric", "raw_value", "value_num", "unit", "scope", "blocked_seats",
    "site_updated_raw", "site_updated_ist", "source_url", "extraction", "notes",
]
STATUS_FIELDS = ["collected_at_ist", "source", "movie_key", "url", "status", "http_status",
                 "records_total", "records_new", "site_updated_raw", "detail"]
STOP_SITE = {"bot_challenge", "auth_required", "robots_unreachable", "robots_disallowed", "blocked_http"}
MIN_DELAY_S = 5.0


# ------------------------------------------------------------------ storage

class Store:
    def __init__(self, outdir: Path):
        self.dir = outdir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.data_csv = self.dir / "box_office_long.csv"
        self.status_csv = self.dir / "status_log.csv"
        self.last: dict[tuple, str] = {}
        if self.data_csv.exists():
            with self.data_csv.open(newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):          # later rows overwrite earlier -> last reading per key
                    self.last[self._key(row)] = row["raw_value"]

    @staticmethod
    def _key(r: dict) -> tuple:
        path = urlparse(r["source_url"]).path
        return (r["source"], r["data_type"], r["movie_key"], r["show_date"] or r["show_date_raw"],
                r["metric"], r["scope"], r["blocked_seats"], path)

    def _append(self, path: Path, fields: list[str], rows: list[dict]) -> None:
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if new:
                w.writeheader()
            w.writerows(rows)

    def add_records(self, rows: list[dict]) -> int:
        """Append only readings whose raw value differs from the last stored reading for the same key."""
        fresh = []
        for r in rows:
            k = self._key(r)
            if self.last.get(k) == r["raw_value"]:
                continue
            self.last[k] = r["raw_value"]
            fresh.append(r)
        if fresh:
            self._append(self.data_csv, DATA_FIELDS, fresh)
        return len(fresh)

    def log_status(self, row: dict) -> None:
        self._append(self.status_csv, STATUS_FIELDS, [{k: row.get(k, "") for k in STATUS_FIELDS}])
        print(f"[{row['status']}] {row['source']} {row['url']} records={row.get('records_total', '')} "
              f"new={row.get('records_new', '')} {row.get('detail', '')}".strip())


class RunLock:
    """Portable lock file so an overlapping hourly run exits instead of doubling the request rate."""
    def __init__(self, path: Path, stale_s: int = 1800):
        self.path, self.stale_s = path, stale_s

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.stale_s:
            self.path.unlink()
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise SystemExit("Another run appears to be in progress (lock file present); exiting.")
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


# ------------------------------------------------------------------ browser session

class Session:
    def __init__(self, ctx, collect_network: bool = False):
        self.ctx = ctx
        self.robots: dict[str, Robots] = {}
        self.last_hit: dict[str, float] = {}
        self.blocked_subrequests: list[str] = []
        self.network: list[dict] = []
        self.collect_network = collect_network

    def ensure_robots(self, url: str) -> tuple[Robots, int]:
        u = urlparse(url)
        origin = f"{u.scheme}://{u.netloc}"
        if origin in self.robots:
            return self.robots[origin], 0
        status, text = 0, None
        for attempt in range(3):
            try:
                resp = self.ctx.request.get(origin + "/robots.txt", timeout=15000)
                status, text = resp.status, (resp.text() if resp.status == 200 else None)
            except PWError:
                status = 0
            if status == 200 or (400 <= status < 500 and status not in (401, 403, 429)):
                break
            time.sleep(3 * (attempt + 1))
        if status == 200:
            rb = Robots(text)
        elif 400 <= status < 500 and status not in (401, 403, 429):
            rb = Robots(None)                       # RFC 9309: no robots file -> nothing disallowed
        else:
            rb = Robots(None, unreachable=True)     # 5xx / network / 401 / 403 / 429 -> be conservative, skip
        self.robots[origin] = rb
        return rb, status

    def polite_wait(self, host: str, rb: Robots) -> None:
        gap = max(MIN_DELAY_S, rb.crawl_delay or 0) + random.uniform(0, 3)
        wait = self.last_hit.get(host, 0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last_hit[host] = time.time()

    def new_page(self):
        page = self.ctx.new_page()

        def route(r):
            req = r.request
            if req.resource_type in ("image", "media", "font"):
                return r.abort()
            u = urlparse(req.url)
            rb = self.robots.get(f"{u.scheme}://{u.netloc}")
            if rb and not rb.allowed(u.path + (f"?{u.query}" if u.query else "")):
                self.blocked_subrequests.append(req.url)
                return r.abort()
            return r.continue_()

        page.route("**/*", route)
        if self.collect_network:
            page.on("response", lambda resp: self.network.append({
                "url": resp.url, "status": resp.status, "type": resp.request.resource_type,
                "content_type": resp.headers.get("content-type", ""), "method": resp.request.method}))
        return page


def settle(page, max_s: float = 25.0) -> tuple[str, str]:
    """Wait for a client-rendered page to stop changing; return (title, visible text)."""
    prev, stable, t0, text = -1, 0, time.time(), ""
    while time.time() - t0 < max_s:
        try:
            text = page.inner_text("body", timeout=5000)
        except (PWError, PWTimeout):
            text = ""
        loading = detect_page_status("", "", text) == "still_loading"
        stable = stable + 1 if (len(text) == prev and not loading and len(text) > 0) else 0
        if stable >= 2:
            break
        prev = len(text)
        time.sleep(1.5)
    return page.title(), text


def load_page(sess: Session, url: str):
    """Return (page|None, http_status, status, detail). Retries 5xx/timeouts; never retries 401/403/429."""
    rb, rstatus = sess.ensure_robots(url)
    if rb.unreachable:
        return None, rstatus, "robots_unreachable", f"robots.txt fetch returned {rstatus or 'no response'}; skipped"
    u = urlparse(url)
    if not rb.allowed(u.path + (f"?{u.query}" if u.query else "")):
        return None, 0, "robots_disallowed", "path disallowed by robots.txt"
    detail = ""
    for attempt in range(1, 4):
        sess.polite_wait(u.netloc, rb)
        page = sess.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            code = resp.status if resp else 0
            if code in (401, 403, 429):
                page.close()
                return None, code, "blocked_http", f"HTTP {code}; not retrying or working around it"
            if code >= 500:
                detail = f"HTTP {code} (attempt {attempt}/3)"
                page.close()
                time.sleep(5 * 3 ** (attempt - 1) + random.uniform(0, 2))
                continue
            return page, code, "loaded", ""
        except (PWTimeout, PWError) as e:
            detail = f"{type(e).__name__}: {str(e).splitlines()[0][:120]} (attempt {attempt}/3)"
            page.close()
            time.sleep(5 * 3 ** (attempt - 1) + random.uniform(0, 2))
    return None, 0, "unavailable", detail


# ------------------------------------------------------------------ one page -> records

def collect_page(sess: Session, store: Store, source: str, movie: dict, url: str, default_type: str,
                 date_hint: str, stamp: str, save_raw: bool, raw_dir: Path) -> str:
    base = {"collected_at_ist": stamp, "source": source, "movie_key": movie["key"], "url": url}
    page, code, status, detail = load_page(sess, url)
    if page is None:
        store.log_status({**base, "status": status, "http_status": code, "detail": detail})
        return status
    try:
        title, text = settle(page)
        pstatus = detect_page_status(page.url, title, text)
        if pstatus != "ok":
            store.log_status({**base, "status": pstatus, "http_status": code,
                              "detail": "stopping for this site; no bypass attempted"})
            return pstatus
        tables = page.evaluate(TABLES_JS)
        res = analyse(page.url, title, text, tables, default_type, movie.get("year"), date_hint)
        rows = []
        for r in res.records:
            d = asdict(r)
            rows.append({"collected_at_ist": stamp, "source": source, "movie_key": movie["key"],
                         "movie_title": movie["title"], "language": movie.get("language", ""),
                         "site_updated_raw": res.updated_raw, "site_updated_ist": res.updated_iso,
                         "source_url": page.url, **{k: ("" if d[k] is None else d[k]) for k in d}})
        for line in res.coverage:
            rows.append({"collected_at_ist": stamp, "source": source, "data_type": default_type,
                         "movie_key": movie["key"], "movie_title": movie["title"], "language": movie.get("language", ""),
                         "show_date": "", "show_date_raw": "", "metric": "coverage_text", "raw_value": line,
                         "value_num": "", "unit": "", "scope": "page", "blocked_seats": "unspecified",
                         "site_updated_raw": res.updated_raw, "site_updated_ist": res.updated_iso,
                         "source_url": page.url, "extraction": "text", "notes": ""})
        n_new = store.add_records(rows)
        st, det = "ok", f"proxied_blocked_subrequests={len(sess.blocked_subrequests)}" if sess.blocked_subrequests else ""
        if not res.records:
            st = "premium_gated_suspected" if looks_premium_gated(text) else "no_data_extracted"
            det = "page loaded but no recognisable data; raw dump saved. " + det
        if save_raw or not res.records:
            raw_dir.mkdir(parents=True, exist_ok=True)
            stem = raw_dir / f"{source}_{movie['key']}_{re.sub(r'[^0-9T]', '', stamp)[:15]}"
            Path(str(stem) + ".txt").write_text(f"URL: {page.url}\nTITLE: {title}\n\n{text}", encoding="utf-8")
            if not res.records:
                Path(str(stem) + ".html").write_text(page.content(), encoding="utf-8")
        store.log_status({**base, "status": st, "http_status": code, "records_total": len(rows),
                          "records_new": n_new, "site_updated_raw": res.updated_raw, "detail": det})
        return st
    finally:
        page.close()


def find_links(sess: Session, home: str, tokens: list[str], limit: int):
    """Open an index page and return up to `limit` same-site links whose text/href mention a movie token."""
    page, code, status, detail = load_page(sess, home)
    if page is None:
        return [], status, detail
    try:
        settle(page)
        anchors = page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => ({t: (a.innerText||'').trim(), h: a.href}))")
    finally:
        page.close()
    host = urlparse(home).netloc
    out = []
    for a in anchors:
        blob = (a["t"] + " " + a["h"]).lower()
        if urlparse(a["h"]).netloc == host and any(t.lower() in blob for t in tokens) and a["h"] not in out:
            out.append(a["h"])
        if len(out) >= limit:
            break
    return out, "ok", ""


# ------------------------------------------------------------------ commands

def launch(pw, args):
    kw = {"headless": not args.headed, "args": ["--no-sandbox"] if os.environ.get("BOT_NO_SANDBOX") else []}
    chrome = args.chrome or os.environ.get("BOT_CHROME_PATH")
    if chrome:
        kw["executable_path"] = chrome
    browser = pw.chromium.launch(**kw)
    ctx = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata", viewport={"width": 1366, "height": 900})
    return browser, ctx


def cmd_run(args) -> int:
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    movies = [m for m in cfg["movies"] if not args.movie or m["key"] in args.movie]
    sites = set(args.sites.split(",")) if args.sites else None
    out = Path(args.out)
    store = Store(out)
    stamp = now_ist().isoformat(timespec="seconds")
    today = now_ist().strftime("%Y%m%d")
    with RunLock(out / ".run.lock"), sync_playwright() as pw:
        browser, ctx = launch(pw, args)
        sess = Session(ctx)
        try:
            for movie in movies:
                for source, scfg in movie["sites"].items():
                    if sites and source not in sites:
                        continue
                    pages = list(scfg.get("pages", []))
                    if scfg.get("find_on"):
                        links, st, det = find_links(sess, scfg["find_on"], scfg["tokens"], scfg.get("max_links", 1))
                        if not links:
                            store.log_status({"collected_at_ist": stamp, "source": source, "movie_key": movie["key"],
                                              "url": scfg["find_on"], "status": st if st != "ok" else "movie_link_not_found",
                                              "detail": det})
                            continue
                        pages += [{"url": l, "default_type": scfg.get("default_type", "unclassified")} for l in links]
                    for pg in pages:
                        url = pg["url"].replace("{yyyymmdd}", today)
                        hint = today[:4] + "-" + today[4:6] + "-" + today[6:] if "{yyyymmdd}" in pg["url"] else ""
                        st = collect_page(sess, store, source, movie, url, pg.get("default_type", "unclassified"),
                                          hint, stamp, args.save_raw, out / "raw")
                        if st in STOP_SITE:
                            break
        finally:
            browser.close()
    return 0


def cmd_discover(args) -> int:
    """Open each configured page once, record request URLs/content-types (nothing is replayed), dump text + tables."""
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    movie = next(m for m in cfg["movies"] if not args.movie or m["key"] == args.movie)
    scfg = movie["sites"][args.site]
    today = now_ist().strftime("%Y%m%d")
    urls = [p["url"].replace("{yyyymmdd}", today) for p in scfg.get("pages", [])] or [scfg["find_on"]]
    out = Path(args.out) / "discovery"
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser, ctx = launch(pw, args)
        sess = Session(ctx, collect_network=True)
        try:
            report = []
            for url in urls:
                sess.network.clear(), sess.blocked_subrequests.clear()
                page, code, status, detail = load_page(sess, url)
                entry = {"url": url, "load_status": status, "http_status": code, "detail": detail}
                if page:
                    title, text = settle(page)
                    entry.update(title=title, page_status=detect_page_status(page.url, title, text),
                                 text_chars=len(text), tables=page.evaluate(TABLES_JS),
                                 data_like_requests=[n for n in sess.network if "json" in n["content_type"].lower()
                                                     or n["type"] in ("xhr", "fetch")],
                                 blocked_by_robots=list(sess.blocked_subrequests), visible_text_head=text[:3000])
                    page.close()
                report.append(entry)
            dest = out / f"{args.site}_{now_ist().strftime('%Y%m%dT%H%M%S')}.json"
            dest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            for e in report:
                print(f"{e['url']}\n  load={e['load_status']} page={e.get('page_status')} tables={len(e.get('tables', []))} "
                      f"data_requests={len(e.get('data_like_requests', []))} robots_blocked={len(e.get('blocked_by_robots', []))}")
            print(f"full report: {dest}")
        finally:
            browser.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "discover"):
        p = sub.add_parser(name)
        p.add_argument("--config", default=str(HERE / "movies.json"))
        p.add_argument("--out", default=str(HERE / "data"))
        p.add_argument("--chrome", help="path to a Chrome/Chromium binary (else Playwright's bundled one)")
        p.add_argument("--headed", action="store_true", help="show the browser window")
        if name == "run":
            p.add_argument("--movie", action="append", help="movie key (repeatable); default all")
            p.add_argument("--sites", help="comma list, e.g. moviemint,sacnilk")
            p.add_argument("--save-raw", action="store_true", help="always save visible-text dumps")
        else:
            p.add_argument("--site", required=True)
            p.add_argument("--movie")
    args = ap.parse_args()
    return {"run": cmd_run, "discover": cmd_discover}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
