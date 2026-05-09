from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "lint_topics.py"
    spec = importlib.util.spec_from_file_location("lint_topics_script", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_lint_topics_classifies_all_four_categories(tmp_path: Path) -> None:
    mod = _load_module()
    vault = tmp_path / "vault"
    topics = vault / "Topics"
    topics.mkdir(parents=True)

    (topics / "empty.md").write_text("", encoding="utf-8")
    _write(topics / "frontmatter-only.md", "---\nkeywords:\n  - alpha\n---\n")
    _write(
        topics / "merge-one-event.md",
        "# Merge\n\n- [[Events/2026-05-06/0900-a.md]]\n",
    )
    _write(
        topics / "enrich-no-keywords.md",
        "# Enrich\n\n- [[Events/2026-05-06/0900-a.md]]\n- [[Events/2026-05-06/0910-b.md]]\n",
    )
    _write(
        topics / "healthy.md",
        "---\nkeywords:\n  - keypulse\n---\n\n今天把主链路打通。\n\n- [[Events/2026-05-06/0900-a.md]]\n- [[Events/2026-05-06/0910-b.md]]\n",
    )

    report = mod.run_lint(vault, apply=False)
    by_category = {item["relative_path"]: item["category"] for item in report["items"]}

    assert by_category["empty.md"] == "delete_candidate"
    assert by_category["frontmatter-only.md"] == "delete_candidate"
    assert by_category["merge-one-event.md"] == "merge_candidate"
    assert by_category["enrich-no-keywords.md"] == "enrich_candidate"
    assert by_category["healthy.md"] == "healthy"


def test_lint_topics_backup_created_only_in_apply(tmp_path: Path) -> None:
    mod = _load_module()
    vault = tmp_path / "vault"
    topics = vault / "Topics"
    topics.mkdir(parents=True)
    _write(topics / "empty.md", "")

    dry = mod.run_lint(vault, apply=False)
    assert dry["backup_path"] is None
    assert not (vault / ".keypulse-backup").exists()

    apply_report = mod.run_lint(vault, apply=True)
    backup_path = Path(apply_report["backup_path"])
    assert backup_path.exists()
    assert backup_path.name.startswith("topics-")
    assert backup_path.suffixes[-2:] == [".tar", ".gz"]
