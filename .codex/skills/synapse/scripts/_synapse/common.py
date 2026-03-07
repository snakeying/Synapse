from __future__ import annotations
from contextlib import contextmanager
import datetime as _dt
import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

class SynapseError(RuntimeError):
    pass

def _is_windows_reserved_name(name: str) -> bool:
    """
    Windows device names are reserved as filenames, even with extensions:
    CON, PRN, AUX, NUL, COM1..COM9, LPT1..LPT9.
    """
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

def _normalize_write_root_token(token: str) -> str:
    """
    Normalize a configured allowed write root (first path component).

    - Strip trailing slashes/backslashes (".synapse/" -> ".synapse")
    - Treat "./" and ".\\" prefixes as cosmetic ("./.synapse" -> ".synapse")
    - On Windows, compare case-insensitively (use casefold)
    """
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
        for r in roots:
            if not isinstance(r, str):
                continue
            norm = _normalize_write_root_token(r)
            if not norm:
                continue
            if norm in seen:
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

def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()

def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def write_text(path: Path, content: str, *, guard: WriteGuard | None = None) -> None:
    if guard:
        guard.assert_allowed(path)
    safe_mkdir(path.parent)
    path.write_text(content, encoding="utf-8", newline="\n")

def read_json(path: Path) -> Any:
    return json.loads(read_text(path))

def write_json_atomic(path: Path, data: Any, *, guard: WriteGuard | None = None) -> None:
    if guard:
        guard.assert_allowed(path)
    safe_mkdir(path.parent)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    if guard:
        guard.assert_allowed(tmp)
    content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        tmp.write_text(content, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


@contextmanager
def file_lock(
    lock_path: Path,
    *,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
    stale_seconds: float = 3600.0,
    guard: WriteGuard | None = None,
):
    if guard:
        guard.assert_allowed(lock_path)
    safe_mkdir(lock_path.parent)
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    fd: Optional[int] = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"pid={os.getpid()} time={utc_now_iso()}\n".encode("utf-8", errors="replace"))
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if stale_seconds > 0 and age > stale_seconds:
                    try:
                        lock_path.unlink(missing_ok=True)
                        continue
                    except OSError:
                        pass
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise SynapseError(f"Timed out acquiring file lock: {lock_path}")
            time.sleep(max(0.01, poll_interval_seconds))
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


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

def resolve_path_within_root(project_root: Path, path: Path) -> Path:
    """
    Resolve a path (absolute or relative to project_root) and ensure it stays within project_root.

    Useful for user-provided paths like --include-file, to avoid accidental traversal outside
    the repo and to keep downstream relative path logic safe.
    """
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

def truncate_bytes(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    b = text.encode("utf-8", errors="replace")
    if len(b) <= max_bytes:
        return text
    suffix = "\n…(truncated)\n"
    suffix_b = suffix.encode("utf-8", errors="replace")
    if len(suffix_b) >= max_bytes:
        return suffix_b[:max_bytes].decode("utf-8", errors="replace")
    cut = b[: max(0, max_bytes - len(suffix_b))]
    return cut.decode("utf-8", errors="replace") + suffix
