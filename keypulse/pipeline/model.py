from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel

from keypulse.config import Config, ModelBackendConfig
from keypulse.pipeline.model_keychain import KeychainCommandError, KeychainUnavailable, read_secret
from keypulse.pipeline.narrative import (
    WorkBlock,
    format_work_block_for_prompt,
    render_daily_narrative as _fallback_daily_narrative,
)
from keypulse.prompts.loader import PromptCapabilityNotFoundError, load_prompt
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


class PipelineQualityError(RuntimeError):
    """Raised when LLM-backed pipeline cannot produce quality output (no backend, response empty, parse failed)."""


class LLMCallError(RuntimeError):
    """Raised when capability call fails after retries."""


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


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _stable_serialize(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, len(stripped) // 4)


def _validate_jsonschema_minimal(schema: dict[str, Any], value: Any, path: str = "$") -> None:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be object")
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        properties = schema.get("properties") or {}
        for key, prop_schema in properties.items():
            if key in value and isinstance(prop_schema, dict):
                _validate_jsonschema_minimal(prop_schema, value[key], f"{path}.{key}")
    elif schema_type == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be array")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                _validate_jsonschema_minimal(item_schema, item, f"{path}[{idx}]")
    elif schema_type == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be string")
    elif schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{path} must be integer")
    elif schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{path} must be number")
    elif schema_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be boolean")
    elif schema_type == "null":
        if value is not None:
            raise ValueError(f"{path} must be null")

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        raise ValueError(f"{path} must be one of {enum_values}")


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
        # Skip backends currently short-circuited (failing fast for backoff window).
        candidates = self._backend_candidates(stage)
        result: list[ModelBackend] = []
        for backend in candidates:
            name = self._backend_name_from_obj(backend)
            if name in {"cloud", "local"} and self._is_short_circuited(name):
                continue
            result.append(backend)
        # If all are short-circuited, return them anyway — better to try a
        # known-bad backend than to skip narrative entirely. Short-circuit
        # state will get refreshed on the attempt.
        return result or candidates

    def _backend_name_from_obj(self, backend: ModelBackend) -> str:
        config = getattr(self, "_config", None)
        if config is None:
            return "unknown"
        try:
            cloud = _model_backend_from_config(config.model.cloud)
            local = _model_backend_from_config(config.model.local)
        except AttributeError:
            return "unknown"
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
        # Prefer backends that are usable AND not in short-circuit cooldown.
        # If everything is in cooldown, fall back to the first usable one
        # (the call will refresh circuit state).
        usable: list[ModelBackend] = []
        for backend in self._backend_candidates(stage):
            if not self._is_backend_usable(backend, require_auth=False):
                continue
            usable.append(backend)
            name = self._backend_name_from_obj(backend)
            if name in {"cloud", "local"} and self._is_short_circuited(name):
                continue
            return backend
        if usable:
            return usable[0]
        return ModelBackend(kind="disabled", base_url="", model="")

    def _request_json(
        self,
        backend: ModelBackend,
        path: str,
        payload: dict[str, Any],
        method: str = "POST",
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        normalized_path = path
        if re.search(r"/v\d+$", backend.base_url) and path.startswith("/v1/"):
            normalized_path = path.removeprefix("/v1")
        url = f"{backend.base_url}{normalized_path}"
        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update({str(k): str(v) for k, v in extra_headers.items() if str(v).strip()})
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

    # Network-layer transients that warrant a single retry before tripping
    # the circuit. 4xx errors and parse errors are not in this set — they
    # need user action, retrying immediately is wasted load.
    _TRANSIENT_RETRYABLE: tuple[type[BaseException], ...] = (URLError, TimeoutError, OSError)

    def _classify_failure(self, exc: BaseException) -> tuple[int, str]:
        """Return (cooldown_minutes, reason) for short-circuit policy.

        Tuned by best-practice: auth/rate failures need user action so we
        cool down longer to stop hammering. Network blips back off shortly.
        """
        if isinstance(exc, HTTPError):
            if exc.code in (401, 403):
                return (30, f"auth_failed_{exc.code}")
            if exc.code == 429:
                return (15, "rate_limited")
            if 500 <= exc.code < 600:
                return (5, f"server_error_{exc.code}")
            return (5, f"http_{exc.code}")
        if isinstance(exc, (json.JSONDecodeError, ValueError)):
            return (5, "bad_response")
        if isinstance(exc, TimeoutError):
            return (2, "timeout")
        if isinstance(exc, URLError):
            return (1, "network_error")
        return (5, type(exc).__name__)

    def _is_retryable(self, exc: BaseException) -> bool:
        if isinstance(exc, HTTPError):
            return 500 <= exc.code < 600
        return isinstance(exc, self._TRANSIENT_RETRYABLE)

    def _resilient_call(
        self,
        backend: ModelBackend,
        prompt: str,
        prompt_patch: str | None = "",
        *,
        max_retries: int = 1,
    ) -> str:
        """Wrap _call_backend with retry-on-transient + circuit breaker.

        Records last_call (with duration) on every attempt outcome. Trips
        short-circuit on terminal failure with cooldown sized by error type.
        On success, _record_last_call clears any active circuit for the
        backend (see method body for that contract).
        """
        backend_name = self._backend_name_from_obj(backend)
        attempts = 0
        last_exc: BaseException | None = None
        started = time.monotonic()
        while True:
            attempt_start = time.monotonic()
            try:
                result = self._call_backend(backend, prompt, prompt_patch=prompt_patch)
            except BaseException as exc:  # capture all to record + classify
                last_exc = exc
                duration_ms = int((time.monotonic() - attempt_start) * 1000)
                if backend_name in {"cloud", "local"}:
                    self._record_last_call(backend_name, duration_ms=duration_ms, ok=False)
                if attempts < max_retries and self._is_retryable(exc):
                    attempts += 1
                    # Small jittered backoff between retries — no scientific
                    # need for full exponential here, single retry suffices.
                    time.sleep(0.5 * attempts)
                    continue
                # Terminal failure. Trip the circuit.
                if backend_name in {"cloud", "local"}:
                    minutes, reason = self._classify_failure(exc)
                    self._short_circuit(backend_name, minutes=minutes, reason=reason)
                raise
            duration_ms = int((time.monotonic() - attempt_start) * 1000)
            if backend_name in {"cloud", "local"}:
                self._record_last_call(backend_name, duration_ms=duration_ms, ok=True)
            return result

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

    _DEFAULT_TIER_PROVIDER_MODEL: dict[str, tuple[str, str]] = {
        "mini": ("ollama_local", "qwen2.5:7b"),
        "standard": ("deepseek", "deepseek-chat"),
        "premium": ("anthropic", "claude-sonnet-4-6"),
    }
    _PROVIDER_BASE_URL: dict[str, str] = {
        "deepseek": "https://api.deepseek.com",
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
    }
    _PROVIDER_API_ENV: dict[str, str] = {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    _TIER_PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
        "mini": (0.10, 0.20),
        "standard": (0.20, 0.80),
        "premium": (2.00, 10.00),
    }

    def _llm_root(self) -> Path:
        root = Path.home() / ".keypulse"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _llm_cache_dir(self) -> Path:
        cache_dir = self._llm_root() / "cache" / "llm"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _llm_cost_path(self) -> Path:
        path = self._llm_root() / "cost.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _capability_tier(self, capability: str, model_tier_override: str | None = None) -> str:
        if model_tier_override:
            candidate = model_tier_override.strip().lower()
            if candidate in self._DEFAULT_TIER_PROVIDER_MODEL:
                return candidate
        capability_key = capability.strip().lower()
        if capability_key in {"l2", "l2_narrative", "narrative", "l3", "l3_topic_naming", "topicnaming"}:
            return "mini"
        return str(getattr(self._config.llm, "tier", "mini")).strip() or "mini"

    def _resolve_provider_model(self, capability: str, model_tier_override: str | None = None) -> dict[str, str]:
        tier = self._capability_tier(capability, model_tier_override=model_tier_override)
        default_provider, default_model = self._DEFAULT_TIER_PROVIDER_MODEL.get(
            tier,
            self._DEFAULT_TIER_PROVIDER_MODEL["mini"],
        )
        provider_override = str(getattr(self._config.llm, "provider", "") or "").strip()
        provider = provider_override or default_provider
        model = default_model
        if provider == "ollama_local":
            base_url = str(getattr(self._config.llm, "local_ollama_url", "") or "").strip() or "http://localhost:11434"
            backend = ModelBackend(kind="ollama", base_url=base_url.rstrip("/"), model=model)
            return {"tier": tier, "provider": provider, "model": model, "base_url": backend.base_url, "kind": backend.kind}
        if provider == "anthropic":
            base_url = self._PROVIDER_BASE_URL["anthropic"]
            return {"tier": tier, "provider": provider, "model": model, "base_url": base_url, "kind": "anthropic"}
        if provider in {"deepseek", "openai"}:
            base_url = self._PROVIDER_BASE_URL[provider]
            return {"tier": tier, "provider": provider, "model": model, "base_url": base_url, "kind": "openai_compatible"}

        cloud = _model_backend_from_config(self._config.model.cloud)
        if cloud.base_url and cloud.model:
            return {
                "tier": tier,
                "provider": provider,
                "model": cloud.model,
                "base_url": cloud.base_url,
                "kind": cloud.kind,
            }
        return {"tier": tier, "provider": provider, "model": model, "base_url": "", "kind": "disabled"}

    def _cache_key(
        self,
        *,
        capability: str,
        prompt_version: str | None,
        prompt: str,
        model_name: str,
        input_data: Any,
    ) -> str:
        payload = {
            "capability": capability,
            "prompt_version": prompt_version or "",
            "prompt": prompt,
            "model": model_name,
            "input_data": input_data,
        }
        return hashlib.sha256(_stable_serialize(payload).encode("utf-8")).hexdigest()

    def _cache_file(self, cache_key: str) -> Path:
        return self._llm_cache_dir() / f"{cache_key}.json"

    def _read_cache_payload(self, cache_path: Path) -> dict[str, Any] | None:
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _cache_is_fresh(self, payload: dict[str, Any]) -> bool:
        ts = _parse_iso_datetime(str(payload.get("ts") or ""))
        if not ts:
            return False
        ttl_days = int(payload.get("ttl_days") or 30)
        return _utc_now() <= ts + timedelta(days=ttl_days)

    def _append_cost_row(
        self,
        *,
        capability: str,
        model_name: str,
        tier: str,
        in_tokens: int,
        out_tokens: int,
        cost_usd: float,
        cache_hit: bool,
        prompt_version: str | None,
    ) -> None:
        row = {
            "ts": _utc_now_iso(),
            "capability": capability,
            "model": model_name,
            "tier": tier,
            "in_tokens": int(in_tokens),
            "out_tokens": int(out_tokens),
            "cost_usd": round(float(cost_usd), 8),
            "cache_hit": bool(cache_hit),
            "prompt_version": prompt_version or "",
        }
        with self._llm_cost_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _schema_validate(self, schema: Any, output_text: str) -> Any:
        if schema is None:
            return output_text
        if isinstance(schema, dict):
            parsed = json.loads(output_text)
            _validate_jsonschema_minimal(schema, parsed)
            return parsed
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate_json(output_text)
        if hasattr(schema, "model_validate_json"):
            return schema.model_validate_json(output_text)
        if hasattr(schema, "model_validate"):
            parsed = json.loads(output_text)
            return schema.model_validate(parsed)
        raise TypeError("unsupported schema type")

    def _estimate_cost_usd(self, *, provider: str, tier: str, in_tokens: int, out_tokens: int) -> float:
        if provider == "ollama_local":
            return 0.0
        in_price, out_price = self._TIER_PRICES_PER_MILLION.get(tier, self._TIER_PRICES_PER_MILLION["mini"])
        return (max(in_tokens, 0) * in_price + max(out_tokens, 0) * out_price) / 1_000_000

    def _call_capability_backend(
        self,
        *,
        provider: str,
        model: str,
        kind: str,
        base_url: str,
        prompt: str,
        max_tokens: int | None,
        temperature: float | None,
        tier: str,
        cache_control: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        if kind == "disabled" or not base_url.strip():
            raise NoBackendAvailable(f"no backend for provider={provider}")

        if kind == "anthropic":
            backend = ModelBackend(kind="anthropic", base_url=base_url.rstrip("/"), model=model)
            api_key = _env_value(self._PROVIDER_API_ENV["anthropic"])
            if not api_key:
                raise NoBackendAvailable("missing ANTHROPIC_API_KEY")
            headers: dict[str, str] = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            if cache_control:
                headers.update({str(k): str(v) for k, v in cache_control.items()})
            payload = {
                "model": model,
                "max_tokens": int(max_tokens or 1024),
                "temperature": float(0 if temperature is None else temperature),
                "messages": [{"role": "user", "content": prompt}],
            }
            data = self._request_json(backend, "/v1/messages", payload, extra_headers=headers)
            content = data.get("content") or []
            text_parts = [
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and str(block.get("type") or "") == "text"
            ]
            text = "\n".join(part for part in text_parts if part).strip()
            usage = data.get("usage") or {}
            in_tokens = int(usage.get("input_tokens") or _estimate_tokens(prompt))
            out_tokens = int(usage.get("output_tokens") or _estimate_tokens(text))
            cost_usd = self._estimate_cost_usd(provider=provider, tier=tier, in_tokens=in_tokens, out_tokens=out_tokens)
            return {"text": text, "in_tokens": in_tokens, "out_tokens": out_tokens, "cost_usd": cost_usd}

        if kind == "ollama":
            backend = ModelBackend(kind="ollama", base_url=base_url.rstrip("/"), model=model)
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": float(0 if temperature is None else temperature)},
            }
            if max_tokens is not None:
                payload["options"]["num_predict"] = int(max_tokens)
            data = self._request_json(backend, "/api/chat", payload, extra_headers=cache_control)
            message = data.get("message") or {}
            text = str(message.get("content") or "").strip()
            in_tokens = int(data.get("prompt_eval_count") or _estimate_tokens(prompt))
            out_tokens = int(data.get("eval_count") or _estimate_tokens(text))
            cost_usd = 0.0
            return {"text": text, "in_tokens": in_tokens, "out_tokens": out_tokens, "cost_usd": cost_usd}

        api_env = self._PROVIDER_API_ENV.get(provider, getattr(self._config.model.cloud, "api_key_env", ""))
        backend = ModelBackend(
            kind="openai_compatible",
            base_url=base_url.rstrip("/"),
            model=model,
            api_key_env=api_env,
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": float(0 if temperature is None else temperature),
            "max_tokens": int(max_tokens or 1024),
        }
        data = self._request_json(backend, "/v1/chat/completions", payload, extra_headers=cache_control)
        choices = data.get("choices") or []
        message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
        text = str((message or {}).get("content") or "").strip()
        usage = data.get("usage") or {}
        in_tokens = int(usage.get("prompt_tokens") or _estimate_tokens(prompt))
        out_tokens = int(usage.get("completion_tokens") or _estimate_tokens(text))
        cost_usd = self._estimate_cost_usd(provider=provider, tier=tier, in_tokens=in_tokens, out_tokens=out_tokens)
        return {"text": text, "in_tokens": in_tokens, "out_tokens": out_tokens, "cost_usd": cost_usd}

    def call(
        self,
        capability: str,
        prompt: str,
        schema: Any = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        prompt_version: str | None = None,
        *,
        input_data: Any = None,
        cache_control: Mapping[str, str] | None = None,
        retries: int | None = None,
    ) -> Any:
        prompt_spec = None
        try:
            prompt_spec = load_prompt(capability)
        except PromptCapabilityNotFoundError:
            prompt_spec = None

        if prompt_spec is not None:
            if schema is None:
                schema = prompt_spec.output_schema
            if max_tokens is None:
                max_tokens = prompt_spec.max_tokens
            if temperature is None:
                temperature = prompt_spec.temperature
            if prompt_version is None:
                prompt_version = f"{prompt_spec.capability}.{prompt_spec.version}"

        target = self._resolve_provider_model(
            capability,
            model_tier_override=(prompt_spec.model_tier if prompt_spec is not None else None),
        )
        tier = str(target["tier"])
        provider = str(target["provider"])
        model = str(target["model"])
        kind = str(target["kind"])
        base_url = str(target["base_url"])
        model_name = f"{provider}/{model}"

        cache_key = self._cache_key(
            capability=capability,
            prompt_version=prompt_version,
            prompt=prompt,
            model_name=model_name,
            input_data=input_data,
        )
        cache_path = self._cache_file(cache_key)
        cache_payload = self._read_cache_payload(cache_path)
        if cache_payload and self._cache_is_fresh(cache_payload):
            try:
                cached_output = str(cache_payload.get("output") or "")
                validated_cached = self._schema_validate(schema, cached_output)
                self._append_cost_row(
                    capability=capability,
                    model_name=model_name,
                    tier=tier,
                    in_tokens=0,
                    out_tokens=0,
                    cost_usd=0.0,
                    cache_hit=True,
                    prompt_version=prompt_version,
                )
                return validated_cached
            except Exception:
                logger.warning("cache decode/validation failed for %s; refreshing", cache_key)

        max_attempts = max(1, int((2 if retries is None else retries)) + 1)
        last_error: Exception | None = None
        for _ in range(max_attempts):
            try:
                result = self._call_capability_backend(
                    provider=provider,
                    model=model,
                    kind=kind,
                    base_url=base_url,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tier=tier,
                    cache_control=cache_control,
                )
                output_text = str(result.get("text") or "").strip()
                validated = self._schema_validate(schema, output_text)
                in_tokens = int(result.get("in_tokens") or _estimate_tokens(prompt))
                out_tokens = int(result.get("out_tokens") or _estimate_tokens(output_text))
                cost_usd = float(result.get("cost_usd") or 0.0)

                payload = {
                    "key": cache_key,
                    "input": {
                        "capability": capability,
                        "prompt_version": prompt_version or "",
                        "prompt": prompt,
                        "model": model_name,
                        "input_data": input_data,
                    },
                    "output": output_text,
                    "ts": _utc_now_iso(),
                    "ttl_days": 30,
                    "model": model_name,
                    "tier": tier,
                    "in_tokens": in_tokens,
                    "out_tokens": out_tokens,
                    "cost_usd": cost_usd,
                    "prompt_version": prompt_version or "",
                    "capability": capability,
                }
                atomic_write_text(
                    cache_path,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                )
                self._append_cost_row(
                    capability=capability,
                    model_name=model_name,
                    tier=tier,
                    in_tokens=in_tokens,
                    out_tokens=out_tokens,
                    cost_usd=cost_usd,
                    cache_hit=False,
                    prompt_version=prompt_version,
                )
                return validated
            except Exception as exc:
                last_error = exc
                continue
        raise LLMCallError(f"capability={capability} failed after {max_attempts} attempts: {last_error}") from last_error

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
            result = self._resilient_call(backend, prompt, prompt_patch=prompt_patch)
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
            result = self._resilient_call(backend, prompt, prompt_patch=prompt_patch)
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
            result = self._resilient_call(backend, prompt, prompt_patch=prompt_patch)
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
        """Bare LLM call with automatic backend fallback for the chosen stage.

        Each backend goes through _resilient_call (single retry on transient
        + circuit breaker), then we walk down the fallback order. A backend
        with an active short-circuit is skipped early by _fallback_order.
        """
        last_error: Exception | None = None
        for backend in self._fallback_order(stage):
            if not self._is_backend_usable(backend, require_auth=False):
                continue
            try:
                return self._resilient_call(backend, prompt, prompt_patch=None)
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
