from __future__ import annotations

from . import main
from penpal.sources.discover import sources_group

main.add_command(sources_group)
main.add_command(sources_group, name="source")  # issue #28 spelling: `penpal source disable NAME`
