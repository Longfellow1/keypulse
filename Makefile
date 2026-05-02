.PHONY: app app-clean app-alias

PYTHON := /Users/Harland/Go/keypulse/.venv/bin/python
ROOT := /Users/Harland/Go/keypulse

app-alias:
	cd /tmp && $(PYTHON) $(ROOT)/setup_app.py py2app -A -d $(ROOT)/dist -b $(ROOT)/build

app:
	rm -rf build dist
	cd /tmp && $(PYTHON) $(ROOT)/setup_app.py py2app -d $(ROOT)/dist -b $(ROOT)/build

app-clean:
	rm -rf build dist
