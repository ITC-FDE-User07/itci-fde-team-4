"""Vastra ecommerce monolith (2014), simplified for the lab.

Run: uvicorn app.monolith:app --port 8000   (start the partner stubs on port 8100 first)

What participants should find (evidence):
- R1: product pages are rendered on the server on every request; no caching headers.
- R2/W2: checkout calls cart, price, promotion, stock and courier one after another, with no timeouts,
  then charges the card with no idempotency key (a retry charges twice).
- R4: the app uses a private endpoint (/app-api/v0/price) with a different copy of the pricing rules.
- R5: stock comes from stock_copy, refreshed every 4 hours by a batch job.
- R15a: one shared database login (config.ini) for everything.
"""
import time, datetime as dt, uuid
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from app import db, pricing_web, app_api

app = FastAPI(title="Vastra legacy monolith")
_partners = None


def partners():
    global _partners
    if _partners is None:
        _partners = httpx.Client(base_url=db.CFG["partners"]["base_url"], timeout=None)   # no timeout (R2)
    return _partners


def product(sku):
    con = db.connect()
    row = con.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    con.close()
    if not row:
        raise HTTPException(404, "unknown sku")
    return dict(row)


@app.get("/product/{sku}", response_class=HTMLResponse)
def product_page(sku: str):
    p = product(sku)
    time.sleep(0.25)   # template rendering and image work on every request (R1)
    return f"<html><body><h1>{p['name']}</h1><p>Price: Rs {pricing_web.price(p)}</p></body></html>"


@app.get("/app-api/v0/price/{sku}")
def app_price(sku: str, coupon_pct: int = 0, member: bool = False):
    """Private endpoint for the mobile app only: no contract, no versioning policy (R4)."""
    return {"sku": sku, "price": app_api.price(product(sku), coupon_pct, member)}


class CheckoutIn(BaseModel):
    sku: str
    qty: int = 1
    coupon_pct: int = 0
    member: bool = False
    pincode: str = "560001"
    channel: str = "web"


@app.post("/checkout")
def checkout(c: CheckoutIn):
    trace, t0 = {}, time.perf_counter()

    def step(name, start):
        trace[name] = round((time.perf_counter() - start) * 1000)

    s = time.perf_counter(); time.sleep(0.3); step("w2_1_cart_ms", s)                       # W2.1 cart reload
    s = time.perf_counter(); p = product(c.sku); unit = pricing_web.price(p, 0, c.member); step("w2_2_price_ms", s)
    s = time.perf_counter(); unit = pricing_web.price(p, c.coupon_pct, c.member); step("w2_3_promo_ms", s)
    s = time.perf_counter()                                                                   # W2.4 stale stock copy
    con = db.connect()
    stock = con.execute("SELECT qty, copied_at FROM stock_copy WHERE sku=?", (c.sku,)).fetchone()
    step("w2_4_stock_ms", s)
    if stock is None or stock["qty"] < c.qty:
        con.close()
        raise HTTPException(409, "out of stock")
    s = time.perf_counter()
    promise = partners().get("/courier/promise", params={"pincode": c.pincode}).json()      # W2.5, no timeout
    step("w2_5_courier_ms", s)
    total = unit * c.qty
    order_ref = uuid.uuid4().hex[:10]
    pay = partners().post("/payment/charge", json={"order_ref": order_ref, "amount": total}).json()   # W2.6, no idempotency
    status = "paid" if pay["status"] == "success" else "payment_failed"
    con.execute("INSERT INTO orders (created_at, channel, sku, qty, total, status) VALUES (?,?,?,?,?,?)",
                (dt.datetime.now(dt.timezone.utc).isoformat(), c.channel, c.sku, c.qty, total, status))
    con.execute("INSERT INTO payments (order_ref, amount, status, at) VALUES (?,?,?,?)",
                (order_ref, total, pay["status"], dt.datetime.now(dt.timezone.utc).isoformat()))
    con.commit(); con.close()
    trace["w2_1_to_w2_5_total_ms"] = round((time.perf_counter() - t0) * 1000)
    return {"order_ref": order_ref, "status": status, "total": total, "delivery_days": promise["delivery_days"],
            "stock_copied_at": stock["copied_at"], "trace": trace}
