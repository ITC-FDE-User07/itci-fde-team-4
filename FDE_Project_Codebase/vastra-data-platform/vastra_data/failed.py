"""Work with ops.failed_records: list, fix, rerun, ignore.

  python -m vastra_data failed list
  python -m vastra_data failed fix 7 --rename payload.avail=payload.available
  python -m vastra_data failed fix 7 --set payload.available=12
  python -m vastra_data failed rerun            # every open or fixed record; loaded exactly once thanks to event_id
"""
import datetime as dt
import json

from vastra_data import config, db, events
from vastra_data.alerts import alert
from vastra_data.load import write_with_retry


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def list_failed(status: str | None = "open", limit: int = 50) -> list[dict]:
    con = db.connect()
    try:
        q, args = "SELECT * FROM ops.failed_records", []
        if status:
            q += " WHERE status IN (" + ",".join("?" * len(status.split(","))) + ")"
            args = status.split(",")
        return [dict(r) for r in con.execute(q + " ORDER BY failed_id DESC LIMIT ?", args + [limit])]
    finally:
        con.close()


def counts() -> dict:
    con = db.connect()
    try:
        rows = con.execute("SELECT stage, status, COUNT(*) n FROM ops.failed_records GROUP BY stage, status").fetchall()
    finally:
        con.close()
    return {f"{r['stage']}:{r['status']}": r["n"] for r in rows}


def _walk(obj, dotted):
    parts = dotted.split(".")
    for p in parts[:-1]:
        obj = obj.setdefault(p, {})
    return obj, parts[-1]


def _coerce(v: str):
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return v


def fix(failed_id: int, renames: dict | None = None, sets: dict | None = None, actor: str | None = None) -> dict:
    con = db.connect()
    try:
        r = con.execute("SELECT * FROM ops.failed_records WHERE failed_id=?", (failed_id,)).fetchone()
    finally:
        con.close()
    if r is None:
        raise KeyError(f"failed record {failed_id} not found")
    row = json.loads(r["fixed_row"] or r["row"])
    for old, new in (renames or {}).items():
        src, k_old = _walk(row, old)
        dst, k_new = _walk(row, new)
        if k_old not in src:
            raise KeyError(f"{old} not in record")
        dst[k_new] = src.pop(k_old)
    for path, value in (sets or {}).items():
        obj, k = _walk(row, path)
        obj[k] = _coerce(value)
    with db.tx() as con:
        con.execute("UPDATE ops.failed_records SET fixed_row=?, status='fixed', resolved_by=? WHERE failed_id=?",
                    (json.dumps(row), actor or config.settings.actor, failed_id))
    return row


def ignore(failed_id: int, reason: str, actor: str | None = None):
    with db.tx() as con:
        con.execute("UPDATE ops.failed_records SET status='ignored', resolved_at=?, resolved_by=?, "
                    "reason=reason || ' | ignored: ' || ? WHERE failed_id=?",
                    (_now(), actor or config.settings.actor, reason, failed_id))


def rerun(ids: list[int] | None = None, actor: str | None = None) -> dict:
    """Load failed records again (the fixed version if there is one). Without ids: every fixed record and every
    database failure. Still-bad records stay open, with the new reason. Already-loaded event_ids are skipped by ON CONFLICT, so a record is never counted twice."""
    con = db.connect()
    try:
        q = "SELECT * FROM ops.failed_records WHERE stage='load' AND status IN ('open','fixed')"
        if ids:          # named records: try them whatever their state
            q += f" AND failed_id IN ({','.join('?' * len(ids))})"
        else:            # default: records someone fixed, and database failures (the data itself was fine)
            q += " AND (status='fixed' OR kind='database')"
        rows = con.execute(q, ids or []).fetchall()
    finally:
        con.close()
    out = {"tried": 0, "loaded": 0, "already_present": 0, "still_failing": 0}
    for r in rows:
        out["tried"] += 1
        row = json.loads(r["fixed_row"] or r["row"])
        try:
            env, payload = events.validate(row)
        except events.ContractError as err:
            with db.tx() as con:
                con.execute("UPDATE ops.failed_records SET reason=? WHERE failed_id=?", (str(err)[:1000], r["failed_id"]))
            out["still_failing"] += 1
            alert("rerun: record still fails its contract", failed_id=r["failed_id"], reason=str(err))
            continue
        n, ok = write_with_retry([(env, payload, row)], r["load_run_id"])
        if not ok:
            out["still_failing"] += 1
            continue
        out["loaded" if n else "already_present"] += 1
        with db.tx() as con:
            con.execute("UPDATE ops.failed_records SET status='loaded', resolved_at=?, resolved_by=? WHERE failed_id=?",
                        (_now(), actor or config.settings.actor, r["failed_id"]))
    return out
