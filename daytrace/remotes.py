"""Loader for config/remotes.yaml — the registry of "other machines this hub
pulls data from."

The hub uses this for two distinct flows:

  1. deploy:  rsync the local repo's code dirs (scripts/, daytrace/, config/)
              to each remote's `repo_path`. Keeps remote collectors in sync
              with whatever we just shipped on the hub.

  2. catchup: per (remote × pending shifted-day), ssh in to run
              `collect_from_config.py --config <remote.config> --date <d>`,
              then rsync the resulting inbox/<device>/<date>/ back.

The schema is intentionally small (4 fields per remote) so the YAML stays
human-readable; everything else (per-source enablement, lookback windows,
etc.) lives in the per-device collector config that `config:` points at.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


@dataclass(frozen=True)
class Remote:
    """One entry in config/remotes.yaml after validation."""

    device_id: str
    ssh: str            # ~/.ssh/config alias (or user@host)
    repo_path: str      # absolute path on the remote where daytrace lives
    config: str         # collector config path relative to repo_path

    def as_cli_spec(self) -> str:
        """Render in the legacy `--remote device=ssh:path:config` form,
        so older code paths (or human debugging via the CLI) keep working."""
        return f"{self.device_id}={self.ssh}:{self.repo_path}:{self.config}"


def load_remotes(path: str | Path = "config/remotes.yaml") -> list[Remote]:
    """Parse the registry file. Returns [] if the file is missing — that's a
    valid configuration (hub with no remotes, single-machine setup)."""
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    items = raw.get("remotes") or []
    out: list[Remote] = []
    seen: set[str] = set()
    for i, item in enumerate(items):
        for key in ("device_id", "ssh", "repo_path", "config"):
            if not item.get(key):
                raise ValueError(
                    f"{path}: remotes[{i}] is missing required field {key!r}"
                )
        dev = item["device_id"]
        if dev in seen:
            raise ValueError(f"{path}: duplicate device_id {dev!r}")
        seen.add(dev)
        out.append(Remote(
            device_id=dev,
            ssh=item["ssh"],
            repo_path=item["repo_path"],
            config=item["config"],
        ))
    return out


def remotes_as_cli_specs(remotes: Iterable[Remote]) -> list[str]:
    """Adapter for code that still takes --remote-style strings."""
    return [r.as_cli_spec() for r in remotes]
