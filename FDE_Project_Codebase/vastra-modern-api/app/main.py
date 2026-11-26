from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles

from .db import connect, init_db

PARTNERS_URL = os.getenv("VASTRA_PARTNERS_URL", "http://localhost:8100")
LEGACY_DB = os.getenv("VASTRA_LEGACY_DB")


class CheckoutIn(BaseModel):
    sku: str
    qty: int = Field(default=1, ge=1, le=100)
    coupon_pct: int = Field(default=0, ge=0, le=100)
    member: bool = False
    pincode: str = "560001"
    channel: str = "web"
    store_id: str | None = None


class StockEventIn(BaseModel):
    event_id: str
    sku: str
    delta: int
    reason: str


class OverrideIn(BaseModel):
    sku: str
    store_id: str
    price: int = Field(gt=0)
    reason: str = Field(min_length=3)
    expires_at: str


app = FastAPI(title="Vastra API v1", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("VASTRA_CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount(
    "/ui",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="api-test-ui",
)

ROLES = {
    "vastra-web": {"catalogue:read", "price:read", "checkout:create"},
    "vastra-app": {"catalogue:read", "price:read", "checkout:create"},
    "vastra-pos": {"catalogue:read", "price:read", "stock:write", "override:write", "audit:read", "checkout:create"},
    "vastra-warehouse": {"stock:write"},
    "vastra-erp": {"events:read"},
}


@app.on_event("startup")
def startup() -> None:
    init_db(LEGACY_DB)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = request_id
    started = dt.datetime.now(dt.timezone.utc)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    elapsed = (dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000
    response.headers["Server-Timing"] = f"vastra;dur={elapsed:.1f}"
    return response


def require_key(request: Request, x_api_key: str | None, required_role: str) -> tuple[str, str]:
    if not x_api_key or x_api_key not in ROLES:
        raise HTTPException(status_code=401, detail="valid X-API-Key required")
    if required_role not in ROLES[x_api_key]:
        raise HTTPException(status_code=403, detail=f"required role: {required_role}")
    return x_api_key, request.headers.get("X-User", "anonymous")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def product(sku: str) -> dict:
    con = connect()
    row = con.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    con.close()
    if row is None:
        raise HTTPException(404, "unknown sku")
    return dict(row)


def quote_price(p: dict, coupon_pct: int, member: bool, store_id: str | None = None) -> dict:
    unit = p["base_price"]
    breakdown = []
    sale_active = False
    if p["sale_price"] and p["sale_ends_utc"]:
        try:
            sale_end = dt.datetime.fromisoformat(p["sale_ends_utc"])
            if sale_end.tzinfo is None:
                sale_end = sale_end.replace(tzinfo=dt.timezone.utc)
            sale_active = dt.datetime.now(dt.timezone.utc) < sale_end
        except ValueError:
            sale_active = False
    if sale_active:
        unit = p["sale_price"]
        breakdown.append({"step": "sale", "amount": unit})

    if coupon_pct:
        unit = round(unit * (100 - coupon_pct) / 100)
        breakdown.append({"step": "coupon", "pct": coupon_pct, "amount": unit})
    if member:
        unit = round(unit * (100 - p["member_discount_pct"]) / 100)
        breakdown.append({"step": "member", "pct": p["member_discount_pct"], "amount": unit})

    if store_id:
        con = connect()
        override = con.execute(
            """
            SELECT price, expires_at FROM price_overrides
            WHERE sku=? AND store_id=? AND expires_at > ?
            ORDER BY id DESC LIMIT 1
            """,
            (p["sku"], store_id, now_iso()),
        ).fetchone()
        con.close()
        if override:
            unit = override["price"]
            breakdown.append({"step": "store_override", "amount": unit})

    return {
        "sku": p["sku"],
        "unit_price": unit,
        "currency": "INR",
        "breakdown": breakdown,
        "policy_version": "v1",
    }


@app.get("/v1/health")
def health() -> dict:
    return {"status": "ok", "service": "vastra-modern-api", "version": "1.0"}


@app.get("/v1/catalogue/{sku}")
def catalogue(
    sku: str,
    request: Request,
    response: Response,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    require_key(request, x_api_key, "catalogue:read")
    p = product(sku)
    payload = {k: p[k] for k in ("sku", "name", "category", "base_price", "sale_price", "sale_ends_utc")}
    etag = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    response.headers["ETag"] = f'"{etag}"'
    response.headers["Cache-Control"] = "public, max-age=60"
    return payload


@app.get("/v1/prices/{sku}")
def prices(
    sku: str,
    request: Request,
    coupon_pct: int = 0,
    member: bool = False,
    qty: int = 1,
    store_id: str | None = None,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    require_key(request, x_api_key, "price:read")
    quote = quote_price(product(sku), coupon_pct, member, store_id)
    quote["qty"] = qty
    quote["total"] = quote["unit_price"] * qty
    return quote


@app.post("/v1/stock/events")
def stock_event(
    event: StockEventIn,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _, actor = require_key(request, x_api_key, "stock:write")
    con = connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute("SELECT event_id FROM stock_events WHERE event_id=?", (event.event_id,)).fetchone()
        if existing:
            con.rollback()
            return {"status": "duplicate", "event_id": event.event_id}
        row = con.execute("SELECT qty FROM stock WHERE sku=?", (event.sku,)).fetchone()
        if row is None:
            raise HTTPException(404, "unknown sku")
        qty = row["qty"] + event.delta
        if qty < 0:
            raise HTTPException(409, "stock cannot be negative")
        created = now_iso()
        con.execute("UPDATE stock SET qty=?, updated_at=? WHERE sku=?", (qty, created, event.sku))
        con.execute(
            "INSERT INTO stock_events (event_id, sku, delta, reason, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (event.event_id, event.sku, event.delta, event.reason, actor, created),
        )
        outbox_id = str(uuid.uuid4())
        con.execute(
            "INSERT INTO outbox (event_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (
                outbox_id,
                "stock.changed",
                json.dumps({"sku": event.sku, "qty": qty, "source_event_id": event.event_id}),
                created,
            ),
        )
        con.commit()
    finally:
        con.close()
    return {"status": "accepted", "event_id": event.event_id, "sku": event.sku, "qty": qty}


@app.post("/v1/overrides")
def create_override(
    override: OverrideIn,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    _, actor = require_key(request, x_api_key, "override:write")
    product(override.sku)
    try:
        expires = dt.datetime.fromisoformat(override.expires_at)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=dt.timezone.utc)
        if expires <= dt.datetime.now(dt.timezone.utc):
            raise HTTPException(422, "expires_at must be in the future")
    except ValueError:
        raise HTTPException(422, "expires_at must be ISO 8601")
    con = connect()
    created = now_iso()
    cur = con.execute(
        """
        INSERT INTO price_overrides (sku, store_id, price, actor, reason, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (override.sku, override.store_id, override.price, actor, override.reason, expires.isoformat(), created),
    )
    con.execute(
        "INSERT INTO audit_log (actor, action, sku, store_id, reason, request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (actor, "price.override", override.sku, override.store_id, override.reason, request.state.request_id, created),
    )
    con.commit()
    con.close()
    return {"id": cur.lastrowid, "sku": override.sku, "store_id": override.store_id, "actor": actor}


@app.get("/v1/audit/overrides")
def override_audit(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    require_key(request, x_api_key, "audit:read")
    con = connect()
    rows = con.execute(
        "SELECT id, actor, action, sku, store_id, reason, request_id, created_at FROM audit_log ORDER BY id DESC"
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.get("/v1/events")
def events(
    request: Request,
    limit: int = 100,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    require_key(request, x_api_key, "events:read")
    con = connect()
    rows = con.execute(
        "SELECT event_id, event_type, payload, published, created_at FROM outbox ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 1000)),),
    ).fetchall()
    con.close()
    return [{"event_id": r["event_id"], "event_type": r["event_type"], "payload": json.loads(r["payload"]),
             "published": bool(r["published"]), "created_at": r["created_at"]} for r in rows]


async def call_courier(pincode: str) -> dict:
    try:
        async with httpx.AsyncClient(base_url=PARTNERS_URL, timeout=httpx.Timeout(0.8)) as client:
            r = await client.get("/courier/promise", params={"pincode": pincode})
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, httpx.TimeoutException):
        return {"pincode": pincode, "delivery_days": 5, "cod_available": False, "fallback": True}


@app.post("/v1/checkout")
async def checkout(
    c: CheckoutIn,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    require_key(request, x_api_key, "checkout:create")
    if len(idempotency_key.strip()) < 8:
        raise HTTPException(400, "Idempotency-Key must be at least 8 characters")

    request_hash = hashlib.sha256(
        json.dumps(c.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    p = product(c.sku)
    quote = quote_price(p, c.coupon_pct, c.member, c.store_id)
    total = quote["unit_price"] * c.qty

    con = connect()
    con.execute("BEGIN IMMEDIATE")
    existing = con.execute(
        "SELECT request_hash, status, response_json FROM checkout_attempts WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    if existing is None:
        legacy_order = con.execute(
            "SELECT response_json FROM orders WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if legacy_order:
            con.close()
            return json.loads(legacy_order["response_json"])
    if existing:
        con.close()
        if existing["request_hash"] != request_hash:
            raise HTTPException(409, "Idempotency-Key was already used for a different request")
        if existing["status"] == "in_progress":
            raise HTTPException(409, "checkout is already in progress; reconcile before retrying")
        if existing["status"] == "completed" and existing["response_json"]:
            return json.loads(existing["response_json"])
        raise HTTPException(500, "checkout attempt has an invalid persisted state")

    stock = con.execute("SELECT qty FROM stock WHERE sku=?", (c.sku,)).fetchone()
    if stock is None or stock["qty"] < c.qty:
        con.close()
        raise HTTPException(409, "out of stock")
    created = now_iso()
    con.execute(
        "INSERT INTO checkout_attempts "
        "(idempotency_key, request_hash, status, created_at) VALUES (?, ?, 'in_progress', ?)",
        (idempotency_key, request_hash, created),
    )
    remaining = stock["qty"] - c.qty
    con.execute("UPDATE stock SET qty=?, updated_at=? WHERE sku=?", (remaining, now_iso(), c.sku))

    reservation_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO reservations (reservation_id, sku, qty, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (reservation_id, c.sku, c.qty, "reserved", created),
    )
    con.commit()
    con.close()

    courier = await call_courier(c.pincode)

    try:
        async with httpx.AsyncClient(base_url=PARTNERS_URL, timeout=httpx.Timeout(3.0)) as client:
            pay = await client.post(
                "/payment/charge",
                json={"order_ref": f"ORD-{reservation_id[:12]}", "amount": total},
                headers={"Idempotency-Key": idempotency_key},
            )
            pay.raise_for_status()
            payment = pay.json()
    except (httpx.HTTPError, httpx.TimeoutException):
        payment = {"status": "failed", "reason": "gateway_timeout"}

    order_ref = f"ORD-{reservation_id[:12]}"
    status = "paid" if payment.get("status") == "success" else "payment_failed"
    response = {
        "order_ref": order_ref,
        "status": status,
        "total": total,
        "delivery_days": courier["delivery_days"],
        "courier_fallback": bool(courier.get("fallback", False)),
        "stock_remaining": remaining,
        "price": quote,
        "reservation_id": reservation_id,
    }

    con = connect()
    con.execute("BEGIN IMMEDIATE")
    if status != "paid":
        stock = con.execute("SELECT qty FROM stock WHERE sku=?", (c.sku,)).fetchone()
        con.execute(
            "UPDATE stock SET qty=?, updated_at=? WHERE sku=?",
            (stock["qty"] + c.qty, now_iso(), c.sku),
        )
        con.execute("UPDATE reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
        response["stock_remaining"] = stock["qty"] + c.qty
    else:
        con.execute("UPDATE reservations SET status='captured' WHERE reservation_id=?", (reservation_id,))

    con.execute(
        "INSERT INTO orders (order_ref, idempotency_key, created_at, channel, sku, qty, total, status, response_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_ref, idempotency_key, created, c.channel, c.sku, c.qty, total, status, json.dumps(response)),
    )
    con.execute(
        "UPDATE checkout_attempts SET status='completed', response_json=? WHERE idempotency_key=?",
        (json.dumps(response), idempotency_key),
    )
    con.execute(
        "INSERT INTO checkout_payments (order_ref, idempotency_key, charge_id, status, amount, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (order_ref, idempotency_key, str(payment.get("charge_id", "")), payment.get("status", "failed"), total, now_iso()),
    )
    if status == "paid":
        con.execute(
            "INSERT INTO outbox (event_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                "order.created",
                json.dumps({"order_ref": order_ref, "sku": c.sku, "qty": c.qty, "total": total, "channel": c.channel}),
                now_iso(),
            ),
        )
    con.commit()
    con.close()
    return response
