# Snapshots every 30 minutes (Hyderabad)

One set of files per half hour (:00 and :30), IST, made automatically about 5 minutes later.

```
snapshots/
  latest-last-1-hour.png                       newest hourly table (always the same name)
  2026-10-18/
    2026-10-18_1100_last-1-hour.png            table: movie, tickets booked in the last 1 hour
    2026-10-18_1100_last-24-hours.png          table for movies that show the 24-hour figure instead
    2026-10-18_1100.csv                        same numbers as data (movie, metric, value, reading time)
```

- The file name time (`1130`) is the snapshot time. Each movie's value is its latest reading at or before that
  time, no older than 75 minutes (the CSV has each reading's exact time). The figure is always BookMyShow's rolling
  "last 1 hour" count, just sampled every 30 minutes, so consecutive snapshots overlap. It is not 30-minute sales.
- A movie appears in the **last-1-hour** table only while BookMyShow shows the hourly figure for it. Late at night
  and for small films it shows the 24-hour figure instead (second image). Movies with no "Trending" line are left out.
- Counter scope is unverified (the Hyderabad page may not mean Hyderabad-only counts). Figures are tickets booked, not revenue.
- Pick a file: `https://github.com/Venkyiiit756/bms_trending/tree/main/snapshots`
- To rebuild an hour by hand: `python snapshot.py --at "2026-10-18 11:00"`
