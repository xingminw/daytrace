# DayTrace Dashboard

Localhost dashboard for the event-based DayTrace SQLite database.

Run:

```bash
python dashboard/server.py --db data/daytrace.sqlite --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

JSON APIs:

```text
/api/summary?date=YYYY-MM-DD
/api/events?date=YYYY-MM-DD
/api/events?date=YYYY-MM-DD&source=git
/api/events?date=YYYY-MM-DD&project=daytrace
```
