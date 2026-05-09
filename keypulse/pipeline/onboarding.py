from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import tomllib


@dataclass(frozen=True)
class OnboardingAnswers:
    work_type: str
    region: str
    work_mode: str
    weekly_trigger: str
    weekly_style: str
    religion: str
    created_at: str


QUESTIONS: list[dict] = [
    {
        "key": "work_type",
        "question": "工作类型",
        "options": ["developer", "product", "design", "writing", "operations", "founder", "general"],
        "default": "developer",
    },
    {
        "key": "region",
        "question": "工作区域",
        "options": ["CN", "NA", "EU", "JPKR", "SEA", "MULTI"],
        "default": "CN",
    },
    {
        "key": "work_mode",
        "question": "工作节奏",
        "options": ["standard", "flexible", "founder_7x16"],
        "default": "flexible",
    },
    {
        "key": "weekly_trigger",
        "question": "周报触发",
        "options": ["fri-pm", "sun-pm", "mon-am", "off"],
        "default": "fri-pm",
    },
    {
        "key": "weekly_style",
        "question": "周报视角",
        "options": ["plain", "exec", "both"],
        "default": "exec",
    },
    {
        "key": "religion",
        "question": "宗教/文化（可选）",
        "options": ["unspecified", "muslim", "hindu", "jewish", "orthodox"],
        "default": "unspecified",
    },
]


def validate_answers(answers: dict) -> list[str]:
    errors: list[str] = []
    question_map = {item["key"]: item for item in QUESTIONS}

    for key, question in question_map.items():
        if key not in answers:
            errors.append(f"missing field: {key}")
            continue
        value = answers.get(key)
        valid_options = set(question["options"])
        if key == "religion":
            valid_options = valid_options | {""}
        if value not in valid_options:
            errors.append(f"invalid value for {key}: {value}")

    for key in answers.keys():
        if key not in question_map and key != "created_at":
            errors.append(f"unknown field: {key}")

    return errors


def _quote_toml(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_profile(answers: OnboardingAnswers, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [
            "# ~/.keypulse/profile.toml",
            "[user]",
            f"work_type = {_quote_toml(answers.work_type)}",
            f"region = {_quote_toml(answers.region)}",
            f"work_mode = {_quote_toml(answers.work_mode)}",
            f"weekly_trigger = {_quote_toml(answers.weekly_trigger)}",
            f"weekly_style = {_quote_toml(answers.weekly_style)}",
            f"religion = {_quote_toml(answers.religion)}",
            f"created_at = {_quote_toml(answers.created_at)}",
            "",
            "[holidays]",
            "follow_strict = false",
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")


def read_profile(path: Path) -> Optional[OnboardingAnswers]:
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None

    user = data.get("user")
    if not isinstance(user, dict):
        return None

    candidate = {
        "work_type": user.get("work_type", ""),
        "region": user.get("region", ""),
        "work_mode": user.get("work_mode", ""),
        "weekly_trigger": user.get("weekly_trigger", ""),
        "weekly_style": user.get("weekly_style", ""),
        "religion": user.get("religion", ""),
        "created_at": user.get("created_at", ""),
    }
    errors = validate_answers(candidate)
    if errors:
        return None

    return OnboardingAnswers(**candidate)


def is_first_run(path: Path) -> bool:
    return not path.exists()
