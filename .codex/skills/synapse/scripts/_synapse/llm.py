from __future__ import annotations

import datetime as _dt
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

from .llm_protocol import model_argv, parse_stream_json_line
from .llm_runner import ModelRun, run_model_once
from .paths import synapse_paths, unique_path
from .safety import WriteGuard


def _backoff_sleep(base: float, *, attempt: int, max_seconds: float, jitter: bool) -> None:
    delay = min(max_seconds, base * (2 ** max(0, attempt - 1)))
    if jitter:
        delay = delay * (0.6 + random.random() * 0.8)
    time.sleep(delay)


def run_model_with_retries(
    *,
    model: str,
    prompt: str,
    project_root: Path,
    resume: Optional[str],
    defaults: dict[str, Any],
    slug: str,
    phase: str,
    run_ts: Optional[str] = None,
) -> ModelRun:
    runner = defaults.get("runner", {})
    timeout_seconds = int(runner.get("timeout_seconds", 3600))
    retries = max(0, int(runner.get("retries", 2)))
    backoff_cfg = runner.get("retry_backoff", {})
    base_seconds = float(backoff_cfg.get("base_seconds", 2))
    max_seconds = float(backoff_cfg.get("max_seconds", 30))
    jitter = bool(backoff_cfg.get("jitter", True))
    stream_json = runner.get("stream_json", {})
    max_line_bytes = int(stream_json.get("max_line_bytes", 10_485_760)) if isinstance(stream_json, dict) else 10_485_760
    if max_line_bytes <= 0:
        max_line_bytes = 10_485_760

    guard = WriteGuard.from_defaults(project_root=project_root, defaults=defaults)
    ts = (run_ts or "").strip() or _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    next_resume = resume

    for attempt in range(1, retries + 2):
        attempt_suffix = "" if attempt == 1 else f"-attempt{attempt}"
        base_log_path = synapse_paths(project_root).logs_dir / f"{ts}-{slug}-{phase}-{model}-stream{attempt_suffix}.jsonl"
        log_path = unique_path(base_log_path)
        guard.assert_allowed(log_path)

        run = ModelRun(
            model=model,
            prompt=prompt,
            cwd=project_root,
            resume=next_resume,
            timeout_seconds=timeout_seconds,
            log_path=log_path,
            max_line_bytes=max_line_bytes,
            guard=guard,
        )
        run = run_model_once(run)
        if run.session_id:
            next_resume = run.session_id
        if run.exit_code == 0 and run.output_text.strip() == "" and not run.error:
            run.error = "exit_code=0 but no assistant output parsed from stream-json"
        if run.exit_code == 0 and run.output_text.strip():
            return run
        if attempt <= retries:
            _backoff_sleep(base_seconds, attempt=attempt, max_seconds=max_seconds, jitter=jitter)
            continue
        return run

    raise AssertionError("unreachable")


def extract_unified_diff(text: str) -> Optional[str]:
    m = re.search(r"```(?:diff|patch)[ \t]*\r?\n(.*?)\r?\n```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        diff = m.group(1).strip("\n") + "\n"
        if "diff --git" in diff or (diff.startswith("---") and "\n+++" in diff):
            return diff
    for match in re.finditer(r"^--- .*$", text, flags=re.MULTILINE):
        candidate = text[match.start() :].strip("\n")
        if "\n+++ " in candidate and "\n@@ " in candidate:
            return candidate + "\n"
    idx = text.find("diff --git")
    if idx != -1:
        return text[idx:].strip("\n") + "\n"
    return None


__all__ = [
    "ModelRun",
    "extract_unified_diff",
    "model_argv",
    "parse_stream_json_line",
    "run_model_once",
    "run_model_with_retries",
]
