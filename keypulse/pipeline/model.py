from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from keypulse.config import Config, ModelBackendConfig
from keypulse.pipeline.model_keychain import KeychainCommandError, KeychainUnavailable, read_secret
from keypulse.pipeline.narrative import (
    WorkBlock,
    format_work_block_for_prompt,
    render_daily_narrative as _fallback_daily_narrative,
)
from keypulse.utils.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)


PROFILE_NAMES = {
    "local-first",
    "cloud-first",
    "cloud-only",
    "local-only",
    "auto",
    "privacy-locked",
}


class NoBackendAvailable(RuntimeError):
    """Raised when no model backend can serve a request."""


@dataclass(frozen=True)
class ModelBackend:
    kind: str
    base_url: str
    model: str
    api_key_source: str = ""
    api_key_env: str = ""
    timeout_sec: int = 20

    def is_disabled(self) -> bool:
        return self.kind == "disabled"


class FallbackPolicy:
    """Resolve backend priority order by active profile."""

    def get_backend_order(self, profile: str, stage: str) -> list[str]:
        if profile == "cloud-first":
            return ["cloud", "local"]
        if profile == "local-first":
            return ["local", "cloud"]
        if profile == "cloud-only":
            return ["cloud"]
        if profile == "local-only":
            return ["local"]
        if profile == "auto":
            return ["local", "cloud"] if stage == "write" else ["cloud", "local"]
        if profile == "privacy-locked":
            return []
        return ["local", "cloud"]


def _state_path(path: str | Path) -> Path:
    return Path(path).expanduser()


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def _state_file_lock(path: Path):
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _env_value(name: str) -> str:
    return os.environ.get(name, "").strip() if name else ""


def _model_backend_from_config(config: ModelBackendConfig) -> ModelBackend:
    return ModelBackend(
        kind=config.kind,
        base_url=config.base_url.strip().rstrip("/"),
        model=config.model.strip(),
        api_key_source=getattr(config, "api_key_source", "").strip(),
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _short_circuit_payload(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    payload: dict[str, Any] = {
        "short_circuits": raw.get("short_circuits") if isinstance(raw.get("short_circuits"), dict) else {},
        "last_call": raw.get("last_call") if isinstance(raw.get("last_call"), dict) else {},
    }
    if "active_profile" in raw:
        payload["active_profile"] = raw["active_profile"]
    return payload


class ModelGateway:
    def __init__(self, config: Config, state_path: str | Path | None = None):
        self._config = config
        self._state_path = _state_path(state_path or config.model.state_path)
        self._state = _short_circuit_payload(_read_state(self._state_path))
        self._policy = FallbackPolicy()

    @property
    def active_profile(self) -> str:
        latest = self._sync_state()
        profile = str(latest.get("active_profile") or self._config.model.active_profile)
        return profile if profile in PROFILE_NAMES else self._config.model.active_profile

    def use_profile(self, profile: str) -> str:
        if profile not in PROFILE_NAMES:
            raise ValueError(f"unknown profile: {profile}")

        def mutator(state: dict[str, Any]) -> None:
            state["active_profile"] = profile

        self._update_state(mutator)
        return profile

    def _sync_state(self) -> dict[str, Any]:
        self._state = _short_circuit_payload(_read_state(self._state_path))
        return self._state

    def _update_state(self, mutator) -> dict[str, Any]:
        with _state_file_lock(self._state_path):
            state = _short_circuit_payload(_read_state(self._state_path))
            mutator(state)
            _write_state(self._state_path, state)
            self._state = state
            return state

    def _backend_map(self) -> dict[str, ModelBackend]:
        return {
            "local": _model_backend_from_config(self._config.model.local),
            "cloud": _model_backend_from_config(self._config.model.cloud),
        }

    def backend_order(self, stage: str = "write") -> list[str]:
        return self._policy.get_backend_order(self.active_profile, stage)

    def _backend_candidates(self, stage: str) -> list[ModelBackend]:
        mapping = self._backend_map()
        return [mapping[name] for name in self.backend_order(stage) if name in mapping]

    def _fallback_order(self, stage: str = "write") -> list[ModelBackend]:
        return self._backend_candidates(stage)

    def _backend_name_from_obj(self, backend: ModelBackend) -> str:
        cloud = _model_backend_from_config(self._config.model.cloud)
        local = _model_backend_from_config(self._config.model.local)
        if backend.kind == cloud.kind and backend.base_url == cloud.base_url and backend.model == cloud.model:
            return "cloud"
        if backend.kind == local.kind and backend.base_url == local.base_url and backend.model == local.model:
            return "local"
        return "unknown"

    def _resolve_api_key(self, backend: ModelBackend) -> str | None:
        source = (backend.api_key_source or "").strip()
        if source.startswith("keychain:"):
            service = source.split(":", 1)[1].strip()
            if service:
                try:
                    secret = read_secret(service)
                except (KeychainUnavailable, KeychainCommandError):
                    secret = None
                if secret:
                    return secret
        env_value = _env_value(backend.api_key_env)
        return env_value or None

    def _auth_mode(self, backend: ModelBackend) -> str:
        if backend.kind != "openai_compatible":
            return "none"
        source = (backend.api_key_source or "").strip()
        if source.startswith("keychain:"):
            service = source.split(":", 1)[1].strip()
            if service:
                try:
                    if read_secret(service):
                        return "keychain"
                except (KeychainUnavailable, KeychainCommandError):
                    pass
            if _env_value(backend.api_key_env):
                return "env"
            return "missing"
        if _env_value(backend.api_key_env):
            return "env"
        return "missing"

    def _is_backend_usable(self, backend: ModelBackend, *, require_auth: bool = False) -> bool:
        if backend.is_disabled() or not backend.model or not backend.base_url:
            return False
        if backend.kind in {"lm_studio", "ollama"}:
            return True
        if backend.kind == "openai_compatible":
            if not require_auth:
                return True
            return bool(self._resolve_api_key(backend))
        return False

    def select_backend(self, stage: str = "write") -> ModelBackend:
        for backend in self._backend_candidates(stage):
            if self._is_backend_usable(backend, require_auth=False):
                return backend
        return ModelBackend(kind="disabled", base_url="", model="")

    def _request_json(
        self,
        backend: ModelBackend,
        path: str,
        payload: dict[str, Any],
        method: str = "POST",
    ) -> dict[str, Any]:
        normalized_path = path
        if re.search(r"/v\d+$", backend.base_url) and path.startswith("/v1/"):
            normalized_path = path.removeprefix("/v1")
        url = f"{backend.base_url}{normalized_path}"
        headers = {"Content-Type": "application/json"}
        if backend.kind == "openai_compatible":
            resolved_key = self._resolve_api_key(backend)
            if resolved_key:
                headers["Authorization"] = f"Bearer {resolved_key}"
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

    def _call_backend(self, backend: ModelBackend, prompt: str, prompt_patch: str | None = "") -> str:
        if backend.is_disabled():
            return ""
        if prompt_patch is None:
            return self._chat(backend, [{"role": "user", "content": prompt}])
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

    def _short_circuit_entry(self, backend_name: str) -> dict[str, Any] | None:
        state = self._sync_state()
        raw = (state.get("short_circuits") or {}).get(backend_name)
        return raw if isinstance(raw, dict) else None

    def _clear_short_circuit(self, backend_name: str) -> None:
        def mutator(state: dict[str, Any]) -> None:
            short_circuits = state.setdefault("short_circuits", {})
            short_circuits[backend_name] = None

        self._update_state(mutator)

    def _is_short_circuited(self, backend_name: str) -> bool:
        entry = self._short_circuit_entry(backend_name)
        if not entry:
            return False
        until = _parse_iso_datetime(str(entry.get("until") or ""))
        if not until:
            return False
        if until <= _utc_now():
            self._clear_short_circuit(backend_name)
            return False
        return True

    def _short_circuit(self, backend_name: str, *, minutes: int, reason: str) -> None:
        now = _utc_now()

        def mutator(state: dict[str, Any]) -> None:
            short_circuits = state.setdefault("short_circuits", {})
            current = short_circuits.get(backend_name) if isinstance(short_circuits.get(backend_name), dict) else {}
            fail_count = int((current or {}).get("fail_count") or 0) + 1
            short_circuits[backend_name] = {
                "until": (now + timedelta(minutes=minutes)).isoformat(),
                "reason": reason,
                "fail_count": fail_count,
            }

        self._update_state(mutator)

    def _record_last_call(self, backend_name: str, *, duration_ms: int, ok: bool) -> None:
        now = _utc_now()

        def mutator(state: dict[str, Any]) -> None:
            last_call = state.setdefault("last_call", {})
            last_call[backend_name] = {
                "at": now.isoformat(),
                "duration_ms": int(duration_ms),
                "ok": bool(ok),
            }
            if ok:
                short_circuits = state.setdefault("short_circuits", {})
                short_circuits[backend_name] = None

        self._update_state(mutator)

    def get_state(self) -> dict[str, Any]:
        return _short_circuit_payload(self._sync_state())

    def backend_status(self, stage: str = "write") -> dict[str, Any]:
        mapping = self._backend_map()
        state = self.get_state()
        short_circuits = state.get("short_circuits") or {}
        last_call = state.get("last_call") or {}
        info: dict[str, Any] = {}

        for name in ("cloud", "local"):
            backend = mapping[name]
            info[name] = {
                "kind": backend.kind,
                "model": backend.model,
                "base_url": backend.base_url,
                "auth_mode": self._auth_mode(backend),
                "usable": self._is_backend_usable(backend, require_auth=True),
                "short_circuit": short_circuits.get(name),
                "last_call": last_call.get(name),
            }

        return {
            "profile": self.active_profile,
            "order": self.backend_order(stage),
            "backends": info,
        }

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
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError, NoBackendAvailable):
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
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError, NoBackendAvailable):
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
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError, NoBackendAvailable) as exc:
            logger.error(
                "daily_narrative fallback backend_kind=%s url=%s model=%s exc_type=%s exc=%s",
                backend.kind,
                backend.base_url,
                backend.model,
                type(exc).__name__,
                exc,
            )
            result = ""
        return result.strip() or _fallback_daily_narrative(blocks)

    def render(self, prompt: str, *, stage: str = "write") -> str:
        """Bare LLM call with automatic backend fallback for the chosen stage."""
        last_error: Exception | None = None
        for backend in self._fallback_order(stage):
            if not self._is_backend_usable(backend, require_auth=False):
                continue
            try:
                return self._call_backend(backend, prompt, prompt_patch=None)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "backend %s failed (%s), trying next",
                    backend.kind,
                    type(exc).__name__,
                )
                continue

        if last_error is not None:
            raise last_error
        raise NoBackendAvailable("no backend available")

    def test_backend(self) -> dict[str, Any]:
        prompt = "Reply with the single word ok."
        try:
            response = self.render(prompt, stage="write")
            status = self.backend_status("write")
            order = status.get("order") or []
            last_call = status.get("backends", {}).get(order[0], {}).get("last_call") if order else None
            return {
                "ok": bool(response.strip()),
                "active_profile": self.active_profile,
                "backend": order[0] if order else "disabled",
                "model": (status.get("backends", {}).get(order[0], {}).get("model") if order else ""),
                "response": response.strip(),
                "prompt_hash": _stable_hash(prompt),
                "last_call": last_call,
            }
        except Exception as exc:  # pragma: no cover - defensive network boundary
            return {
                "ok": False,
                "active_profile": self.active_profile,
                "backend": "disabled",
                "model": "",
                "error": str(exc),
            }

def load_model_gateway(config: Config, state_path: str | Path | None = None) -> ModelGateway:
    return ModelGateway(config, state_path=state_path)
