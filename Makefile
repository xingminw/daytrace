# DayTrace convenience targets. Just a thin wrapper over the underlying
# python scripts; everything works without `make` too.

PY      ?= .venv/bin/python
DB      ?= data/daytrace.sqlite
PORT    ?= 8766
DEVICE  ?= mac

.PHONY: help install install-config dashboard daily weekly export-daily export-weekly \
        sync-tasks deploy clean-feishu test status

help:                  ## Show this help
	@awk 'BEGIN{FS=":.*##"; printf "DayTrace targets:\n"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:               ## Install runtime dependencies (then run `make install-config`)
	$(PY) -m pip install -r requirements.txt

install-config:        ## Copy config/*.example.yaml templates to their real locations
	@for f in config/*.example.yaml; do \
		dst=$${f%.example.yaml}.yaml; \
		[ -f $$dst ] && echo "  exists: $$dst (skipping)" || { cp $$f $$dst && echo "  created: $$dst"; }; \
	done

dashboard:             ## Start the local dashboard at $(PORT)
	DAYTRACE_DISABLE_AI=1 DAYTRACE_TIMEZONE=Asia/Shanghai $(PY) dashboard/server.py --db $(DB) --port $(PORT)

daily:                 ## Pull + import + regenerate yesterday on this hub
	DAYTRACE_TIMEZONE=Asia/Shanghai DAYTRACE_DISABLE_AI=1 $(PY) scripts/run_local.py

weekly:                ## Render last completed ISO week locally
	bash scripts/daytrace-weekly.sh

export-daily:          ## One-off: render locally yesterday's report
	DAYTRACE_DISABLE_AI=1 $(PY) scripts/export_report.py

export-weekly:         ## One-off: render locally last week's report
	DAYTRACE_DISABLE_AI=1 $(PY) scripts/export_report.py

sync-tasks:            ## Compatibility alias for local repository indexing
	$(PY) scripts/run_daily.py work-items-sync

translate-tasks:       ## Retired: local repository names need no translation
	$(PY) scripts/translate_work_items.py

deploy:                ## rsync code to every remote in config/remotes.yaml
	$(PY) scripts/run_daily.py deploy

status:                ## Dry-run: which (device, date) pairs are pending?
	$(PY) scripts/run_daily.py status

clean-feishu:          ## Retired: exits without contacting Feishu
	$(PY) scripts/cleanup_feishu_reports.py --apply

test:                  ## Run the pytest suite
	$(PY) -m pytest -q

sync-projects:         ## Index local Git repositories and rebuild attribution
	$(PY) scripts/sync_projects.py
