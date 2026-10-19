#!/usr/bin/env bash
# Penpal — One-click installer for macOS
# Usage:
#   bash install.sh                 # public Homebrew install
#   bash install.sh --dev           # local editable install fallback
#   bash install.sh --no-brew       # alias for --dev
#   bash install.sh --dev --no-launchd
set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  →${RESET} $*"; }
success() { echo -e "${GREEN}  ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}  ⚠${RESET} $*"; }
error()   { echo -e "${RED}  ✗${RESET} $*" >&2; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── Flags ─────────────────────────────────────────────────────────────────────
INSTALL_LAUNCHD=true
DEV_MODE=false
NO_BREW=false
for arg in "$@"; do
  case $arg in
    --no-launchd) INSTALL_LAUNCHD=false ;;
    --dev)        DEV_MODE=true ;;
    --no-brew)    DEV_MODE=true; NO_BREW=true ;;
    -h|--help)
      cat <<'HELP'
Penpal installer

Usage:
  bash install.sh                 Install via Homebrew, then run penpal install init
  bash install.sh --dev           Developer fallback: editable install into ~/.penpal/venv
  bash install.sh --no-brew       Alias for --dev
  bash install.sh --dev --no-launchd
HELP
      exit 0
      ;;
    *) error "Unknown option: $arg"; exit 1 ;;
  esac
done

# ── Paths ─────────────────────────────────────────────────────────────────────
PENPAL_DIR="$HOME/.penpal"
VENV_DIR="$PENPAL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$REPO_DIR/scripts/compat-wrapper.sh"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/com.penpal.daemon.plist"
HEALTHCHECK_PLIST="$HOME/Library/LaunchAgents/com.penpal.healthcheck.plist"
CONFIG_FILE="$PENPAL_DIR/config.toml"

# ── Detect shell profile ───────────────────────────────────────────────────────
detect_profile() {
  if [[ -n "${ZSH_VERSION:-}" ]] || [[ "$SHELL" == */zsh ]]; then
    echo "$HOME/.zshrc"
  elif [[ -n "${BASH_VERSION:-}" ]] || [[ "$SHELL" == */bash ]]; then
    [[ -f "$HOME/.bash_profile" ]] && echo "$HOME/.bash_profile" || echo "$HOME/.bashrc"
  else
    echo "$HOME/.profile"
  fi
}

write_plist_if_needed() {
  local path="$1"
  local content="$2"
  local label="$3"

  if [[ -f "$path" ]] && grep -qF "$label" "$path" && cmp -s <(printf '%s' "$content") "$path"; then
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  printf '%s' "$content" > "$path"
}

reload_launchd_plist() {
  local path="$1"
  local success_message="$2"
  local bootstrap_success_message="$3"
  local fallback_message="$4"

  launchctl unload "$path" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$path" 2>/dev/null || true

  if launchctl load "$path" 2>/dev/null; then
    success "$success_message"
  else
    warn "launchd load failed (may need macOS 13+ syntax)"
    info "Trying bootstrap…"
    launchctl bootstrap "gui/$(id -u)" "$path" 2>/dev/null && \
      success "$bootstrap_success_message" || \
      warn "$fallback_message"
  fi
}

# Old KeyPulse daemon/healthcheck labels and removed Obsidian jobs must be gone before install.
remove_legacy_obsidian_plists() {
  local label path
  for label in \
    com.keypulse.daemon \
    com.keypulse.healthcheck \
    com.keypulse.hud \
    com.keypulse.obsidian-sync \
    com.keypulse.obsidian-sync-hourly; do
    path="$HOME/Library/LaunchAgents/$label.plist"
    if [[ -e "$path" ]]; then
      launchctl bootout "gui/$(id -u)" "$path" 2>/dev/null || true
      launchctl unload "$path" 2>/dev/null || true
      rm -f "$path" 2>/dev/null || true
      info "Removed legacy launchd job: $(basename "$path")"
    else
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      launchctl remove "$label" 2>/dev/null || true
    fi
    if launchctl list 2>/dev/null | grep -qF "$label"; then
      error "Legacy launchd service $label is still loaded; unload it before continuing."
      return 1
    fi
  done
  return 0
}

# ─────────────────────────────────────────────────────────────────────────────
header "Penpal Installer"
echo "  Repo:    $REPO_DIR"
if [[ "$DEV_MODE" == true ]]; then
  echo "  Mode:    developer editable install"
  echo "  Venv:    $VENV_DIR"
  echo "  Bin:     $BIN_DIR/penpal"
else
  echo "  Mode:    Homebrew public install"
  echo "  Tap:     Longfellow1/penpal"
  echo "  Formula: penpal"
fi
echo "  Config:  $CONFIG_FILE"
[[ "$DEV_MODE" == true ]] && warn "Dev mode enabled (editable install)"
[[ "$NO_BREW" == true ]] && warn "--no-brew selected; using developer fallback"

# ── 1. macOS check ────────────────────────────────────────────────────────────
header "1/7  Checking system"

if [[ "$(uname)" != "Darwin" ]]; then
  error "macOS required. Got: $(uname)"
  exit 1
fi
success "macOS $(sw_vers -productVersion)"

# Select Python before unloading old jobs; migration itself must happen only after
# the old KeyPulse launchd services have been booted out.
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" &>/dev/null; then
    ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    major=${ver%%.*}; minor=${ver##*.}
    if [[ $major -ge 3 && $minor -ge 11 ]]; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  error "Python 3.11+ not found."
  echo "  Install via Homebrew:  brew install python@3.12"
  echo "  Or via python.org:     https://www.python.org/downloads/"
  exit 1
fi
success "Python $($PYTHON --version 2>&1 | awk '{print $2}')  ($PYTHON)"

# Stop old services before get_data_dir() can rename their working directory.
remove_legacy_obsidian_plists

# The helper uses only the standard library and checkout code; it never copies on EXDEV.
if ! PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -c '
import sys
from penpal.utils.paths import DataDirMigrationError, get_data_dir
try:
    get_data_dir()
except DataDirMigrationError as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
'; then
  error "Data directory migration failed; the installer stopped without merging or copying data."
  exit 1
fi

if [[ "$DEV_MODE" != true ]]; then
  header "2/4  Checking Homebrew"
  if ! command -v brew &>/dev/null; then
    error "Homebrew is required for the public installer."
    echo "  Install Homebrew first:"
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    exit 1
  fi
  success "Homebrew $(brew --version | head -n 1 | awk '{print $2}')"

  header "3/4  Installing Penpal"
  # Users of the pre-rename tap (Longfellow1/keypulse) get it removed. If it still has
  # installed formulae, brew refuses; that is tolerated and the new tap is added anyway.
  old_taps="$(brew tap 2>/dev/null || true)"
  if printf '%s\n' "$old_taps" | grep -qix 'longfellow1/keypulse'; then
    info "Removing old tap Longfellow1/keypulse…"
    brew untap longfellow1/keypulse 2>/dev/null || warn "Old tap Longfellow1/keypulse still has installed formulae; leaving it in place"
  fi
  info "Tapping Longfellow1/penpal…"
  brew tap Longfellow1/penpal
  if brew list --formula penpal &>/dev/null; then
    info "Penpal already installed; upgrading…"
    brew upgrade penpal || brew reinstall penpal
  else
    brew install penpal
  fi
  success "penpal executable installed"

  header "4/4  Initializing runtime"
  penpal install init
  mkdir -p "$BIN_DIR"
  penpal_install_compat_wrapper "$BIN_DIR/keypulse"

  echo ""
  echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo -e "${GREEN}${BOLD}  Penpal installed successfully!${RESET}"
  echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  echo ""
  echo "  Next steps:"
  echo -e "     ${CYAN}penpal doctor${RESET}"
  echo ""
  exit 0
fi

# ── 3. Create venv ────────────────────────────────────────────────────────────
header "2/7  Setting up virtual environment"

mkdir -p "$PENPAL_DIR"

if [[ -d "$VENV_DIR" ]]; then
  stale_shebang=false
  for entrypoint in "$VENV_DIR/bin/penpal" "$VENV_DIR/bin/keypulse"; do
    [[ -f "$entrypoint" ]] || continue
    IFS= read -r shebang < "$entrypoint" || true
    if [[ "$shebang" == *"$HOME/.keypulse/venv/"* ]]; then
      stale_shebang=true
      break
    fi
  done
  if [[ "$stale_shebang" == true ]]; then
    warn "Moved venv entry point still targets ~/.keypulse/venv; rebuilding it under ~/.penpal/venv."
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
    success "Venv rebuilt after data-directory migration"
  else
    warn "Venv already exists at $VENV_DIR — reusing"
  # Ensure it's usable
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
      warn "Venv looks broken, recreating…"
      rm -rf "$VENV_DIR"
      "$PYTHON" -m venv "$VENV_DIR"
      success "Venv recreated"
    fi
  fi
else
  info "Creating venv…"
  "$PYTHON" -m venv "$VENV_DIR"
  success "Venv created"
fi

VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# Disable global pip --user config inside venv (Homebrew Python sets this globally)
export PIP_USER=false

# Upgrade pip silently (suppress version notice)
"$VENV_PIP" install --upgrade pip --quiet 2>/dev/null || true

# ── 4. Install Penpal ───────────────────────────────────────────────────────
header "3/7  Installing Penpal"

info "Editable install from $REPO_DIR"
"$VENV_PIP" install --no-user -e "$REPO_DIR" --quiet
success "Penpal installed"

# Install macOS-specific pyobjc — try prebuilt wheel first, then source
info "Installing macOS dependencies (pyobjc)…"
if "$VENV_PIP" install --no-user \
    "pyobjc-framework-AppKit" \
    "pyobjc-framework-Quartz" \
    --quiet 2>/dev/null; then
  success "pyobjc installed"
else
  warn "pyobjc wheel not available for this Python — trying with --no-binary (slower)…"
  if "$VENV_PIP" install --no-user \
      "pyobjc-framework-AppKit" \
      "pyobjc-framework-Quartz" \
      --no-binary pyobjc-framework-AppKit,pyobjc-framework-Quartz \
      --quiet 2>/dev/null; then
    success "pyobjc installed from source"
  else
    warn "pyobjc install failed — window/clipboard watchers will be disabled"
    warn "Install manually later:  $VENV_PIP install pyobjc-framework-AppKit pyobjc-framework-Quartz"
  fi
fi

# Quick sanity check
"$VENV_DIR/bin/penpal" --help &>/dev/null && \
  success "Binary works" || \
  { error "penpal binary not working after install"; exit 1; }

# ── 5. Wrapper + PATH ─────────────────────────────────────────────────────────
header "4/7  Installing penpal command"

mkdir -p "$BIN_DIR"

# Write wrapper that directly calls into venv (no activation needed)
cat > "$BIN_DIR/penpal" << WRAPPER
#!/usr/bin/env bash
exec "$VENV_DIR/bin/penpal" "\$@"
WRAPPER
chmod +x "$BIN_DIR/penpal"
success "Wrapper written: $BIN_DIR/penpal"
penpal_install_compat_wrapper "$BIN_DIR/keypulse"

# Auto-add ~/.local/bin to shell profile if not already there
PROFILE="$(detect_profile)"
PATH_LINE="export PATH=\"\$HOME/.local/bin:\$PATH\""

if echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
  success "~/.local/bin already in PATH"
else
  if grep -qF '.local/bin' "$PROFILE" 2>/dev/null; then
    warn "~/.local/bin line already in $PROFILE (but not active yet)"
  else
    echo "" >> "$PROFILE"
    echo "# Added by Penpal installer" >> "$PROFILE"
    echo "$PATH_LINE" >> "$PROFILE"
    success "Added ~/.local/bin to $PROFILE"
  fi
  warn "PATH updated — run: source $PROFILE"
  # Also export for the remainder of this script
  export PATH="$BIN_DIR:$PATH"
fi

# ── 6. Default config ─────────────────────────────────────────────────────────
header "5/7  Configuration"

if [[ -f "$CONFIG_FILE" ]]; then
  warn "Config already exists — skipping ($CONFIG_FILE)"
else
  cp "$REPO_DIR/config.toml" "$CONFIG_FILE"
  success "Default config written to $CONFIG_FILE"
fi

# ── 7. launchd (auto-start on login) ─────────────────────────────────────────
header "6/7  launchd auto-start"

if [[ "$INSTALL_LAUNCHD" == false ]]; then
  warn "Skipped (--no-launchd)"
else
  DAEMON_PLIST_CONTENT=$(cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.penpal.daemon</string>

    <key>ProgramArguments</key>
    <array>
        <string>$BIN_DIR/penpal</string>
        <string>serve</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>$PENPAL_DIR/launchd.log</string>

    <key>StandardErrorPath</key>
    <string>$PENPAL_DIR/launchd.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$BIN_DIR:/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLIST
)

  if [[ -f "$LAUNCHD_PLIST" ]]; then
    warn "launchd plist already exists — refreshing managed contents"
  fi
  write_plist_if_needed "$LAUNCHD_PLIST" "$DAEMON_PLIST_CONTENT" "com.penpal.daemon"

  if launchctl load "$LAUNCHD_PLIST" 2>/dev/null; then
    success "launchd plist installed — Penpal will start on login"
  else
    warn "launchd load failed (may need macOS 13+ syntax)"
    info "Trying bootstrap…"
    launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_PLIST" 2>/dev/null && \
      success "launchd bootstrap succeeded" || \
      warn "Try manually: launchctl load '$LAUNCHD_PLIST'"
  fi
fi

# ── 8. launchd healthcheck job ───────────────────────────────────────────────
header "7/7  launchd healthcheck job"

if [[ "$INSTALL_LAUNCHD" == false ]]; then
  warn "Skipped healthcheck job (--no-launchd)"
else
  HEALTHCHECK_PLIST_CONTENT=$(cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.penpal.healthcheck</string>

    <key>ProgramArguments</key>
    <array>
        <string>$BIN_DIR/penpal</string>
        <string>healthcheck</string>
    </array>

    <key>StartInterval</key>
    <integer>600</integer>

    <key>RunAtLoad</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$PENPAL_DIR/healthcheck.log</string>

    <key>StandardErrorPath</key>
    <string>$PENPAL_DIR/healthcheck.err</string>
</dict>
</plist>
PLIST
)
  if [[ -f "$HEALTHCHECK_PLIST" ]]; then
    warn "healthcheck plist already exists — refreshing managed contents"
  fi
  write_plist_if_needed "$HEALTHCHECK_PLIST" "$HEALTHCHECK_PLIST_CONTENT" "com.penpal.healthcheck"

  if launchctl load "$HEALTHCHECK_PLIST" 2>/dev/null; then
    success "healthcheck plist installed — will run every 10 minutes"
  else
    warn "launchd load failed (may need macOS 13+ syntax)"
    info "Trying bootstrap…"
    launchctl bootstrap "gui/$(id -u)" "$HEALTHCHECK_PLIST" 2>/dev/null && \
      success "launchd bootstrap succeeded for healthcheck" || \
      warn "Try manually: launchctl load '$HEALTHCHECK_PLIST'"
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}${BOLD}  Penpal installed successfully!${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo "  Next steps:"
echo ""
echo -e "  ${BOLD}1. Reload your shell (PATH update):${RESET}"
echo -e "     ${CYAN}source $PROFILE${RESET}"
echo ""
echo -e "  ${BOLD}2. Check dependencies & permissions:${RESET}"
echo -e "     ${CYAN}penpal doctor${RESET}"
echo ""
echo -e "  ${BOLD}3. Start monitoring:${RESET}"
echo -e "     ${CYAN}penpal start${RESET}"
echo ""
echo -e "  ${BOLD}4. View today's activity:${RESET}"
echo -e "     ${CYAN}penpal timeline --today${RESET}"
echo ""
echo "  Config:  $CONFIG_FILE"
echo "  Data:    $PENPAL_DIR/penpal.db"
echo "  Logs:    $PENPAL_DIR/penpal.log"
echo ""

# Run doctor using full path (PATH may not be reloaded yet in this shell)
echo -e "${BOLD}Running penpal doctor…${RESET}"
echo ""
"$BIN_DIR/penpal" doctor || true
