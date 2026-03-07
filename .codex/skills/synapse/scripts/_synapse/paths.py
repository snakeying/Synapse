from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .file_ops import read_json, safe_mkdir, utc_now_iso, write_json_atomic
from .process_utils import run_cmd
from .safety import SynapseError, WriteGuard


def _is_windows_reserved_name(name: str) -> bool:
    if os.name != "nt":
        return False
    n = (name or "").strip().lower()
    if not n:
        return False
    base = n.split(".", 1)[0]
    if base in {"con", "prn", "aux", "nul"}:
        return True
    if base.startswith("com") and base[3:].isdigit():
        return 1 <= int(base[3:]) <= 9
    if base.startswith("lpt") and base[3:].isdigit():
        return 1 <= int(base[3:]) <= 9
    return False


def find_project_root(project_dir: Path) -> Path:
    project_dir = project_dir.resolve()
    try:
        res = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=project_dir, timeout_seconds=10)
    except SynapseError:
        return project_dir
    if res.code != 0:
        return project_dir
    root = res.stdout.strip()
    return Path(root).resolve() if root else project_dir


def is_git_repo(project_root: Path) -> bool:
    try:
        res = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=project_root, timeout_seconds=10)
    except SynapseError:
        return False
    return res.code == 0 and res.stdout.strip() == "true"


@dataclass(frozen=True)
class SynapsePaths:
    project_root: Path
    synapse_dir: Path
    plan_dir: Path
    context_dir: Path
    logs_dir: Path
    patches_dir: Path
    prompts_dir: Path
    index_json: Path
    state_json: Path


def synapse_paths(project_root: Path) -> SynapsePaths:
    syn_dir = project_root / ".synapse"
    return SynapsePaths(
        project_root=project_root,
        synapse_dir=syn_dir,
        plan_dir=syn_dir / "plan",
        context_dir=syn_dir / "context",
        logs_dir=syn_dir / "logs",
        patches_dir=syn_dir / "patches",
        prompts_dir=syn_dir / "prompts",
        index_json=syn_dir / "index.json",
        state_json=syn_dir / "state.json",
    )


def ensure_synapse_layout(paths: SynapsePaths, *, guard: WriteGuard | None = None) -> None:
    if guard:
        guard.assert_allowed(paths.synapse_dir)
        guard.assert_allowed(paths.plan_dir)
        guard.assert_allowed(paths.context_dir)
        guard.assert_allowed(paths.logs_dir)
        guard.assert_allowed(paths.patches_dir)
        guard.assert_allowed(paths.prompts_dir)

    safe_mkdir(paths.plan_dir)
    safe_mkdir(paths.context_dir)
    safe_mkdir(paths.logs_dir)
    safe_mkdir(paths.patches_dir)
    safe_mkdir(paths.prompts_dir)

    if not paths.index_json.exists():
        write_json_atomic(paths.index_json, {"version": 1, "plans": [], "updated_at": utc_now_iso()}, guard=guard)

    if not paths.state_json.exists():
        write_json_atomic(
            paths.state_json,
            {
                "version": 1,
                "project_root": ".",
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "last": {},
                "sessions": {"gemini": {"by_slug": {}}, "claude": {"by_slug": {}}},
            },
            guard=guard,
        )
        return

    state = read_json(paths.state_json)
    if not isinstance(state, dict):
        raise SynapseError(f"Invalid state.json (not an object): {paths.state_json}")
    state.setdefault("version", 1)
    state.setdefault("project_root", ".")
    state.setdefault("created_at", utc_now_iso())
    state["updated_at"] = utc_now_iso()
    state.setdefault("last", {})
    sessions = state.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
        state["sessions"] = sessions
    for model in ("gemini", "claude"):
        m = sessions.get(model)
        if not isinstance(m, dict):
            m = {}
            sessions[model] = m
        by_slug = m.get("by_slug")
        if not isinstance(by_slug, dict):
            m["by_slug"] = {}
    write_json_atomic(paths.state_json, state, guard=guard)


def slugify(text: str, *, max_len: int = 48) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]+", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    if not text:
        text = "task"
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    if _is_windows_reserved_name(text):
        text = f"task-{text}"
        if len(text) > max_len:
            text = text[:max_len].rstrip("-")
    return text or "task"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for i in range(2, 1000):
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise SynapseError(f"Unable to allocate unique path for: {path}")
