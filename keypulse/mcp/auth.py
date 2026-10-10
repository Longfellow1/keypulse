"""Minimal local-token auth (D15): one random token per Host, only its sha256 is stored."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from keypulse.store.db import get_conn

TOKEN_ENV = "KEYPULSE_TOKEN"
_TOKEN_PREFIX = "kp_"


@dataclass(frozen=True)
class Grant:
    id: int
    client_id: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_grant(client_id: str) -> str:
    """Create a grant and return the plaintext token. The token is shown once and never stored."""
    client_id = (client_id or "").strip()
    if not client_id:
        raise ValueError("client_id must not be empty")
    token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    conn = get_conn()
    conn.execute(
        "INSERT INTO access_grants(client_id, token_hash, created_at) VALUES (?,?,?)",
        (client_id, hash_token(token), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return token


def revoke_grants(client_id: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE access_grants SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), client_id),
    )
    conn.commit()
    return cur.rowcount


def list_grants() -> list[dict]:
    rows = get_conn().execute(
        "SELECT id, client_id, created_at, revoked_at FROM access_grants ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def authenticate(token: Optional[str]) -> Optional[Grant]:
    """Return the active grant for `token`, or None (missing / unknown / revoked)."""
    if not token:
        return None
    digest = hash_token(token)
    row = get_conn().execute(
        "SELECT id, client_id, token_hash FROM access_grants WHERE token_hash = ? AND revoked_at IS NULL",
        (digest,),
    ).fetchone()
    if row is None or not hmac.compare_digest(row["token_hash"], digest):
        return None
    return Grant(id=row["id"], client_id=row["client_id"])
