"""Stripe Checkout for paid letters."""
from __future__ import annotations

import sqlite3

import stripe

from redtape.config import Settings, get_settings


def create_order(con: sqlite3.Connection, n_letters: int, email: str, s: Settings | None = None) -> int:
    s = s or get_settings()
    cur = con.execute("INSERT INTO orders (amount_cents, email) VALUES (?, ?)", (n_letters * s.letter_price_cents, email))
    con.commit()
    return cur.lastrowid


def checkout_url(con: sqlite3.Connection, order_id: int, n_letters: int, email: str, batch: str,
                 s: Settings | None = None) -> str:
    s = s or get_settings()
    stripe.api_key = s.stripe_secret_key
    session = stripe.checkout.Session.create(
        mode="payment",
        customer_email=email,
        line_items=[{
            "quantity": n_letters,
            "price_data": {
                "currency": "usd",
                "unit_amount": s.letter_price_cents,
                "product_data": {"name": "Printed letter mailed to your member of Congress (USPS First-Class)"},
            },
        }],
        metadata={"order_id": str(order_id), "batch": batch},
        success_url=f"{s.base_url}/congress/status/{batch}?paid=1",
        cancel_url=f"{s.base_url}/congress/status/{batch}?canceled=1",
    )
    con.execute("UPDATE orders SET stripe_session=? WHERE id=?", (session["id"], order_id))
    con.commit()
    return session["url"]


def handle_webhook(con: sqlite3.Connection, payload: bytes, sig: str, s: Settings | None = None) -> int | None:
    """Verify the Stripe signature; on a paid checkout, mark the order paid and release its letters.
    Returns the order id whose letters are now queued, else None."""
    s = s or get_settings()
    event = stripe.Webhook.construct_event(payload, sig, s.stripe_webhook_secret)
    if event["type"] not in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        return None
    obj = event["data"]["object"]
    if obj.get("payment_status") != "paid":
        return None
    order_id = int(obj["metadata"]["order_id"])
    updated = con.execute("UPDATE orders SET status='paid' WHERE id=? AND status!='paid'", (order_id,)).rowcount
    con.execute("UPDATE messages SET status='queued' WHERE order_id=? AND status='awaiting_payment'", (order_id,))
    con.commit()
    return order_id if updated else None
