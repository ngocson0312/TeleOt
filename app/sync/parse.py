"""Turn KiotViet JSON into plain values.

Several field names and shapes are unconfirmed (section 3 of the plan), so each reader
accepts every spelling the docs use. `python -m app.cli probe` shows which one is real.
"""
from __future__ import annotations

from typing import Any

# KiotViet stores "no max set" as 999999999.
UNSET_MAX = 999_999_000


def pick(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def to_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def min_qty(inv: dict) -> float | None:
    v = to_float(pick(inv, "minQuantity", "minQuality"), 0.0)
    return v if v > 0 else None


def max_qty(inv: dict) -> float | None:
    v = to_float(pick(inv, "maxQuantity", "maxQuality"), 0.0)
    return v if 0 < v < UNSET_MAX else None


def product_batches(product: dict) -> list[dict]:
    raw = pick(product, "productBatchExpires", "batchExpires", default=[])
    return raw if isinstance(raw, list) else [raw]


def invoice_details(invoice: dict) -> list[dict]:
    """The docs show invoiceDetails both as an array and as a single object."""
    raw = invoice.get("invoiceDetails")
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if "productId" in raw:
            return [raw]
        for v in raw.values():
            if isinstance(v, list):
                return v
    return []
