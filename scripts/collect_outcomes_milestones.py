#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from daytrace.io import read_events, write_events
from daytrace.schema import TraceEvent


def load_many(paths: list[str]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for pattern in paths:
        matches = (
            list(Path().glob(pattern))
            if any(ch in pattern for ch in "*?[")
            else [Path(pattern)]
        )
        for path in matches:
            events.extend(read_events(path))
    return events


def project_of(event: TraceEvent) -> str:
    return canonical_project(event.project_guess)


def canonical_project(project: str | None) -> str:
    if not project:
        return "misc"
    if project.lower() == "loft-sim":
        return "LOFT-Sim"
    return project


def make_outcomes(day: str, events: list[TraceEvent]) -> list[TraceEvent]:
    outcomes: list[TraceEvent] = []
    for e in events:
        if e.source == "github" and e.kind == "remote_commit":
            sha = str(e.evidence.get("sha", ""))
            outcomes.append(
                TraceEvent(
                    id="outcome-github-commit-"
                    + (sha[:16] or hashlib.sha1(e.id.encode()).hexdigest()[:16]),
                    source="outcome",
                    kind="remote_commit_created",
                    start=e.start,
                    end=None,
                    title=e.title,
                    summary=e.summary,
                    project_guess=canonical_project(e.project_guess)
                    if e.project_guess
                    else None,
                    sensitivity=e.sensitivity,
                    evidence={"from_event": e.id, **e.evidence},
                    raw_ref=e.raw_ref,
                )
            )
        elif e.source == "github" and e.kind in {
            "pull_request_activity",
            "issue_activity",
        }:
            outcomes.append(
                TraceEvent(
                    id="outcome-github-activity-"
                    + hashlib.sha1(e.id.encode()).hexdigest()[:16],
                    source="outcome",
                    kind=e.kind,
                    start=e.start,
                    end=None,
                    title=e.title,
                    summary=e.summary,
                    project_guess=canonical_project(e.project_guess)
                    if e.project_guess
                    else None,
                    sensitivity=e.sensitivity,
                    evidence={"from_event": e.id, **e.evidence},
                    raw_ref=e.raw_ref,
                )
            )
        elif e.source == "git" and e.kind == "commit":
            sha = str(e.evidence.get("sha", ""))
            outcomes.append(
                TraceEvent(
                    id="outcome-commit-"
                    + (sha[:16] or hashlib.sha1(e.id.encode()).hexdigest()[:16]),
                    source="outcome",
                    kind="commit_created",
                    start=e.start,
                    end=None,
                    title="Commit: " + e.title,
                    summary=e.summary,
                    project_guess=canonical_project(e.project_guess)
                    if e.project_guess
                    else None,
                    sensitivity="normal",
                    evidence={"from_event": e.id, **e.evidence},
                    raw_ref=e.raw_ref,
                )
            )
        elif e.source == "git" and e.kind == "working_tree_change":
            outcomes.append(
                TraceEvent(
                    id="outcome-dirty-" + hashlib.sha1(e.id.encode()).hexdigest()[:16],
                    source="outcome",
                    kind="uncommitted_changes",
                    start=e.start,
                    end=None,
                    title=e.title,
                    summary=e.summary,
                    project_guess=canonical_project(e.project_guess)
                    if e.project_guess
                    else None,
                    sensitivity="normal",
                    evidence={"from_event": e.id, **e.evidence},
                    raw_ref=e.raw_ref,
                )
            )
        elif e.source == "docs" and e.kind == "document_modified":
            outcomes.append(
                TraceEvent(
                    id="outcome-doc-" + hashlib.sha1(e.id.encode()).hexdigest()[:16],
                    source="outcome",
                    kind="document_updated",
                    start=e.start,
                    end=None,
                    title=e.title,
                    summary=e.summary,
                    project_guess=canonical_project(e.project_guess)
                    if e.project_guess
                    else None,
                    sensitivity=e.sensitivity,
                    evidence={"from_event": e.id, **e.evidence},
                    raw_ref=e.raw_ref,
                )
            )
        elif e.source == "hermes" and e.kind == "assistant_result":
            text = (e.summary or "").lower()
            if any(
                token in text
                for token in ["passed", "测试通过", "已运行", "已改", "改好了", "完成"]
            ):
                outcomes.append(
                    TraceEvent(
                        id="outcome-agent-result-"
                        + hashlib.sha1(e.id.encode()).hexdigest()[:16],
                        source="outcome",
                        kind="agent_result_reported",
                        start=e.start,
                        end=None,
                        title=e.title,
                        summary=e.summary,
                        project_guess=canonical_project(e.project_guess)
                        if e.project_guess
                        else None,
                        sensitivity=e.sensitivity,
                        evidence={"from_event": e.id, **e.evidence},
                        raw_ref=e.raw_ref,
                    )
                )
    # de-dupe
    seen = set()
    out = []
    for e in outcomes:
        if e.id not in seen:
            seen.add(e.id)
            out.append(e)
    return out


def make_milestones(
    day: str, all_events: list[TraceEvent], outcomes: list[TraceEvent]
) -> list[TraceEvent]:
    by_project: dict[str, list[TraceEvent]] = defaultdict(list)
    for e in all_events + outcomes:
        by_project[project_of(e)].append(e)
    milestones: list[TraceEvent] = []
    now = datetime.now().isoformat(timespec="seconds")
    for project, items in sorted(by_project.items()):
        if project == "misc":
            continue
        inputs = [
            e
            for e in items
            if e.kind == "user_input" and e.source in {"codex", "hermes"}
        ]
        outs = [e for e in items if e.source == "outcome"]
        commits = [e for e in outs if e.kind == "commit_created"]
        if not inputs and not outs:
            continue
        if commits:
            status = "reached"
            title = f"{project}: commit milestone"
        elif outs and inputs:
            status = "partial"
            title = f"{project}: input-to-outcome work block"
        elif inputs:
            status = "discussed"
            title = f"{project}: user input captured"
        else:
            status = "observed"
            title = f"{project}: outcome observed"
        input_samples = [e.title for e in inputs[:3]]
        outcome_samples = [e.title for e in outs[:4]]
        summary = (
            f"Inputs={len(inputs)}, outcomes={len(outs)}, commits={len(commits)}. "
        )
        if input_samples:
            summary += "Input examples: " + " / ".join(input_samples)[:280] + ". "
        if outcome_samples:
            summary += "Outcome examples: " + " / ".join(outcome_samples)[:280] + "."
        evidence = {
            "input_ids": [e.id for e in inputs[:20]],
            "outcome_ids": [e.id for e in outs[:20]],
            "status": status,
            "rule": "same project + same day; commit/user-input/outcome heuristic",
        }
        milestones.append(
            TraceEvent(
                id="milestone-"
                + hashlib.sha1(
                    f"{day}:{project}:{status}:{len(inputs)}:{len(outs)}".encode()
                ).hexdigest()[:16],
                source="milestone",
                kind=status,
                start=now,
                end=None,
                title=title,
                summary=summary,
                project_guess=project,
                sensitivity="private",
                evidence=evidence,
            )
        )
    return milestones


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--events", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    base_events = load_many(args.events)
    outcomes = make_outcomes(args.date, base_events)
    milestones = make_milestones(args.date, base_events, outcomes)
    write_events(args.out, outcomes + milestones)
    print(
        f"wrote {len(outcomes)} outcomes and {len(milestones)} milestones to {args.out}"
    )


if __name__ == "__main__":
    main()
