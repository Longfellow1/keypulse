from __future__ import annotations

import plistlib
from pathlib import Path

from keypulse.sources.discoverers.plist import discover_plist_candidates


def test_discover_plist_candidates_finds_recent_keys(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    pref_dir = home / "Library" / "Preferences"
    pref_dir.mkdir(parents=True, exist_ok=True)

    textedit = pref_dir / "com.apple.TextEdit.plist"
    with textedit.open("wb") as handle:
        plistlib.dump({"NSRecentDocuments": []}, handle)

    app_pref = pref_dir / "com.example.MyApp.plist"
    with app_pref.open("wb") as handle:
        plistlib.dump({"recentFiles": ["/tmp/a.txt"]}, handle)

    ignored = pref_dir / "com.example.NoHistory.plist"
    with ignored.open("wb") as handle:
        plistlib.dump({"WindowState": {}}, handle)

    broken = pref_dir / "com.example.Broken.plist"
    broken.write_text("not a plist", encoding="utf-8")

    candidates = discover_plist_candidates(exclude_paths=set())

    by_path = {candidate.path: candidate for candidate in candidates}
    assert str(textedit.resolve()) in by_path
    assert str(app_pref.resolve()) in by_path
    assert str(ignored.resolve()) not in by_path
    assert str(broken.resolve()) not in by_path

    textedit_candidate = by_path[str(textedit.resolve())]
    assert textedit_candidate.discoverer == "plist"
    assert textedit_candidate.app_hint == "TextEdit"
    assert textedit_candidate.confidence == "high"

    app_candidate = by_path[str(app_pref.resolve())]
    assert app_candidate.app_hint == "MyApp"
    assert app_candidate.confidence == "medium"


def test_discover_plist_candidates_returns_empty_when_missing_dir(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    candidates = discover_plist_candidates(exclude_paths=set())

    assert candidates == []
