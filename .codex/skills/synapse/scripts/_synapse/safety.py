from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SynapseError(RuntimeError):
    pass


def _normalize_write_root_token(token: str) -> str:
    t = (token or "").strip()
    t = t.rstrip("/\\")
    if t.startswith("./") or t.startswith(".\\"):
        t = t[2:]
    if os.name == "nt":
        t = t.casefold()
    return t


_FILE_ONLY_WRITE_ROOTS = frozenset(_normalize_write_root_token(x) for x in ("AGENTS.md", ".gitignore"))


@dataclass(frozen=True)
class WriteGuard:
    project_root: Path
    allowed_roots: tuple[str, ...]

    @classmethod
    def from_defaults(cls, *, project_root: Path, defaults: dict[str, Any]) -> "WriteGuard":
        safety = defaults.get("safety") if isinstance(defaults, dict) else None
        roots = safety.get("allowed_write_roots") if isinstance(safety, dict) else None
        if not isinstance(roots, list):
            roots = []
        allowed: list[str] = []
        seen: set[str] = set()
        for root_token in roots:
            if not isinstance(root_token, str):
                continue
            norm = _normalize_write_root_token(root_token)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            allowed.append(norm)
        if not allowed:
            allowed = [_normalize_write_root_token(x) for x in ("AGENTS.md", ".gitignore", ".synapse")]
        return cls(project_root=project_root.resolve(), allowed_roots=tuple(allowed))

    def assert_allowed(self, path: Path) -> None:
        full = path if path.is_absolute() else (self.project_root / path)
        try:
            full = full.resolve()
        except Exception as e:
            raise SynapseError(f"Unable to resolve write path: {full}") from e

        try:
            rel = full.relative_to(self.project_root)
        except Exception as e:
            raise SynapseError(f"Write outside project root is not allowed: {full}") from e

        parts = rel.parts
        if not parts or parts == (".",):
            raise SynapseError(f"Write target is not a file path: {full}")

        root = _normalize_write_root_token(parts[0])
        if root in self.allowed_roots:
            if root in _FILE_ONLY_WRITE_ROOTS and len(parts) != 1:
                raise SynapseError(f"Write blocked by safety policy: {full} (root {root} is file-only)")
            return
        raise SynapseError(f"Write blocked by safety policy: {full} (allowed roots: {', '.join(self.allowed_roots)})")
