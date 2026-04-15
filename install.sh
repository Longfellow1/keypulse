#!/usr/bin/env bash
# KeyPulse — One-click installer for macOS
# Usage: bash install.sh [--no-launchd] [--dev]
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
for arg in "$@"; do
  case $arg in
    --no-launchd) INSTALL_LAUNCHD=false ;;
    --dev)        DEV_MODE=true ;;
  esac
done

# ── Paths ─────────────────────────────────────────────────────────────────────
KEYPULSE_DIR="$HOME/.keypulse"
VENV_DIR="$KEYPULSE_DIR/venv"
BIN_DIR="$HOME/.local/bin"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/com.keypulse.daemon.plist"
CONFIG_FILE="$KEYPULSE_DIR/config.toml"

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

# ─────────────────────────────────────────────────────────────────────────────
header "KeyPulse Installer"
echo "  Repo:    $REPO_DIR"
echo "  Venv:    $VENV_DIR"
echo "  Bin:     $BIN_DIR/keypulse"
echo "  Config:  $CONFIG_FILE"
[[ "$DEV_MODE" == true ]] && warn "Dev mode enabled (editable install)"

# ── 1. macOS check ────────────────────────────────────────────────────────────
header "1/6  Checking system"

if [[ "$(uname)" != "Darwin" ]]; then
  error "macOS required. Got: $(uname)"
  exit 1
fi
success "macOS $(sw_vers -productVersion)"

# ── 2. Python 3.11+ ───────────────────────────────────────────────────────────
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

# ── 3. Create venv ────────────────────────────────────────────────────────────
header "2/6  Setting up virtual environment"

mkdir -p "$KEYPULSE_DIR"

if [[ -d "$VENV_DIR" ]]; then
  warn "Venv already exists at $VENV_DIR — reusing"
  # Ensure it's usable
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    warn "Venv looks broken, recreating…"
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
    success "Venv recreated"
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

# ── 4. Install KeyPulse ───────────────────────────────────────────────────────
header "3/6  Installing KeyPulse"

if [[ "$DEV_MODE" == true ]]; then
  info "Editable install (dev mode)…"
  "$VENV_PIP" install -e "$REPO_DIR" --quiet
else
  info "Installing KeyPulse…"
  "$VENV_PIP" install "$REPO_DIR" --quiet
fi
success "KeyPulse installed"

# Install macOS-specific pyobjc — try prebuilt wheel first, then source
info "Installing macOS dependencies (pyobjc)…"
if "$VENV_PIP" install \
    "pyobjc-framework-AppKit" \
    "pyobjc-framework-Quartz" \
    --quiet 2>/dev/null; then
  success "pyobjc installed"
else
  warn "pyobjc wheel not available for this Python — trying with --no-binary (slower)…"
  if "$VENV_PIP" install \
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
"$VENV_DIR/bin/keypulse" --help &>/dev/null && \
  success "Binary works" || \
  { error "keypulse binary not working after install"; exit 1; }

# ── 5. Wrapper + PATH ─────────────────────────────────────────────────────────
header "4/6  Installing keypulse command"

mkdir -p "$BIN_DIR"

# Write wrapper that directly calls into venv (no activation needed)
cat > "$BIN_DIR/keypulse" << WRAPPER
#!/usr/bin/env bash
exec "$VENV_DIR/bin/keypulse" "\$@"
WRAPPER
chmod +x "$BIN_DIR/keypulse"
success "Wrapper written: $BIN_DIR/keypulse"

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
    echo "# Added by KeyPulse installer" >> "$PROFILE"
    echo "$PATH_LINE" >> "$PROFILE"
    success "Added ~/.local/bin to $PROFILE"
  fi
  warn "PATH updated — run: source $PROFILE"
  # Also export for the remainder of this script
  export PATH="$BIN_DIR:$PATH"
fi

# ── 6. Default config ─────────────────────────────────────────────────────────
header "5/6  Configuration"

if [[ -f "$CONFIG_FILE" ]]; then
  warn "Config already exists — skipping ($CONFIG_FILE)"
else
  cp "$REPO_DIR/config.toml" "$CONFIG_FILE"
  success "Default config written to $CONFIG_FILE"
fi

# ── 7. launchd (auto-start on login) ─────────────────────────────────────────
header "6/6  launchd auto-start"

if [[ "$INSTALL_LAUNCHD" == false ]]; then
  warn "Skipped (--no-launchd)"
elif [[ -f "$LAUNCHD_PLIST" ]]; then
  warn "launchd plist already exists — skipping"
  echo "  To reload:  launchctl unload '$LAUNCHD_PLIST' && launchctl load '$LAUNCHD_PLIST'"
else
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$LAUNCHD_PLIST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.keypulse.daemon</string>

    <key>ProgramArguments</key>
    <array>
        <string>$BIN_DIR/keypulse</string>
        <string>start</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <false/>

    <key>StandardOutPath</key>
    <string>$KEYPULSE_DIR/launchd.log</string>

    <key>StandardErrorPath</key>
    <string>$KEYPULSE_DIR/launchd.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$BIN_DIR:/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLIST

  if launchctl load "$LAUNCHD_PLIST" 2>/dev/null; then
    success "launchd plist installed — KeyPulse will start on login"
  else
    warn "launchd load failed (may need macOS 13+ syntax)"
    info "Trying bootstrap…"
    launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_PLIST" 2>/dev/null && \
      success "launchd bootstrap succeeded" || \
      warn "Try manually: launchctl load '$LAUNCHD_PLIST'"
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}${BOLD}  KeyPulse installed successfully!${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo "  Next steps:"
echo ""
echo -e "  ${BOLD}1. Reload your shell (PATH update):${RESET}"
echo -e "     ${CYAN}source $PROFILE${RESET}"
echo ""
echo -e "  ${BOLD}2. Check dependencies & permissions:${RESET}"
echo -e "     ${CYAN}keypulse doctor${RESET}"
echo ""
echo -e "  ${BOLD}3. Start monitoring:${RESET}"
echo -e "     ${CYAN}keypulse start${RESET}"
echo ""
echo -e "  ${BOLD}4. View today's activity:${RESET}"
echo -e "     ${CYAN}keypulse timeline --today${RESET}"
echo ""
echo "  Config:  $CONFIG_FILE"
echo "  Data:    $KEYPULSE_DIR/keypulse.db"
echo "  Logs:    $KEYPULSE_DIR/keypulse.log"
echo ""

# Run doctor using full path (PATH may not be reloaded yet in this shell)
echo -e "${BOLD}Running keypulse doctor…${RESET}"
echo ""
"$BIN_DIR/keypulse" doctor || true
