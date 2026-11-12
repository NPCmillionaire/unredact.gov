"""Accounts. Passwords use stdlib scrypt; no extra crypto dependency."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3

from unredact.config import Settings, get_settings

_N, _R, _P = 2**14, 8, 1


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=_N, r=_R, p=_P, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except Exception:  # noqa: BLE001
        return False


class SignupError(ValueError):
    pass


def create(con: sqlite3.Connection, email: str, password: str, name: str = "") -> dict:
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise SignupError("Enter a valid email address.")
    if len(password) < 10:
        raise SignupError("Password must be at least 10 characters.")
    if by_email(con, email):
        raise SignupError("An account with that email already exists.")
    con.execute("INSERT INTO users (email, pw_hash, name) VALUES (?,?,?)", (email, hash_password(password), name.strip()))
    con.commit()
    return by_email(con, email)


def authenticate(con: sqlite3.Connection, email: str, password: str) -> dict | None:
    u = by_email(con, email)
    if u and verify_password(password, u["pw_hash"]):
        return u
    verify_password(password, hash_password("timing-equalizer"))  # don't leak which emails exist
    return None


def get(con: sqlite3.Connection, user_id: int | None) -> dict | None:
    if not user_id:
        return None
    r = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(r) if r else None


def by_email(con: sqlite3.Connection, email: str) -> dict | None:
    r = con.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    return dict(r) if r else None


PROFILE_FIELDS = ("name", "phone", "address", "organization", "fee_category")


def update_profile(con: sqlite3.Connection, user_id: int, **fields) -> None:
    sets = {k: (v or "").strip() for k, v in fields.items() if k in PROFILE_FIELDS}
    if sets:
        con.execute(f"UPDATE users SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), user_id))
        con.commit()


def is_admin(user: dict | None, s: Settings | None = None) -> bool:
    s = s or get_settings()
    return bool(user and user["email"] in s.admin_emails)


def requester_for(con: sqlite3.Connection, req: dict, s: Settings | None = None) -> dict:
    """Who signs the letter, and where replies go.

    Operator requests (CLI, no user) use REQUESTER_* from the environment. User requests are signed by the
    user, but the contact email is the platform inbox (INBOUND_ADDRESS): agency replies are captured and
    matched by token, and the user's personal address isn't handed to every agency.
    """
    s = s or get_settings()
    u = get(con, req.get("user_id"))
    if not u:
        return s.requester
    return {
        "name": u["name"] or u["email"],
        "email": s.inbound_address or s.requester_email or u["email"],
        "phone": u["phone"],
        "address": u["address"],
        "organization": u["organization"],
        "user_email": u["email"],
    }
