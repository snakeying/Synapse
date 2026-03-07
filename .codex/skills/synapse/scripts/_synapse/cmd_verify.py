from __future__ import annotations
import argparse
import datetime as _dt
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
from .config import load_defaults
from .file_ops import utc_now_iso, write_text
from .paths import ensure_synapse_layout, find_project_root, slugify, synapse_paths, unique_path
from .process_utils import run_cmd
from .safety import SynapseError, WriteGuard
from .state import rebuild_index, update_state
from .verify.dotnet import detect_dotnet_steps
from .verify.golang import detect_go_steps
from .verify.node import detect_node_steps
from .verify.python import detect_python_steps
from .verify.rust import detect_rust_steps
from .verify.types import VerifyStep, VerifyStepResult

def _ensure_layout(project_root: Path, *, guard: WriteGuard | None):
    paths = synapse_paths(project_root)
    ensure_synapse_layout(paths, guard=guard)
    return paths

def _detect_steps(project_root: Path, *, defaults: dict[str, Any], no_install: bool) -> list[VerifyStep]:
    runner = defaults.get("runner", {})
    timeout_seconds = int(runner.get("timeout_seconds", 3600))

    steps: list[VerifyStep] = []
    steps.extend(detect_node_steps(project_root, timeout_seconds=timeout_seconds, no_install=no_install))
    steps.extend(detect_python_steps(project_root, timeout_seconds=timeout_seconds, no_install=no_install))
    steps.extend(detect_rust_steps(project_root, timeout_seconds=timeout_seconds))
    steps.extend(detect_go_steps(project_root, timeout_seconds=timeout_seconds))
    steps.extend(detect_dotnet_steps(project_root, timeout_seconds=timeout_seconds))
    return steps

def _write_verify_log(
    log_path: Path,
    *,
    step: VerifyStep,
    exit_code: Optional[int],
    stdout: str,
    stderr: str,
    error: Optional[str],
    guard: WriteGuard | None,
) -> None:
    parts: list[str] = []
    parts.append(f"step: {step.name}")
    parts.append(f"kind: {step.kind}")
    parts.append(f"cwd: {step.cwd}")
    parts.append(f"argv: {' '.join(step.argv)}")
    if exit_code is not None:
        parts.append(f"exit_code: {exit_code}")
    if error:
        parts.append(f"error: {error}")
    parts.append("")
    if stdout:
        parts.append("### STDOUT")
        parts.append(stdout.rstrip())
        parts.append("")
    if stderr:
        parts.append("### STDERR")
        parts.append(stderr.rstrip())
        parts.append("")
    write_text(log_path, "\n".join(parts).rstrip() + "\n", guard=guard)

def cmd_verify(args: argparse.Namespace) -> int:
    defaults = load_defaults()
    project_root = find_project_root(Path(args.project_dir))
    guard = WriteGuard.from_defaults(project_root=project_root, defaults=defaults)
    paths = _ensure_layout(project_root, guard=guard)

    dry_run = bool(getattr(args, "dry_run", False))
    no_install = bool(getattr(args, "no_install", False))
    keep_going = bool(getattr(args, "keep_going", False))

    steps = _detect_steps(project_root, defaults=defaults, no_install=no_install)
    if not steps:
        print("verify: no recognizable toolchain found (nothing to run).")
        update_state(paths, last={"command": "verify", "at": utc_now_iso(), "results": [], "note": "no steps"}, guard=guard)
        return 0

    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    results: list[VerifyStepResult] = []

    print("verify: planned_steps:")
    for s in steps:
        print(f"- {s.name}: {' '.join(s.argv)}")

    if dry_run:
        for s in steps:
            results.append(
                VerifyStepResult(
                    name=s.name,
                    argv=s.argv,
                    kind=s.kind,
                    log_path=None,
                    status="DRY_RUN",
                    exit_code=None,
                    duration_seconds=None,
                    error=None,
                )
            )
        rebuild_index(paths, guard=guard)
        update_state(
            paths,
            last={"command": "verify", "at": utc_now_iso(), "dry_run": True, "results": [asdict(r) for r in results]},
            guard=guard,
        )
        return 0

    ok = True
    for step in steps:
        log_name = f"{ts}-verify-{slugify(step.name)}.log"
        log_path = unique_path(paths.logs_dir / log_name)

        start = time.time()
        exit_code: Optional[int] = None
        stdout = ""
        stderr = ""
        error: Optional[str] = None

        try:
            res = run_cmd(step.argv, cwd=step.cwd, timeout_seconds=step.timeout_seconds)
            exit_code = res.code
            stdout = res.stdout
            stderr = res.stderr
            status = "OK" if res.code == 0 else "FAILED"
            if res.code != 0:
                ok = False
        except SynapseError as e:
            ok = False
            status = "BLOCKED"
            error = str(e)

        duration = time.time() - start
        try:
            _write_verify_log(log_path, step=step, exit_code=exit_code, stdout=stdout, stderr=stderr, error=error, guard=guard)
        except Exception as e:
            error = (error + " | " if error else "") + f"log_error: {type(e).__name__}: {e}"

        results.append(
            VerifyStepResult(
                name=step.name,
                argv=step.argv,
                kind=step.kind,
                log_path=str(log_path),
                status=status,
                exit_code=exit_code,
                duration_seconds=duration,
                error=error,
            )
        )

        print(f"verify: {step.name}: {status} ({duration:.1f}s) -> {log_path}")

        if status != "OK" and not keep_going:
            break

    rebuild_index(paths, guard=guard)
    update_state(
        paths,
        last={
            "command": "verify",
            "at": utc_now_iso(),
            "results": [asdict(r) for r in results],
        },
        guard=guard,
    )

    return 0 if ok else 2
