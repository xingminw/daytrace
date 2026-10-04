# Explicit evidence-based daily and weekly briefs

The normal generation entry point is `scripts/generate_brief.py`. Reading the
website or exporting saved reports never starts generation. Collection jobs
keep `DAYTRACE_DISABLE_AI=1`; an API key alone does not authorize processing.

## Offline plan, bounded generation, local publication

```sh
# No credentials or API access. Dates and weeks are repeatable options.
.venv/bin/python scripts/generate_brief.py plan \
  --date 2026-10-03 --week 2026-W40 --out data/brief-plan.json

# Only after approving the exact saved plan, outbound categories and total cap:
.venv/bin/python scripts/generate_brief.py execute \
  --plan data/brief-plan.json --approve-plan HASH_FROM_PLAN --budget-rmb 1

# Review the generated draft, then explicitly publish it locally:
.venv/bin/python scripts/generate_brief.py publish \
  --draft PATH_FROM_RESULT --accept-draft HASH_FROM_RESULT
```

`plan` reads filtered database events and local Git metadata discovered through
`repo_projects` / `repo_checkouts`, including initialized submodules. It never
fetches, visits remote hosts, reads document bodies, or imports manual examples.
Repository identities merge multiple checkouts of the same repository. Distinct
repositories remain distinct unless the index already identifies them together.
The configured reporting timezone and 04:00 workday boundary apply to Git dates.
Missing, timed-out, remote and truncated checkouts appear in the source audit.

Plans are immutable review snapshots with their exact outbound prompt, evidence,
source locations (local only), estimated worst-case cost and hash. Only sanitized
request/discussion text, project labels, dates and Git commit subjects leave the
machine during an explicitly approved execution. Full source text is checked
before retaining up to 800 characters; sensitive, ambiguous multiline, credential
and pasted transcript content remains excluded. These heuristics do not prove
that all semantic private information has been recognized; review the plan.

`execute` produces drafts only. It uses the fixed official DeepSeek endpoint,
`deepseek-flash`, thinking disabled, no redirects/proxies and no hidden retries.
`data/brief-generation-ledger.sqlite` is independent of historical backfill
accounting. Each plan has a persistent explicit cap; it cannot be raised in
place. Each request permits two total attempts across restarts. Reservations
cover UTF-8 request bytes plus framing and full output limits at the configured
peak rates (RMB 2/M input, RMB 8/M output). Unknown outcomes retain full reserves;
provider token-contract mismatches halt the authorization. Rates are assumptions
of this local accounting control, not an account-level provider billing cap.
Reusing a completed immutable request reads its persistent response cache.

`publish` verifies the draft hash and unchanged event/Git evidence, backs up
SQLite, then archives any previous brief before replacement. It does not modify
raw events or historical AI channel tables. Manual local examples cannot replace
actual generated reports. Empty evidence produces a clearly labeled local state
without a paid call, and cannot replace an existing brief.

## Evidence and quality boundaries

Daily and weekly generation share the same dynamic pipeline. Weekly prompts
receive cross-day evidence directly, not concatenated daily narratives. Requests
support requested/discussed states; Git commits support repository changes.
Neither establishes deployment, acceptance, completed experiments or scientific
validity. The automatic source adapter does not currently read acceptance
reports, meeting notes, full documents, assistant replies or tool receipts.
It therefore cannot reproduce insights that require manually reviewed documents.

Each claim must cite same-project evidence, quote an exact fragment, and use a
supported state/scope. Invalid claims are rejected individually; valid claims are
not removed by blanket completion-word filtering. Structural checks do not prove
semantic entailment, and the headline also needs human review. Generated drafts
are labeled as source-checked, not as independently verified facts.

Saved manual review samples remain clearly labeled and separate from the normal
pipeline. They are local data, not committed examples or proof of model quality.

## Retired paths and tests

The old `backfill_ai_bounded.py --execute` and unbudgeted `ai_client.call_json`
entry points explicitly refuse execution and direct callers here. Historical
results and ledgers remain readable. Deterministic regeneration defaults to
`include_ai=False`; enabling an old flag cannot restore the retired transport.
The dashboard and Markdown exports prefer saved evidence briefs and otherwise
show existing legacy caches. They do not load API credentials to read reports.

Tests use temporary Git repositories/databases and injected simulated provider
responses through the same CLI execution function used in production. They
cover multiple dates/weeks, unknown projects, requests without receipts, commits
without acceptance, empty evidence, source changes, budget exhaustion, retained
unknown costs, persisted retry limits, idempotent resume and backup publication.
Real provider output quality requires a separately approved run; it is not
claimed from mock tests.
