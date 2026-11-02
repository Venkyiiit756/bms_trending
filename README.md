# Movie ticket trends (BookMyShow, Hyderabad)

Tracks how many tickets BookMyShow says were booked for each film, hour by hour, plus how many people marked
"interested" in films not yet released. Everything runs on GitHub Actions; the site is published to
https://venkyiiit756.github.io/hyd-movie-trends/

- **Site tabs:** Now, Movie, Studio (images for X), Explore, Totals (4 AM to 4 AM days), Upcoming, Reports (weekly report, release radar, tracker status), Auto posts (daily briefing), Data.
- **Data:** `data/*.csv` (one file per film) and `data/interest.csv`; `catalog.json` lists every film seen.
- **Agents:** daily briefing and weekly report (Claude writes, Python checks every number), release radar, watchdog, and ask-the-data (open an issue titled `? your question`).
- **Notes for working on it:** see `HANDOFF.md`.

Counts are tickets booked per BookMyShow listing, not revenue.
