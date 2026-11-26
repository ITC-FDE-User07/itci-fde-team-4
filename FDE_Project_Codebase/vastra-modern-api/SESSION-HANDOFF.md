# Vastra API v1 — Session Handoff

Updated: 2026-11-26

## Goal and current state

The workspace contains a runnable legacy ecommerce lab and a separate Vastra API v1 service.
Modern APIs, both browser test pages, tests, comparison notes and a migration plan have been
implemented. The legacy monolith and the modern API are intended to run beside each other so that
the old and new behavior can be compared. A root `docker-compose.yml` now builds and runs the
partner stub, legacy monolith, modern API, and NGINX gateway as a local container stack.

The comprehensive setup and architecture guide is in [README.md](./README.md).

## Local service addresses

When the local services are running:

| Service | Address |
|---|---|
| NGINX gateway | `http://127.0.0.1:8080` |
| Modern API workbench (through gateway) | `http://127.0.0.1:8080/ui/` |
| Modern API docs (through gateway) | `http://127.0.0.1:8080/docs` |
| Legacy test page (through gateway) | `http://127.0.0.1:8080/legacy/test-ui/` |
| Partner stubs | `http://127.0.0.1:8100` |
| Legacy monolith | `http://127.0.0.1:8000` |
| Legacy browser test page | `http://127.0.0.1:8000/test-ui/` |
| Modern API | `http://127.0.0.1:8200` |
| Modern API workbench | `http://127.0.0.1:8200/ui/` |
| Modern OpenAPI docs | `http://127.0.0.1:8200/docs` |

Start the container stack from the repository root with
`docker --context default compose up --build`; stop it with
`docker --context default compose down`. SQLite data is retained in named volumes.

## Implementation map

- `vastra-legacy-lab/app/monolith.py`: legacy endpoints and JSON landing route.
- `vastra-legacy-lab/app/static/index.html`: test page for legacy product page, app price, and checkout.
- `vastra-legacy-lab/tests/test_legacy_characterisation.py`: legacy characterization and UI checks.
- `vastra-modern-api/app/main.py`: modern API routes, pricing, access roles, stock movements, overrides, outbox, checkout.
- `vastra-modern-api/app/db.py`: API schema and initial read-only bootstrap from legacy SQLite.
- `vastra-modern-api/app/static/index.html`: modern browser test workbench and smoke tests.
- `docker-compose.yml`: local four-service container stack, health checks, and persistent database volumes.
- `vastra-legacy-lab/Dockerfile`, `vastra-modern-api/Dockerfile`: images for the legacy/partner services and modern API.
- `gateway/nginx.conf`: NGINX routing for legacy paths, versioned API paths, test UI, docs, and gateway health.
- `.env.example` and root `.gitignore`: local partner tuning template and exclusion of local environment/bytecode files.
- `vastra-modern-api/tests/conftest.py`: shared isolated product/database fixture for focused API tests.
- `vastra-modern-api/tests/test_*.py`: separate test programs for health, UI, catalogue, pricing, stock, overrides, outbox, and checkout.
- `vastra-modern-api/demo.py`: sample quote and idempotent retry client.
- `vastra-modern-api/README.md`: full local setup, architecture, API contracts, examples, tests and limitations.
- `vastra-modern-api/LEGACY-VS-MODERN.md`: compact old/new comparison with code snippets.
- `vastra-modern-api/MIGRATION.md`: proposed migration and rollback sequence.

## Validation completed

- Modern API test suite: **24 passed** (including channel pricing, stock retries, audit, outbox, checkout concurrency/restart recovery and UI).
- Checkout idempotency hardening adds persisted request claims, concurrent duplicate rejection, payload mismatch rejection, completed-result replay after database reinitialization, and safe rejection of stale in-progress attempts.
- Legacy characterization suite after adding the legacy UI: **8 passed**.
- Modern UI was opened in the integrated browser; read-only smoke tests passed **3/3**.
- Legacy product and app-price controls were exercised in the browser and returned HTTP 200.
- Legacy `/` route was confirmed to return a service/endpoints list instead of a 404.
- Modern checkout was previously exercised against local partner stubs and a repeated request with
  the same completed idempotency key returned the original order.
- Local Docker Compose deployment was built and started. All four services (NGINX, legacy,
  partners, modern API) reported healthy. Through NGINX, gateway/API health, modern UI/docs,
  legacy UI/product/pricing, and a successful paid checkout were smoke-tested.
- In this Windows environment the `desktop-linux` context returned an engine error; the working
  `default` context was used for compose commands.

## Design section 2.3: pricing example and proof

The document example uses base ₹2,000, active sale ₹1,600, coupon 10%, and member discount 10%.
Expected legacy outputs are website ₹1,296, app ₹1,440, and POS ₹1,600. The modern pricing policy
returns ₹1,296 for web, app, and POS by applying coupon to sale price first, then the member
discount: ₹1,600 → ₹1,440 → ₹1,296.

Automated proof uses a fixed test product with those prices and a future sale expiry:

```powershell
cd FDE_Project_Codebase\vastra-legacy-lab
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_legacy_characterisation.py -k "three_pricing_copies or sale_ends_later"

cd ..\vastra-modern-api
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_pricing_policy.py -k documented_price
```

The legacy assertions check the exact channel totals and the app's late sale-end behavior. The
modern parameterized test calls `/v1/prices/SKU00003` with each of `vastra-web`, `vastra-app`, and
`vastra-pos`, then verifies the same ₹1,296 and the sale/coupon/member breakdown. The locally
seeded SKU can have different price data, so it is not the fixture for proving the document's
exact numbers. More detail is in [LEGACY-VS-MODERN.md](./LEGACY-VS-MODERN.md).

## Known limitations to address before production

1. Demo API keys are hard-coded; use a real identity provider and managed secret storage.
2. The API uses SQLite; migrate to PostgreSQL and verify concurrency/locking semantics.
3. Crashed in-progress checkouts are rejected for manual reconciliation; no automatic recovery
   workflow exists for reservations or ambiguous payment results.
4. The local payment stub ignores the forwarded idempotency key; validate provider behavior before
   enabling automated payment retries.
5. Courier and payment are sequential; consider parallel orchestration only if business
   dependencies and payment/reservation semantics allow it.
6. Outbox events are readable by polling, but there is no publisher/acknowledgment process.
7. Legacy-to-modern product/stock import is an initial copy only. Define reconciliation and a
   single-writer cutover policy before migrating live traffic.
8. Coupon/member discount order is the current demonstration policy and needs pricing approval.
9. Browser checkout controls are state-changing and should only be used with local test services.

## Suggested next continuation steps

- Re-run both test commands from the README after any changes.
- Define and implement an operator reconciliation workflow for persisted in-progress checkouts.
- Decide and verify the real payment provider's idempotency and payment-status lookup behavior
  before attempting automated recovery.
- Replace `on_event("startup")` with a FastAPI lifespan handler to clear the current deprecation warning.
- Review the checkout example/UI: the modern API requires a `coupon_pct` and member value; keep the
  pricing and checkout inputs clearly synchronized.
- Confirm production pricing policy, stock ownership, operational rollback, and monitoring requirements.
- Revisit `MIGRATION.md` stage gates and traffic percentages with the Vastra teams before treating
  the plan as committed delivery dates.

## Test programs and commands

The modern tests are separate, independently runnable programs. They share the clean temporary
database and product fixture in `tests/conftest.py`; external services are mocked where checkout
needs them. The legacy suite is separate and checks the old behavior (including its intentional
defects).

| Program | Cases covered |
|---|---|
| `test_service_health.py` | Health endpoint returns service status |
| `test_api_workbench.py` | Static browser test workbench is served |
| `test_catalogue_api.py` | API-key requirement, product response, ETag and cache headers |
| `test_pricing_policy.py` | Exact documented sale/coupon/member breakdown is consistent for web/app/POS; quantity total; price role |
| `test_stock_ledger.py` | Stock update, duplicate event retry, negative inventory rejection, role enforcement |
| `test_price_overrides.py` | Named audit, store-scoped override, default price unaffected, role enforcement |
| `test_outbox_events.py` | Stock update produces integration event; ERP role required |
| `test_checkout.py` | Idempotency key validation, completed retry, concurrent retry rejection, restart persistence, mismatched/stale key rejection, oversell rejection, successful order outbox event, payment failure stock release, courier fallback |
| Legacy `test_legacy_characterisation.py` | Legacy channel pricing differences and timezone defect, root/test-page routes, sequential checkout trace, stale stock copy and duplicate payment behavior |

Run the complete modern suite or an individual test program from the repository root, using the
existing `.venv`:

```powershell
cd FDE_Project_Codebase\vastra-modern-api
..\..\.venv\Scripts\python.exe -m pytest -q tests
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_service_health.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_api_workbench.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_catalogue_api.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_pricing_policy.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_stock_ledger.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_price_overrides.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_outbox_events.py
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_checkout.py

cd ..\vastra-legacy-lab
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_legacy_characterisation.py
```

Most recently verified totals: modern **24 passed** and legacy **8 passed**. Pytest reports
FastAPI/Starlette deprecation warnings for the current startup hook and dependency alias; these
warnings do not fail the tests.
