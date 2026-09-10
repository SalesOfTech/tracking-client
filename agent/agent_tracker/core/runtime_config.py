from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
import sys
from importlib import resources


DEFAULT_CONFIG_FILENAME = "agent_config.json"
EMBEDDED_CONFIG_FILENAME = "embedded_config.json"


@dataclass
class RuntimeConfig:
    company_id: str
    base_url: str
    config_endpoint: str = "/soft-agent/config"
    events_endpoint: str = "/soft-agent/events"


def load_runtime_config(path: str | None) -> RuntimeConfig:
    config_path = _resolve_config_path(path)
    if not config_path.exists():
        _write_embedded_config(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data.setdefault("config_endpoint", "/soft-agent/config")
    data.setdefault("events_endpoint", "/soft-agent/events")
    return RuntimeConfig(**data)


def _resolve_config_path(path: str | None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name(DEFAULT_CONFIG_FILENAME)
    return Path(DEFAULT_CONFIG_FILENAME).resolve()


def _write_embedded_config(target: Path) -> None:
    try:
        embedded = resources.files("agent_tracker").joinpath(EMBEDDED_CONFIG_FILENAME)
        payload = embedded.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise FileNotFoundError(
            f"Config file not found at {target}. Provide --config or place {DEFAULT_CONFIG_FILENAME} next to binary."
        ) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
