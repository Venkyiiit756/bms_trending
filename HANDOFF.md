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
| `bms_api_headers*.csv` | (removed) Cache-header logs, off since `LOG_HEADERS=0`; set `LOG_HEADERS=1` to bring them back. Old copies are in git history. |

The bot commits as `github-actions[bot]` ("Update booking logs [skip ci]") every few minutes.
**Always `git fetch` and read `origin/main:bms_booking_log.csv`**, because a local checkout goes stale quickly.

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

## Verified facts and the X (Twitter) posting setup (added 2026-10-21)
- **Counter scope**: the same figure comes back with region headers HYD, MUMBAI and BANG (3 movies tested), so it is per listing, not per city. Do not call it "Hyderabad" in public posts. (It may still depend on the requester's location; not testable from GitHub's servers.)
- **Listings**: `ET00518674` "Jailer 2" (key `jailer2_hyd`, shown as "Jailer 2") is event group `EG00515691` = Tamil + Telugu + Kannada, all formats; `ET00518675` is the Telugu page of the same group (identical counter). `ET00429213` (key `jailer2`, shown as "Jailer 2 (Hindi)") is group `EG00427297`, Hindi only. Names, groups, languages and release dates come from `/api/movies/v1/synopsis/init/dynamic` (`meta.event`); `discover.py` now fetches them once per movie and `update_movies.py` tracks one listing per group.
- **Shared code**: `series.py` (clean readings: drops stale cached copies by `fresh_at`; display names; `HANDLE`), `cards.py` (image style).
- **Posts** (`posts.py`, `xpost.py`): every hour 08:00-23:00 IST (16/day) a leaderboard image (last hour, change vs same time yesterday) plus a rotating second image (08:00 yesterday's busiest hour; 12/15/18/21:00 four-day same-time table; 14/20/23:00 top movie's line today vs yesterday), caption and alt text. Logged in `posts_log.json` (30 days). Posted to X when the secrets `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` exist; otherwise shown as a kit on the website. Guard: figures at most 30 min old at the post time; never published more than 35 min late.
- **Health** (`health.py`): opens a GitHub issue "Data collection looks stalled" (emails the owner) when the main movies have had no reading for 45 min; closes it when data is back. Writes `health.json` for the site. Limitation: it runs from the snapshot workflow, which the tracker triggers; if the tracker chain itself dies, only the unreliable cron backup can run it.
- **Images** are no longer committed here; they go to `build/` and only the website repo keeps them (folders older than 45 days pruned). Tracker pushes at most every 10 minutes (`COMMIT_EVERY`) and no longer logs cache headers (`LOG_HEADERS=0`).

## Daily totals (sales.py, Totals tab)
- `sales.py` turns each movie's readings into estimated tickets per half hour (`data/sales.json` on the site):
  half the hourly figure at each half hour, short data gaps filled linearly, night gaps (only the 24-hour figure
  shows, ~2-7 AM) filled with a dip to zero x NIGHT_SHARE 0.3 (fitted), daytime slots scaled by a per-movie factor
  (~1.2) fitted on the last 7 nights. Validated leave-one-night-out on 45 nights: typical 0.7%, 90% within 3.7%.
  Films with < 3 nights and < 40% hourly readings get no estimates. `d24` = BookMyShow's exact 24-hour figures.
- Website **Totals** tab: pick a movie and drag From/To (30-minute steps); days are real 24 hours from "From".
  A day ending at (or within an hour of) a BookMyShow 24-hour figure uses that figure; otherwise it is an estimate (≈).
  "Days from 4 AM" aligns days to when BookMyShow shows its own figure.
- Studio types using it: `board24` (last 24 hours leaderboard), `days24` (tickets per day across days),
  `totals` / `totalsbar` (one movie, per day and total; link params `ra`/`rb` = range, empty = tracking start / latest).
- Night sweep (`tracker.py`, `SWEEP_AT` 03:45 IST): every movie with an `every` interval is asked once just
  before 4 AM, so every movie has BookMyShow's own 24-hour figure for each 4 AM-4 AM day (~25 extra requests a day).
  Before this, hourly-tier movies (e.g. Ramayana) often had no reading between ~2 and 8 AM.
- Discovery runs three times a day: `tracker.py` dispatches `discover_movies.yml` at DISCOVER_AT (06:40, 13:40,
  20:40 IST); the daily cron stays as a backup (GitHub ran it 4-5 h late every day).
- `probe.py` / `probe.yml` (manual): prints the booking-related fields BookMyShow returns for given event codes.
- Booking alerts (`discover.py`): every movie's `meta.event.bookButton` and release date are stored in catalog.json.
  Each run also reads Hyderabad's upcoming-movies (about a week ahead) and coming-soon (further out) pages (`UPCOMING`). Upcoming movies not
  bookable yet and `watchlist.json` entries (`{"code": "ET...", "name": ...}` or `{"name": ..., "match": [words]}`;
  words are matched against listed movies' page slugs/names) are re-checked each discovery run; a watched film's
  first listing is reported as "Listed on BookMyShow, bookings not open yet"; when booking opens (or a movie releasing later is first found already bookable)
  the workflow opens a GitHub issue "Bookings opened: ..." / "Advance bookings open: ..." (once per movie).
  BookMyShow returns 429 after ~10 discovery requests, so details (12 max, 12 s apart) run before the crawl.

## Interest ("I'm interested") counts (interest.py)
- Source: app endpoint `/api/movies/v1/synopsis/init/static?eventcode=...` -> text " 35.5K+ are interested ".
- `tracker.py` asks each film from `interest.targets()` (catalog films not released yet, incl. undated ones on an upcoming/coming-soon page in the last 3 days, + watchlist
  codes, one listing per BookMyShow group, max 30) every hour inside its paced loop (`InterestJob`), rows in
  `data/interest.csv`. Site: `data/interest.json`, "Upcoming" tab, Studio type `interest` ("Most awaited").
- Replaces the separate `bms-interest-tracker` repo (Puppeteer scrape of web pages, fixed 2025 list, 1/19 working).
- Interest history on the site: Upcoming tab "Interest over time" (chart of gain-since-start or total, hour-by-hour table, CSV) and
  Studio types `interestline` / `interesttable`. `build_site.py` keeps one entry per BookMyShow group (other listings of the same film
  show the same count) and copies `data/interest.csv` to the site.
- **Movie tab** (`site_src/index.html`, "MOVIE page" block): pick a film (ticket films + interest-only upcoming films) and get its KPIs,
  day-by-day totals (4 AM-4 AM, bars + table), hour-by-hour chart (days / vs other films), time-of-day heat grid, comparison with other films
  (yesterday's total, last hour), interest chart + hourly table. Every block has "Download image" (Studio look, style picker on top), CSV and
  "Studio". Link: `#movie?f=<name>`; film names on the Now and Upcoming tabs link to it. Ticket and interest films are matched by name.
- **Image look** (`site_src/index.html`, "modern look" block): Colour and Dark use card panels, a rounded gradient header, change pills, KPI tiles
  (`frame(..., {tiles})`), gradient bars and area-under-line charts; Minimal keeps the classic bordered tables (`tableClassic` / `frameClassic`).
  Page canvases pass `box.flat` (no panel). The auto-posted X images (Python `cards.py`) still use the older bordered look.
- Movie tab additions: today's pace vs same time yesterday, best day, 7-day average, milestones, vs-last-week column, cumulative chart,
  "When people book" (share of a day's tickets by morning / afternoon / evening / night, estimated from half-hour slots), interest day-by-day
  table (calendar days IST, gain, growth, days to release) with chart / table images.
- **X images in the new look**: `cards.square_table` / `square_lines` (used by `posts.py`) now draw the website's modern style: gradient background,
  white card with soft shadow, rounded gradient header, change pills, KPI tiles (`tiles=`), area under the line. Font: Inter, bundled in `fonts/`
  (OFL, `fonts/LICENSE-Inter.txt`) so the runner renders the same typeface. `compact_table` (the stored half-hour snapshot tables) keeps the
  bordered look. `posts.py` shows one row per film (other listings of a film repeat its name); `series.short_name` drops "(AK 47)" / "House No: 47".

## Daily briefing agent (briefing_facts.py, briefing.py, .github/workflows/daily_briefing.yml)
- 08:05 IST `tracker.py` starts the `Daily Briefing` workflow (cron `40 2 * * *` is only a backup). Steps:
  1. `briefing_facts.facts()` computes everything (yesterday = 4 AM to 4 AM, vs day before / last week, busiest hour, interest, bookings opened, flags).
     Day totals use `sales.day24`, a Python mirror of the website's rule.
  2. `briefing.py` asks Claude through the official `anthropic` SDK (`ANTHROPIC_API_KEY` secret, model `claude-haiku-5-5` = cheapest, $0.10/$0.50 per million tokens, about 0.1 cent a day; override with the `ANTHROPIC_MODEL` variable, e.g. `claude-sonnet-5-5`; effort `low`, no temperature because the API rejects sampling settings) for headline, bullets and
     3 X drafts as JSON. No key or API failure -> the template writer. The template output must pass its own check.
  3. `verify()`: every quantity (217K, 1.07M, 130%) must appear in the facts, no "collection / gross / crore / revenue", posts <= 272 characters.
     Failures go back to the model once; whatever still fails is replaced field by field by the template. `mode` in briefing.json says what happened
     (`claude`, `claude+template`, `template`).
  4. Writes `briefing.json` (+ `briefings/<date>.json`, 30 days). `build_site.py` copies it to `data/briefing.json`; the website shows it in the
     Auto posts tab (and a headline strip on Now). Posts are drafts: nothing is sent to X.
- To turn the model on: add the repository secret `ANTHROPIC_API_KEY`, then run the workflow with "force". Run locally:
  `python briefing.py --date 2026-10-31 --force --no-llm --out /tmp/b.json`.
- Day totals (`sales.day24` and its twin in `site_src/index.html`): the exact BookMyShow 24-hour figure is used at 4 AM; if the slot itself has no
  reading, the figures on both sides within an hour are interpolated (exact); then the older slot-corrected neighbour; then a one-sided figure
  within 30 minutes; only then the estimate. Fixes a bug where the day that had just closed fell back to an estimate 11% too high while the
  night's half-hour slots were still unknown (found with Aadarsha Kutumbam, 1 Nov: 232K estimate vs 208.5K real).

## Small agents (all cheap; Claude Haiku 5.5 only where words are written)
- **Weekly report** (`weekly.py`, `weekly_report.yml`): Mondays 08:20 IST (tracker dispatch, cron backup). Week = seven 4 AM-4 AM days
  from `sales.day24`; Python computes totals, change vs the week before, best day, interest gained in 7 days; Claude writes a headline and 4
  bullets; every number is checked (`briefing.verify`); template fallback. Output `weekly.json`, `weeklies/`. About 0.05 cents a run.
- **Release radar** (`radar.py`): no model. Films releasing in 21 days with interest, 24h/7-day change, bookings open. Written to `data/radar.json` by `build_site.py`.
- **Watchdog** (`health.py`): hourly checks (ticket readings, interest counts, discovery, daily briefing) in `health.json` `checks`; one GitHub issue
  "Tracker needs attention" lists failures and closes itself when green.
- **Ask the data** (`ask.py`, `ask.yml`): the owner opens an issue titled "? question" (or label `ask`); Claude answers from a Python-built digest,
  numbers checked, answer posted as a comment and the issue closed. Only the repository owner's issues are answered.
- Site: **Reports** tab (weekly report, radar, status, ask button). Shared helpers in `agentkit.py`.

## Polling order (fix for films that were never asked)
Runs end early (HTTP 429), and the slow (hourly) films used to be spread over the hour in a fixed order, so films at the end of
`movies.json` (like Cult) were starved. `tracker.py` now saves when each film was last asked (`data/poll_state.json`) and every run asks the
film asked longest ago first; a film asked recently waits until its interval is up.

## Day totals when nothing was read near 4 AM
`day24` (sales.py and the website) now also uses BookMyShow's nearest 24-hour figure within 5 hours (`NEAR` = 10 slots) of 4 AM: blended
when there is one on each side, else the one side; marked exact, with `near` = distance in slots. Tested on days that do have a reading at
4 AM: median error 0.3% at 3 hours, 1.0% at 4 hours (90th percentile 2.5%), because night sales are small and the 24-hour window drops
off about as much as it adds. Fixes blank days (Ramayana 25-28 Oct).

## Mandatory night read (3-5 AM, aimed at 4 AM)
`tracker.py`: at 3:45 IST every film except those released more than `OLD_DAYS` (50) days ago is queued (`PENDING`) and asked in turn, longest-waiting
first; interest jobs wait until the queue is empty. A 429 during the window retries after the block lifts (not after the normal hour); the window
closes at 5:00 (`WINDOW`) and logs any film that was not read. A run that starts inside the window queues whoever has no read since 3:00.
A request that gets a 429 no longer counts as "asked" in `data/poll_state.json`. Simulated with a fake clock: 85 of 85 films read between 3:45 and 4:12.

## Night read retries and the first day
- `tracker.py`: a film whose night read finds no counter is asked again up to `NIGHT_TRIES` (3) times, `NIGHT_RETRY` (10 min) apart, until 5 AM; a 429 retries when the block lifts.
- Website: when the Totals range starts at the first 4 AM (Align button), or the Movie tab's day-by-day starts, a row for the day before is added from BookMyShow's own 24-hour figure near that 4 AM (Ramayana: Thu 22 4 AM -> Fri 23 4 AM = 23.5K from the 1:51 AM reading).

## Day total rule order (final)
`day24` (sales.py and website, identical results on 825 day totals): (0) exact reading at 4 AM / blend of readings on both sides within 1 h /
slot-corrected neighbour within 1 h; (1) BookMyShow 24-hour reading from about 1:30 to 5 AM (`WIN_BEFORE`=5, `WIN_AFTER`=2 slots), nearest or blended - marked
BookMyShow (tested: median 0.1%, worst 3.2% over 482 comparisons); (2) none in that window: estimate from hourly figures, marked ≈ "from hourly figures"
(median 3% when the 24-hour readings are removed); (3) no usable hourly figures: nearest 24-hour reading up to 5 h away, marked ≈ with its distance;
(4) otherwise blank. Hourly estimates agree with nearby 24-hour readings (Jailer 2 18 Oct: 1.071M vs 1.070M).

## Website look (refresh)
`site_src/index.html`: one CSS layer ("visual refresh") defines the tokens (light and dark), depth, rounded controls, gradient primary/selected states, sticky pill tab bar with an edge fade
(`nav.more`), tinted chips and KPI tiles. Upcoming tab: release dates carry the year when it is not this year, BookMyShow stand-in dates (31 Dec) are "tentative", old past dates without
bookings are "date not confirmed", released films sit under "Recently released", and on phones the 24-hour gain sits under the interested count. Auto posts: latest 6 shown, the rest behind
"Show earlier posts"; "Not posted" chips are hidden; the briefing's model/cost line is inside "How this was made".

## Night read, second version (after the first real night, 3 Nov)
First night: only 8 of 87 films read in 3-5 AM. Cause: 74 of the 87 films have no counter (not released), so most of the night read's requests were wasted; a 429 at 3:48 AM then blocked
all requests until 4:29. Now `night_films` = only films with a 1-hour/24-hour reading in the last 3 days (or released within 3 days), not older than 50 days (14 films today).
From 3:30 to 5:00 (`QUIET_FROM`, `WINDOW`) nothing else is asked, so the allowance is not used up, and the read starts at 3:55 (`SWEEP_AT`), about 30 s apart. Simulated with a fake clock:
14 of 14 films read 3:50-3:58, 0 other requests in the quiet window.

## Night read, result of the second night (4 Nov)
12 of 16 films with a counter were read 3-5 AM (7 within 10 min of 4 AM). The 4 misses (Ranabaali, 418, Nayyi Navelli, Clayface) show NO trending widget at night
("no trending widget in response" in the log), so there was nothing to read; their 4 AM figure comes from the first morning reading (day totals use a 24-hour reading up to 5 h away, marked approximate).
A 429 still hit at 4:12 AM because the always-polled films were asked again every 3 minutes (cached answers). Now a film already read in the quiet window is not asked again until 5:00 (simulated: 20 requests in 3:30-5:00 instead of 67).

## Table images: "Sheet" look (default) and the Scene duplicate
- **Sheet look** (website Studio / Movie images: `tableSheet`, `frameSheet`, `finishSheet` in `site_src/index.html`; X post images: `cards.sheet_table`, used by `cards.square_table`): a plain bordered grid like a spreadsheet screenshot - thin grid lines, heavier outer border, tinted header row, optional summary strip, green/red text for changes (no pills, no gradients). Columns are only as wide as their content, long names wrap onto a second line, the text is scaled to fill the picture width, and the picture is exactly as tall as the table (+ footer). Style buttons: Sheet (default), Colour, Minimal, Dark. Charts are unchanged.
- **Duplicate listings** (Scene and Scene (Telugu) showed the same figures): `update_movies.py` now also marks a listing as `dup` when its last readings match another tracked listing's (5+ identical hour/kind/value points), even if BookMyShow's group is not known yet. `build_site.py` leaves such names out of older snapshots; `series.ALIASES` maps their old names.

## Forecast agent, version 1 (`forecast.py`)
- Numbers (every hour, in `build_site.py` -> `data/forecast.json`; no model): next 3 hours = latest hourly figure x how the same hours moved 1 and 7 days ago
  (tested on ~650 past hours: median error 6%, repeating the last hour is ~10%; range = 10th-90th percentile of past ratios); today's total = so far + what is left
  of yesterday/last week at today's pace; tomorrow = today's projection x the usual weekday-to-weekday change across films (range from the middle half). Only films that
  currently show the hourly counter (>= 240 hourly readings, newest within 90 min).
- Words (`python forecast.py --outlook`, `.github/workflows/forecast_outlook.yml`, 11:10 / 16:10 / 20:10 IST, ~0.1 cent a run): Claude Haiku explains the facts; every number
  checked with `briefing.verify`; template fallback; written to `outlook.json`.
- Inputs you can edit: `signals.json` (`[{"film": "Scene", "talk": "good"|"mixed"|"bad", "note": "..."}]`, nudges tomorrow x1.10 / x0.85 and is shown) and `holidays.json` (dates the
  writer may mention). NOT included yet (version 2): review scores / word of mouth (could read BookMyShow's rating and votes), show and screen counts, weather, release-day
  surges for films with no history. Site: Reports tab, "Next hour forecast".

### Forecast agent: whole-day, hour by hour
`forecast.json` now has, per film, `hours` = every reported hour of the day (7-8 AM ... 10-11 PM): booked (actual) for past hours, expected + range for the rest, plus the same hour yesterday;
`peak_ahead` = the busiest hour still ahead. Ranges per hour-ahead come from the backtest (error grows: ~6% one hour ahead, ~12% at 4 hours, ~21% at 8). Today's total = so far + the hourly
forecast for the rest of the evening x the counter-to-ticket factor + a night allowance (yesterday's night x today's evening vs yesterday's); backtested: ~13% off at 3 PM, 7% at 6 PM, 4% at 9 PM.
Site: Reports tab -> chart (booked solid, expected dashed, range shaded, yesterday grey) + table with a film picker.

## Forecast agent, version 2: outside signals and release-day patterns
- **BookMyShow rating** (`ratings.py`, fields found with `probe_ratings.py`): init/static rating widget (`styleId` `overall-rating`, `review-count`) and secondary `meta.ratings.aggregatedRating` + "Top reviews" count -> `ratings.json`.
- **Web research** (`intel.py`, Claude Haiku 5.5 + web search `web_search_20250305`, max 5 searches per film; first real run: 21 searches + 330K tokens = 25 cents for 6 films, so only films in the forecast or awaited by 60K+ people are researched): critic reviews and ratings, IMDb / Google scores, word of mouth, reported screens, competing releases -> `intel.json`.
  Every item needs a URL that really appeared in that run's search results, ratings must be on a sane scale, otherwise it is dropped. Films in their opening days (within 2 days of release) are re-researched every run, others once per 30 h.
- Both run in `forecast_outlook.yml` (11:10 / 16:10 / 20:10 IST), then the outlook text is written.
- **Nudge** (`forecast.buzz`): critics (+/-6%), BookMyShow rating (+/-7%, needs 500+ votes), IMDb (+/-4%), Google (+/-3%), word of mouth (good +5%, bad -8%), competition from our own data (a release within 2 days at least half as awaited: -2% each, max 2; competitors found on the web are shown, not counted), your `signals.json` note; total capped at about +16% / -18%. It is
  faded in over the hours ahead (nothing on the next hour, full from ~7 hours ahead) and applied to tomorrow's total. NOT tested against history (there is none): it only leans the forecast. Screens are shown, not used.
- **Holidays**: `holidays.json`; a holiday tomorrow multiplies tomorrow by 1.12 (assumption, marked in the basis text).
- **Release-day pattern**: for a film 0-1 days after release the hourly forecast is the average of the usual pattern (yesterday / last week) and the average release-day / day-2 shape of other films (scaled to how today has gone so far); ranges widen to cover the opening-day backtest (median error ~10% on 108 cases, versus ~9.5% for the usual method on all days but with bigger misses on opening days).
- Site: Reports tab -> "What the forecast takes into account" (chips + signals table with sources and each nudge).
