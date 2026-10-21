# Snapshots every 30 minutes

The numbers behind the website's tables, one CSV per half hour (:00 and :30, IST), made about
5 minutes later by `snapshot.py`. The table images are kept in the website repository, not here.

```
snapshots/2026-10-21/2026-10-21_1130.csv   movie, metric (1_hour / 24_hours), value, reading time
```

- Each movie's value is its latest reading at or before the snapshot time, no older than 75 minutes, with
  stale cached copies removed (`series.py`). The CSV has each reading's exact time.
- The figure is BookMyShow's rolling "tickets booked in the last 1 hour" (or "last 24 hours", shown at
  night and for small films). Consecutive snapshots overlap; they are not 30-minute sales.
- The counter is per BookMyShow movie listing and is not city-specific (checked: the same number is
  returned for Hyderabad, Mumbai and Bengaluru). A listing covers all its languages and formats, e.g.
  "Jailer 2" = Tamil, Telugu and Kannada; "Jailer 2 (Hindi)" is a separate listing.
- Figures are tickets booked, not revenue.
