# Vastra API v1 migration

## 12-week migration

1. **Weeks 1-2: Foundation**
   Deploy NGINX, API v1, OpenAPI and request IDs. Keep legacy traffic at 100%.
2. **Weeks 3-4: Pricing**
   Mirror 1% of web/app traffic to `/v1/prices`, then 10%, 50% and 100%.
3. **Weeks 5-6: Catalogue**
   Enable HTTP cache headers and route catalogue reads through `/v1/catalogue`.
4. **Weeks 7-8: Stock**
   Bootstrap live ledger from `stock_copy`, publish POS/warehouse events and compare ledgers.
5. **Weeks 9-10: Checkout**
   Canary checkout at 1%, 10%, 50%, 100%. Require idempotency and reserve stock before payment.
6. **Weeks 11-12: Integration**
   Move ERP/WMS consumption to `/v1/events`, observe the outbox, then retire legacy batch jobs.

## Rollback

At every step, routing weights in NGINX can move traffic back to `/legacy/*`. API data remains
independent, so the cutover team must compare the last reconciled ledger and order state before
returning stock or order ownership to the legacy path.

Do not disable the legacy route until an agreed observation window has no unresolved drift.
