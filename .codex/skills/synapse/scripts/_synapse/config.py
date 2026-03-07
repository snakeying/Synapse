from __future__ import annotations

from pathlib import Path
from typing import Any

from .file_ops import read_json
from .safety import SynapseError


def defaults_path() -> Path:
    skill_dir = Path(__file__).resolve().parents[2]
    return skill_dir / "assets" / "defaults.json"


def load_defaults() -> dict[str, Any]:
    path = defaults_path()
    if not path.exists():
        raise SynapseError(f"defaults.json not found: {path}")
    data = read_json(path)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise SynapseError(f"Unsupported defaults.json: {path}")
    return data
