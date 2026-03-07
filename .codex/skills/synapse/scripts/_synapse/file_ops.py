from __future__ import annotations

import datetime as _dt
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from .safety import SynapseError, WriteGuard


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
