"""Turn rendered page content (tables + visible text) into long-format records.

Nothing here knows a site's CSS classes: columns are recognised by header wording, and every cell that is NOT
recognised is still kept as an `unmapped:<header>` record with its raw text, so no visible value is silently lost.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from normalize import parse_amount, parse_date, parse_percent, parse_site_updated, unit_from_text

DATA_TYPES = ("advance_booking", "tracked_completed_shows", "estimated_boxoffice", "unclassified")

# JS run inside the page: every <table> (and ARIA table/grid) with its nearest preceding heading.
TABLES_JS = r"""
() => {
  const heads = [...document.querySelectorAll('h1,h2,h3,h4,h5,[role=heading]')];
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const nodes = [...document.querySelectorAll('table,[role=table],[role=grid]')];
  for (const t of nodes) {
    let best = null;
    for (const h of heads) if (h.compareDocumentPosition(t) & Node.DOCUMENT_POSITION_FOLLOWING) best = h;
    let rows;
    if (t.tagName === 'TABLE') rows = [...t.querySelectorAll('tr')].map(tr => [...tr.children].map(c => clean(c.innerText)));
    else rows = [...t.querySelectorAll('[role=row]')].map(r => [...r.querySelectorAll('[role=columnheader],[role=cell],[role=gridcell],[role=rowheader]')].map(c => clean(c.innerText)));
    out.push({heading: best ? clean(best.innerText) : '', caption: clean((t.querySelector('caption') || {}).innerText), rows: rows.filter(r => r.length)});
  }
  return out;
}
"""

# (metric, header regex) - first match wins, so order matters.
HEADER_MAP: list[tuple[str, re.Pattern]] = [(m, re.compile(p, re.I)) for m, p in [
    ("show_date", r"^(show\s*)?(date|day)\b"),
    ("cum_tickets_excl_blocked", r"(cumul|cume|total|overall).*(ticket|footfall|admit|sold).*(without|excl\w*)\W*block|(without|excl\w*)\W*block.*(cumul|cume|total).*(ticket|footfall)"),
    ("cum_gross_excl_blocked_inr", r"(cumul|cume|total|overall).*(gross|collection).*(without|excl\w*)\W*block|(without|excl\w*)\W*block.*(cumul|cume|total).*(gross|collection)"),
    ("tickets_excl_blocked", r"(ticket|footfall|admit|sold).*(without|excl\w*)\W*block|(without|excl\w*)\W*block.*(ticket|footfall|sold)"),
    ("gross_excl_blocked_inr", r"(gross|collection|revenue).*(without|excl\w*)\W*block|(without|excl\w*)\W*block.*(gross|collection|revenue)"),
    ("cum_tickets", r"(cumul|cume|total|overall).*(ticket|footfall|admit|sold)"),
    ("cum_gross_inr", r"(cumul|cume|total|overall).*(gross|collection|revenue)|(gross|collection).*(cumul|cume)"),
    ("net_inr", r"\bnet\b"),
    ("gross_inr", r"gross|collection|revenue|earn"),
    ("tickets", r"ticket|footfall|admit|\bsold\b"),
    ("shows", r"\bshows?\b"),
    ("occupancy_pct", r"occup|fill"),
]]
_PCT_METRICS = {"occupancy_pct"}
_COUNT_METRICS = {"tickets", "shows", "cum_tickets", "tickets_excl_blocked", "cum_tickets_excl_blocked"}


def metric_for_header(header: str) -> str | None:
    h = header.strip()
    for metric, rx in HEADER_MAP:
        if rx.search(h):
            return metric
    return None


def blocked_flag(*texts: str) -> str:
    t = " ".join(texts).lower()
    if re.search(r"(without|excl\w*|ex)\W*block", t):
        return "excluding_blocked"
    if re.search(r"(with|incl\w*)\W*block", t):
        return "including_blocked"
    return "unspecified"


def classify_section(text: str, default: str = "unclassified") -> str:
    t = text.lower()
    if re.search(r"advance|pre-?sale|presale", t):
        return "advance_booking"
    if re.search(r"footfall|tracked|completed show|show[- ]wise|live tracker", t):
        return "tracked_completed_shows"
    if re.search(r"india net|net collection|worldwide|box office collection|day wise|day-wise", t):
        return "estimated_boxoffice"
    return default


@dataclass
class Record:
    data_type: str
    show_date: str            # ISO date or ''
    show_date_raw: str
    metric: str
    raw_value: str
    value_num: float | None
    unit: str
    scope: str                # table heading / row label (geography, language, format ...)
    blocked_seats: str
    extraction: str           # 'table' | 'text_pairs'
    notes: str = ""


@dataclass
class PageResult:
    records: list[Record] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    updated_raw: str = ""
    updated_iso: str = ""


COVERAGE_RX = re.compile(r"(coverage|pan[- ]?india|national chains?|cities|states?|theatres?|cinemas?|screens?|multiplex)", re.I)
UPDATED_RX = re.compile(r"((?:last\s+)?updated[^\n|]{0,60}|as of[^\n|]{0,60}|data (?:as|till|upto|up to)[^\n|]{0,60})", re.I)


def extract_updated(text: str) -> tuple[str, str]:
    """Return (raw 'Updated ...' text with its neighbouring line for the date, parsed ISO or '')."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if UPDATED_RX.search(line):
            window = " ".join(lines[max(0, i - 1): i + 2])
            m = UPDATED_RX.search(line)
            raw = window if parse_site_updated(window) else m.group(1).strip()
            return raw, parse_site_updated(window)
    return "", ""


def extract_coverage(text: str, limit: int = 8) -> list[str]:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if 6 <= len(s) <= 160 and COVERAGE_RX.search(s) and re.search(r"\d|india|chain|cities|states", s, re.I):
            out.append(s)
        if len(out) >= limit:
            break
    return out


def extract_tables(tables: list[dict], default_type: str, default_year: int | None, date_hint: str = "") -> list[Record]:
    records: list[Record] = []
    for t in tables:
        rows = t.get("rows") or []
        if len(rows) < 2:
            continue
        header, body = rows[0], rows[1:]
        section = " ".join([t.get("heading", ""), t.get("caption", "")]).strip()
        dtype = classify_section(section, default_type)
        metrics = [metric_for_header(h) for h in header]
        date_col = metrics.index("show_date") if "show_date" in metrics else None
        for row in body:
            row_scope, show_raw, show_iso, note = section, "", date_hint, ""
            if date_col is not None and date_col < len(row):
                show_raw = row[date_col]
                show_iso, note = parse_date(show_raw, default_year)
            elif row:
                row_scope = (section + " | " + row[0]).strip(" |")   # e.g. state-wise tables: first column is the label
            has_digits = any(re.search(r"\d", c) for c in row[1:] if c)
            # keep placeholder rows ("₹ - Cr") for a recognised show date so "not yet available" is visible in the data
            if not has_digits and not (date_col is not None and show_iso):
                continue
            for idx, cell in enumerate(row):
                if cell == "" or idx == date_col or (date_col is None and idx == 0):
                    continue
                head = header[idx] if idx < len(header) else f"col{idx}"
                metric = metrics[idx] if idx < len(metrics) else None
                flag = blocked_flag(head, section)
                if metric in _PCT_METRICS:
                    val, unit = parse_percent(cell), "pct"
                elif metric:
                    val, unit = parse_amount(cell, unit_from_text(head))
                else:
                    metric, val, unit = f"unmapped:{head}", None, ""
                records.append(Record(dtype, show_iso, show_raw, metric, cell, val, unit, row_scope, flag, "table", note))
    return records


_LABEL_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z /().&-]{2,45}?)\s*[:\-–]\s*(.+?)\s*$")


def extract_text_pairs(text: str, default_type: str, date_hint: str = "") -> list[Record]:
    """Fallback for pages that are not <table>-based: 'Label: value' lines for known metric labels only."""
    records: list[Record] = []
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _LABEL_LINE.match(s)
        if not m:
            if len(s) < 80 and not re.search(r"\d{2,}", s):
                section = s
            continue
        label, value = m.groups()
        metric = metric_for_header(label)
        if not metric or metric == "show_date" or not re.search(r"\d", value):
            continue
        dtype = classify_section(section + " " + label, default_type)
        if metric in _PCT_METRICS:
            val, unit = parse_percent(value), "pct"
        else:
            val, unit = parse_amount(value, unit_from_text(label))
        records.append(Record(dtype, date_hint, "", metric, value, val, unit, section, blocked_flag(label, value),
                              "text_pairs", "low_confidence_text_match"))
    return records


def detect_page_status(url: str, title: str, text: str) -> str:
    head = f"{title}\n{text[:4000]}".lower()
    if re.search(r"just a moment|verify (that )?you are (a )?human|attention required|captcha|checking your browser", head):
        return "bot_challenge"
    if "/login" in url.lower().split("?")[0] or re.search(r"sign in to continue|log in to continue", head):
        return "auth_required"
    if re.search(r"connecting to server|loading\.\.\.", head) and len(text) < 1500:
        return "still_loading"
    return "ok"


PREMIUM_RX = re.compile(r"(premium (feature|content|members?|only)|unlock|subscribe to (view|see|unlock)|upgrade to (view|see|pro|premium)|pro (members?|subscribers?) only|locked)", re.I)


def looks_premium_gated(text: str) -> bool:
    return bool(PREMIUM_RX.search(text))


def analyse(url: str, title: str, text: str, tables: list[dict], default_type: str,
            default_year: int | None, date_hint: str = "") -> PageResult:
    res = PageResult()
    res.records = extract_tables(tables, default_type, default_year, date_hint)
    if not res.records:
        res.records = extract_text_pairs(text, default_type, date_hint)
    res.updated_raw, res.updated_iso = extract_updated(text)
    res.coverage = extract_coverage(text)
    return res
