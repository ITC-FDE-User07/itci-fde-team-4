# Vastra: Legacy vs Modern API

## At a glance

The legacy lab models a 2014 ecommerce monolith. The modern service adds versioned APIs beside
it and lets callers move capability by capability. The legacy application is not rewritten or
changed by this API project.

| Area | Legacy estate | Vastra API v1 |
|---|---|---|
| Entry points | Server-rendered product page, private app price endpoint and `/checkout` | Versioned `/v1/...` REST endpoints behind the gateway |
| Pricing | Separate website, app and POS rules can return different totals | One pricing function returns the price and its calculation breakdown |
| Checkout | Sequential partner calls with no configured HTTP timeout | Requires an idempotency key, reserves API-owned stock, and bounds courier/payment requests with timeouts |
| Stock | Checkout checks a periodic `stock_copy` snapshot | Stock events update a separate ledger transactionally; checkout reserves from that ledger |
| Retries | The legacy payment call sends no idempotency key | The API returns the stored order for a completed request with the same key and forwards the key to payment |
| Overrides | Legacy POS has a shared override code with no actor audit trail | Overrides are store-scoped, expire, and record actor, reason, and request ID |
| Integrations | Orders are exported through a nightly batch | Order and stock changes create outbox events exposed through `/v1/events` |
| Database | One legacy database used by the monolith and jobs | API-owned SQLite tables; legacy product/stock data can be bootstrapped read-only |

## Code examples

### Pricing: channel-specific rules vs one quote function

The legacy website applies a coupon and then a member discount, while the app uses different
stacking and sale-time rules:

```python
# Legacy app/pricing_web.py
if coupon_pct:
    unit = unit * (100 - coupon_pct) / 100
if member:
    unit = unit * (100 - product["member_discount_pct"]) / 100
```

In the modern service, callers use the same pricing implementation. The API also returns a
breakdown so a channel can explain how the price was calculated:

```python
# Modern API: app/main.py
if sale_active:
    unit = p["sale_price"]
    breakdown.append({"step": "sale", "amount": unit})

if coupon_pct:
    unit = round(unit * (100 - coupon_pct) / 100)
    breakdown.append({"step": "coupon", "pct": coupon_pct, "amount": unit})

if member:
    unit = round(unit * (100 - p["member_discount_pct"]) / 100)
    breakdown.append({"step": "member", "pct": p["member_discount_pct"], "amount": unit})
```

### Checkout: stale stock and unbounded calls vs reservation and deadlines

The legacy checkout reads the periodically copied stock table and calls partners without a
configured timeout or payment idempotency key:

```python
# Legacy app/monolith.py
stock = con.execute(
    "SELECT qty, copied_at FROM stock_copy WHERE sku=?", (c.sku,)
).fetchone()
promise = partners().get(
    "/courier/promise", params={"pincode": c.pincode}
).json()
pay = partners().post(
    "/payment/charge", json={"order_ref": order_ref, "amount": total}
).json()
```

The modern checkout first checks for a completed request with the same idempotency key and
reserves stock in a database transaction:

```python
# Modern API: app/main.py
existing = con.execute(
    "SELECT response_json FROM orders WHERE idempotency_key=?",
    (idempotency_key,),
).fetchone()
if existing:
    return json.loads(existing["response_json"])

remaining = stock["qty"] - c.qty
con.execute(
    "UPDATE stock SET qty=?, updated_at=? WHERE sku=?",
    (remaining, now_iso(), c.sku),
)
```

Courier and payment requests use bounded timeouts. The idempotency key is forwarded to the
payment partner:

```python
# Modern API: app/main.py
httpx.AsyncClient(base_url=PARTNERS_URL, timeout=httpx.Timeout(0.8))

httpx.AsyncClient(base_url=PARTNERS_URL, timeout=httpx.Timeout(3.0))
# Payment request headers:
{"Idempotency-Key": idempotency_key}
```

### Stock: periodic copy vs event-driven ledger

Legacy checkout reads `stock_copy`; the new stock endpoint makes a deduplicated movement in the
API-owned ledger and writes a corresponding integration event:

```python
# Modern API: app/main.py
if existing:
    con.rollback()
    return {"status": "duplicate", "event_id": event.event_id}

qty = row["qty"] + event.delta
if qty < 0:
    raise HTTPException(409, "stock cannot be negative")

con.execute("UPDATE stock SET qty=?, updated_at=? WHERE sku=?", (qty, created, event.sku))
```

### Changes and integrations: shared override vs audit and outbox

The modern override endpoint stores the named actor and appends an audit record. Successful
orders and stock changes are also written to the outbox so integrations can read changes without
the nightly order export:

```python
# Modern API: app/main.py
con.execute(
    "INSERT INTO audit_log (actor, action, sku, store_id, reason, request_id, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)",
    (actor, "price.override", override.sku, override.store_id,
     override.reason, request.state.request_id, created),
)
```

## Important scope and reliability notes

- The API is a lab implementation using SQLite and named demo API keys. The README describes
  replacing these with PostgreSQL and OAuth2/OIDC for production.
- The legacy database bootstrap copies products and initial `stock_copy` quantities into the API
  database. Later stock changes belong to the API ledger; this is not live replication from the
  legacy database.
- The API replays a **completed** checkout response for a repeated idempotency key. A durable
  in-progress/idempotency state and recovery for a process failure between the payment call and
  order persistence are not implemented yet.
- The key is forwarded to the payment partner, but the repository's payment stub does not
  implement idempotency itself. End-to-end protection across ambiguous payment timeouts depends
  on the real payment provider honoring that key.
- The modern checkout applies deadlines, but its courier and payment calls are still sequential;
  it does not implement parallel fan-out.

See [README.md](./README.md) for run instructions, API key roles, examples, and the functional
flow diagram.
