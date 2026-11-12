# Vastra legacy lab (S1-S2 starter)

A small, runnable stand-in for Vastra & Co.'s current estate, plus three months of logs for evidence collection.
Everything is synthetic. It deliberately contains the defects behind R2, R4, R5 and R15a.

## What is inside

| Path | What it is |
|---|---|
| `app/monolith.py` | The 2014 ecommerce monolith: server-rendered product page, private app endpoint, sequential checkout |
| `app/pricing_web.py`, `app/app_api.py`, `pos/pricing_pos.py` | Three copies of the pricing and promotion rules that disagree (R4) |
| `app/partners/stubs.py` | Partner APIs (courier, payment gateway, SMS and email): the only real APIs today |
| `app/jobs/` | Batch jobs: stock sync every 4 hours (R5), nightly orders to ERP (E14), nightly price list |
| `config.ini` | One shared database login in plain text (R15a) |
| `tests/test_legacy_characterisation.py` | Tests that pin down today's defects (they pass now; S2 replaces them) |
| `tools/generate_logs.py` | Regenerates the logs (12 Aug - 10 Nov 2026) |
| `data/logs/` | 26 log files for evidence E1-E29 |
| `evidence/` | Evidence map, loader, SQL query pack and the facilitator answer key |

## Run it

```bash
#create the environment
python -m venv .venv

#Activate env
.venv\Scripts\Activate.ps1

# if any error on .ps1 run, then run below command and then activate ps1
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

#Activate env
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python -m app.db                                  # create and seed the lab database
python -m uvicorn app.partners.stubs:app --port 8100        # partner APIs
python -m uvicorn app.monolith:app --port 8000               # the monolith
curl -X POST localhost:8000/checkout -H 'content-type: application/json' -d '{"sku":"SKU00003","coupon_pct":10,"member":true}'
curl 'localhost:8000/app-api/v0/price/SKU00003?coupon_pct=10&member=true'   # compare with the web total
pytest -q
```

The checkout response includes a `trace` with the time of each step (W2.1-W2.5): compare it with `checkout_traces_sample.csv`.

## Collect evidence (S1, Activity 2)

```bash
python evidence/load_logs.py                       # builds evidence/evidence.db
sqlite3 evidence/evidence.db < evidence/queries.sql
```

See `evidence/EVIDENCE_MAP.md` for which file proves which evidence item. Facilitators can print every figure with
`python evidence/compute_evidence.py`.

## Notes

- The lab uses SQLite in place of the monolith's MySQL so it runs anywhere; tables and behaviour are the same.
- Large logs are samples (for example, orders are a 10% sample); the evidence map says which.
