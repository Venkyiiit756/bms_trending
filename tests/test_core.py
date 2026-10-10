import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pytest
from normalize import Robots, parse_amount, parse_date, parse_percent, parse_site_updated, unit_from_text
from extract import blocked_flag, classify_section, metric_for_header, extract_tables, detect_page_status


@pytest.mark.parametrize("raw,val", [
    ("₹1.25 Cr", 12_500_000), ("12.5L", 1_250_000), ("3.4K", 3_400), ("2,45,000", 245_000),
    ("₹27.92 Cr (Without Block Seats)", 279_200_000), ("1.2 Lakh", 120_000), ("86.63 L", 8_663_000),
    ("7.40 Cr", 74_000_000), ("1,234", 1234), ("2.5M", 2_500_000)])
def test_amounts(raw, val):
    assert parse_amount(raw)[0] == pytest.approx(val)

@pytest.mark.parametrize("raw", ["₹ - Cr", "-", "", "N/A"])
def test_amount_missing(raw):
    assert parse_amount(raw)[0] is None

def test_header_unit():
    assert parse_amount("12.5", unit_from_text("Gross (₹ L)"))[0] == 1_250_000
    assert unit_from_text("Gross (₹ Cr)") == 1e7 and unit_from_text("Gross") is None

def test_misc_parsers():
    assert parse_percent("45.5%") == 45.5
    assert parse_date("Sat 3 Oct 2026") == ("2026-10-03", "")
    assert parse_date("October 9, 2026 at 12:06 AM IST") == ("2026-10-09", "")
    assert parse_date("2 Oct", 2026) == ("2026-10-02", "year_from_config")
    assert parse_date("20261009") == ("2026-10-09", "")
    assert parse_site_updated("Sat 3 Oct 2026 Updated 12:00 PM IST") == "2026-10-03T12:00:00+05:30"
    assert parse_site_updated("Updated 3 min ago") == ""        # relative -> raw only, never guessed

def test_robots_longest_match_not_file_order():
    rb = Robots("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /*?q=\nAllow: /assets/\nDisallow: /assets/private\n")
    assert rb.allowed("/movie/x?date=1") and not rb.allowed("/api/x") and not rb.allowed("/a?q=1")
    assert rb.allowed("/assets/a.js") and not rb.allowed("/assets/private/z")
    assert not Robots(None, unreachable=True).allowed("/")

def test_headers_and_flags():
    assert metric_for_header("Cumulative Gross") == "cum_gross_inr"
    assert metric_for_header("Gross (Without Block Seats)") == "gross_excl_blocked_inr"
    assert metric_for_header("Tickets Sold") == "tickets" and metric_for_header("Footfalls") == "tickets"
    assert metric_for_header("India Net") == "net_inr" and metric_for_header("Mood") is None
    assert blocked_flag("Gross", "Advance (Without Block Seats)") == "excluding_blocked"
    assert classify_section("Advance Booking") == "advance_booking"
    assert classify_section("Tracked Collections") == "tracked_completed_shows"
    assert classify_section("something") == "unclassified"

def test_status_detection():
    assert detect_page_status("https://x/", "Just a moment...", "") == "bot_challenge"
    assert detect_page_status("https://x/login?r=1", "Login", "hello") == "auth_required"

def test_table_extraction_keeps_unmapped_and_types_separate():
    t = [{"heading": "Advance Booking", "caption": "", "rows": [["Date", "Gross (₹ L)", "Mood"], ["2 Oct 2026", "12.5", "hi"]]},
         {"heading": "Tracked Collections", "caption": "", "rows": [["Date", "Gross"], ["2 Oct 2026", "₹4.5 Cr"]]}]
    recs = extract_tables(t, "unclassified", 2026)
    by = {(r.data_type, r.metric): r for r in recs}
    assert by[("advance_booking", "gross_inr")].value_num == 1_250_000
    assert by[("tracked_completed_shows", "gross_inr")].value_num == 45_000_000
    assert by[("advance_booking", "unmapped:Mood")].raw_value == "hi"
