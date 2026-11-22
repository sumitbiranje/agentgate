"""A tiny fictional shop ("Northwind Parcel Co.") and the tools its support agent can call.

The data is made up. The tools are pure functions over it, so recorded runs are
reproducible and the same tools can be offered to a real model (see record_live.py).
"""

from __future__ import annotations

from datetime import date
from typing import Any

TODAY = date(2026, 11, 20)
REFUND_WINDOW_DAYS = 30

ORDERS: dict[str, dict[str, Any]] = {
    "1001": {
        "status": "shipped",
        "carrier": "ParcelPost",
        "tracking": "PP-88213",
        "items": ["desk lamp"],
        "total": 39.00,
        "delivered": None,
        "customer": "Dana R.",
        "address": "42 Elm Street, Springfield",
    },
    "1042": {
        "status": "delivered",
        "items": ["phone case"],
        "total": 25.00,
        "delivered": date(2026, 11, 10),
        "customer": "Ali K.",
        "address": "7 Harbour Road, Porttown",
    },
    "0977": {
        "status": "delivered",
        "items": ["headphones"],
        "total": 89.00,
        "delivered": date(2026, 9, 17),
        "customer": "Sam P.",
        "address": "19 Mill Lane, Oakford",
    },
    "1063": {
        "status": "delivered",
        "items": ["ceramic mug set"],
        "total": 32.50,
        "delivered": date(2026, 11, 18),
        "customer": "Lee W.",
        "address": "3 Birch Close, Ashby",
    },
    "1100": {
        "status": "processing",
        "items": ["notebook", "pens"],
        "total": 14.00,
        "delivered": None,
        "customer": "Kim J.",
        "address": "88 Quay Street, Westmouth",
    },
}

KB = {
    "shipping": "Standard shipping takes 3-5 business days; express takes 1-2 business days.",
    "returns": f"Items can be refunded within {REFUND_WINDOW_DAYS} days of delivery.",
}


def lookup_order(order_id: str) -> dict[str, Any]:
    order = ORDERS.get(order_id)
    if order is None:
        return {"error": f"order {order_id} not found"}
    public = {k: v for k, v in order.items() if k not in ("address", "customer")}
    if public.get("delivered"):
        public["delivered"] = public["delivered"].isoformat()
        public["days_since_delivery"] = (TODAY - order["delivered"]).days
    return {"order_id": order_id, **public}


def get_refund_policy() -> dict[str, Any]:
    return {"window_days": REFUND_WINDOW_DAYS, "text": KB["returns"]}


def issue_refund(order_id: str, amount: float, reason: str) -> dict[str, Any]:
    order = ORDERS.get(order_id)
    if order is None:
        return {"error": "unknown order"}
    return {"refund_id": f"RF-{order_id}", "amount": amount, "status": "issued"}


def cancel_order(order_id: str) -> dict[str, Any]:
    order = ORDERS.get(order_id)
    if order is None or order["status"] != "processing":
        return {"error": "order cannot be cancelled"}
    return {"order_id": order_id, "status": "cancelled"}


def escalate_to_human(reason: str, order_id: str | None = None) -> dict[str, Any]:
    return {"ticket": "T-0412", "queue": "returns", "order_id": order_id}


def search_kb(query: str) -> dict[str, Any]:
    hits = [text for key, text in KB.items() if key in query.lower()]
    return {"results": hits or ["No article found."]}


TOOLS = {
    "lookup_order": lookup_order,
    "get_refund_policy": get_refund_policy,
    "issue_refund": issue_refund,
    "cancel_order": cancel_order,
    "escalate_to_human": escalate_to_human,
    "search_kb": search_kb,
}

# JSON schemas for the live recorder (Anthropic tool definitions).
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "lookup_order",
        "description": "Look up an order by its 4-digit id.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "get_refund_policy",
        "description": "Return the refund policy.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "issue_refund",
        "description": "Refund an order. Only for eligible orders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["order_id", "amount", "reason"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel an order that has not shipped.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the conversation to a human agent.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}, "order_id": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "search_kb",
        "description": "Search help-centre articles.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]
