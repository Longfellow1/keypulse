.PHONY: app app-clean app-alias install reload preflight install-plists

PYTHON := /Users/Harland/Go/keypulse/.venv/bin/python
ROOT := /Users/Harland/Go/keypulse
APP_DEST := /Applications/KeyPulse.app
PLIST_SRC := $(ROOT)/integrations/launchd
PLIST_DST := $(HOME)/Library/LaunchAgents
LAUNCH_AGENTS := \
	$(PLIST_DST)/com.keypulse.daemon.plist \
	$(PLIST_DST)/com.keypulse.healthcheck.plist \
	$(PLIST_DST)/com.keypulse.obsidian-sync.plist \
	$(PLIST_DST)/com.keypulse.obsidian-sync-hourly.plist

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
install: app preflight
	rm -rf $(APP_DEST)
	cp -R $(ROOT)/dist/KeyPulse.app $(APP_DEST)
	$(MAKE) install-plists
	$(MAKE) reload

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
