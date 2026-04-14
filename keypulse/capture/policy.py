from __future__ import annotations
import re
from typing import Optional
from keypulse.store.models import RawEvent
from keypulse.store.repository import get_all_policies

# Policy modes
ALLOW = "allow"
DENY = "deny"
METADATA_ONLY = "metadata-only"
REDACT = "redact"
TRUNCATE = "truncate"


class PolicyEngine:
    def __init__(self):
        self._policies: list[dict] = []

    def reload(self):
        """Reload policies from DB."""
        self._policies = get_all_policies()

    def evaluate(self, event: RawEvent) -> str:
        """
        Returns the effective mode for this event.
        Default is ALLOW if no rules match.
        Priority: lower number = higher priority.
        """
        for policy in sorted(self._policies, key=lambda p: p.get("priority", 100)):
            if not policy.get("enabled", 1):
                continue
            scope_type = policy["scope_type"]
            scope_value = policy["scope_value"]
            mode = policy["mode"]

            match = False
            if scope_type == "app" and event.app_name:
                match = scope_value.lower() in event.app_name.lower()
            elif scope_type == "source":
                match = scope_value == event.source
            elif scope_type == "window" and event.window_title:
                match = scope_value.lower() in event.window_title.lower()
            elif scope_type == "content" and event.content_text:
                try:
                    match = bool(re.search(scope_value, event.content_text, re.IGNORECASE))
                except re.error:
                    match = scope_value.lower() in event.content_text.lower()

            if match:
                return mode

        return ALLOW

    def apply(self, event: RawEvent) -> Optional[RawEvent]:
        """
        Apply policy to event. Returns modified event or None if denied.
        """
        mode = self.evaluate(event)
        if mode == DENY:
            return None
        if mode == METADATA_ONLY:
            event.content_text = None
            event.content_hash = None
        if mode == REDACT:
            # Caller should run desensitizer separately; mark level
            event.sensitivity_level = max(event.sensitivity_level, 2)
        if mode == TRUNCATE:
            if event.content_text and len(event.content_text) > 500:
                event.content_text = event.content_text[:500] + "...[policy truncated]"
        return event
