from __future__ import annotations

from . import main
from penpal.mcp.cli import mcp_group

main.add_command(mcp_group)
