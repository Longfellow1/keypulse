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
def grant_command(client_id: str) -> None:
    from keypulse.mcp.auth import TOKEN_ENV, create_grant

    init_db(Config.load().db_path_expanded)
    try:
        token = create_grant(client_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    exe = shutil.which("keypulse") or "keypulse"
    click.echo(f"Granted client '{client_id}'. Token (shown once, only its hash is stored):\n")
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
        click.echo(f"{g['id']}\t{g['client_id']}\t{g['created_at']}\t{state}")
