# Vastra API v1 — Modernised Legacy Estate

Vastra API v1 is a FastAPI service that incrementally modernises the synthetic Vastra ecommerce
legacy estate. It runs beside the 2014 monolith rather than replacing it. The API gives web, app,
POS, warehouse and ERP/WMS callers a shared interface for catalogue, pricing, stock, checkout,
auditing and integration events.

This repository is a local learning/demo implementation. It uses SQLite and demo API keys; the
production architecture described in the companion documents calls for PostgreSQL and
OAuth2/OIDC.

## What is included

| Component | Location | Purpose |
|---|---|---|
| Legacy monolith | `vastra-legacy-lab/app/monolith.py` | Original server-rendered product page, private app pricing endpoint and sequential checkout |
| Legacy test page | `vastra-legacy-lab/app/static/index.html` | Browser controls for the old product, mobile price and checkout routes |
| Partner stubs | `vastra-legacy-lab/app/partners/stubs.py` | Local courier and payment APIs used by both implementations |
| Modern API | `vastra-modern-api/app/main.py` | Versioned REST endpoints and business rules |
| Modern API database | `vastra-modern-api/app/db.py` | API-owned schema and optional read-only bootstrap from the legacy SQLite database |
| Modern API test page | `vastra-modern-api/app/static/index.html` | Read-only smoke checks and manual API requests |
| API tests | `vastra-modern-api/tests/test_*.py` | Focused, independently runnable programs for each API behavior |
| Legacy tests | `vastra-legacy-lab/tests/test_legacy_characterisation.py` | Tests describing legacy behavior and its known defects |
| Migration plan | `vastra-modern-api/MIGRATION.md` | Proposed 12-week incremental migration with rollback checkpoints |
| Legacy comparison | `vastra-modern-api/LEGACY-VS-MODERN.md` | Feature comparison and representative old/new code snippets |
| Demo client | `vastra-modern-api/demo.py` | Script that requests a quote and repeats checkout with the same idempotency key |

## Architecture

```mermaid
flowchart LR
    Channels["Web / App / POS / Warehouse / ERP"]
    Gateway["NGINX gateway :8080"]
    Legacy["Legacy monolith :8000"]
    API["Vastra API v1 :8200"]
    Partners["Partner stubs :8100"]
    LegacyDB["Legacy SQLite database"]
    ModernDB["API-owned SQLite database"]
    ERP["ERP / WMS consumer"]

    Channels --> Gateway
    Gateway -->|"/legacy/*"| Legacy
    Gateway -->|"/v1/*"| API
    Channels -->|local direct access| Legacy
    Channels -->|local direct access| API
    Legacy --> Partners
    Legacy --> LegacyDB
    API --> Partners
    API -->|read-only initial bootstrap| LegacyDB
    API --> ModernDB
    ModernDB -->|outbox via GET /v1/events| ERP
```

In the container setup, NGINX is the local entry point on port 8080. It routes `/legacy/*` to the
legacy monolith, `/v1/*` to the modern API, and exposes the modern workbench and docs. The Compose
file at the repository root builds and runs all services with persistent SQLite volumes. For
direct local development without containers, the services can also be run on ports 8000, 8100 and
8200.

## Functional flow

Every modern request receives an `X-Request-ID` and `Server-Timing` response header. The API checks
the caller's demo API key and its role before running the endpoint. Catalogue and price requests
read API-owned product data. Stock events, overrides and checkout persist changes in the modern
database, and significant changes create outbox events for downstream consumers.

```mermaid
flowchart TD
    Caller["Channel client"] --> API["Vastra API v1"]
    API --> Context["Request ID and timing middleware"]
    Context --> Auth{"API key and role allowed?"}
    Auth -- No --> Deny["401 or 403"]
    Auth -- Yes --> Route{"Endpoint"}

    Route -- "GET catalogue" --> Catalogue["Read product"]
    Catalogue --> Cache["ETag and cache headers"]
    Route -- "GET prices" --> Pricing["Shared pricing rules"]
    Pricing --> Quote["Quote and calculation breakdown"]

    Route -- "POST stock event" --> Dedup{"Event ID already processed?"}
    Dedup -- Yes --> Duplicate["Return duplicate result"]
    Dedup -- No --> UpdateStock["Transactionally update stock and record movement"]
    UpdateStock --> StockEvent["Write stock.changed to outbox"]

    Route -- "POST override" --> Override["Validate store, reason and expiry"]
    Override --> Audit["Save override and audit entry"]
    Route -- "GET audit / events" --> ReadRecords["Return audit records or outbox events"]

    Route -- "POST checkout" --> Idempotency{"Key already claimed?"}
    Idempotency -- Completed / same payload --> Original["Return saved response"]
    Idempotency -- In progress --> InProgress["409; reconcile before retry"]
    Idempotency -- Different payload --> Mismatch["409; key belongs to another request"]
    Idempotency -- New key --> Reserve{"Claim key and reserve stock atomically"}
    Reserve -- "Insufficient" --> Conflict["409 out of stock"]
    Reserve -- Available --> Courier["Courier request, 0.8 s timeout"]
    Courier -- Timeout --> Fallback["Use estimated delivery fallback"]
    Courier -- Success --> Promise["Use courier promise"]
    Fallback --> Payment["Payment request, 3 s timeout"]
    Promise --> Payment
    Payment --> Paid{"Payment success?"}
    Paid -- Yes --> Capture["Capture reservation"]
    Capture --> SaveOrder["Save order and payment"]
    SaveOrder --> OrderEvent["Write order.created to outbox"]
    Paid -- No --> Release["Release reserved stock"]
    Release --> SaveFailure["Save failed payment result"]
```

## Functional areas

### Catalogue

`GET /v1/catalogue/{sku}` reads the product and returns an `ETag` and `Cache-Control` header.
Unknown SKUs return 404.

### Centralised pricing

`GET /v1/prices/{sku}` applies one policy to all callers:

1. Start with the base price.
2. Apply an active sale price using its UTC end time.
3. Apply the coupon percentage.
4. Apply the member discount.
5. Apply an active store-specific override when `store_id` is provided.

The response includes the unit price, quantity, total, currency, policy version and price
breakdown. The order of operations is the API's current lab policy and should be confirmed by
pricing stakeholders before production use.

#### Design example: same basket, one price

The design document's section 2.3 uses a ₹2,000 base price, an active ₹1,600 sale price, a 10%
coupon and a 10% member discount:

| Channel | Legacy result | Modern API result |
|---|---:|---:|
| Website | ₹1,296 | ₹1,296 |
| Mobile app | ₹1,440 | ₹1,296 |
| Store POS | ₹1,600 | ₹1,296 |

The modern rule applies the coupon to the sale price, then applies the member discount:
`₹1,600 × 0.90 = ₹1,440`, followed by `₹1,440 × 0.90 = ₹1,296`. The API response breakdown
should therefore show sale ₹1,600, coupon result ₹1,440, and member result ₹1,296.

Run the characterization and API tests from their respective project directories:

```powershell
cd FDE_Project_Codebase\vastra-legacy-lab
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_legacy_characterisation.py -k "three_pricing_copies or sale_ends_later"

cd ..\vastra-modern-api
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_pricing_policy.py -k documented_price
```

The legacy test asserts the three exact channel totals and the app's late sale-expiry defect. The
modern parameterized test requests the same quote with the web, app and POS demo keys and verifies
all three return ₹1,296 with the expected breakdown. Its fixed product fixture uses the document's
prices and a future sale expiry so the test is repeatable. The locally seeded `SKU00003` can have
different values; use the tests—not that seeded SKU—to prove the exact example.

### Stock ledger

`POST /v1/stock/events` accepts an `event_id`, SKU, quantity delta and reason. Repeated event IDs
are ignored; an update that would make stock negative returns 409. Stock and the corresponding
outbox event are committed together.

### Checkout

`POST /v1/checkout` requires an `Idempotency-Key`. The API persists a request claim before
contacting partners, reserves live API-ledger stock, requests a courier promise, and then requests
payment. A completed retry with the same key and payload returns the stored result; concurrent
retries receive `409`, and reusing a key with a different payload also receives `409`. If payment
fails, the API releases the stock reservation. Successful orders create an `order.created` outbox
event.

An attempt left `in_progress` by a process crash is deliberately not replayed: the API returns
`409` and requires the operator to reconcile the reservation and payment with the partner before
taking action. This avoids an automatic duplicate charge when payment outcome is ambiguous, but
there is no reconciliation workflow yet. Production recovery requires a provider contract for
idempotent payment retries or payment-status lookup.

Checkout is state-changing and calls the payment partner. Only exercise it against local test
services. Courier and payment calls are time-bounded, but currently happen sequentially.

### Price overrides and audit

`POST /v1/overrides` creates a store-scoped override with a reason and future expiry, and writes an
audit record containing the actor and request ID. `GET /v1/audit/overrides` reads those records.
The endpoint takes the acting user from the `X-User` header; this is demo identity, not proof of
authentication in production.

### Outbox

Stock changes and successful orders create events in the `outbox` table. `GET /v1/events` exposes
them for ERP/WMS consumers. This lab exposes a pull endpoint; it does not yet include a background
publisher or a message broker.

## Local setup

The following commands are for Windows PowerShell. Run each service in its own terminal, starting
from the repository root.

### 1. Create a shared development environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r FDE_Project_Codebase\vastra-modern-api\requirements.txt
```

The modern requirements include the shared FastAPI, Uvicorn, HTTPX and pytest dependencies used
for this local lab, plus `python-dotenv` so Uvicorn can load the repository-root `.env` file.

The local `.env` contains settings for both the partner stub and modern API. It is ignored by Git;
the matching `.env.example` is the template to copy when setting up another checkout:

```powershell
Copy-Item .env.example .env
```

### 2. Create and seed the legacy database

Run once from the legacy lab directory:

```powershell
cd FDE_Project_Codebase\vastra-legacy-lab
..\..\.venv\Scripts\python.exe -m app.db
```

This writes `data/vastra_legacy.db`. Seeding re-creates the legacy tables and sample products; do
not run it against a database whose contents you need to preserve.

### 3. Start the partner stubs

In a new terminal from the legacy lab directory:

```powershell
cd FDE_Project_Codebase\vastra-legacy-lab
$env:LATENCY_SCALE = "0.01"
$env:PAYMENT_FAIL_RATE = "0"
..\..\.venv\Scripts\python.exe -m uvicorn app.partners.stubs:app --host 127.0.0.1 --port 8100 --env-file ..\..\.env
```

The lower latency and zero failure rate are convenient for a local demonstration; remove or
adjust these variables to exercise slower or failing partner responses.

### 4. Start the legacy monolith (optional)

In another terminal:

```powershell
cd FDE_Project_Codebase\vastra-legacy-lab
..\..\.venv\Scripts\python.exe -m uvicorn app.monolith:app --host 127.0.0.1 --port 8000 --env-file ..\..\.env
```

Legacy pages:

- Test page: `http://127.0.0.1:8000/test-ui/`
- Service endpoint list: `http://127.0.0.1:8000/`
- OpenAPI docs: `http://127.0.0.1:8000/docs`

### Container deployment

From the repository root, start the full local stack:

```powershell
docker --context default compose up --build
```

The NGINX gateway is available on port 8080:

- API test workbench: `http://127.0.0.1:8080/ui/`
- OpenAPI docs: `http://127.0.0.1:8080/docs`
- API health: `http://127.0.0.1:8080/v1/health`
- Legacy test page: `http://127.0.0.1:8080/legacy/test-ui/`

Compose health-checks the services before starting the gateway. The SQLite databases are stored in
named Docker volumes. `docker --context default compose down` stops and removes containers but
retains those volumes. Use `docker --context default compose down -v` only when intentionally
deleting the local container database state. The explicit `default` context avoids a Docker Desktop
engine error observed in this development environment; omit `--context default` if your active
Docker context is healthy.

### Direct local development (without containers)

To run the modern API directly, start the partner stub separately and run Uvicorn on port 8200:

```powershell
cd FDE_Project_Codebase\vastra-modern-api
..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8200 --env-file ..\..\.env
```

Direct local pages are at `http://127.0.0.1:8200/ui/` and `http://127.0.0.1:8200/docs`.

If `VASTRA_LEGACY_DB` is set, startup copies products and initial `stock_copy` quantities into the
API database using a read-only connection to the legacy database. Existing API rows are preserved
by `INSERT OR IGNORE`; re-seeding the legacy database will not automatically refresh API data.

## API reference

All modern endpoints are under `/v1`. Except for health, requests require `X-API-Key`.

| Method and path | Role | Purpose |
|---|---|---|
| `GET /v1/health` | None | Health/status check |
| `GET /v1/catalogue/{sku}` | `catalogue:read` | Product read with cache headers |
| `GET /v1/prices/{sku}` | `price:read` | Price quote |
| `POST /v1/stock/events` | `stock:write` | Apply an idempotent stock movement |
| `POST /v1/overrides` | `override:write` | Create an audited store price override |
| `GET /v1/audit/overrides` | `audit:read` | Read override audit history |
| `GET /v1/events` | `events:read` | Read outbox events |
| `POST /v1/checkout` | `checkout:create` | Reserve stock and place a checkout |

Demo API keys and their role grants:

| Key | Roles |
|---|---|
| `vastra-web` | Catalogue read, price read, checkout create |
| `vastra-app` | Catalogue read, price read, checkout create |
| `vastra-pos` | Catalogue read, price read, stock write, override write, audit read, checkout create |
| `vastra-warehouse` | Stock write |
| `vastra-erp` | Events read |

These are hard-coded, publicly known demo values in `app/main.py`; they are not production
credentials and should not be exposed outside a trusted local environment.

## Examples

### Read product and quote

```powershell
curl "http://127.0.0.1:8200/v1/catalogue/SKU00003" `
  -H "X-API-Key: vastra-web"

curl "http://127.0.0.1:8200/v1/prices/SKU00003?coupon_pct=10&member=true&qty=2" `
  -H "X-API-Key: vastra-web"
```

### Post a stock movement

```powershell
curl -X POST "http://127.0.0.1:8200/v1/stock/events" `
  -H "X-API-Key: vastra-warehouse" `
  -H "X-User: warehouse-operator" `
  -H "content-type: application/json" `
  -d '{\"event_id\":\"delivery-001\",\"sku\":\"SKU00003\",\"delta\":5,\"reason\":\"warehouse.received\"}'
```

### Create an audited override

```powershell
curl -X POST "http://127.0.0.1:8200/v1/overrides" `
  -H "X-API-Key: vastra-pos" `
  -H "X-User: manager-07" `
  -H "content-type: application/json" `
  -d '{\"sku\":\"SKU00003\",\"store_id\":\"ST001\",\"price\":999,\"reason\":\"Local promotion\",\"expires_at\":\"2099-12-31T23:59:59+00:00\"}'
```

### Place checkout

```powershell
curl -X POST "http://127.0.0.1:8200/v1/checkout" `
  -H "X-API-Key: vastra-web" `
  -H "Idempotency-Key: checkout-demo-001" `
  -H "content-type: application/json" `
  -d '{\"sku\":\"SKU00003\",\"qty\":1,\"coupon_pct\":10,\"member\":true,\"pincode\":\"560001\"}'
```

## Tests and demo

Each modern test file is a focused pytest program for a specific modernization fix:

| Test program | Behavior covered |
|---|---|
| `test_service_health.py` | Service health endpoint |
| `test_api_workbench.py` | Browser workbench is served |
| `test_catalogue_api.py` | Catalogue authentication, product response and caching headers |
| `test_pricing_policy.py` | One price and calculation breakdown across web, app and POS |
| `test_stock_ledger.py` | Live stock changes, duplicate event protection, negative-stock rejection and roles |
| `test_price_overrides.py` | Named override auditing, store scope and role protection |
| `test_outbox_events.py` | Stock integration events and ERP-only feed access |
| `test_checkout.py` | Required idempotency key, concurrent and restart retry behavior, key/payload mismatch, oversell rejection, payment failure stock release, courier fallback and order outbox event |

The tests use `tests/conftest.py` to create a fresh temporary SQLite database and a predictable
SKU00003 fixture for every test. They call the application through FastAPI's test client and mock
partner calls where needed; the three local services do not need to be running to execute the
modern API test suite.

Run any one program on its own from the modern API directory:

```powershell
cd FDE_Project_Codebase\vastra-modern-api
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_service_health.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_api_workbench.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_catalogue_api.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_pricing_policy.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_stock_ledger.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_price_overrides.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_outbox_events.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_checkout.py
```

Run all modern test programs together (currently **21 tests**):

```powershell
..\..\.venv\Scripts\python.exe -m pytest -q tests
```

The legacy characterization suite has its own fixture and tests the original application
behavior, including intentional defects. Run it from the legacy lab directory:

```powershell
cd FDE_Project_Codebase\vastra-legacy-lab
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_legacy_characterisation.py
```

The legacy tests use `TestClient` and an isolated temporary database. They mock the partner stub
for checkout tests, so ports 8000 and 8100 do not need to be running. The suite currently has
**8 tests**.

The browser pages are useful for manual comparison:

- Legacy test UI: `http://127.0.0.1:8000/test-ui/`
- Modern API workbench: `http://127.0.0.1:8200/ui/`

The modern UI smoke suite checks health, catalogue and pricing without writes. Stock events and
overrides modify the API database. Checkout reduces ledger stock and calls payment. The legacy
checkout can charge on repeated requests because it does not implement idempotency; its UI asks
for confirmation before sending the request.

The demonstration client can be run with:

```powershell
cd FDE_Project_Codebase\vastra-modern-api
..\..\.venv\Scripts\python.exe demo.py
```

It uses a fixed demonstration idempotency key. The first call may create an order; subsequent
calls using the same key return the previously completed order.

## Current limitations and production follow-up

- **Authentication:** API keys and `X-User` are demo-only; use managed secrets and OAuth2/OIDC.
- **Database:** SQLite is for the local lab; production reservations require PostgreSQL and tested
  transaction/locking behavior.
- **Checkout recovery:** The API persists in-progress keys and rejects retries after a crash for
  manual reconciliation. It does not yet provide an operator reconciliation workflow for stock
  reservations or an ambiguous payment outcome.
- **Payment provider:** The idempotency key is forwarded, but the included payment stub ignores
  it. End-to-end duplicate-charge safety requires provider support and reconciliation.
- **Partner order:** Courier and payment calls are sequential, not parallel. Each has a timeout,
  but the overall endpoint duration is the combined duration.
- **Outbox delivery:** Events can be pulled from the endpoint; no queue publisher, consumer
  acknowledgement, or published-state workflow is included.
- **Stock ownership:** The legacy bootstrap is an initial copy, not continuous legacy-to-modern
  replication. Production cutover needs reconciliation and a defined single-writer point.
- **Pricing policy:** The order of coupon and member discounts is a demo policy and requires
  business approval.
- **Gateway:** The local container stack runs NGINX on port 8080. Direct development can bypass it
  and call the services on their individual ports.

## Related documents

- [Legacy vs Modern API](./LEGACY-VS-MODERN.md) — quick comparison and code snippets.
- [Migration plan](./MIGRATION.md) — proposed rollout and rollback stages.
- [Session handoff](./SESSION-HANDOFF.md) — current implementation state and continuation checklist.
