from __future__ import annotations

import logging
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from keypulse.config import Config, ModelBackendConfig
from keypulse.pipeline.narrative import (
    WorkBlock,
    format_work_block_for_prompt,
    render_daily_narrative as _fallback_daily_narrative,
)

logger = logging.getLogger(__name__)


PROFILE_NAMES = {"local-first", "cloud-first", "auto", "privacy-locked"}


@dataclass(frozen=True)
class ModelBackend:
    kind: str
    base_url: str
    model: str
    api_key_env: str = ""
    timeout_sec: int = 20

    def is_disabled(self) -> bool:
        return self.kind == "disabled"


def _state_path(path: str | Path) -> Path:
    return Path(path).expanduser()


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _env_value(name: str) -> str:
    return os.environ.get(name, "").strip() if name else ""


def _model_backend_from_config(config: ModelBackendConfig) -> ModelBackend:
    return ModelBackend(
        kind=config.kind,
        base_url=config.base_url.strip().rstrip("/"),
        model=config.model.strip(),
        api_key_env=config.api_key_env.strip(),
        timeout_sec=config.timeout_sec,
    )


def _fallback_markdown(text: str) -> str:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank:
            if not previous_blank:
                compact.append("")
            previous_blank = True
            continue
        compact.append(line)
        previous_blank = False
    return "\n".join(compact).strip()


def _stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class ModelGateway:
    def __init__(self, config: Config, state_path: str | Path | None = None):
        self._config = config
        self._state_path = _state_path(state_path or config.model.state_path)
        self._state = _read_state(self._state_path)

    @property
    def active_profile(self) -> str:
        profile = str(self._state.get("active_profile") or self._config.model.active_profile)
        return profile if profile in PROFILE_NAMES else self._config.model.active_profile

    def use_profile(self, profile: str) -> str:
        if profile not in PROFILE_NAMES:
            raise ValueError(f"unknown profile: {profile}")
        self._state = {**self._state, "active_profile": profile}
        _write_state(self._state_path, self._state)
        return profile

    def _backend_candidates(self, stage: str) -> list[ModelBackend]:
        local = _model_backend_from_config(self._config.model.local)
        cloud = _model_backend_from_config(self._config.model.cloud)
        profile = self.active_profile

        if profile == "privacy-locked":
            return [ModelBackend(kind="disabled", base_url="", model="")]
        if profile == "local-first":
            return [local, cloud]
        if profile == "cloud-first":
            return [cloud, local]
        if stage == "write":
            return [local, cloud]
        return [cloud, local]

    def _is_backend_usable(self, backend: ModelBackend) -> bool:
        if backend.is_disabled() or not backend.model or not backend.base_url:
            return False
        if backend.kind in {"lm_studio", "ollama"}:
            return True
        if backend.kind == "openai_compatible":
            return True
        return False

    def select_backend(self, stage: str = "write") -> ModelBackend:
        for backend in self._backend_candidates(stage):
            if self._is_backend_usable(backend):
                return backend
        return ModelBackend(kind="disabled", base_url="", model="")

    def _request_json(self, backend: ModelBackend, path: str, payload: dict[str, Any], method: str = "POST") -> dict[str, Any]:
        normalized_path = path
        if re.search(r"/v\d+$", backend.base_url) and path.startswith("/v1/"):
            normalized_path = path.removeprefix("/v1")
        url = f"{backend.base_url}{normalized_path}"
        headers = {"Content-Type": "application/json"}
        if backend.kind == "openai_compatible" and backend.api_key_env:
            api_key = _env_value(backend.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=backend.timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            error_body = e.read().decode("utf-8")
            logger.debug("HTTPError body: %s", error_body)
            raise

    def _chat(self, backend: ModelBackend, messages: list[dict[str, str]], prompt_patch: str = "") -> str:
        if backend.kind == "ollama":
            payload = {
                "model": backend.model,
                "messages": messages,
                "stream": False,
            }
            if prompt_patch:
                payload["options"] = {"temperature": 0}
            data = self._request_json(backend, "/api/chat", payload)
            message = data.get("message") or {}
            return str(message.get("content") or "").strip()

        payload = {
            "model": backend.model,
            "messages": messages,
            "temperature": 0,
        }
        if prompt_patch:
            payload["frequency_penalty"] = 0
        data = self._request_json(backend, "/v1/chat/completions", payload)
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()

    def _generate(self, backend: ModelBackend, prompt: str) -> str:
        if backend.kind == "ollama":
            payload = {"model": backend.model, "prompt": prompt, "stream": False}
            data = self._request_json(backend, "/api/generate", payload)
            return str(data.get("response") or "").strip()
        return self._chat(
            backend,
            [
                {"role": "system", "content": "Return only the rewritten text."},
                {"role": "user", "content": prompt},
            ],
        )

    def _call_backend(self, backend: ModelBackend, prompt: str, prompt_patch: str = "") -> str:
        if backend.is_disabled():
            return ""
        if backend.kind == "ollama":
            return self._generate(backend, prompt)
        return self._chat(
            backend,
            [
                {"role": "system", "content": prompt_patch or "You are a concise formatting assistant."},
                {"role": "user", "content": prompt},
            ],
            prompt_patch=prompt_patch,
        )

    def normalize_markdown(self, text: str, prompt_patch: str = "") -> str:
        backend = self.select_backend("write")
        prompt = (
            "Normalize the markdown without changing meaning. "
            "Keep bullets, headings, and code fences intact.\n\n"
            f"{text.strip()}"
        )
        if backend.is_disabled():
            return _fallback_markdown(text)
        try:
            result = self._call_backend(backend, prompt, prompt_patch=prompt_patch)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
            result = ""
        return result.strip() or _fallback_markdown(text)

    def summarize_theme(self, theme_name: str, evidence_lines: Iterable[str], prompt_patch: str = "") -> str:
        evidence_list = [str(line) for line in evidence_lines]
        backend = self.select_backend("aggregate")
        prompt = "\n".join(
            [
                f"Theme: {theme_name}",
                "Summarize this theme in a compact paragraph.",
                "Evidence:",
                *[f"- {line}" for line in evidence_list],
            ]
        )
        if prompt_patch:
            prompt = f"{prompt}\n\nPatch:\n{prompt_patch.strip()}"
        if backend.is_disabled():
            head = evidence_list[:3]
            tail = f" ({len(evidence_list)} lines)" if len(evidence_list) > 3 else ""
            return f"{theme_name}: " + "; ".join(head) + tail if head else f"{theme_name}: no evidence"
        try:
            result = self._call_backend(backend, prompt, prompt_patch=prompt_patch)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
            result = ""
        if result.strip():
            return result.strip()
        if evidence_list:
            return f"{theme_name}: " + "; ".join(evidence_list[:3])
        return f"{theme_name}: no evidence"

    def render_daily_narrative(
        self,
        work_blocks: Iterable[Any],
        prompt_patch: str = "",
        user_intent: str = "",
    ) -> str:
        blocks = [
            block if isinstance(block, WorkBlock) else WorkBlock(**dict(block))
            for block in work_blocks
        ]
        # Limit to top 3 blocks by duration to avoid timeout
        sorted_blocks = sorted(blocks, key=lambda b: b.duration_sec, reverse=True)
        limited_blocks = sorted_blocks[:3]
        blocks_payload = [format_work_block_for_prompt(block) for block in limited_blocks]
        backend = self.select_backend("write")
        prompt = "\n".join(
            [
                "根据结构化工作块写一段第二人称的日报叙述。",
                "规则：",
                "1. 只能使用提供的工作块，不要补充外部事实。",
                "2. 按时间顺序组织。",
                "3. 明确写出时间、应用名和 continuity。",
                "4. 不要列表化，不要夸张。",
                "5. 只输出 `## 今日主线` 这一节的 Markdown。",
                "6. 叙述结构必须二元：先写\"你做了什么\"（仅用 user_candidates 展开），再写\"系统显示了什么\"（用 system_candidates，且外层用 <details> 折叠）。",
                "7. user_candidates 是用户真实键入/粘贴/主动保存的内容，是主语；system_candidates 是机器/屏幕呈现给用户看到的，是背景。不要混淆二者的主语。",
                "8. 如果某个时间块的 user_candidates 为空，跳过该块或归入碎片。",
                "",
                "工作块数据：",
                json.dumps(blocks_payload, ensure_ascii=False, indent=2, sort_keys=True),
            ]
        )
        if prompt_patch:
            prompt = f"{prompt}\n\nPatch:\n{prompt_patch.strip()}"
        if user_intent:
            prompt = "\n\n".join(
                [
                    prompt,
                    f'用户昨天在报告里写下："{user_intent.strip()}"\n'
                    "请把这个作为今日叙述的主线锚点，优先围绕它展开；如果今日数据与意图相关则明确呼应，无关则客观叙述。",
                ]
            )
        if backend.is_disabled():
            return _fallback_daily_narrative(blocks)
        try:
            result = self._call_backend(backend, prompt, prompt_patch=prompt_patch)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error("daily_narrative fallback backend_kind=%s url=%s model=%s exc_type=%s exc=%s", backend.kind, backend.base_url, backend.model, type(exc).__name__, exc)
            result = ""
        return result.strip() or _fallback_daily_narrative(blocks)

    def render(self, prompt: str) -> str:
        """Bare LLM call: send prompt directly to the underlying client, no template, no system instructions.
        For narrative_v2 Pass 1/2 where the caller owns the complete prompt.
        Raises on failure — the caller handles fallback.
        """
        backend = self.select_backend("write")
        if backend.is_disabled():
            raise RuntimeError("no backend available")
        return self._chat(
            backend,
            [{"role": "user", "content": prompt}],
        )

    def test_backend(self) -> dict[str, Any]:
        backend = self.select_backend("write")
        if backend.is_disabled():
            return {
                "ok": False,
                "active_profile": self.active_profile,
                "backend": backend.kind,
                "message": "no backend available",
            }
        prompt = "Reply with the single word ok."
        try:
            response = self._call_backend(backend, prompt)
            ok = bool(response.strip())
            return {
                "ok": ok,
                "active_profile": self.active_profile,
                "backend": backend.kind,
                "model": backend.model,
                "response": response.strip(),
                "prompt_hash": _stable_hash(prompt),
            }
        except Exception as exc:  # pragma: no cover - defensive network boundary
            return {
                "ok": False,
                "active_profile": self.active_profile,
                "backend": backend.kind,
                "model": backend.model,
                "error": str(exc),
            }


def load_model_gateway(config: Config, state_path: str | Path | None = None) -> ModelGateway:
    return ModelGateway(config, state_path=state_path)
