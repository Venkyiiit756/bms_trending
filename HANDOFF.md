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
