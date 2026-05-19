# DayTrace convenience targets. Just a thin wrapper over the underlying
# python scripts; everything works without `make` too.

PY      ?= python3
DB      ?= data/daytrace.sqlite
PORT    ?= 8765
DEVICE  ?= mac

.PHONY: help install dashboard daily weekly export-daily export-weekly \
        sync-tasks deploy clean-feishu test status

help:                  ## Show this help
	@awk 'BEGIN{FS=":.*##"; printf "DayTrace targets:\n"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:               ## Install runtime dependencies
	$(PY) -m pip install -r requirements.txt

dashboard:             ## Start the local dashboard at $(PORT)
	$(PY) dashboard/server.py --db $(DB) --port $(PORT)

daily:                 ## Pull + import + regenerate yesterday on this hub
	$(PY) scripts/run_daily.py catchup --config config/devices/$(DEVICE).yaml

weekly:                ## Render last completed ISO week + Feishu + email
	$(PY) -m bash -c "scripts/daytrace-weekly.sh" || bash scripts/daytrace-weekly.sh

export-daily:          ## One-off: render + upload yesterday's report
	$(PY) scripts/export_report.py --upload-feishu

export-weekly:         ## One-off: render + upload + email last week's report
	$(PY) scripts/export_report.py --upload-feishu --email

sync-tasks:            ## Pull Feishu work_items + rebuild event links + translate titles
	$(PY) scripts/run_daily.py work-items-sync

translate-tasks:       ## (Re-)translate work_items.title → title_en via DeepSeek
	$(PY) scripts/translate_work_items.py

deploy:                ## rsync code to every remote in config/remotes.yaml
	$(PY) scripts/run_daily.py deploy

status:                ## Dry-run: which (device, date) pairs are pending?
	$(PY) scripts/run_daily.py status

clean-feishu:          ## Drop stale revisions in the Feishu drive folders
	$(PY) scripts/cleanup_feishu_reports.py --apply

test:                  ## Run the pytest suite
	$(PY) -m pytest -q
