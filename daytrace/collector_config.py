from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .schema import TraceEvent


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
UNRESOLVED_ENV_RE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|%[A-Za-z_][A-Za-z0-9_]*%")


class CollectorConfigError(ValueError):
    pass


def ensure_safe_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CollectorConfigError(f"collector config requires {field}")
    if not SAFE_ID_RE.fullmatch(text):
        raise CollectorConfigError(
            f"{field} must be a safe path segment matching {SAFE_ID_RE.pattern}: {text!r}"
        )
    return text


def expand_path(value: str | Path) -> Path:
    expanded = os.path.expandvars(str(value))
    if UNRESOLVED_ENV_RE.search(expanded):
        raise CollectorConfigError(f"unresolved environment variable in path: {value}")
    return Path(expanded).expanduser()


def load_collector_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser()
    if not config_path.exists():
        raise CollectorConfigError(f"collector config not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    if config_path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise CollectorConfigError("collector config must be a mapping")
    validate_collector_config(data)
    return data


def validate_collector_config(config: dict[str, Any]) -> None:
    device = config.get("device")
    if not isinstance(device, dict):
        raise CollectorConfigError("collector config requires device mapping")
    ensure_safe_id(device.get("id"), "device.id")
    ensure_safe_id(device.get("collector_id"), "device.collector_id")
    ensure_safe_id(device.get("location_id") or "unknown", "device.location_id")
    sources = config.get("sources")
    if sources is not None and not isinstance(sources, dict):
        raise CollectorConfigError("collector config sources must be a mapping")


TRUE_VALUES = {"enabled", "true", "yes", "on"}
FALSE_VALUES = {"disabled", "false", "no", "off"}


def enabled_source(config: dict[str, Any], name: str) -> dict[str, Any] | None:
    sources = config.get("sources") or {}
    source = sources.get(name)
    if source is None:
        return None
    if source is False:
        return None
    if source is True:
        return {"enabled": True}
    if isinstance(source, str):
        value = source.strip().lower()
        if value in TRUE_VALUES:
            return {"enabled": True}
        if value in FALSE_VALUES:
            return None
        raise CollectorConfigError(f"source {name!r} has unknown state: {source!r}")
    if not isinstance(source, dict):
        raise CollectorConfigError(f"source {name!r} must be bool/string/mapping")
    if "enabled" not in source:
        raise CollectorConfigError(f"source {name!r} requires explicit enabled value")
    enabled = source.get("enabled")
    if isinstance(enabled, str):
        value = enabled.strip().lower()
        if value in FALSE_VALUES:
            return None
        if value not in TRUE_VALUES:
            raise CollectorConfigError(
                f"source {name!r} has unknown enabled value: {enabled!r}"
            )
    elif enabled is False:
        return None
    elif enabled is not True:
        raise CollectorConfigError(
            f"source {name!r} enabled must be a boolean or known string: {enabled!r}"
        )
    return source


def stamp_event(event: TraceEvent, config: dict[str, Any]) -> TraceEvent:
    device = config["device"]
    device_id = ensure_safe_id(device["id"], "device.id")
    location_id = ensure_safe_id(device.get("location_id") or "unknown", "device.location_id")
    collector_id = ensure_safe_id(device["collector_id"], "device.collector_id")
    evidence = {
        **event.evidence,
        "collector_config_device": device_id,
        "collector_config_location": location_id,
        "collector_config_collector": collector_id,
    }
    if device.get("name"):
        evidence["collector_config_device_name"] = str(device["name"])
    return replace(
        event,
        device_id=device_id,
        location_id=location_id,
        collector_id=collector_id,
        evidence=evidence,
    )


def stamp_events(events: list[TraceEvent], config: dict[str, Any]) -> list[TraceEvent]:
    return [stamp_event(event, config) for event in events]
