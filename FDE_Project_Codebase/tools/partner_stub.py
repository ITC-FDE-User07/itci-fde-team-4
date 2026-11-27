"""Stand-in courier and payment partner (Option B). Run: python -m uvicorn partner_stub:app --port 8100"""
import asyncio
import os

from fastapi import FastAPI

app = FastAPI()
DELAY = float(os.getenv("COURIER_DELAY", "0.2"))
n = 0


@app.get("/courier/promise")
async def promise(pincode: str):
    await asyncio.sleep(DELAY)
    return {"delivery_days": 2, "cod_available": True}


@app.post("/payment/charge")
async def charge(body: dict):
    global n
    n += 1
    return {"status": "success", "charge_id": 9000 + n}
