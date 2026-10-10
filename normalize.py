"""Number/date normalisation and a small RFC 9309-style robots.txt matcher."""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# multiplier for K / L / Cr style suffixes (K=thousand, L=lakh, Cr=crore, M=million)
UNIT_MULT = {
    "k": 1e3, "thousand": 1e3,
    "l": 1e5, "lac": 1e5, "lacs": 1e5, "lakh": 1e5, "lakhs": 1e5,
    "cr": 1e7, "crore": 1e7, "crores": 1e7,
    "m": 1e6, "mn": 1e6, "million": 1e6,
}
_AMOUNT = re.compile(
    r"(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)\s*"
    r"(?P<unit>crores?|cr|lakhs?|lacs?|l|k|thousand|million|mn|m)?(?![A-Za-z])",
    re.I,
)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def now_ist() -> datetime:
    return datetime.now(IST)


def unit_from_text(text: str) -> float | None:
    """Detect a unit hint such as '(Cr)' or '₹ L' in a column header."""
    m = re.search(r"(?:₹|rs\.?|inr)?\s*[\(\[]?\s*(crores?|cr|lakhs?|lacs?|l|k)\s*[\)\]]?\s*$", text.strip(), re.I)
    if m:
        return UNIT_MULT[m.group(1).lower()]
    return None


def parse_amount(raw: str, default_mult: float | None = None) -> tuple[float | None, str]:
    """Return (numeric value, unit label) for strings like '₹1.25 Cr', '12.5L', '3.4K', '2,45,000'.

    Returns (None, '') when there is no number (e.g. '₹ - Cr'). The raw string is always kept by the caller.
    """
    if raw is None:
        return None, ""
    m = _AMOUNT.search(raw)
    if not m:
        return None, ""
    num = float(m.group("num").replace(",", ""))
    unit = (m.group("unit") or "").lower()
    if unit:
        return round(num * UNIT_MULT[unit], 2), unit
    if default_mult:
        return round(num * default_mult, 2), "header_unit"
    return num, ""


def parse_percent(raw: str) -> float | None:
    m = _PCT.search(raw or "")
    return float(m.group(1)) if m else None


_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_DATE_FORMS = [
    (re.compile(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS})[a-z]*,?\s+(\d{{4}})", re.I), "dmy"),
    (re.compile(rf"({_MONTHS})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", re.I), "mdy"),
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "iso"),
    (re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b"), "dmy_num"),
    (re.compile(r"\b(\d{4})(\d{2})(\d{2})\b"), "iso_compact"),
    (re.compile(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS})[a-z]*(?![a-z\d])", re.I), "dm"),  # no year
]


def parse_date(raw: str, default_year: int | None = None) -> tuple[str, str]:
    """Return (ISO date or '', note). note='year_from_config' when the year was not on the page."""
    for rx, kind in _DATE_FORMS:
        m = rx.search(raw or "")
        if not m:
            continue
        try:
            g = m.groups()
            if kind == "dmy":
                d, mo, y = int(g[0]), _month(g[1]), int(g[2])
            elif kind == "mdy":
                mo, d, y = _month(g[0]), int(g[1]), int(g[2])
            elif kind in ("iso", "iso_compact"):
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
            elif kind == "dmy_num":
                d, mo, y = int(g[0]), int(g[1]), int(g[2])
            else:  # dm
                if not default_year:
                    return "", "year_missing"
                d, mo, y = int(g[0]), _month(g[1]), default_year
                return datetime(y, mo, d).date().isoformat(), "year_from_config"
            return datetime(y, mo, d).date().isoformat(), ""
        except ValueError:
            continue
    return "", ""


def _month(s: str) -> int:
    return _MONTHS.split("|").index(s[:3].lower()) + 1


_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.I)


def parse_site_updated(text: str) -> str:
    """Best-effort ISO timestamp (IST) from text like 'Sat 3 Oct 2026 Updated 12:00 PM IST'.

    Only parsed when a date, a time and 'IST' are all present; otherwise returns '' (raw text is kept separately).
    """
    if not text or "IST" not in text.upper():
        return ""
    t = _TIME.search(text)
    d, _ = parse_date(text)
    if not (t and d):
        return ""
    hh, mm, ap = int(t.group(1)), int(t.group(2)), t.group(3).upper()
    hh = hh % 12 + (12 if ap == "PM" else 0)
    y, mo, da = map(int, d.split("-"))
    return datetime(y, mo, da, hh, mm, tzinfo=IST).isoformat()


# ---------------------------------------------------------------- robots.txt

class Robots:
    """Longest-match robots.txt evaluation for the generic '*' group (RFC 9309 precedence: longest rule wins, Allow wins ties).

    Python's urllib.robotparser applies rules in file order, so a leading 'Allow: /' hides every later Disallow --
    which is exactly how both MovieMint's and Sacnilk's files are written, hence this implementation.
    """

    def __init__(self, text: str | None, unreachable: bool = False):
        self.unreachable = unreachable
        self.rules: list[tuple[bool, re.Pattern, int]] = []
        self.crawl_delay: float | None = None
        if text:
            self._parse(text)

    def _parse(self, text: str) -> None:
        in_star = False
        prev_was_ua = False
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            field, value = (s.strip() for s in line.split(":", 1))
            field = field.lower()
            if field == "user-agent":
                if not prev_was_ua:
                    in_star = False
                in_star = in_star or value == "*"
                prev_was_ua = True
                continue
            prev_was_ua = False
            if not in_star:
                continue
            if field in ("allow", "disallow") and value:
                self.rules.append((field == "allow", self._rx(value), len(value)))
            elif field == "crawl-delay":
                try:
                    self.crawl_delay = float(value)
                except ValueError:
                    pass

    @staticmethod
    def _rx(pattern: str) -> re.Pattern:
        anchored = pattern.endswith("$")
        body = re.escape(pattern.rstrip("$")).replace(r"\*", ".*")
        return re.compile("^" + body + ("$" if anchored else ""))

    def allowed(self, path_and_query: str) -> bool:
        if self.unreachable:
            return False
        best_len, best_allow = -1, True
        for allow, rx, ln in self.rules:
            if rx.match(path_and_query) and (ln > best_len or (ln == best_len and allow)):
                best_len, best_allow = ln, allow
        return best_allow
