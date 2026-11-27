# vastra-data-platform (Week 2, S3)

Trusted data for P2: one load job, one database with three layers (raw, clean, ready), four DPDP tables and a
fitness scorecard. Builds on the Week 1 API (its outbox is the best data Vastra has).

```
python -m vastra_data init                  # schemas + data catalogue
python -m vastra_data simulate              # 24 months of POS/ERP/WMS/legacy history into inbox/
python -m vastra_data load --source all     # contract check every record; bad ones -> failed_records + alert
python -m vastra_data clean                 # UTC, de-duplicate, fix codes; tokens only
python -m vastra_data ready                 # ready tables; contract checks; rolls back if a check fails
python -m vastra_data scorecard             # six questions, a traffic light each
python -m pytest -v                         # 19 tests
```

| Folder / file | What it is |
|---|---|
| `vastra_data/events.py` | Record contracts: the six-field envelope + one pydantic payload model per event type |
| `vastra_data/load.py` | The load job: no silent failures, retry 2/4/8 s for database busy, ON CONFLICT (event_id) DO NOTHING |
| `vastra_data/failed.py` | failed_records: list, fix, rerun (loaded exactly once), ignore |
| `vastra_data/clean.py`, `ready.py` | raw -> clean -> ready, rebuilt from raw at any time |
| `contracts/*.yaml` | Table contracts (owner, purpose, label, columns, unique, fresh_within, change rule) |
| `vastra_data/governance.py` | consent, person_tokens (restricted), data_catalogue, access_log, erasure |
| `vastra_data/fitness.py`, `demand.py` | Fitness scorecard; is_sold_out and adjusted demand (censored demand) |
| `sql/postgres/001_schemas.sql` | Same schema for PostgreSQL (tested on PostgreSQL 16) |
| `samples/demo_bad_stock_count.jsonl` | Demo step 1: `available` renamed to `avail` |

The lab runs on SQLite files attached under schema names (`raw.events`, `ready.stock_position`, ...) so it needs no
server, the same choice the Week 1 API made. See the solution document for the full walkthrough.
