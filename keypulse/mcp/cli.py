from __future__ import annotations

import json
import shlex
import shutil

import click

from keypulse.config import Config
from keypulse.store.db import init_db


@click.group(name="mcp", help="KeyPulse MCP server and client access.")
def mcp_group() -> None:
    pass


@mcp_group.command("serve", help="Run the MCP server over stdio (token via env KEYPULSE_TOKEN).")
def serve_command() -> None:
    from keypulse.mcp.server import serve_stdio

    init_db(Config.load().db_path_expanded)
    serve_stdio()


@mcp_group.command("grant", help="Authorize an AI client: creates a token (shown once) and prints connection config.")
@click.argument("client_id")
@click.option("--buckets", default="Work", show_default=True,
              help="Comma-separated buckets the client may read: Work,Personal,Private,Financial.")
@click.option("--sources", default=None, help="Comma-separated source names (default: every source inside the buckets).")
@click.option("--operations", default="read", show_default=True, help="Comma-separated: read[,write].")
@click.option("--purpose", default=None, help="Why this client needs access (shown in 'mcp list').")
@click.option("--expires-in-days", type=click.IntRange(min=1), default=None, help="Grant expires after N days.")
def grant_command(client_id: str, buckets: str, sources: str | None, operations: str,
                  purpose: str | None, expires_in_days: int | None) -> None:
    from datetime import datetime, timedelta, timezone

    from keypulse.mcp.auth import TOKEN_ENV, create_grant

    init_db(Config.load().db_path_expanded)
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days else None
    try:
        token = create_grant(
            client_id,
            buckets=buckets.split(","),
            sources=sources.split(",") if sources is not None else None,
            operations=operations.split(","),
            purpose=purpose,
            expires_at=expires_at,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    exe = shutil.which("keypulse") or "keypulse"
    scope = f"buckets={buckets}, sources={sources or 'all in buckets'}, operations={operations}"
    click.echo(f"Granted client '{client_id}' ({scope}). Token (shown once, only its hash is stored):\n")
    click.echo(f"  {token}\n")
    click.echo("Claude Code (CLI):\n")
    click.echo(
        f"  claude mcp add keypulse -e {TOKEN_ENV}={token} -- {shlex.quote(exe)} mcp serve\n"
    )
    click.echo(".mcp.json / claude_desktop_config.json snippet:\n")
    snippet = {
        "mcpServers": {
            "keypulse": {"command": exe, "args": ["mcp", "serve"], "env": {TOKEN_ENV: token}}
        }
    }
    click.echo(json.dumps(snippet, indent=2))
    click.echo("\nKeep the config file readable by your user only. Revoke with: keypulse mcp revoke " + client_id)


@mcp_group.command("revoke", help="Revoke all active tokens of a client.")
@click.argument("client_id")
def revoke_command(client_id: str) -> None:
    from keypulse.mcp.auth import revoke_grants

    init_db(Config.load().db_path_expanded)
    count = revoke_grants(client_id)
    click.echo(f"Revoked {count} grant(s) for '{client_id}'.")


@mcp_group.command("list", help="List client grants (never shows tokens).")
def list_command() -> None:
    from keypulse.mcp.auth import list_grants

    init_db(Config.load().db_path_expanded)
    for g in list_grants():
        state = f"revoked {g['revoked_at']}" if g["revoked_at"] else "active"
        scope = f"buckets={g['buckets']} sources={g['sources'] or 'all'} ops={g['operations']}"
        expiry = f" expires {g['expires_at']}" if g["expires_at"] else ""
        click.echo(f"{g['id']}\t{g['client_id']}\t{g['created_at']}\t{state}\t{scope}{expiry}")
