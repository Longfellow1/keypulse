"""Test helpers: call the MCP tool layer / server as the Host that owns a token.

Serving now needs the matching client id (PENPAL_CLIENT) as well as the token; most tests just
want "this token's own Host", so these helpers look the client up from the grant row.
"""
from __future__ import annotations

from penpal.mcp import auth, tools
from penpal.mcp.server import build_server
from penpal.store.db import get_conn


def client_of(token):
    if not token:
        return None
    row = get_conn().execute(
        "SELECT client_id FROM access_grants WHERE token_hash = ?", (auth.hash_token(token),)
    ).fetchone()
    return row["client_id"] if row else "ghost"


def search(token, *args, **kwargs):
    return tools.search_context(token, *args, client=client_of(token), **kwargs)


def server_for(token):
    return build_server(token_provider=lambda: token, client_provider=lambda: client_of(token))
