from __future__ import annotations

from pathlib import Path

from .file_ops import read_text, truncate_bytes
from .paths import is_git_repo
from .process_utils import run_cmd
from .context_sensitivity import is_sensitive_file_candidate


def select_key_files(project_root: Path, *, max_files: int, extra_files: list[Path] | None = None) -> list[Path]:
    candidates: list[Path] = []
    explicit: set[Path] = set()
    if extra_files:
        for p in extra_files:
            try:
                if p.exists() and p.is_file():
                    candidates.append(p)
                    explicit.add(p.resolve())
            except Exception:
                continue
    preferred_names = [
        "AGENTS.md",
        ".gitignore",
        "README.md",
        "README.txt",
        "README",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "Dockerfile",
    ]
    for name in preferred_names:
        p = project_root / name
        if p.exists() and p.is_file():
            if p.resolve() not in explicit and is_sensitive_file_candidate(p):
                continue
            candidates.append(p)
    if is_git_repo(project_root):
        try:
            st = run_cmd(["git", "status", "--porcelain", "-z"], cwd=project_root, timeout_seconds=20)
            if st.code == 0:
                items = st.stdout.split("\0")
                i = 0
                while i < len(items):
                    entry = items[i]
                    i += 1
                    if not entry or len(entry) < 4:
                        continue
                    status = entry[:2]
                    path_part = entry[3:]
                    if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
                        if i < len(items) and items[i]:
                            path_part = items[i]
                        i += 1
                    if not path_part:
                        continue
                    p = project_root / path_part
                    if p.exists() and p.is_file():
                        if p.resolve() not in explicit and is_sensitive_file_candidate(p):
                            continue
                        candidates.append(p)
        except Exception:
            pass

    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(p)
        if len(unique) >= max_files:
            break
    return unique


def snippet_for_file(path: Path, *, max_lines: int, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
        if size > max(256_000, max_bytes * 4):
            return f"(skipped: file too large: {size} bytes)"
        text = read_text(path)
    except Exception as e:
        return f"(skipped: {type(e).__name__}: {e})"

    lines = text.splitlines()
    head = lines[:max_lines]
    out = "\n".join(head)
    out = truncate_bytes(out, max_bytes)
    if len(lines) > max_lines:
        out += "\n…(truncated)\n"
    return out
