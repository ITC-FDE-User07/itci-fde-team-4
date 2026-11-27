"""DPDP basics with four small tables: consent, person_tokens (restricted), data_catalogue, access_log.

Engineering interpretation for learning purposes, not legal advice: Vastra's legal team and DPO confirm each control.
"""
import datetime as dt
import glob
import json
import os
import secrets

import pandas as pd
import yaml

from vastra_data import config, db

PURPOSES = ("orders", "loyalty", "marketing", "personalisation")


class PurposeNotAllowed(PermissionError):
    pass


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ data catalogue
def catalogue_entries() -> list[dict]:
    """Every contract file describes one table; catalogue.yaml adds the raw, clean, gov and restricted tables."""
    out = []
    for path in sorted(glob.glob(os.path.join(config.settings.contracts_dir, "*.yaml"))):
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        if "tables" in doc:                      # catalogue.yaml
            out += doc["tables"]
        else:
            out.append({"table": doc["table"], "owner": doc["owner"], "source": doc.get("source", "ready build"),
                        "classification": doc["classification"], "purpose": doc["purpose"],
                        "contract_file": os.path.basename(path), "description": doc.get("description", "")})
    return out


def sync_catalogue():
    entries = catalogue_entries()
    with db.tx() as con:
        for e in entries:
            con.execute("INSERT INTO gov.data_catalogue VALUES (?,?,?,?,?,?,?) ON CONFLICT(table_name) DO UPDATE SET "
                        "owner=excluded.owner, source=excluded.source, classification=excluded.classification, "
                        "allowed_purposes=excluded.allowed_purposes, contract_file=excluded.contract_file, "
                        "description=excluded.description",
                        (e["table"], e["owner"], e["source"], e["classification"], json.dumps(e["purpose"]),
                         e.get("contract_file"), e.get("description", "")))
    return len(entries)


def log_access(con, table: str, purpose: str, action: str, allowed: bool, detail: str = "", actor: str = None):
    con.execute("INSERT INTO gov.access_log (at_utc, actor, table_name, purpose, action, allowed, detail) "
                "VALUES (?,?,?,?,?,?,?)", (_now(), actor or config.settings.actor, table, purpose, action,
                                           int(allowed), detail))


def read_table(table: str, purpose: str, actor: str = None, where: str = "", params=()) -> pd.DataFrame:
    """Purpose limitation: a table may be read only for a purpose listed in the catalogue. Every read is logged."""
    con = db.connect(restricted=table.startswith("restricted."))
    try:
        row = con.execute("SELECT allowed_purposes, classification FROM gov.data_catalogue WHERE table_name=?",
                          (table,)).fetchone()
        allowed = bool(row) and purpose in json.loads(row["allowed_purposes"])
        con.execute("BEGIN")
        log_access(con, table, purpose, "read", allowed, "" if row else "not in catalogue", actor)
        con.execute("COMMIT")
        if not allowed:
            raise PurposeNotAllowed(f"{table} may not be read for purpose '{purpose}'"
                                    + ("" if row else " (table is not in the data catalogue)"))
        return pd.read_sql_query(f"SELECT * FROM {table} {where}", con, params=params)
    finally:
        con.close()


# ------------------------------------------------------------------ tokens (restricted) and consent
def tokenise(con, customer_ref: str, name=None, phone=None, email=None) -> str:
    """Inside the caller's transaction (restricted attached). Same person -> same token; details stay restricted."""
    r = con.execute("SELECT customer_token FROM restricted.person_tokens WHERE customer_ref=?",
                    (customer_ref,)).fetchone()
    if r:
        if name or phone or email:
            con.execute("UPDATE restricted.person_tokens SET name=COALESCE(?,name), phone=COALESCE(?,phone), "
                        "email=COALESCE(?,email) WHERE customer_ref=?", (name, phone, email, customer_ref))
        return r["customer_token"]
    token = "cust_" + secrets.token_hex(8)       # random: cannot be reversed to the person
    con.execute("INSERT INTO restricted.person_tokens VALUES (?,?,?,?,?,?)",
                (token, customer_ref, name, phone, email, _now()))
    return token


def token_for(con, customer_ref: str):
    r = con.execute("SELECT customer_token FROM restricted.person_tokens WHERE customer_ref=?",
                    (customer_ref,)).fetchone()
    return r["customer_token"] if r else None


def record_consent(con, token, purpose, status, notice_version, at_utc, source, event_id=None):
    con.execute("INSERT OR IGNORE INTO gov.consent (customer_token, purpose, status, notice_version, at_utc, source, "
                "event_id) VALUES (?,?,?,?,?,?,?)", (token, purpose, status, notice_version, at_utc, source, event_id))


def consent_status(customer_token: str) -> dict:
    con = db.connect()
    try:
        rows = con.execute("SELECT purpose, status, notice_version, at_utc FROM gov.consent WHERE customer_token=? "
                           "ORDER BY at_utc, consent_id", (customer_token,)).fetchall()
    finally:
        con.close()
    latest = {}
    for r in rows:
        latest[r["purpose"]] = {"status": r["status"], "notice_version": r["notice_version"], "at": r["at_utc"]}
    return {p: latest.get(p, {"status": "not given"}) for p in PURPOSES}


def has_consent(customer_token: str, purpose: str) -> bool:
    return consent_status(customer_token)[purpose]["status"] == "given"


def withdraw(customer_ref: str, purposes=PURPOSES, actor: str = None, notice_version="lab") -> str:
    """Withdrawal is as easy as giving: one call, logged."""
    with db.tx(restricted=True) as con:
        token = token_for(con, customer_ref)
        if not token:
            raise KeyError(f"no person with ref {customer_ref}")
        for p in purposes:
            record_consent(con, token, p, "withdrawn", notice_version, _now(), "dpo-tool")
        log_access(con, "gov.consent", "data_principal_request", "withdraw", True, ",".join(purposes), actor)
    return token


def erase(customer_ref: str, actor: str = None, request_ref: str = "") -> dict:
    """Erasure in one step: delete the person's row in person_tokens. Their tokens remain elsewhere but can no
    longer be linked to anyone (the full design calls this crypto-shredding)."""
    with db.tx(restricted=True) as con:
        token = token_for(con, customer_ref)
        if not token:
            raise KeyError(f"no person with ref {customer_ref}")
        for p in PURPOSES:
            record_consent(con, token, p, "withdrawn", "erasure", _now(), "dpo-tool")
        n = con.execute("DELETE FROM restricted.person_tokens WHERE customer_token=?", (token,)).rowcount
        log_access(con, "restricted.person_tokens", "data_principal_request", "erase", True,
                   f"token={token} rows_deleted={n} request={request_ref}", actor)
    return {"customer_token": token, "rows_deleted": n}


def who_is(customer_token: str, actor: str = None, reason: str = "") -> dict | None:
    """Re-identify a token (DPO / customer-service only). Logged with the reason."""
    with db.tx(restricted=True) as con:
        r = con.execute("SELECT * FROM restricted.person_tokens WHERE customer_token=?", (customer_token,)).fetchone()
        log_access(con, "restricted.person_tokens", "customer_service", "lookup", True,
                   f"token={customer_token} found={bool(r)} reason={reason}", actor)
    return dict(r) if r else None


def access_log(limit: int = 20) -> list[dict]:
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM gov.access_log ORDER BY access_id DESC LIMIT ?", (limit,))]
    finally:
        con.close()
