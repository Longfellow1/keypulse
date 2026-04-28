from __future__ import annotations

import getpass
import plistlib
import platform
import subprocess
from pathlib import Path


class KeychainUnavailable(RuntimeError):
    """Raised when keychain operations are requested on unsupported systems."""


class KeychainCommandError(RuntimeError):
    """Raised when the `security` command fails unexpectedly."""


def _require_macos() -> None:
    if platform.system() != "Darwin":
        raise KeychainUnavailable("macOS Keychain is unavailable on this platform")


def _account_name() -> str:
    account = (getpass.getuser() or "").strip()
    return account or "keypulse"


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["security", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise KeychainUnavailable("security command not found") from exc


def _is_not_found(result: subprocess.CompletedProcess[str]) -> bool:
    stderr = (result.stderr or "").lower()
    stdout = (result.stdout or "").lower()
    return "could not be found" in stderr or "could not be found" in stdout


def store_secret(service: str, key: str) -> None:
    """Store/update a secret in macOS Keychain."""
    _require_macos()
    result = _run_security(
        [
            "add-generic-password",
            "-s",
            service,
            "-a",
            _account_name(),
            "-w",
            key,
            "-U",
        ]
    )
    if result.returncode != 0:
        raise KeychainCommandError((result.stderr or result.stdout or "store secret failed").strip())


def read_secret(service: str) -> str | None:
    """Read a secret from macOS Keychain, returning None when not found."""
    _require_macos()
    result = _run_security(["find-generic-password", "-s", service, "-w"])
    if result.returncode == 0:
        value = (result.stdout or "").strip()
        return value or None
    if _is_not_found(result):
        return None
    raise KeychainCommandError((result.stderr or result.stdout or "read secret failed").strip())


def delete_secret(service: str) -> None:
    """Delete a secret from macOS Keychain."""
    _require_macos()
    result = _run_security(["delete-generic-password", "-s", service])
    if result.returncode == 0 or _is_not_found(result):
        return
    raise KeychainCommandError((result.stderr or result.stdout or "delete secret failed").strip())


def _daemon_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.keypulse.daemon.plist"


def _load_daemon_plist(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            payload = plistlib.load(fh)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def check_daemon_keychain_access() -> bool:
    """Best-effort check: daemon context appears compatible with keychain access."""
    if platform.system() != "Darwin":
        return False
    plist_payload = _load_daemon_plist(_daemon_plist_path())
    if bool(plist_payload.get("SessionCreate")):
        return False
    try:
        # A missing item still proves the keychain query path is available.
        read_secret("com.keypulse.model.cloud")
        return True
    except KeychainCommandError:
        return False
    except KeychainUnavailable:
        return False


def render_plist_advice() -> str:
    """Render launchd migration advice for legacy ARK_API_KEY environment usage."""
    plist_payload = _load_daemon_plist(_daemon_plist_path())
    env_vars = plist_payload.get("EnvironmentVariables") if isinstance(plist_payload, dict) else {}
    if not isinstance(env_vars, dict):
        env_vars = {}
    if "ARK_API_KEY" in env_vars:
        return (
            "daemon 现在从 Keychain 读 API Key，无需 launchd plist 注入 env。\n"
            "如需移除，可手动编辑 ~/Library/LaunchAgents/com.keypulse.daemon.plist\n"
            "把 EnvironmentVariables 里的 ARK_API_KEY 删掉，然后 launchctl reload。"
        )
    return ""
