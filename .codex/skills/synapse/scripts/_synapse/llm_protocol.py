from __future__ import annotations

import json
import os
import shutil
from typing import Optional

from .safety import SynapseError


def model_argv(model: str, *, resume: Optional[str]) -> list[str]:
    if model == "gemini":
        cmd = "gemini"
        if os.name == "nt" and shutil.which("gemini.cmd"):
            cmd = "gemini.cmd"
        argv = [cmd, "-o", "stream-json", "--approval-mode", "default", "-p", ""]
        if resume:
            argv += ["--resume", resume]
        return argv
    if model == "claude":
        cmd = "claude"
        if os.name == "nt" and shutil.which("claude.cmd"):
            cmd = "claude.cmd"
        argv = [
            cmd,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--disable-slash-commands",
            "--permission-mode",
            "plan",
            "--tools",
            "",
            "--strict-mcp-config",
        ]
        if resume:
            argv += ["--resume", resume]
        return argv
    raise SynapseError(f"Unknown model: {model}")


def parse_stream_json_line(model: str, line: str) -> tuple[Optional[str], Optional[str]]:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(obj, dict):
        return None, None

    session_id = obj.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = None

    if model == "gemini":
        if obj.get("role") != "assistant":
            return None, session_id
        content = obj.get("content")
        return (content if isinstance(content, str) and content else None), session_id

    if model == "claude":
        result = obj.get("result")
        return (result if isinstance(result, str) and result else None), session_id

    return None, session_id
