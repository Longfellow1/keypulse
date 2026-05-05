from __future__ import annotations

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts, truthy_env
from keypulse.config import Config
from keypulse.store.repository import get_state


class LLMBackendCapability(Capability):
    name = "llm_backend"
    level_when_failed = "warn"
    label_when_failed = "LLM 异常"
    error_codes = {"llm_no_key", "llm_invalid_key", "llm_timeout"}

    _HINTS = {
        "llm_no_key": "LLM API key 未配置，请前往 ~/.keypulse/secrets.env 配置",
        "llm_invalid_key": "LLM API key 失效或余额不足，请检查",
        "llm_timeout": "LLM 调用超时，请检查网络",
    }

    _REQUIRE_CLOUD_PROFILES = {"cloud-first", "cloud-only", "auto"}

    def _state_code(self) -> str:
        return str(get_state("llm_error_code") or "").strip()

    def _needs_cloud_key(self, cfg: Config) -> bool:
        profile = str(cfg.model.active_profile or "")
        if profile not in self._REQUIRE_CLOUD_PROFILES:
            return False
        return str(cfg.model.cloud.kind or "") == "openai_compatible"

    def _probe(self) -> CheckResult:
        code = self._state_code()
        if code in self._HINTS:
            return CheckResult(ok=False, code=code, hint=self._HINTS[code])

        cfg = Config.load()
        if self._needs_cloud_key(cfg):
            key_env = str(cfg.model.cloud.api_key_env or "").strip()
            if key_env and not truthy_env(key_env):
                return CheckResult(ok=False, code="llm_no_key", hint=self._HINTS["llm_no_key"])

        return CheckResult(ok=True, code="ok")

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="skipped")

    def monitor(self) -> HealthState:
        check = self._probe()
        return HealthState(ok=check.ok, code=check.code, last_checked=now_ts(), detail=check.hint)

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        hint = self._HINTS.get(state.code, "LLM 调用异常，请稍后重试")
        return Signal(level=self.level_when_failed, label=self.label_when_failed, hint=hint, action=None)
