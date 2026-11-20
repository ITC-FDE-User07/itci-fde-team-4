# Vastra API v1 — Session Handoff

Updated: 2026-11-20

## Goal and current state

The workspace contains a runnable legacy ecommerce lab and a separate Vastra API v1 service.
Modern APIs, both browser test pages, tests, comparison notes and a migration plan have been
implemented. The legacy monolith and the modern API are intended to run beside each other so that
the old and new behavior can be compared.

The comprehensive setup and architecture guide is in [README.md](./README.md).

## Local service addresses

When the local services are running:

| Service | Address |
|---|---|
| Partner stubs | `http://127.0.0.1:8100` |
| Legacy monolith | `http://127.0.0.1:8000` |
| Legacy browser test page | `http://127.0.0.1:8000/test-ui/` |
| Modern API | `http://127.0.0.1:8200` |
| Modern API workbench | `http://127.0.0.1:8200/ui/` |
| Modern OpenAPI docs | `http://127.0.0.1:8200/docs` |

At the last status check, ports 8000, 8100 and 8200 were listening. After source changes, restart
the corresponding Uvicorn process if hot reload is not enabled. The integrated browser had the
legacy test UI open at `http://127.0.0.1:8000/test-ui/`.

## Implementation map

- `vastra-legacy-lab/app/monolith.py`: legacy endpoints and JSON landing route.
- `vastra-legacy-lab/app/static/index.html`: test page for legacy product page, app price, and checkout.
- `vastra-legacy-lab/tests/test_legacy_characterisation.py`: legacy characterization and UI checks.
- `vastra-modern-api/app/main.py`: modern API routes, pricing, access roles, stock movements, overrides, outbox, checkout.
- `vastra-modern-api/app/db.py`: API schema and initial read-only bootstrap from legacy SQLite.
- `vastra-modern-api/app/static/index.html`: modern browser test workbench and smoke tests.
- `vastra-modern-api/tests/test_api.py`: modern API tests.
- `vastra-modern-api/demo.py`: sample quote and idempotent retry client.
- `vastra-modern-api/README.md`: full local setup, architecture, API contracts, examples, tests and limitations.
- `vastra-modern-api/LEGACY-VS-MODERN.md`: compact old/new comparison with code snippets.
- `vastra-modern-api/MIGRATION.md`: proposed migration and rollback sequence.

## Validation completed

- Modern API test suite: **14 passed**.
- Legacy characterization suite after adding the legacy UI: **8 passed**.
- Modern UI was opened in the integrated browser; read-only smoke tests passed **3/3**.
- Legacy product and app-price controls were exercised in the browser and returned HTTP 200.
- Legacy `/` route was confirmed to return a service/endpoints list instead of a 404.
- Modern checkout was previously exercised against local partner stubs and a repeated request with
  the same completed idempotency key returned the original order.

## Known limitations to address before production

1. Demo API keys are hard-coded; use a real identity provider and managed secret storage.
2. The API uses SQLite; migrate to PostgreSQL and verify concurrency/locking semantics.
3. Checkout has completed-result idempotency only; add persistent in-progress state and recovery
   for crashes or ambiguous payment results.
4. The local payment stub ignores the forwarded idempotency key; validate provider behavior.
5. Courier and payment are sequential; consider parallel orchestration only if business
   dependencies and payment/reservation semantics allow it.
6. Outbox events are readable by polling, but there is no publisher/acknowledgment process.
7. Legacy-to-modern product/stock import is an initial copy only. Define reconciliation and a
   single-writer cutover policy before migrating live traffic.
8. Coupon/member discount order is the current demonstration policy and needs pricing approval.
9. Browser checkout controls are state-changing and should only be used with local test services.

## Suggested next continuation steps

- Re-run both test commands from the README after any changes.
- Add checkout tests for payment timeout/failure and verify reservation release.
- Add tests for simultaneous requests sharing an idempotency key and for process-restart recovery.
- Decide and document the expected idempotency behavior of the real payment provider.
- Replace `on_event("startup")` with a FastAPI lifespan handler to clear the current deprecation warning.
- Review the checkout example/UI: the modern API requires a `coupon_pct` and member value; keep the
  pricing and checkout inputs clearly synchronized.
- Confirm production pricing policy, stock ownership, operational rollback, and monitoring requirements.
- Revisit `MIGRATION.md` stage gates and traffic percentages with the Vastra teams before treating
  the plan as committed delivery dates.

## Local test commands

From the repository root, using the existing `.venv`:

```powershell
cd FDE_Project_Codebase\vastra-modern-api
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_api.py

cd ..\vastra-legacy-lab
..\..\.venv\Scripts\python.exe -m pytest -q tests\test_legacy_characterisation.py
```
