from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_to_keypulse_dir.py"
    spec = importlib.util.spec_from_file_location("migrate_to_keypulse_dir_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_rewrite_wiki_links_updates_events_and_topics_targets() -> None:
    mod = _load_module()
    text = "\n".join(
        [
            "# 2026-05-06",
            "- [[Events/2026-05-06/0900-a|事件A]]",
            "- [[Topics/project-alpha|主题A]]",
            "- [[../.keypulse/events/2026-05-06/0901-b|已迁移事件]]",
        ]
    )

    rewritten, changes = mod.rewrite_wiki_links(text)

    assert changes == 2
    assert "[[../.keypulse/events/2026-05-06/0900-a|事件A]]" in rewritten
    assert "[[../.keypulse/topics/project-alpha|主题A]]" in rewritten
    assert "[[../.keypulse/events/2026-05-06/0901-b|已迁移事件]]" in rewritten


def test_migration_backup_created_only_in_apply(tmp_path: Path) -> None:
    mod = _load_module()
    vault = tmp_path / "vault"
    _write(vault / "Events" / "2026-05-06" / "0900-a.md", "event-a")
    _write(vault / "Topics" / "project-alpha.md", "topic-a")
    _write(
        vault / "Daily" / "2026-05-06.md",
        "- [[Events/2026-05-06/0900-a]]\n- [[Topics/project-alpha]]\n",
    )

    dry_report = mod.run_migration(vault, apply=False)
    assert dry_report["backup_path"] is None
    assert not (vault / ".keypulse-backup").exists()

    keypulse_home = tmp_path / "kp-home"
    apply_report = mod.run_migration(vault, apply=True, keypulse_home=keypulse_home)
    backup_path = Path(apply_report["backup_path"])
    assert backup_path.exists()
    assert backup_path.name.startswith("vault-")
    assert backup_path.suffixes[-2:] == [".tar", ".gz"]
