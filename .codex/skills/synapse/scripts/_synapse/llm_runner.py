from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .file_ops import safe_mkdir
from .llm_protocol import model_argv, parse_stream_json_line
from .llm_stream import read_pipe, write_stderr_line, write_truncated_marker
from .safety import SynapseError, WriteGuard


@dataclass
class ModelRun:
    model: str
    prompt: str
    cwd: Path
    resume: Optional[str]
    timeout_seconds: int
    log_path: Path
    max_line_bytes: int
    guard: Optional[WriteGuard] = None
    output_text: str = ""
    session_id: Optional[str] = None
    exit_code: Optional[int] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    truncated_stdout_lines: int = 0
    truncated_stderr_lines: int = 0
def run_model_once(run: ModelRun) -> ModelRun:
    start = time.time()
    argv = model_argv(run.model, resume=run.resume)
    if run.guard:
        run.guard.assert_allowed(run.log_path)
    safe_mkdir(run.log_path.parent)
    buf: list[str] = []
    final_text: Optional[str] = None
    session_id: Optional[str] = None
    try:
        with run.log_path.open("w", encoding="utf-8", newline="\n") as logf:
            proc = subprocess.Popen(
                argv,
                cwd=str(run.cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if proc.stdin is not None:
                try:
                    proc.stdin.write(run.prompt)
                    if not run.prompt.endswith("\n"):
                        proc.stdin.write("\n")
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.stdout is None:
                raise SynapseError("No stdout pipe from model process")

            sentinel = object()
            stdout_q: queue.Queue[object] = queue.Queue()
            stderr_q: queue.Queue[object] = queue.Queue()
            t_out = threading.Thread(target=read_pipe, args=(proc.stdout, stdout_q, sentinel), daemon=True)
            t_err = threading.Thread(target=read_pipe, args=(proc.stderr, stderr_q, sentinel), daemon=True)
            t_out.start()
            t_err.start()

            stdout_done = False
            stderr_done = False
            timed_out = False
            deadline = time.monotonic() + run.timeout_seconds

            while True:
                if not timed_out and time.monotonic() > deadline and proc.poll() is None:
                    timed_out = True
                    try:
                        proc.kill()
                    except Exception:
                        pass

                try:
                    item = stdout_q.get(timeout=0.05)
                except queue.Empty:
                    item = None
                if item is sentinel:
                    stdout_done = True
                elif isinstance(item, str):
                    line = item
                    if run.max_line_bytes > 0:
                        b = line.encode("utf-8", errors="replace")
                        if len(b) > run.max_line_bytes:
                            run.truncated_stdout_lines += 1
                            write_truncated_marker(logf, stream="stdout", original_bytes=len(b), limit_bytes=run.max_line_bytes, prefix=b[: run.max_line_bytes].decode("utf-8", errors="replace"))
                            continue
                    logf.write(line)
                    stripped = line.strip()
                    if stripped:
                        delta, sid = parse_stream_json_line(run.model, stripped)
                        if sid and not session_id:
                            session_id = sid
                        if delta:
                            if run.model == "gemini":
                                buf.append(delta)
                            else:
                                final_text = delta

                for _ in range(200):
                    try:
                        eitem = stderr_q.get_nowait()
                    except queue.Empty:
                        break
                    if eitem is sentinel:
                        stderr_done = True
                        break
                    if not isinstance(eitem, str):
                        continue
                    line = eitem
                    if run.max_line_bytes > 0:
                        b = line.encode("utf-8", errors="replace")
                        if len(b) > run.max_line_bytes:
                            run.truncated_stderr_lines += 1
                            write_truncated_marker(logf, stream="stderr", original_bytes=len(b), limit_bytes=run.max_line_bytes, prefix=b[: run.max_line_bytes].decode("utf-8", errors="replace"))
                            continue
                    write_stderr_line(logf, line)

                if stdout_done and stderr_done and proc.poll() is not None:
                    break
                if timed_out and proc.poll() is None and time.monotonic() > deadline + 5:
                    break

            if proc.poll() is None:
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=1)
                    except Exception:
                        pass
            t_out.join(timeout=2)
            t_err.join(timeout=2)

            if timed_out:
                run.exit_code = None
                run.error = f"timeout after {run.timeout_seconds}s"
                run.duration_seconds = time.time() - start
                run.session_id = session_id
                run.output_text = final_text or "".join(buf)
                return run
            run.exit_code = proc.returncode
    except Exception as e:
        try:
            if run.guard:
                run.guard.assert_allowed(run.log_path)
            safe_mkdir(run.log_path.parent)
            with run.log_path.open("a", encoding="utf-8", newline="\n") as logf:
                logf.write(json.dumps({"type": "synapse_error", "error_type": type(e).__name__, "message": str(e)}, ensure_ascii=False) + "\n")
        except Exception:
            pass
        run.exit_code = None
        run.error = f"{type(e).__name__}: {e}"
        run.duration_seconds = time.time() - start
        run.session_id = session_id
        run.output_text = final_text or "".join(buf)
        return run

    run.duration_seconds = time.time() - start
    run.session_id = session_id
    run.output_text = "".join(buf) if run.model == "gemini" else (final_text or "")
    return run
