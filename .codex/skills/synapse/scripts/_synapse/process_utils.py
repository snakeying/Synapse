from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .safety import SynapseError


@dataclass(frozen=True)
class CmdResult:
    code: int
    stdout: str
    stderr: str


def run_cmd(
    argv: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout_seconds: Optional[int] = None,
    check: bool = False,
) -> CmdResult:
    if cwd is not None:
        if not cwd.exists():
            raise SynapseError(f"Working directory not found: {cwd}")
        if not cwd.is_dir():
            raise SynapseError(f"Working directory is not a directory: {cwd}")
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=check,
        )
    except FileNotFoundError as e:
        raise SynapseError(f"Command not found: {argv[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise SynapseError(f"Command timed out after {timeout_seconds}s: {' '.join(argv)}") from e
    return CmdResult(code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
