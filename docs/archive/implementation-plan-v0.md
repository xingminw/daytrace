# DayTrace v0 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a local-first DayTrace v0 that collects personal activity traces from a few initial sources, normalizes them into standard events, generates a daily Markdown record, and prepares Feishu/dashboard output paths.

**Architecture:** Connector scripts collect source-specific data into JSONL events. A normalization layer converts them into one standard schema. Source summaries and project/day aggregation feed a central DayTrace Agent prompt that generates the final daily record. macOS activity starts as a lightweight local collector, not a full menu bar app.

**Tech Stack:** Python 3, JSONL/SQLite optional later, Git CLI, macOS shell/AppleScript/IOKit-compatible commands where possible, Hermes cron for scheduling, Markdown for outputs.

---

## Phase 0: Product structure and repo hygiene

### Task 0.1: Keep product docs as the source of truth

**Objective:** Ensure the repo clearly separates product, agent, experience, data, and engineering docs.

**Files:**
- Existing: `README.md`
- Existing: `docs/product-brief.md`
- Existing: `docs/agent-architecture.md`
- Existing: `docs/experience-design.md`
- Existing: `docs/information-design.md`
- Existing: `docs/data-sources-and-permissions.md`
- Existing: `docs/engineering-spec.md`

**Steps:**
1. Verify all docs are linked from `README.md`.
2. Ensure `product-brief.md` states DayTrace is personal-first and agent-centered.
3. Ensure `agent-architecture.md` owns central Agent logic.
4. Ensure `experience-design.md` owns daily UX and output layers.
5. Run:

```bash
git status --short
```

**Expected:** docs exist locally; no commit/push without explicit user approval.

---

## Phase 1: Project skeleton

### Task 1.1: Create source directories

**Objective:** Add a minimal executable project skeleton.

**Files:**
- Create: `daytrace/__init__.py`
- Create: `daytrace/schema.py`
- Create: `daytrace/io.py`
- Create: `daytrace/cli.py`
- Create: `scripts/collect_git.py`
- Create: `scripts/collect_docs.py`
- Create: `scripts/collect_hermes_sessions.py`
- Create: `scripts/collect_macos_activity.py`
- Create: `scripts/generate_daily_report.py`
- Create: `config/sources.yaml`
- Create: `config/rules.yaml`
- Create: `prompts/daily_report.md`
- Create: `outputs/.gitkeep`
- Create: `events/.gitkeep`
- Create: `tests/test_schema.py`

**Steps:**
1. Create the directories and empty files.
2. Do not add external dependencies yet.
3. Run:

```bash
python -m compileall daytrace scripts
```

**Expected:** compile succeeds.

---

## Phase 2: Standard event schema

### Task 2.1: Define `TraceEvent`

**Objective:** Create the shared schema every connector writes to.

**Files:**
- Modify: `daytrace/schema.py`
- Test: `tests/test_schema.py`

**Schema fields:**

```python
@dataclass
class TraceEvent:
    id: str
    source: str
    kind: str
    start: str
    end: str | None
    title: str
    summary: str
    project_guess: str | None
    confidence: float
    sensitivity: str
    evidence: dict[str, Any]
    raw_ref: str | None = None
```

**Steps:**
1. Write tests for JSON roundtrip.
2. Write tests for required fields.
3. Implement dataclass and `to_dict` / `from_dict` helpers.
4. Run:

```bash
python -m pytest tests/test_schema.py -v
```

**Expected:** tests pass.

---

### Task 2.2: Add JSONL IO helpers

**Objective:** Make all collectors write the same JSONL format.

**Files:**
- Modify: `daytrace/io.py`
- Test: `tests/test_io.py`

**Functions:**

```python
def write_events(path: Path, events: Iterable[TraceEvent]) -> None: ...
def read_events(path: Path) -> list[TraceEvent]: ...
def append_events(path: Path, events: Iterable[TraceEvent]) -> None: ...
```

**Steps:**
1. Test writing two events and reading them back.
2. Ensure parent directories are created automatically.
3. Preserve UTF-8.
4. Run:

```bash
python -m pytest tests/test_io.py -v
```

**Expected:** tests pass.

---

## Phase 3: Initial connectors

### Task 3.1: Git connector

**Objective:** Collect commits and uncommitted activity from local repos.

**Files:**
- Modify: `scripts/collect_git.py`
- Test: `tests/test_collect_git.py`

**Behavior:**
- Scan configured roots such as `~/Projects`.
- Detect git repos.
- Collect commits since date start.
- Emit `TraceEvent` with `source=git`, `kind=commit`.
- Optionally emit `kind=working_tree_change` for uncommitted files.

**Commands to use:**

```bash
git -C <repo> log --since=<start> --until=<end> --pretty=format:%H%x09%ad%x09%s --date=iso

git -C <repo> status --short
```

**Verification:**

```bash
python scripts/collect_git.py --date YYYY-MM-DD --out events/git-YYYY-MM-DD.jsonl
python -m json.tool < events/git-YYYY-MM-DD.jsonl
```

**Expected:** JSONL events are produced for active repos.

---

### Task 3.2: Docs connector

**Objective:** Collect Markdown/LaTeX writing activity from configured paths.

**Files:**
- Modify: `scripts/collect_docs.py`
- Test: `tests/test_collect_docs.py`

**Behavior:**
- Scan configured roots.
- Include `.md`, `.tex`, `.txt` initially.
- Use modification time to select files modified on date.
- Emit `source=docs`, `kind=document_modified`.
- Include path, extension, mtime, and small excerpt if safe.

**Verification:**

```bash
python scripts/collect_docs.py --date YYYY-MM-DD --out events/docs-YYYY-MM-DD.jsonl
```

**Expected:** modified docs are listed as events.

---

### Task 3.3: Hermes sessions connector

**Objective:** Collect same-day Hermes conversation signals without dumping full transcripts.

**Files:**
- Modify: `scripts/collect_hermes_sessions.py`
- Test: `tests/test_collect_hermes_sessions.py`

**Behavior:**
- Read local Hermes session JSON/JSONL metadata.
- Select sessions active on date.
- Emit summaries based on message previews, session title/source, and user-visible text snippets.
- Avoid storing secrets or full raw transcripts in the event by default.

**Verification:**

```bash
python scripts/collect_hermes_sessions.py --date YYYY-MM-DD --out events/hermes-YYYY-MM-DD.jsonl
```

**Expected:** events mention project discussions and AI collaboration traces.

---

### Task 3.4: macOS activity collector, basic mode

**Objective:** Add a lightweight collector for frontmost app and idle/active state.

**Files:**
- Modify: `scripts/collect_macos_activity.py`
- Test: `tests/test_macos_activity_parse.py`

**Behavior:**
- Basic mode records frontmost app name and timestamp.
- Active/idle detection should be best-effort.
- Window title is optional and disabled by default.
- Output JSONL events with `source=macos_activity`, `kind=app_focus_sample`.

**Initial implementation options:**
- `osascript` for frontmost app name.
- A later Swift helper for more reliable active/idle detection.

**Verification:**

```bash
python scripts/collect_macos_activity.py --duration-seconds 30 --interval-seconds 10 --out events/macos-sample.jsonl
```

**Expected:** 3 samples are written with app names.

---

## Phase 4: Aggregation and report generation

### Task 4.1: Source summary generation

**Objective:** Convert raw events into per-source summaries before final daily report.

**Files:**
- Create: `daytrace/summarize.py`
- Test: `tests/test_summarize.py`

**Behavior:**
- Group events by source.
- Count events by kind.
- Extract top projects and artifacts.
- Keep evidence references.

**Verification:**

```bash
python -m pytest tests/test_summarize.py -v
```

**Expected:** summary object includes source counts and key evidence.

---

### Task 4.2: Project/day aggregation

**Objective:** Combine multiple source summaries into project-centric and time-centric daily structures.

**Files:**
- Modify: `daytrace/summarize.py`
- Test: `tests/test_daily_aggregation.py`

**Behavior:**
- Group by `project_guess` when available.
- Create low-confidence bucket for unknown project events.
- Identify artifacts: commit, document, session decision, app activity segment.
- Preserve uncertainty.

**Verification:**

```bash
python -m pytest tests/test_daily_aggregation.py -v
```

**Expected:** produces a `DailyTrace` object suitable for prompt input.

---

### Task 4.3: Daily Markdown report generator

**Objective:** Generate a readable Markdown daily record from aggregated data.

**Files:**
- Modify: `scripts/generate_daily_report.py`
- Modify: `prompts/daily_report.md`
- Test: `tests/test_report_template.py`

**Behavior:**
- Accept date and event files.
- Produce `outputs/YYYY-MM-DD.md`.
- Include: overview, work time, location, project progress, code, writing/docs, AI collaboration, evidence, low-confidence items.
- If no LLM is invoked, generate deterministic draft summary.
- Later Hermes cron can use the prompt for richer natural language.

**Verification:**

```bash
python scripts/generate_daily_report.py --date YYYY-MM-DD --events events/*.jsonl --out outputs/YYYY-MM-DD.md
```

**Expected:** Markdown report exists and is readable.

---

## Phase 5: Feishu and cron integration

### Task 5.1: Add cron prompt scaffold

**Objective:** Prepare DayTrace to run as a project-backed Hermes cron agent.

**Files:**
- Create: `.hermes/cron_prompt.md`

**Content should instruct Hermes to:**
1. Run collectors for the target date.
2. Generate the daily report.
3. Read the Markdown report.
4. Send a short Feishu summary.
5. Mention low-confidence corrections.

**Verification:**

```bash
python scripts/generate_daily_report.py --date YYYY-MM-DD --out outputs/YYYY-MM-DD.md
```

**Expected:** The prompt has exact commands and expected output locations.

---

### Task 5.2: Create Feishu short summary formatter

**Objective:** Create a short form of the report suitable for Feishu.

**Files:**
- Create: `daytrace/feishu_summary.py`
- Test: `tests/test_feishu_summary.py`

**Behavior:**
- Parse Markdown report or DailyTrace object.
- Return 5-8 line summary.
- Include link/path to full report.

**Verification:**

```bash
python -m pytest tests/test_feishu_summary.py -v
```

**Expected:** Feishu summary is concise and does not dump evidence details.

---

## Phase 6: Dashboard placeholder

### Task 6.1: Add static dashboard placeholder

**Objective:** Reserve the dashboard path without overbuilding UI.

**Files:**
- Create: `dashboard/README.md`
- Create: `dashboard/static-example.md`

**Behavior:**
- Document intended dashboard panels.
- Include sample timeline/projects/artifacts/source/evidence panels.
- Do not implement full web app until data pipeline works.

**Verification:**

```bash
git status --short dashboard
```

**Expected:** dashboard docs exist.

---

## Phase 7: End-to-end local verification

### Task 7.1: Run one full local daily trace

**Objective:** Verify v0 works end-to-end locally.

**Commands:**

```bash
python scripts/collect_git.py --date YYYY-MM-DD --out events/git-YYYY-MM-DD.jsonl
python scripts/collect_docs.py --date YYYY-MM-DD --out events/docs-YYYY-MM-DD.jsonl
python scripts/collect_hermes_sessions.py --date YYYY-MM-DD --out events/hermes-YYYY-MM-DD.jsonl
python scripts/collect_macos_activity.py --duration-seconds 30 --interval-seconds 10 --out events/macos-YYYY-MM-DD.jsonl
python scripts/generate_daily_report.py --date YYYY-MM-DD --events events/*YYYY-MM-DD.jsonl --out outputs/YYYY-MM-DD.md
```

**Expected:**
- Event files exist.
- `outputs/YYYY-MM-DD.md` exists.
- Report includes at least Git/docs/Hermes/macOS sections where data exists.
- Low-confidence items are explicitly marked.

---

## Commit / push gate

User requires explicit approval before any `git commit` or `git push`.

At each meaningful checkpoint:

```bash
git status --short
git diff -- <relevant files>
```

Then stop and wait for explicit approval before committing. Ask separately before pushing.

---

## Implementation order recommendation

1. Standard event schema.
2. JSONL IO.
3. Git connector.
4. Docs connector.
5. Hermes sessions connector.
6. Basic macOS activity collector.
7. Aggregation and deterministic Markdown report.
8. Cron prompt and Feishu summary.
9. Dashboard placeholder.
10. End-to-end verification.

This order gives value quickly while preserving the central Agent architecture for Rich Data Mode.
