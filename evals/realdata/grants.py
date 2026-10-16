"""In-process grant creation against the DB copy. Tokens stay in memory: never printed, never stored."""
from __future__ import annotations

import os
from pathlib import Path


def client_id(prefix: str, buckets: tuple[str, ...]) -> str:
    return f"{prefix}-" + "-".join(b.lower() for b in buckets)


def write_fake_home(home: Path, db: Path) -> None:
    """Fake HOME whose ~/.penpal/config.toml points `penpal mcp serve` at the DB copy."""
    (home / ".penpal").mkdir(parents=True, exist_ok=True)
    (home / ".penpal" / "config.toml").write_text(f'[app]\ndb_path = "{db}"\n', encoding="utf-8")


def make_grants(home: Path, db: Path, bucket_sets: set[tuple[str, ...]], prefix: str) -> dict[tuple[str, ...], tuple[str, str]]:
    """Return {buckets: (client_id, token)}; old grants of the same client ids are revoked first."""
    from penpal.mcp.auth import create_grant, revoke_grants
    from penpal.store.db import init_db

    write_fake_home(home, db)
    real_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        init_db(db)
        out = {}
        for buckets in sorted(bucket_sets):
            cid = client_id(prefix, buckets)
            revoke_grants(cid)
            token = create_grant(cid, buckets=list(buckets), sources=None, operations=["read"],
                                 purpose="realdata eval", expires_at=None)
            out[buckets] = (cid, token)
        return out
    finally:
        if real_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = real_home
