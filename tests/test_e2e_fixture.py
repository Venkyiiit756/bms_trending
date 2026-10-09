"""End-to-end check of the browser path against a LOCAL synthetic page (not a real site)."""
import csv, sys, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
from playwright.sync_api import sync_playwright
import boxoffice as bo

SITE = Path(__file__).parent / "fixture_site"
HITS = []

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=str(SITE), **k)
    def do_GET(self):
        HITS.append(self.path); super().do_GET()
    def log_message(self, *a): pass

@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()

def test_pipeline(server, tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(bo, "MIN_DELAY_S", 0.2)
    monkeypatch.setenv("BOT_NO_SANDBOX", "1")
    chrome = os.environ.get("BOT_CHROME_PATH") or None
    movie = {"key": "fixture-movie", "title": "FIXTURE MOVIE", "language": "Hindi", "year": 2026}
    store = bo.Store(tmp_path)
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, args=["--no-sandbox"], **({"executable_path": chrome} if chrome else {}))
        ctx = b.new_context(locale="en-IN", timezone_id="Asia/Kolkata")
        sess = bo.Session(ctx, collect_network=True)
        st = bo.collect_page(sess, store, "fixture", movie, server + "/movie.html", "unclassified", "", "2026-10-09T11:00:00+05:30", True, tmp_path / "raw")
        assert st == "ok"
        st2 = bo.collect_page(sess, store, "fixture", movie, server + "/movie.html", "unclassified", "", "2026-10-09T12:00:00+05:30", False, tmp_path / "raw")
        assert st2 == "ok"
        assert bo.collect_page(sess, store, "fixture", movie, server + "/api/x", "unclassified", "", "t", False, tmp_path / "raw") == "robots_disallowed"
        assert bo.collect_page(sess, store, "fixture", movie, server + "/locked.html", "unclassified", "", "t", False, tmp_path / "raw") == "premium_gated_suspected"
        assert bo.collect_page(sess, store, "fixture", movie, server + "/challenge.html", "unclassified", "", "t", False, tmp_path / "raw") == "bot_challenge"
        b.close()
    assert not any(p.startswith("/api/") for p in HITS), "robots-disallowed path was requested"
    assert any("/api/secret" in u for u in sess.blocked_subrequests)
    rows = list(csv.DictReader((tmp_path / "box_office_long.csv").open(encoding="utf-8-sig")))
    first = [r for r in rows if r["collected_at_ist"].startswith("2026-10-09T11")]
    assert not [r for r in rows if r["collected_at_ist"].startswith("2026-10-09T12")], "second identical run must add nothing"
    g = next(r for r in first if r["data_type"] == "advance_booking" and r["show_date"] == "2026-10-02" and r["metric"] == "gross_inr")
    assert g["value_num"] == "1250000.0" and g["raw_value"] == "12.5" and g["blocked_seats"] == "excluding_blocked"
    assert g["site_updated_ist"] == "2026-10-03T12:00:00+05:30"
    assert any(r["metric"] == "coverage_text" and "Pan India" in r["raw_value"] for r in first)
    assert any(r["data_type"] == "tracked_completed_shows" and r["metric"] == "tickets" and r["value_num"] == "120000.0" for r in first)
    assert any(r["metric"] == "gross_inr" and r["raw_value"] == "₹ - Cr" and r["value_num"] == "" for r in first)
