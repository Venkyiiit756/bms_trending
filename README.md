# Box-office snapshot tracker (MovieMint · CineNation · Sacnilk)

Hourly, low-volume collector that reads **rendered public pages** with Playwright and appends normalised, deduplicated
readings to CSV. First movie: *Drishyam: The Conclusion* (Hindi, 2026). Add more in `movies.json`.

## Status: what is verified and what is not

I (Claude) could not reach any of the three sites from my sandbox and no browser was connected, so **no live extraction
was run**. The code is tested only against local synthetic pages (`tests/`, 21 tests passing).

| Site | Verified | Not verified (assumption) |
|---|---|---|
| **MovieMint** | `robots.txt`: `Allow: /`, `Disallow: /api/`, `/admin/`. Page HTML is a client-rendered app (shows only "Connecting to server 0%" before JS runs). Nav pages: `/tracked`, `/advance`, `/multiplex-report`. Meta says it shows day-wise gross, advance sales, state/language/format breakdowns. | Which URL actually delivers the numbers. If it is under `/api/`, that path is disallowed for automated clients, so there is **no robots-compliant API method**; the script then reports `no_data_extracted` and `discover` lists the blocked call. Table layout/headers unknown. Your 403 on plain HTTP is consistent with bot protection (a `cdn-cgi` path appears on the page) but I did not confirm that. |
| **CineNation** | Only that my page-reader tool refused it as automated access, plus your 502. | Everything else: URL scheme, data source, current availability. Script checks robots at runtime, retries 5xx with backoff, then logs `robots_unreachable`/`unavailable`. |
| **Sacnilk** | `robots.txt` does not disallow `/livetracker/`, `/quicknews/`, `/news/`, `/movie/`; it disallows `/api/`; `Crawl-delay: 1`. These Drishyam URLs exist as links on the homepage: `/livetracker/drishyam_3_hindi_2026_Advance-Booking-Live`, `/news/drishyam_3_hindi_2026_Box_Office_Collection_Day_Wise_Worldwide`, `/quicknews/drishyam_3_hindi_2026_Box_Office_Collection_Day_{N}`, `/movie/drishyam_3_hindi_2026`. The live-tracker page text carries an "Updated … IST" header but **no tables** in the server-rendered text I could read. | Whether the tables render client-side or sit behind Premium (your report; I could not confirm). Sacnilk calls the film "Drishyam 3: The Conclusion"; mapping it to MovieMint's "Drishyam: The Conclusion" is assumed from your request. |

## Extraction method (per site)

All three use the same path: robots check → open page in Chromium → wait until the text stops changing → read every
`<table>`/ARIA grid plus visible text → recognise columns by header wording → one CSV row per cell (long format).

- **Unrecognised columns are kept** as `unmapped:<header>` with the raw text, so nothing visible is dropped.
- **Robots:** evaluated with longest-match rules (Python's stdlib parser gets these files wrong because they start with `Allow: /`). Disallowed pages are not opened; disallowed *first-party sub-requests* (e.g. `/api/…`) made by a page are aborted and listed, never replayed. Third-party hosts are not robots-checked (URLs are recorded in `discover` output so you can review them).
- **No bypassing:** a challenge page (`bot_challenge`), login redirect (`auth_required`), HTTP 401/403/429 (`blocked_http`) or unreachable robots stops that site for the run. Premium-looking pages with no data are logged `premium_gated_suspected`. The script never logs in or solves anything. If you have a licence/permission for Premium data or a MovieMint data feed, that needs your authorised credentials or the owner's permission, and would be a separate, explicit addition.
- **Volume:** ≥5 s (+ jitter) between page loads per host, images/fonts/media blocked, ~4 page loads per movie per hour, retries only for 5xx/timeouts (3 tries, backoff).

## Data model (`data/box_office_long.csv`, one row per reading)

`collected_at_ist, source, data_type, movie_key, movie_title, language, show_date, show_date_raw, metric, raw_value, value_num, unit, scope, blocked_seats, site_updated_raw, site_updated_ist, source_url, extraction, notes`

- `data_type` keeps **`advance_booking`**, **`tracked_completed_shows`**, **`estimated_boxoffice`** (and `unclassified`) apart. It comes from the section heading wording, else the page default in `movies.json`. Never sum or compare across `source` or `data_type`.
- `metric`: `gross_inr`, `net_inr`, `tickets`, `shows`, `occupancy_pct`, `cum_gross_inr`, `cum_tickets`, `*_excl_blocked`, `coverage_text`, `unmapped:*`. "Tickets"/"footfalls" both map to `tickets`; the original header word is in `scope`/`raw_value` context, and `unmapped` keeps anything else.
- `raw_value` is exactly what the page showed; `value_num` is the number after K/L/Cr/M normalisation (`₹ - Cr` → empty, kept as a "not available yet" reading). `unit` shows the suffix seen (`header_unit` = unit taken from the column header).
- `blocked_seats`: `excluding_blocked` / `including_blocked` / `unspecified` (from header or section wording).
- `scope`: the table heading or row label (geography, language, format). `coverage_text` rows capture any page lines about cities/chains/pan-India.
- `site_updated_raw` always kept; `site_updated_ist` only when the text has a date, time and "IST". Relative text like "3 min ago" is not guessed.
- Dedup: a row is written only if its `raw_value` differs from the last stored reading for the same source/type/movie/date/metric/scope/blocked flag/URL path. `data/status_log.csv` records every page attempt (status, HTTP code, rows found/new, detail).

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium            # add --with-deps on bare Linux/WSL
python boxoffice.py discover --site sacnilk      # step 1: see what each page really loads (nothing is replayed)
python boxoffice.py discover --site moviemint
python boxoffice.py run                          # one collection pass -> data/*.csv
python boxoffice.py run --sites sacnilk --movie drishyam-the-conclusion --save-raw
pip install -r requirements-dev.txt && pytest -q tests      # local-fixture tests (set BOT_CHROME_PATH / BOT_NO_SANDBOX if needed)
```

**Do step 1 first.** `data/discovery/*.json` shows the tables found, JSON/XHR requests (including any blocked by robots)
and the first 3,000 characters of visible text. If a page yields `no_data_extracted`, the raw text/HTML dump is saved in
`data/raw/`; send me that (or the discovery JSON) and I will tune the header map for the real layout.

## Hourly scheduling

- **Windows + WSL (recommended):** install the project inside WSL, then run `schedule_windows.ps1` in PowerShell (untested here). Runs only while the PC is on/awake (`-StartWhenAvailable` catches up after sleep). WSL cron does not start by itself, which is why Task Scheduler is used.
- **GitHub Actions:** `.github/workflows/hourly.yml` (hourly at :07 UTC, commits the two CSVs). Limits: runs on shared datacentre IPs, so Cloudflare-style protection on MovieMint or any site may block it, and per the rules above the script then just logs and skips; scheduled runs can be delayed or skipped under load, and GitHub disables scheduled workflows in public repos after ~60 days without repo activity (from my knowledge, not checked today). Use a **private** repo so you are not republishing the sites' figures.

## Files

`boxoffice.py` CLI/browser/storage · `extract.py` table + text extraction, classification · `normalize.py` numbers, dates, robots · `movies.json` movie/site URLs · `tests/` · `data/observed_seed_NOT_scraper_output.csv` (hand-read headline values, see below).
