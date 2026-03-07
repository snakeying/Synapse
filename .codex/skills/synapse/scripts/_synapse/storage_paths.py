from __future__ import annotations

from pathlib import Path
from typing import Any

from .safety import SynapseError


def relative_storage_path(project_root: Path, path: Path | str | None) -> str | None:
    if path is None:
        return None
    if isinstance(path, Path):
        p = path
    else:
        raw = str(path).strip()
        if not raw:
            return raw
        p = Path(raw)
    try:
        proj = project_root.resolve()
        full = p if p.is_absolute() else (project_root / p)
        rel = full.resolve().relative_to(proj)
        return "." if not rel.parts else str(rel).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


def normalize_storage_paths(value: Any, *, project_root: Path) -> Any:
    if isinstance(value, dict):
        return {k: normalize_storage_paths(v, project_root=project_root) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_storage_paths(v, project_root=project_root) for v in value]
    if isinstance(value, tuple):
        return [normalize_storage_paths(v, project_root=project_root) for v in value]
    if isinstance(value, Path):
        return relative_storage_path(project_root, value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return value
        p = Path(value)
        if p.is_absolute() or (p.anchor and not p.is_absolute()):
            return relative_storage_path(project_root, value)
    return value


def resolve_path_within_root(project_root: Path, path: Path) -> Path:
    base = path if path.is_absolute() else (project_root / path)
    try:
        full = base.resolve()
    except Exception as e:
        raise SynapseError(f"Unable to resolve path: {path} -> {base}") from e

    try:
        full.relative_to(project_root.resolve())
    except Exception as e:
        raise SynapseError(f"Path escapes project root: {path} -> {full}") from e

    return full
