"""Turn a contract YAML into checks. Four kinds, as in the beginner contract (Listing 2):

  required    no empty values in a required column (plus the right type: int, real, date, UTC timestamp)
  valid       values inside min / max / allowed
  unique      no duplicate keys
  fresh       the newest row is within fresh_within (alert only: the table is still usable, but late)

The first three are blocking: ready.build() rolls back the whole build if any fails, so the previous good
ready tables stay in place. `compatible(old, new)` enforces the change rule: add columns only.
"""
import datetime as dt
import glob
import os

import yaml

from vastra_data import config

BLOCKING = ("required", "type", "valid", "unique")
_UNITS = {"minute": 60, "minutes": 60, "hour": 3600, "hours": 3600, "day": 86400, "days": 86400}


def load(table: str) -> dict:
    path = os.path.join(config.settings.contracts_dir, f"{table}.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def all_ready() -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(config.settings.contracts_dir, "ready.*.yaml"))):
        with open(p, encoding="utf-8") as f:
            out.append(yaml.safe_load(f))
    return out


def seconds(text: str) -> int:
    n, unit = str(text).split()
    return int(float(n) * _UNITS[unit.lower()])


_TYPE_SQL = {
    "int": "typeof({c}) != 'integer'",
    "real": "typeof({c}) NOT IN ('real','integer')",
    "text": "typeof({c}) != 'text'",
    "date": "NOT ({c} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')",
    "timestamp": "NOT ({c} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*')",
}


def check(con, contract: dict, now: dt.datetime | None = None) -> list[dict]:
    t = contract["table"]
    res = []

    def add(name, failing, detail):
        res.append({"table": t, "check": name, "passed": failing == 0, "failing_rows": int(failing), "detail": detail})

    total = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    add("rows", 0 if total else 1, f"{total} rows")
    for col, spec in contract["columns"].items():
        if spec.get("required"):
            n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} IS NULL OR CAST({col} AS TEXT) = ''").fetchone()[0]
            add(f"required:{col}", n, "no empty values")
        typ = spec.get("type")
        if typ in _TYPE_SQL:
            cond = _TYPE_SQL[typ].format(c=col)
            if typ == "timestamp" and spec.get("timezone") == "UTC":
                cond = f"({cond} OR NOT ({col} LIKE '%+00:00' OR {col} LIKE '%Z'))"
            n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} IS NOT NULL AND {cond}").fetchone()[0]
            add(f"type:{col}", n, typ + (" in UTC" if spec.get("timezone") == "UTC" else ""))
        if "min" in spec:
            n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} < ?", (spec["min"],)).fetchone()[0]
            add(f"valid:{col}>={spec['min']}", n, "minimum")
        if "max" in spec:
            n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} > ?", (spec["max"],)).fetchone()[0]
            add(f"valid:{col}<={spec['max']}", n, "maximum")
        if "allowed" in spec:
            vals = spec["allowed"]
            n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} IS NOT NULL AND {col} NOT IN "
                            f"({','.join('?' * len(vals))})", vals).fetchone()[0]
            add(f"valid:{col} in {vals}", n, "allowed values")
    checks = contract.get("checks", {})
    if checks.get("unique"):
        key = ", ".join(checks["unique"])
        n = con.execute(f"SELECT COALESCE(SUM(c - 1), 0) FROM (SELECT COUNT(*) c FROM {t} GROUP BY {key} "
                        "HAVING c > 1)").fetchone()[0]
        add(f"unique:({key})", n, "no duplicates")
    if checks.get("fresh_within"):
        col = checks.get("fresh_column")
        newest = con.execute(f"SELECT MAX({col}) FROM {t}").fetchone()[0]
        now = now or dt.datetime.now(dt.timezone.utc)
        if newest is None:
            add(f"fresh:{checks['fresh_within']}", 1, "empty table")
        else:
            if len(newest) == 10:   # a business date: the day counts as complete at 23:59:59 IST
                ts = dt.datetime.fromisoformat(newest + "T23:59:59+05:30")
            else:
                ts = dt.datetime.fromisoformat(newest.replace("Z", "+00:00"))
            age = (now - ts).total_seconds()
            add(f"fresh:{checks['fresh_within']}", 0 if age <= seconds(checks["fresh_within"]) else 1,
                f"newest {newest}, age {age / 60:.0f} min")
    return res


def blocking_failures(results: list[dict]) -> list[dict]:
    return [r for r in results if not r["passed"] and r["check"].split(":")[0] in BLOCKING + ("rows",)]


def compatible(old: dict, new: dict) -> list[str]:
    """Change rule: add columns only. Removing or renaming a column, or changing its type, needs a new version."""
    problems = []
    if new.get("version", 1) > old.get("version", 1):
        return problems
    for col, spec in old["columns"].items():
        if col not in new["columns"]:
            problems.append(f"column '{col}' removed or renamed without a new version")
        elif new["columns"][col].get("type") != spec.get("type"):
            problems.append(f"column '{col}' changed type {spec.get('type')} -> {new['columns'][col].get('type')}")
    for col, spec in new["columns"].items():
        if col not in old["columns"] and spec.get("required"):
            problems.append(f"new column '{col}' is required: existing producers would break (make it optional)")
    return problems

