"""Stub partner APIs (the only real APIs in today's estate): courier, payment gateway, SMS and email.

Run: uvicorn app.partners.stubs:app --port 8100
LATENCY_SCALE (env) shrinks delays for tests; PAYMENT_FAIL_RATE (env) sets payment failures.
"""
import os, random, time, math, itertools
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Vastra partner stubs")
SCALE = float(os.getenv("LATENCY_SCALE", "1.0"))
FAIL = float(os.getenv("PAYMENT_FAIL_RATE", "0.031"))
_charge_ids = itertools.count(1)
CHARGES = []                         # every successful charge, to show duplicates on retry


class Charge(BaseModel):
    order_ref: str
    amount: int


@app.get("/courier/promise")
def courier_promise(pincode: str = "560001"):
    # W2.5: 300 ms - 2 s, sometimes much slower
    ms = min(max(random.lognormvariate(math.log(620), 0.51), 300), 2000) if random.random() > 0.017 else random.uniform(2000, 5000)
    time.sleep(ms / 1000 * SCALE)
    return {"pincode": pincode, "delivery_days": random.choice([2, 3, 4]), "cod_available": random.random() < 0.8}


@app.post("/payment/charge")
def payment_charge(c: Charge):
    time.sleep(random.uniform(0.2, 0.6) * SCALE)
    if random.random() < FAIL:
        return {"status": "failed", "reason": random.choice(["gateway_timeout", "otp_failed", "bank_declined"])}
    cid = next(_charge_ids)
    CHARGES.append({"charge_id": cid, "order_ref": c.order_ref, "amount": c.amount})
    return {"status": "success", "charge_id": cid}   # no idempotency key accepted: retries charge again


@app.post("/notify")
def notify(to: str = "customer", channel: str = "sms"):
    return {"queued": True, "to": to, "channel": channel}
