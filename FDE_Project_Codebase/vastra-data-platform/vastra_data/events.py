"""Record contracts for the load job: the six-field envelope plus one payload model per event type.

pydantic turns each contract into a check in a few lines. `extra="forbid"` means an unknown or renamed field
(for example `avail` instead of `available`) is bad data and goes to failed_records, not silently dropped.
"""
import datetime as dt
import re
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SOURCES = ("api-v1", "pos", "erp", "wms", "legacy")

# Sources that send local times without an offset. They are declared, not guessed (the R4 defect was guessing).
NAIVE_TIMEZONE = {"pos": "Asia/Kolkata", "legacy": "Asia/Kolkata"}

PERSONAL_KEYS = {"name", "customer_name", "phone", "mobile", "email", "address"}
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


def parse_time(value: str) -> dt.datetime:
    """ISO-8601 only. '25/11/2026 10:00' or 'yesterday' is bad data."""
    if not isinstance(value, str) or not _ISO.match(value.strip()):
        raise ValueError(f"occurred_at must be ISO-8601 like 2026-11-25T06:00:00Z, got {value!r}")
    return dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


# ------------------------------------------------------------------ envelope
class Envelope(Strict):
    event_id: str = Field(..., min_length=1, max_length=120)
    event_type: str
    occurred_at: str
    source: Literal["api-v1", "pos", "erp", "wms", "legacy"]
    actor: str = Field(..., min_length=1, max_length=120)
    payload: dict

    @model_validator(mode="after")
    def _time_is_known(self):
        t = parse_time(self.occurred_at)
        if t.tzinfo is None and self.source not in NAIVE_TIMEZONE:
            raise ValueError(f"occurred_at has no time zone and source '{self.source}' must send UTC")
        return self


# ------------------------------------------------------------------ payloads (one per event type)
Code = Field(..., min_length=1, max_length=40)


class OrderPlaced(Strict):                       # api-v1: order.placed / order.payment_pending
    order_id: str = Code
    sku: str = Code
    qty: int = Field(..., ge=1)
    total: int = Field(..., ge=0)
    channel: Literal["web", "app"]
    pincode: str = Field(..., pattern=r"^\d{6}$")
    request_id: Optional[str] = None


class StockMoved(Strict):                        # api-v1 / pos / wms
    sku: str = Code
    location_id: str = Code
    kind: Literal["store_sale", "online_sale", "return", "receipt", "transfer_in", "transfer_out"]
    qty: int = Field(..., ge=0)
    ref: str = Field(..., min_length=1, max_length=80)


class StockCounted(Strict):                      # wms cycle counts, legacy stock copy
    sku: str = Code
    location_id: str = Code
    available: int = Field(..., ge=0)            # a negative stock count is bad data
    on_order: int = Field(0, ge=0)


class SalesDaily(Strict):                        # pos end-of-day totals per SKU and store
    business_date: dt.date
    sku: str = Code
    location_id: str = Code
    units: int = Field(..., ge=0)
    net_amount: int = Field(..., ge=0)


class PoRaised(Strict):                          # erp
    po_id: str = Code
    sku: str = Code
    location_id: str = Code
    qty: int = Field(..., gt=0)
    supplier_id: str = Code
    expected_date: Optional[dt.date] = None
    lead_time_days: Optional[int] = Field(None, ge=0, le=120)


class GoodsReceived(Strict):                     # wms
    po_id: str = Code
    sku: str = Code
    location_id: str = Code
    qty: int = Field(..., gt=0)


class PriceChanged(Strict):                      # erp list/promo prices, api-v1 overrides
    sku: str = Code
    location_id: Optional[str] = None
    price: int = Field(..., gt=0)
    kind: Literal["list", "promo", "override"]
    valid_from: Optional[dt.date] = None
    valid_to: Optional[dt.date] = None
    override_id: Optional[int] = None

    @model_validator(mode="after")
    def _window(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to is before valid_from")
        return self


class PriceOverrideCreated(Strict):             # api-v1 (Week 1 name kept: renaming would break consumers)
    override_id: int
    sku: str = Code
    store: str = Field(..., pattern=r"^ST\d{3}$")
    price: int = Field(..., gt=0)


class ProductUpserted(Strict):                   # erp master + the web/app/pos copies (R12 consistency)
    sku: str = Code
    channel: Literal["erp", "web", "app", "pos"]
    product_name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=60)
    colour: Optional[str] = None
    top500: bool = False


class StoreUpserted(Strict):                     # erp store master
    location_id: str = Code
    store_name: str = Field(..., min_length=1)
    city: str
    region: Literal["North", "South", "East", "West"]
    tier: Literal["metro", "tier-2", "tier-3"]
    opened_on: dt.date


class CustomerRegistered(Strict):                # pos loyalty sign-up: the ONLY payload allowed personal details
    customer_ref: str = Code                     # loyalty id (personal)
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    home_store: str = Code


class ConsentChanged(Strict):                    # pos / api-v1
    customer_ref: str = Code
    purpose: Literal["orders", "loyalty", "marketing", "personalisation"]
    status: Literal["given", "withdrawn"]
    notice_version: str = Field(..., min_length=1)


PAYLOADS = {
    "order.placed": OrderPlaced,
    "order.payment_pending": OrderPlaced,
    "stock.moved": StockMoved,
    "stock.counted": StockCounted,
    "sales.daily": SalesDaily,
    "po.raised": PoRaised,
    "goods.received": GoodsReceived,
    "price.changed": PriceChanged,
    "price.override.created": PriceOverrideCreated,
    "product.upserted": ProductUpserted,
    "store.upserted": StoreUpserted,
    "customer.registered": CustomerRegistered,
    "consent.changed": ConsentChanged,
}
PERSONAL_EVENT_TYPES = {"customer.registered"}   # tokenised by the load job before anything reaches raw

# Which source may send which event type (a POS cannot publish a purchase order).
ALLOWED = {
    "api-v1": {"order.placed", "order.payment_pending", "stock.moved", "stock.counted", "price.changed",
               "price.override.created", "consent.changed"},
    "pos": {"sales.daily", "stock.moved", "customer.registered", "consent.changed", "product.upserted"},
    "erp": {"po.raised", "price.changed", "product.upserted", "store.upserted"},
    "wms": {"goods.received", "stock.moved", "stock.counted"},
    "legacy": {"stock.counted"},
}


class ContractError(ValueError):
    """Bad data: never retried, goes straight to failed_records with the reason."""


def _scan_for_personal(payload: dict, path="payload"):
    for k, v in payload.items():
        if k.lower() in PERSONAL_KEYS:
            raise ContractError(f"{path}.{k}: personal details are not allowed outside person_tokens")
        if isinstance(v, str) and (_EMAIL.search(v) or _PHONE.search(v)):
            raise ContractError(f"{path}.{k}: value looks like an email or phone number")
        if isinstance(v, dict):
            _scan_for_personal(v, f"{path}.{k}")


def _explain(err: ValidationError, prefix: str = "") -> str:
    parts = []
    for e in err.errors():
        loc = prefix + (".".join(str(x) for x in e["loc"]) or "record")
        msg = e["msg"].replace("Value error, ", "")
        if e["type"] == "extra_forbidden":
            msg = "unknown field (not in the contract)"
        parts.append(f"{loc}: {msg}")
    return "; ".join(parts)


def validate(row: dict) -> tuple[Envelope, BaseModel]:
    """Check one record against the envelope and its payload contract. Raises ContractError with a plain reason."""
    try:
        env = Envelope(**row)
    except ValidationError as err:
        raise ContractError(_explain(err)) from None
    except TypeError as err:
        raise ContractError(f"record is not an object: {err}") from None
    model = PAYLOADS.get(env.event_type)
    if model is None:
        raise ContractError(f"event_type: unknown type '{env.event_type}'")
    if env.event_type not in ALLOWED[env.source]:
        raise ContractError(f"event_type: source '{env.source}' may not publish '{env.event_type}'")
    if env.event_type not in PERSONAL_EVENT_TYPES:
        _scan_for_personal(env.payload)
    try:
        payload = model(**env.payload)
    except ValidationError as err:
        raise ContractError(_explain(err, prefix="payload.")) from None
    return env, payload


def mask_personal(obj):
    """Copy of a record with personal details masked, safe to keep in failed_records and logs."""
    if isinstance(obj, dict):
        return {k: ("***" if k.lower() in PERSONAL_KEYS and v else mask_personal(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_personal(v) for v in obj]
    if isinstance(obj, str):
        return _PHONE.sub("***", _EMAIL.sub("***", obj))
    return obj
