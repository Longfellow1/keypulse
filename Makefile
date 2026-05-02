.PHONY: app app-clean app-alias install reload

PYTHON := /Users/Harland/Go/keypulse/.venv/bin/python
ROOT := /Users/Harland/Go/keypulse
APP_DEST := /Applications/KeyPulse.app
LAUNCH_AGENTS := \
	$(HOME)/Library/LaunchAgents/com.keypulse.daemon.plist \
	$(HOME)/Library/LaunchAgents/com.keypulse.obsidian-sync.plist \
	$(HOME)/Library/LaunchAgents/com.keypulse.obsidian-sync-hourly.plist

app-alias:
	cd /tmp && $(PYTHON) $(ROOT)/setup_app.py py2app -A -d $(ROOT)/dist -b $(ROOT)/build

app:
	rm -rf build dist
	cd /tmp && $(PYTHON) $(ROOT)/setup_app.py py2app -d $(ROOT)/dist -b $(ROOT)/build

app-clean:
	rm -rf build dist

# install: build .app, copy to /Applications, reload all launchd agents.
# Run after any code change that needs to take effect in the running daemon.
install: app
	rm -rf $(APP_DEST)
	cp -R $(ROOT)/dist/KeyPulse.app $(APP_DEST)
	$(MAKE) reload

reload:
	@for plist in $(LAUNCH_AGENTS); do \
		echo "reloading $$plist"; \
		launchctl unload "$$plist" 2>/dev/null || true; \
		launchctl load "$$plist"; \
	done
	@echo "--- launchctl list ---"
	@launchctl list | grep keypulse || true
