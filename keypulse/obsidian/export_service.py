from __future__ import annotations

from typing import Any, Optional

from keypulse.obsidian.exporter import export_obsidian as export_obsidian_notes


def export_obsidian(
    output_dir: str,
    days: Optional[int] = None,
    date_str: Optional[str] = None,
    vault_name: str = "KeyPulse",
    model_gateway: Any = None,
    incremental: bool = False,
    db_path: str | None = None,
    cursor_path: str | None = None,
    wiki_link_mode: str = "relative",
    humanize_titles: bool = False,
) -> list[str]:
    written = export_obsidian_notes(
        output_dir=output_dir,
        vault_name=vault_name,
        days=days,
        date_str=date_str,
        model_gateway=model_gateway,
        incremental=incremental,
        db_path=db_path,
        cursor_path=cursor_path,
        wiki_link_mode=wiki_link_mode,
        humanize_titles=humanize_titles,
    )
    return [str(path) for path in written]
