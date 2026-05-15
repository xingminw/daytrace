from daytrace.collector_config import (
    CollectorConfigError,
    enabled_source,
    expand_path,
    load_collector_config,
    stamp_event,
)
from daytrace.schema import TraceEvent
from scripts.collect_git import find_repos
from scripts.collect_from_config import collect_source_for_day


def make_event():
    return TraceEvent(
        id="e1",
        source="codex",
        kind="user_input",
        start="2026-05-14T09:00:00",
        end=None,
        title="t",
        summary="s",
        project_guess="daytrace",
        confidence=0.9,
        sensitivity="private",
        evidence={"x": 1},
    )


def test_load_collector_config_requires_device(tmp_path):
    path = tmp_path / "collector.yaml"
    path.write_text(
        """
device:
  id: omen
  collector_id: hermes-mtl
sources:
  codex:
    enabled: true
""",
        encoding="utf-8",
    )
    config = load_collector_config(path)
    assert config["device"]["id"] == "omen"


def test_expand_path_supports_environment_variables(monkeypatch):
    monkeypatch.setenv("OMEN_WINDOWS_USERPROFILE", "/mnt/c/Users/mtl")
    assert str(expand_path("$OMEN_WINDOWS_USERPROFILE/.codex")) == "/mnt/c/Users/mtl/.codex"


def test_expand_path_rejects_unresolved_environment_variables(monkeypatch):
    monkeypatch.delenv("OMEN_WINDOWS_USERPROFILE", raising=False)
    try:
        expand_path("$OMEN_WINDOWS_USERPROFILE/.codex")
    except CollectorConfigError as exc:
        assert "unresolved environment variable" in str(exc)
    else:
        raise AssertionError("expected unresolved env var to fail")


def test_config_rejects_unsafe_device_id(tmp_path):
    path = tmp_path / "collector.yaml"
    path.write_text(
        """
device:
  id: ../escape
  collector_id: hermes-mtl
""",
        encoding="utf-8",
    )
    try:
        load_collector_config(path)
    except CollectorConfigError as exc:
        assert "device.id must be a safe path segment" in str(exc)
    else:
        raise AssertionError("expected unsafe device id to fail")


def test_find_repos_accepts_exact_repo_without_siblings(tmp_path):
    repo = tmp_path / "daytrace"
    sibling = tmp_path / "private"
    (repo / ".git").mkdir(parents=True)
    (sibling / ".git").mkdir(parents=True)
    assert find_repos([repo]) == [repo]


def test_enabled_source_skips_false_like_strings():
    for value in ["disabled", "false", "off", "no"]:
        assert enabled_source({"sources": {"codex": value}}, "codex") is None
        assert enabled_source({"sources": {"codex": {"enabled": value}}}, "codex") is None


def test_enabled_source_rejects_unknown_strings():
    try:
        enabled_source({"sources": {"codex": "maybe"}}, "codex")
    except CollectorConfigError as exc:
        assert "unknown state" in str(exc)
    else:
        raise AssertionError("expected unknown source state to fail")


def test_enabled_source_rejects_invalid_enabled_types():
    for value in [0, 1, None, [], {}]:
        try:
            enabled_source({"sources": {"codex": {"enabled": value}}}, "codex")
        except CollectorConfigError as exc:
            assert "enabled must be a boolean or known string" in str(exc)
        else:
            raise AssertionError(f"expected invalid enabled value to fail: {value!r}")


def test_enabled_source_rejects_missing_enabled_in_mapping():
    try:
        enabled_source({"sources": {"codex": {"limit": 10}}}, "codex")
    except CollectorConfigError as exc:
        assert "requires explicit enabled value" in str(exc)
    else:
        raise AssertionError("expected missing enabled value to fail")


def test_collect_git_source_requires_explicit_roots_or_repos():
    try:
        collect_source_for_day("git", {"enabled": True}, "2026-05-14")
    except CollectorConfigError as exc:
        assert "git source requires explicit roots or repos" in str(exc)
    else:
        raise AssertionError("expected git source without roots/repos to fail")


def test_stamp_event_applies_device_metadata():
    config = {
        "device": {
            "id": "omen",
            "name": "OMEN Desktop WSL2",
            "location_id": "unknown",
            "collector_id": "hermes-mtl",
        }
    }
    stamped = stamp_event(make_event(), config)
    assert stamped.device_id == "omen"
    assert stamped.location_id == "unknown"
    assert stamped.collector_id == "hermes-mtl"
    assert stamped.evidence["collector_config_device"] == "omen"
    assert stamped.evidence["x"] == 1
