from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass
class EvidenceUnit:
    ts_start: datetime
    ts_end: datetime
    where: str
    who: str
    what: str
    evidence_refs: list[int]
    semantic_weight: float
    machine_online: bool
    confidence: float


def _score_confidence(semantic_weight: float, evidence_refs: list[int], machine_online: bool) -> float:
    if not machine_online:
        return 0.1
    if semantic_weight >= 0.8 and len(evidence_refs) >= 3:
        return 0.9
    if semantic_weight >= 0.5 and len(evidence_refs) >= 1:
        return 0.5
    return 0.2


def _parse_dt(ts: str) -> datetime:
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.min.replace(tzinfo=timezone.utc)


def load_profile_dict(db_path: Path) -> dict[str, str]:
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT alias, canonical_name FROM profile_entities").fetchall()
            return {alias: canonical for alias, canonical in rows}
        except sqlite3.OperationalError:
            return {}
        finally:
            conn.close()
    except Exception:
        return {}


def _apply_profile(text: str, profile_dict: dict[str, str]) -> str:
    if not profile_dict or not text:
        return text
    for alias, canonical in profile_dict.items():
        text = text.replace(alias, canonical)
    return text


def extract_evidence_units(
    block: Any,
    profile_dict: dict[str, str],
    raw_events_provider: Callable[[str, str], list[dict[str, Any]]],
) -> list[EvidenceUnit]:
    # TODO: split block into multiple units when duration >2h and entities fully non-overlapping
    raw_events = raw_events_provider(block.ts_start, block.ts_end)

    evidence_refs = [int(e["id"]) for e in raw_events if e.get("id") is not None]
    weights = [float(e.get("semantic_weight", 0.5)) for e in raw_events]
    avg_weight = sum(weights) / len(weights) if weights else float(getattr(block, "semantic_weight", 0.5))

    where = block.primary_app or "offline"

    # Derive who from user_candidates or fallback to "user"
    user_candidates = getattr(block, "user_candidates", [])
    who = "user"
    if user_candidates:
        first = user_candidates[0]
        who = str(first.get("speaker") or first.get("who") or "user")
    who = _apply_profile(who, profile_dict)

    what = _apply_profile(block.theme or "", profile_dict)

    ts_start = _parse_dt(block.ts_start)
    ts_end = _parse_dt(block.ts_end)

    confidence = _score_confidence(avg_weight, evidence_refs, machine_online=True)

    return [
        EvidenceUnit(
            ts_start=ts_start,
            ts_end=ts_end,
            where=where,
            who=who,
            what=what,
            evidence_refs=evidence_refs,
            semantic_weight=avg_weight,
            machine_online=True,
            confidence=confidence,
        )
    ]


# 真正会显示密码输入框的应用：Keychain Access / 1Password。
# loginwindow 不在这里 —— macOS 锁屏/解锁时 active_app 会被误标为 loginwindow，
# 但窗口本身不显示用户密码，把它当 deny 关键词会把真实工作内容一起丢掉。
# 短文本/空文本的 loginwindow 噪声由 capture 层的 L3 过滤兜底。
_LOGIN_APP_KEYWORDS = ("keychain access", "1password")
_PRIVACY_KEYWORDS = ("password", "passwd", "密码", "验证码", "verification code", "otp", "2fa")


def sanitize_unit(unit: EvidenceUnit) -> EvidenceUnit | None:
    """Drop units that look like login/password/verification-code input.
    Returns None if the unit should be dropped before LLM sees it; else returns unit unchanged.
    Command/path rewriting is handled by the LLM via Pass-1 prompt rules, not here.
    """
    where_lc = (unit.where or "").lower()
    what_lc = (unit.what or "").lower()
    if any(kw in where_lc for kw in _LOGIN_APP_KEYWORDS):
        return None
    if any(kw in where_lc for kw in _PRIVACY_KEYWORDS):
        return None
    if any(kw in what_lc for kw in _PRIVACY_KEYWORDS):
        return None
    return unit


def generate_offline_placeholder(ts_start: datetime, ts_end: datetime) -> EvidenceUnit:
    return EvidenceUnit(
        ts_start=ts_start,
        ts_end=ts_end,
        where="offline",
        who="-",
        what="",
        evidence_refs=[],
        semantic_weight=0.0,
        machine_online=False,
        confidence=0.1,
    )


def enrich_with_evidence(
    blocks: list[Any],
    db_path: Path,
) -> list[tuple[Any, list[EvidenceUnit]]]:
    profile_dict = load_profile_dict(db_path)

    def _raw_events_provider(ts_start: str, ts_end: str) -> list[dict[str, Any]]:
        if not db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute(
                    "SELECT id, semantic_weight FROM raw_events WHERE ts_start >= ? AND ts_start <= ?",
                    (ts_start, ts_end),
                ).fetchall()
                return [{"id": row[0], "semantic_weight": row[1]} for row in rows]
            except sqlite3.OperationalError:
                return []
            finally:
                conn.close()
        except Exception:
            return []

    def _count_raw_events_in_gap(ts_start: str, ts_end: str) -> int:
        if not db_path.exists():
            return 0
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM raw_events WHERE ts_start > ? AND ts_start < ?",
                    (ts_start, ts_end),
                ).fetchone()
                return row[0] if row else 0
            except sqlite3.OperationalError:
                return 0
            finally:
                conn.close()
        except Exception:
            return 0

    result: list[tuple[Any, list[EvidenceUnit]]] = []

    for i, block in enumerate(blocks):
        # Check gap before this block
        if i > 0:
            prev_block = blocks[i - 1]
            prev_end_dt = _parse_dt(prev_block.ts_end)
            curr_start_dt = _parse_dt(block.ts_start)
            gap_seconds = (curr_start_dt - prev_end_dt).total_seconds()
            if gap_seconds > 15 * 60:
                gap_event_count = _count_raw_events_in_gap(prev_block.ts_end, block.ts_start)
                if gap_event_count == 0:
                    placeholder = generate_offline_placeholder(prev_end_dt, curr_start_dt)
                    # Attach placeholder to the previous block's unit list
                    if result:
                        prev_pair = result[-1]
                        result[-1] = (prev_pair[0], prev_pair[1] + [placeholder])

        units = extract_evidence_units(block, profile_dict, _raw_events_provider)
        result.append((block, units))

    return result
