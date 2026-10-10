"""Local-token auth (D15) + AccessGrant scope (§3.5.2): one random token per Host, only its sha256
is stored. A grant carries the buckets / sources / operations the Host may use, a purpose and an
optional expiry. Scope parsing fails closed: a malformed scope column grants nothing."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from keypulse.store.buckets import normalize_bucket
from keypulse.store.db import get_conn

TOKEN_ENV = "KEYPULSE_TOKEN"
_TOKEN_PREFIX = "kp_"
DEFAULT_BUCKETS = ("Work",)  # least privilege: a new grant reads Work only
OPERATIONS = ("read", "write")


@dataclass(frozen=True)
class Grant:
    id: int
    client_id: str
    buckets: tuple[str, ...] = ()
    sources: Optional[tuple[str, ...]] = None  # None = any source inside the granted buckets
    operations: tuple[str, ...] = ()
    purpose: Optional[str] = None
    expires_at: Optional[str] = None

    def allows(self, operation: str) -> bool:
        return operation in self.operations


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_list(values: Optional[Sequence[str]]) -> Optional[list[str]]:
    if values is None:
        return None
    out: list[str] = []
    for v in values:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def create_grant(
    client_id: str,
    *,
    buckets: Optional[Sequence[str]] = None,
    sources: Optional[Sequence[str]] = None,
    operations: Optional[Sequence[str]] = None,
    purpose: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> str:
    """Create a grant and return the plaintext token. The token is shown once and never stored."""
    client_id = (client_id or "").strip()
    if not client_id:
        raise ValueError("client_id must not be empty")
    bucket_list = [normalize_bucket(b) for b in (_clean_list(buckets) or DEFAULT_BUCKETS)]
    bucket_list = list(dict.fromkeys(bucket_list))
    source_list = _clean_list(sources)
    if source_list is not None and not source_list:
        raise ValueError("sources must not be empty (omit it to allow every source in the granted buckets)")
    op_list = _clean_list(operations) or ["read"]
    bad_ops = [o for o in op_list if o not in OPERATIONS]
    if bad_ops:
        raise ValueError(f"unknown operation {bad_ops[0]!r}; choose from {', '.join(OPERATIONS)}")
    expiry = None
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expiry = expires_at.astimezone(timezone.utc).isoformat()
        if expires_at <= _now():
            raise ValueError("expiry must be in the future")
    token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    conn = get_conn()
    conn.execute(
        "INSERT INTO access_grants(client_id, token_hash, created_at, buckets, sources, operations, purpose, expires_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            client_id,
            hash_token(token),
            _now().isoformat(),
            json.dumps(bucket_list),
            json.dumps(source_list) if source_list is not None else None,
            json.dumps(op_list),
            (purpose or "").strip() or None,
            expiry,
        ),
    )
    conn.commit()
    return token


def revoke_grants(client_id: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE access_grants SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL",
        (_now().isoformat(), client_id),
    )
    conn.commit()
    return cur.rowcount


def list_grants() -> list[dict]:
    rows = get_conn().execute(
        "SELECT id, client_id, created_at, revoked_at, buckets, sources, operations, purpose, expires_at"
        " FROM access_grants ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def _json_tuple(raw: Optional[str]) -> Optional[tuple[str, ...]]:
    """Parse a JSON string array; raises ValueError on anything else."""
    if raw is None:
        return None
    data = json.loads(raw)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError("not a string array")
    return tuple(data)


def _expired(expires_at: Optional[str]) -> bool:
    if not expires_at:
        return False
    try:
        when = datetime.fromisoformat(expires_at)
    except ValueError:
        return True  # unreadable expiry: fail closed
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when <= _now()


def authenticate(token: Optional[str]) -> Optional[Grant]:
    """Return the active grant for `token`, or None (missing / unknown / revoked / expired / malformed scope)."""
    if not token:
        return None
    digest = hash_token(token)
    row = get_conn().execute(
        "SELECT id, client_id, token_hash, buckets, sources, operations, purpose, expires_at"
        " FROM access_grants WHERE token_hash = ? AND revoked_at IS NULL",
        (digest,),
    ).fetchone()
    if row is None or not hmac.compare_digest(row["token_hash"], digest):
        return None
    if _expired(row["expires_at"]):
        return None
    try:
        buckets = _json_tuple(row["buckets"]) or ()
        sources = _json_tuple(row["sources"])
        operations = _json_tuple(row["operations"]) or ()
        buckets = tuple(normalize_bucket(b) for b in buckets)
    except (ValueError, TypeError):
        return None
    return Grant(
        id=row["id"],
        client_id=row["client_id"],
        buckets=buckets,
        sources=sources,
        operations=operations,
        purpose=row["purpose"],
        expires_at=row["expires_at"],
    )
