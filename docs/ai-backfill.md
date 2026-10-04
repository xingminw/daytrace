# Historical AI backfill compatibility

The former `scripts/backfill_ai_bounded.py --execute sample|remaining` execution
path is retired. It refuses before accessing credentials or sending requests.
Use the normal [evidence-brief CLI](evidence-briefs.md) for any future approved
report generation, with a new exact plan and independent persistent budget.

Existing historical ledgers, response caches, reports, backups and privacy
exclusion records remain local under `data/`. The compatibility planning and
verification helpers are retained for inspecting those artifacts, not as an
alternative paid generation route. Old budgets never authorize new processing.

The new pipeline preserves whole-record privacy exclusions and fixes the old
blanket completion-word filter by checking individual claims against typed
source evidence. It does not silently rewrite historical results. Empty days do
not imply inactivity and are not filled with fabricated narratives.
