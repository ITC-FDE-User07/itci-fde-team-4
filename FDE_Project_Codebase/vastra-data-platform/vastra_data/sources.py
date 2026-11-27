"""Where records come from. Each source yields batches of envelope dicts plus the watermark to save after them.

  api-v1   the Week 1 outbox, read through its published feed GET /v1/integration/events (best data Vastra has)
  pos/erp/wms   file drops: inbox/<source>/*.jsonl, one envelope per line
  legacy   the 2014 stock_copy table, opened read-only (Red for P2: kept only to show how wrong it was)
"""
import datetime as dt
import glob
import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Iterator, Optional

import httpx

from vastra_data import config


@dataclass
class Batch:
    rows: list            # dicts, or {"_unparsed": "<text>"} for a line that is not JSON
    watermark: str        # saved in ops.watermarks only after the batch is handled


class FileSource:
    """inbox/<source>/*.jsonl. Watermark = {"file.jsonl": lines_done, ...}; new lines appended later are picked up."""

    def __init__(self, name: str):
        self.name = name
        self.folder = os.path.join(config.settings.inbox_dir, name)

    def batches(self, since: Optional[str]) -> Iterator[Batch]:
        done = json.loads(since) if since else {}
        size = config.settings.batch_size
        for path in sorted(glob.glob(os.path.join(self.folder, "*.jsonl"))):
            fname = os.path.basename(path)
            skip = done.get(fname, 0)
            rows, n = [], 0
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, start=1):
                    if n <= skip or not line.strip():
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        rows.append({"_unparsed": line.strip()[:2000]})
                    if len(rows) >= size:
                        done[fname] = n
                        yield Batch(rows, json.dumps(done))
                        rows = []
            if n > skip:
                done[fname] = n
                yield Batch(rows, json.dumps(done))


class ApiOutboxSource:
    """Pages through the Week 1 integration feed. Works with v1.0 (no envelope fields) and v1.1 (envelope fields)."""
    name = "api-v1"

    def __init__(self, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(base_url=config.settings.api_url, timeout=10)

    @staticmethod
    def to_envelope(e: dict) -> dict:
        return {
            "event_id": e.get("event_uid") or f"api-v1:{e['event_id']}",
            "event_type": e.get("event_type") or e["type"],
            "occurred_at": e.get("occurred_at") or e["at"],
            "source": e.get("source") or "api-v1",
            "actor": e.get("actor") or "system",
            "payload": e["payload"],
        }

    def batches(self, since: Optional[str]) -> Iterator[Batch]:
        after = int(since or 0)
        while True:
            r = self.client.get("/v1/integration/events", params={"after_id": after, "limit": 500},
                                headers={"X-API-Key": config.settings.api_key})
            r.raise_for_status()
            body = r.json()
            if not body["events"]:
                return
            after = body["next_after_id"]
            yield Batch([self.to_envelope(e) for e in body["events"]], str(after))


class LegacySource:
    """The legacy stock copy: exported history files in inbox/legacy/ (if any), then the live legacy database read
    read-only (mode=ro), as the Week 1 anti-corruption layer does. Watermark = {"files": {...}, "db": copied_at}."""
    name = "legacy"

    def batches(self, since: Optional[str]) -> Iterator[Batch]:
        mark = json.loads(since) if since else {"files": None, "db": ""}
        for b in FileSource("legacy").batches(mark["files"]):
            mark["files"] = b.watermark
            yield Batch(b.rows, json.dumps(mark))
        path = os.path.abspath(config.settings.legacy_db_path)
        if not os.path.exists(path):
            return                     # no legacy lab on this machine: history files only
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute("SELECT sku, qty, copied_at FROM stock_copy WHERE copied_at > ? ORDER BY copied_at",
                               (mark["db"],)).fetchall()
        finally:
            con.close()
        if not rows:
            return
        out = [{"event_id": f"legacy:stock_copy:{r['sku']}:{r['copied_at']}", "event_type": "stock.counted",
                "occurred_at": r["copied_at"], "source": "legacy", "actor": "system",
                "payload": {"sku": r["sku"], "location_id": "NETWORK", "available": r["qty"], "on_order": 0}}
               for r in rows]
        mark["db"] = max(r["copied_at"] for r in rows)
        yield Batch(out, json.dumps(mark))


def get(name: str, client=None):
    if name == "api-v1":
        return ApiOutboxSource(client)
    if name == "legacy":
        return LegacySource()
    if name in ("pos", "erp", "wms"):
        return FileSource(name)
    raise ValueError(f"unknown source {name}")


def now_utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
