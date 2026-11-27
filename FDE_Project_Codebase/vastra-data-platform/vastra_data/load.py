"""The load job: pick up new records, check each against its contract, put good records in raw.

Four rules (no silent failures, fixes E10):
  1. Bad data (missing field, negative stock, wrong time format) is NOT retried: it goes straight to
     ops.failed_records with the reason.
  2. A temporary problem (database busy) is retried up to 3 times, waiting 2, 4 and 8 seconds.
  3. Every failure raises an alert, and the failed count is on one query (`python -m vastra_data status`).
  4. After the fix, `python -m vastra_data failed rerun` loads the failed records again. Because of event_id
     (ON CONFLICT DO NOTHING), nothing is counted twice.

Run:  python -m vastra_data load --source api-v1     (or pos, erp, wms, legacy, all)
"""
import datetime as dt
import json
import logging
import sqlite3
import time

from vastra_data import config, db, events, governance, sources
from vastra_data.alerts import alert

log = logging.getLogger("vastra_data")
ALL_SOURCES = ("erp", "wms", "pos", "legacy", "api-v1")   # reference data first, then movements


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ failed records
def save_failed(con, stage, row, reason, kind, run_id=None):
    safe = events.mask_personal(row) if isinstance(row, dict) else row
    con.execute("INSERT INTO ops.failed_records (stage, load_run_id, source, event_id, event_type, row, reason, kind, "
                "failed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (stage, run_id, (row or {}).get("source") if isinstance(row, dict) else None,
                 (row or {}).get("event_id") if isinstance(row, dict) else None,
                 (row or {}).get("event_type") if isinstance(row, dict) else None,
                 json.dumps(safe, default=str), reason[:1000], kind, _now()))


# ------------------------------------------------------------------ writing to raw
def _prepare(con, env: events.Envelope, payload, row: dict) -> tuple:
    """Return the raw.events tuple. Personal details are swapped for a token BEFORE anything reaches raw."""
    body = row["payload"]
    if env.event_type == "customer.registered":
        token = governance.tokenise(con, payload.customer_ref, payload.name, payload.phone, payload.email)
        body = {"customer_token": token, "home_store": payload.home_store}
    elif env.event_type == "consent.changed":
        token = governance.tokenise(con, payload.customer_ref)
        body = {k: v for k, v in body.items() if k != "customer_ref"} | {"customer_token": token}
    return (env.event_id, env.event_type, env.occurred_at, env.source, env.actor,
            json.dumps(body, separators=(",", ":"), default=str), _now())


def insert_raw(con, prepared: list[tuple], run_id) -> int:
    """ON CONFLICT (event_id) DO NOTHING: loading the same record twice never counts twice."""
    before = con.total_changes
    con.executemany("INSERT INTO raw.events (event_id, event_type, occurred_at, source, actor, payload, loaded_at, "
                    f"load_run_id) VALUES (?,?,?,?,?,?,?,{int(run_id or 0)}) ON CONFLICT (event_id) DO NOTHING",
                    prepared)
    return con.total_changes - before


def write_with_retry(valid: list[tuple], run_id, watermark=None, source=None) -> tuple[int, bool]:
    """Insert one batch of valid records. Returns (inserted, ok). Retries only temporary database problems."""
    attempts = config.settings.retry_attempts
    for attempt in range(1, attempts + 1):
        try:
            with db.tx(restricted=True) as con:
                prepared = [_prepare(con, env, payload, row) for env, payload, row in valid]
                n = insert_raw(con, prepared, run_id)
                if watermark is not None:
                    con.execute("INSERT INTO ops.watermarks VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET "
                                "position=excluded.position, updated_at=excluded.updated_at",
                                (source, watermark, _now()))
            return n, True
        except sqlite3.OperationalError as err:          # database busy / locked: wait, then retry
            if attempt == attempts:
                with db.tx() as con:
                    for _, _, row in valid:
                        save_failed(con, "load", row, f"database error after {attempts} tries: {err}", "database",
                                    run_id)
                alert("load batch failed after retries", source=source, rows=len(valid), error=str(err))
                return 0, False
            wait = config.settings.retry_base_s ** attempt
            log.warning("database busy (%s), retry %s/%s in %ss", err, attempt, attempts, wait)
            time.sleep(wait)
    return 0, False


# ------------------------------------------------------------------ one run
def run(source_name: str, client=None) -> dict:
    db.init()
    src = sources.get(source_name, client)
    with db.tx() as con:
        run_id = con.execute("INSERT INTO ops.load_runs (source, started_at, actor) VALUES (?,?,?)",
                             (source_name, _now(), config.settings.actor)).lastrowid
        r = con.execute("SELECT position FROM ops.watermarks WHERE source=?", (source_name,)).fetchone()
    since = r["position"] if r else None
    stats = {"run_id": run_id, "source": source_name, "read": 0, "loaded": 0, "duplicates": 0, "failed": 0}
    reasons = {}
    try:
        for batch in src.batches(since):
            valid, bad = [], []
            for row in batch.rows:
                stats["read"] += 1
                if "_unparsed" in row:
                    bad.append(({**row, "source": source_name}, "line is not valid JSON"))
                    continue
                try:
                    env, payload = events.validate(row)   # contract check (pydantic)
                    if env.source != source_name:
                        raise events.ContractError(f"source: record says '{env.source}' but arrived on "
                                                   f"'{source_name}'")
                    valid.append((env, payload, row))
                except events.ContractError as err:       # bad data: do not retry
                    bad.append((row, str(err)))
            if bad:
                with db.tx() as con:
                    for row, reason in bad:
                        save_failed(con, "load", row, reason, "contract", run_id)
                for row, reason in bad:
                    reasons[reason.split(":")[0]] = reasons.get(reason.split(":")[0], 0) + 1
                    alert("record failed its contract", source=source_name,
                          event_id=row.get("event_id") if isinstance(row, dict) else None, reason=reason)
                stats["failed"] += len(bad)
            n, ok = write_with_retry(valid, run_id, batch.watermark, source_name)
            if ok:
                stats["loaded"] += n
                stats["duplicates"] += len(valid) - n
            else:
                stats["failed"] += len(valid)
                # watermark still moves on: the rows are safe in failed_records and come back via rerun
                with db.tx() as con:
                    con.execute("INSERT INTO ops.watermarks VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET "
                                "position=excluded.position, updated_at=excluded.updated_at",
                                (source_name, batch.watermark, _now()))
        status = "ok" if stats["failed"] == 0 else "ok_with_failures"
    except Exception as err:   # source unreachable etc.: the run itself fails loudly
        status = "error"
        alert("load run failed", source=source_name, error=f"{type(err).__name__}: {err}")
        stats["error"] = f"{type(err).__name__}: {err}"
    with db.tx() as con:
        con.execute("UPDATE ops.load_runs SET finished_at=?, read=?, loaded=?, duplicates=?, failed=?, status=? "
                    "WHERE run_id=?", (_now(), stats["read"], stats["loaded"], stats["duplicates"], stats["failed"],
                                       status, run_id))
    stats["status"] = status
    if reasons:
        stats["failure_reasons"] = reasons
    return stats


def run_all(client=None) -> list[dict]:
    return [run(s, client if s == "api-v1" else None) for s in ALL_SOURCES]
