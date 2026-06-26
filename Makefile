# Local dev helpers (not used by Home Assistant).
#
# The Digi session is cached in tools/.digi_state.json (git-ignored), so 2FA is
# only needed when that cookie expires — `make fetch` / `make diag` reuse it.
#
# Override the interpreter if `python` is not on PATH, e.g.:
#   make fetch PY="/c/Users/<you>/AppData/Local/Programs/Python/Python313/python.exe"
PY ?= python
PROBE := $(PY) tools/probe_digi.py

.PHONY: help login code fetch diag test

help:   ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-8s %s\n", $$1, $$2}'

login:  ## Start login + send the 2FA SMS (creds from tools/.digi_creds.json)
	$(PROBE) login

code:   ## Validate the SMS code:  make code CODE=123456
	$(PROBE) code $(CODE)

fetch:  ## Fetch invoices using the cached session (no 2FA)
	$(PROBE) fetch

diag:   ## Dump the invoices page diagnostics using the cached session
	$(PROBE) diag

test:   ## Run the test suite
	$(PY) -m pytest -q
