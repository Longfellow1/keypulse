.PHONY: bootstrap app app-clean app-alias install reload preflight install-plists tcc-reset onboard post-onboard-kick

PYTHON := /Users/Harland/Go/keypulse/.venv/bin/python
BOOTSTRAP_PYTHON := python3
ROOT := /Users/Harland/Go/keypulse
APP_DEST := /Applications/KeyPulse.app
PLIST_SRC := $(ROOT)/integrations/launchd
PLIST_DST := $(HOME)/Library/LaunchAgents
LAUNCH_AGENTS := \
	$(PLIST_DST)/com.keypulse.daemon.plist \
	$(PLIST_DST)/com.keypulse.healthcheck.plist \
	$(PLIST_DST)/com.keypulse.obsidian-sync.plist \
	$(PLIST_DST)/com.keypulse.obsidian-sync-hourly.plist

bootstrap:
	rm -rf $(ROOT)/.venv
	$(BOOTSTRAP_PYTHON) -m venv $(ROOT)/.venv
	$(PYTHON) -m pip install --no-user --upgrade pip
	$(PYTHON) -m pip install --no-user -e '.[macos,build]'

app-alias:
	cd /tmp && $(PYTHON) $(ROOT)/setup_app.py py2app -A -d $(ROOT)/dist -b $(ROOT)/build

app:
	rm -rf build dist
	cd /tmp && $(PYTHON) $(ROOT)/setup_app.py py2app -d $(ROOT)/dist -b $(ROOT)/build

app-clean:
	rm -rf build dist

# preflight: verify built bundle has required Info.plist keys + pyobjc frameworks.
# Aborts install if anything is missing — prevents shipping a silently-broken bundle.
preflight:
	$(PYTHON) $(ROOT)/scripts/preflight_app.py $(ROOT)/dist/KeyPulse.app

# install-plists: copy any plist sources that aren't yet present in LaunchAgents.
# We only copy if the destination doesn't exist OR the source is newer, so we
# don't blow away user-customized agents.
install-plists:
	@mkdir -p $(PLIST_DST)
	@for src in $(PLIST_SRC)/com.keypulse.*.plist; do \
		dst="$(PLIST_DST)/$$(basename $$src)"; \
		if [ ! -f "$$dst" ] || [ "$$src" -nt "$$dst" ]; then \
			echo "installing $$dst"; \
			cp "$$src" "$$dst"; \
		fi; \
	done

# install: build .app, preflight, copy to /Applications, sync plists, reload all agents.
# Always cleans dist/KeyPulse.app at the end — leaving it would create a duplicate
# entry in System Settings → Accessibility (same bundle id, different path) and
# users grant permission to the wrong binary. See docs/incident-2026-05-17-selfheal-loop.md.
install: app preflight
	rm -rf $(APP_DEST)
	cp -R $(ROOT)/dist/KeyPulse.app $(APP_DEST)
	rm -rf $(ROOT)/dist/KeyPulse.app
	$(MAKE) install-plists
	$(MAKE) tcc-reset
	$(MAKE) reload
	$(MAKE) onboard
	$(MAKE) post-onboard-kick

# tcc-reset: clear TCC entries that get invalidated by bundle re-signing.
# Without this, macOS keeps the old "denied" record and silently refuses to
# re-prompt — leaving the user stuck with no obvious recovery path.
# AppleEvents (browser automation) is NOT reset here: it stays valid across
# repacks, and resetting it forces the user to re-grant Chrome control.
tcc-reset:
	@echo "--- tcc reset (Accessibility / Input Monitoring) ---"
	-@tccutil reset Accessibility com.keypulse.app 2>/dev/null && echo "  reset Accessibility" || true
	-@tccutil reset ListenEvent com.keypulse.app 2>/dev/null && echo "  reset ListenEvent" || true
	-@tccutil reset PostEvent com.keypulse.app 2>/dev/null && echo "  reset PostEvent" || true

# onboard: launch interactive permission walkthrough using the installed .app
# binary, so prompt API runs under the correct bundle identity.
onboard:
	@echo "--- permission onboard ---"
	-$(APP_DEST)/Contents/MacOS/KeyPulse install onboard || true

# post-onboard-kick: macOS daemons cache TCC state at process start. After the
# user grants permissions during onboard, the still-running daemon won't see
# the new state — capability monitor keeps reporting ax_denied even though
# watchers can emit. A second kickstart forces the daemon to re-read TCC.
post-onboard-kick:
	@echo "--- post-onboard daemon kick (refresh TCC cache) ---"
	-@launchctl kickstart -k gui/$$(id -u)/com.keypulse.daemon 2>/dev/null && echo "  daemon kicked" || true

reload:
	@for plist in $(LAUNCH_AGENTS); do \
		if [ ! -f "$$plist" ]; then \
			echo "skipping $$plist (not installed)"; \
			continue; \
		fi; \
		echo "reloading $$plist"; \
		launchctl unload "$$plist" 2>/dev/null || true; \
		launchctl load "$$plist"; \
	done
	@echo "--- launchctl list ---"
	@launchctl list | grep keypulse || true
