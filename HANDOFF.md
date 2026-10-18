# BMS trend tracker: handoff notes

Read this first when picking the project up in a new session. Written 2026-10-10 (IST).

## What this is
A tracker that logs the "Trending" line BookMyShow shows for **Drishyam: The Conclusion**
(event `ET00477911`, region HYD), for example "13.38K tickets booked in last 1 hour".
It runs on GitHub Actions and commits to this repo (public, so Actions minutes are free).
Nothing needs to run locally; the data accumulates on `main`.

## Files
| File | Purpose |
|---|---|
| `tracker.py` | Polls BookMyShow's app API and writes the CSVs. Runs inside the workflow. |
| `.github/workflows/run_tracker.yml` | Hourly-chained job: runs `tracker.py --minutes 60 --commit`, then a last step dispatches the next run. An hourly cron (`:07`) is only a backup. |
| `bms_booking_log.csv` | The main log (see below). |
| `bms_api_headers.csv` | One row per poll with cache headers (`cf-cache-status`, `age`...). Diagnostic, about 120 KB/day. Set `LOG_HEADERS=0` to switch off. |
| `window_sales.py` | Estimates tickets booked between two IST times. |

The bot commits as `github-actions[bot]` ("Update booking logs [skip ci]") every few minutes.
**Always `git fetch` and read `origin/main:bms_booking_log.csv`**, because a local checkout goes stale quickly.
`window_sales.py` reads `bms_booking_log.csv` from the current directory unless you pass `--csv <path>`.

## The log: `bms_booking_log.csv`
Columns: `timestamp, movie_name, metric_type, metric_value, raw_text, prev_poll, kind, fresh_at`
- Times are **IST with `+05:30`**. The very first rows have no `kind` or `fresh_at`.
- **`metric_type`**
  - `1_hour` is a *rolling* "tickets booked in the previous hour" figure. It is not cumulative, and it can go down.
  - `24_hours` is the rolling 24-hour total (about 240K-256K). BookMyShow shows it **instead of** the hourly figure when hourly sales are very low (roughly 02:30-07:30). Don't mix the two.
  - `error` marks a failed request. `metric_value` is the HTTP status, for example 429.
- **`metric_value`** is text such as `7.49K`. K figures have two decimals with trailing zeros dropped (`14K` means 14.00K, good to about 10 tickets).
- **`kind`** is `change`, `heartbeat`, `error` or `start`. A row is written **only when the value changes**. A `heartbeat` row is added after 15 minutes without a change.
- **`fresh_at`** is when BookMyShow's server actually produced the figure. Prefer it over `timestamp` for timing. `prev_poll` is older and mostly superseded.
- Data starts 2026-10-07 10:08 IST. Earlier test rows (Oct 6-7 night, UTC) were cleared at the owner's request; they are in git history.

## How the data behaves
- Cloudflare **caches the API response for 600 s** (`Age` counts up to about 599). The figure can change at most once per ~10 min, so a new value arrives about every 10.1 minutes.
- The tracker asks once at each expected expiry (not constantly), so there are about 6-7 requests an hour.
- The Android app can **lead the tracker by a few minutes**, because each Cloudflare location has its own cache clock.
- At night, stale copies are sometimes served (ages of 1,000 s or more), which produces occasional flip-flop rows (a value, an older value, the value again within seconds). Treat them as noise.
- Occasional HTTP **429** rate-limit errors, mostly overnight. Back-off is 120/240/480/900 s, and `Retry-After` has been seen at 3,600 s. When the wait exceeds the run's remaining window, the run ends and the next run polls immediately from a different runner.
- Known gaps: 2026-10-07 14:19-14:39 (a stall) and 16:52-17:09 (a 429 block). Those readings are lost.

## `window_sales.py`
`python window_sales.py 10:00 12:00` or `python window_sales.py "2026-10-08 10:00" "2026-10-08 12:00" --csv <fresh copy>`
- Times are IST. It sums whole-hour readings (each reading is the hour before it), interpolating between refreshes, and prints an estimate with a low-high range.
- It refuses end times in the future and times before the data starts. Error rows and `24_hours` rows are ignored.
- The docstring says "~15 minutes"; the real refresh is about 10 minutes.

## Reference numbers (tickets in the previous hour, K)
| Time (IST) | Thu 8 Oct | Fri 9 Oct | Sat 10 Oct |
|---|---|---|---|
| 10:00 | 6.51 | 6.01 | 14.95 |
| 12:00 | 11.08 | 9.22 | 24.62 |
| 13:00 | 12.44 | 10.06 | 26.54 |

Whole-day totals (from the 24-hour figure each early morning): Oct 6 281.6K, Oct 7 240.2K, Oct 8 237.8K, Oct 9 255.6K.
Wednesday Oct 7 data only exists from 10:08. Thursday's hourly figure starts at 07:37.

## Decisions made (so they are not re-litigated)
- **Rate-limit hardening is deliberately not done.** It would replace the 60 s fallback poll (a likely 429 trigger) with a 5-minute poll, persist the back-off wait across runs, and ignore out-of-order stale copies. The tracker works well as is. Revisit if there are several `error` rows in an hour, or a gap over 20 minutes in daytime data.
- **No clean-export file was built.** The idea: one tidy CSV with numeric `tickets_1h` / `tickets_24h`, with heartbeats, errors and stale copies removed. Build it on request.
- Not bypassing Cloudflare's cache. A faster poll can't give newer data and risks blocks.

## Working notes
- A running job's log can't be read until it finishes. To diagnose a stall, cancel the run (the last step starts a replacement), then read the log.
- A cloud session may push only to its own branch; merge via PR (squash). Cancel or start runs with the Actions tools.
- Preferences of the owner: IST times, numbers in **K**, short answers. Tables should be compact: a bordered grid, data only, no titles, footnotes or delta columns (Thu/Fri/Sat style). Images are made with matplotlib (`pip install matplotlib`). Values at half-hour marks are interpolated between readings, and only when two readings under 25 minutes apart bracket the time.

## Multiple movies (added 2026-10-15)
- Movies live in `movies.json` (key, name, eventcode, region, page_slug, csv names). The workflow starts one `tracker.py --movie <key>` process per entry, staggered 20 s.
- Drishyam keeps `bms_booking_log.csv`; each other movie has its own `csv` / `headers_csv` pair. `window_sales.py --csv <file>` works on any of them.
- **Jailer 2** (`ET00429213`, region MUMBAI, page `/movies/mumbai/rajini-the-jailer-2/`) logs to `bms_booking_log_jailer2.csv` and `bms_api_headers_jailer2.csv`. The region code `MUMBAI` is a guess; if the log shows `error` rows or no Trending widget, check it.
- Jailer 2 has several separate BookMyShow events (found in the ChatGPT handoff zip): `ET00429213` "Rajini: The Jailer 2" (key `jailer2`, Mumbai), `ET00518674` "Jailer 2" (key `jailer2_hyd`, Hyderabad main listing) and `ET00518675` (key `jailer2_telugu`, slug `jailer-2-telugu`, seen in the footer). Check language/version before adding them together. The zip also lists 28 Hyderabad movies with event codes; add more to `movies.json` only on request.
- The Mumbai Jailer 2 response came back uncached (`no-store`, MISS), unlike Drishyam's.

## All Hyderabad movies (added 2026-10-15)
- `movies.json` now holds 29 events: the four above plus 25 from the ChatGPT zip's Hyderabad list (28 movies as of 2026-10-15). The new ones log to `data/<eventcode>.csv` (cache headers in `data/<eventcode>_headers.csv`).
- `tracker.py` is now ONE process for all movies (`--movie KEY` limits it): the four priority movies are requested at each cache expiry (about every 10 min); the 25 zip movies have `"every": 3600` in `movies.json`, so they are requested once an hour (spread across the hour), enough for hourly sales. Requests are at least 15 s apart (`MIN_GAP`). An access failure (429/403) backs off **all** movies together. A movie whose response has no counter is retried hourly and logged as an `error` row with status 200.
- **Daily discovery does not work**: the explore page is blocked (403 / Cloudflare challenge) for GitHub runners. `discover.py` and `discover_movies.yml` exist but the schedule is off. To refresh the list, run `discover.console.js` in a normal browser on the Hyderabad page and add new events to `movies.json` (key = lowercase code, csv `data/<code>.csv`).
- First attempt polled all 29 movies at every cache expiry (about 175 requests an hour): the runner got HTTP 429 with Retry-After 3600 after only about 12 requests in 3 minutes, three runs in a row. Hence the hourly interval for the extras (about 50 requests an hour in total). If 429s still appear, lower the request count further (longer `every`, or fewer movies).

- 2026-10-15: stopped tracking `jailer2` (Mumbai, the Hindi version, ~4K/h) and `jailer2_telugu` (identical figures to `jailer2_hyd`). Their CSVs stay in the repo. Jailer 2 is now tracked only as `jailer2_hyd` (ET00518674, Hyderabad, the common Telugu version).
- 2026-10-15 (later): `jailer2` (Mumbai, `ET00429213`, the Hindi version) is tracked again at the owner's request. Jailer 2 is now `jailer2_hyd` (Telugu/common) plus `jailer2` (Hindi). `jailer2_telugu` stays dropped (duplicate).
- 2026-10-15: Ranabaali (`ET00483565`) moved to the 10-minute cadence (no `every`) at the owner's request.

## Hourly snapshot images (added 2026-10-18)
- `snapshot.py` + `.github/workflows/hourly_snapshot.yml` (cron `5 * * * *`, also manual dispatch) write `snapshots/<date>/<date>_<HHMM>_last-1-hour.png` (+ `_last-24-hours.png`, `.csv`) and `snapshots/latest-last-1-hour.png`. See `snapshots/README.md`. Each run also fills missing hours of the last 6.
- Names: `NAMES` in `snapshot.py` (Jailer 2 Telugu/Hindi). Movies come from `movies.json`.

## Website (added 2026-10-18)
- `site_src/index.html` + `build_site.py` build `site/` (hour picker, images, table, CSV downloads, per-movie history). `site/` is git-ignored; the hourly snapshot workflow builds it and pushes it to a SEPARATE public repo (`SITE_REPO`, default `Venkyiiit756/hyd-movie-trends`) using the `SITE_TOKEN` secret (fine-grained token with Contents write on that repo). That repo serves GitHub Pages, so the site URL does not mention this repo. No login: the repos are public.
- Without `SITE_TOKEN` the publish step just skips.

- 2026-10-18: snapshots now every 30 min (cron `5,35 * * * *`, files `..._HH00` / `..._HH30`). Movies currently showing an hourly figure (Premada Oorali, Prahaar, Nayyi Navelli, Street Fighter, Garividi Lakshmi) have `every: 1800`; the other extras stay hourly. Figures stay rolling-1-hour counts, not 30-minute sales.

## Automatic movie discovery and tiers (added 2026-10-18)
- The explore page is blocked for runners, but each synopsis response has a widget (`widgets/*/listData`) with ~10 other movies (event codes + links). `discover.py` crawls those lists from the tracked movies (<= 80 requests, 8 s apart, stops on 403/429) and writes `catalog.json`.
- `update_movies.py` adds new movies to `movies.json` (hourly at first, `added` date) and sets tiers from the collected data: >= 8K/h and top 4 -> every cache expiry (~10 min); >= 3K/h -> `every: 1800`; has a figure -> 3600; no counter for 3 days -> `every: 21600`; not listed for 7 days and no counter -> `paused`. Entries with `"manual": true` (Drishyam, Jailer 2 x2, Ranabaali) are never changed. Request budget 80/h. Changes are logged in `MOVIE_CHANGES.md`.
- `.github/workflows/discover_movies.yml` runs both daily at 06:00 IST (`30 0 * * *`) and can be dispatched by hand. `tracker.py` skips `paused` movies and polls `every > 3600` movies only when their last logged row is older than the interval.
- Open question: the "other movies" list is BookMyShow's own suggestion, so it may miss movies or include ones without shows today; no counter -> they just drop to the slow tier.

- 2026-10-18: scheduled (cron) runs of the snapshot workflow never fired, so `tracker.py` now requests a snapshot itself at every :05/:35 IST (`gh workflow run hourly_snapshot.yml`, needs `SNAPSHOT_DISPATCH=1` and `GH_TOKEN`, set in `run_tracker.yml`). The cron stays as a backup; `snapshot.py` backfills any missed half-hour.
