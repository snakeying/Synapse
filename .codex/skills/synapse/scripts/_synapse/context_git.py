from __future__ import annotations

from pathlib import Path

from .file_ops import truncate_bytes
from .process_utils import run_cmd
from .context_sensitivity import filter_sensitive_diff


def build_git_section(
    *,
    project_root: Path,
    status_max_lines: int,
    diff_max_lines: int,
    diff_max_bytes: int,
) -> list[str]:
    parts: list[str] = []
    parts.append("## Git")
    parts.append("")

    head = run_cmd(["git", "rev-parse", "HEAD"], cwd=project_root, timeout_seconds=10)
    branch = run_cmd(["git", "branch", "--show-current"], cwd=project_root, timeout_seconds=10)
    parts.append(f"- branch: {branch.stdout.strip() or '(detached)'}")
    parts.append(f"- head: {head.stdout.strip() if head.code == 0 else '(no commits yet)'}")
    parts.append("")

    status = run_cmd(["git", "status", "--porcelain", "-b"], cwd=project_root, timeout_seconds=20)
    status_lines = status.stdout.splitlines()[:status_max_lines]
    parts.append("### `git status --porcelain -b`")
    parts.append("")
    parts.append("```")
    parts.append("\n".join(status_lines) if status_lines else "(clean)")
    parts.append("```")
    parts.append("")

    diff_stat = run_cmd(["git", "diff", "--stat"], cwd=project_root, timeout_seconds=30)
    parts.append("### `git diff --stat`")
    parts.append("")
    parts.append("```")
    parts.append(diff_stat.stdout.strip() or "(no diff)")
    parts.append("```")
    parts.append("")

    diff = run_cmd(["git", "diff"], cwd=project_root, timeout_seconds=60)
    diff_text = "\n".join(diff.stdout.splitlines()[:diff_max_lines])
    diff_text = truncate_bytes(diff_text, diff_max_bytes).rstrip()
    diff_text, redacted_diff_files = filter_sensitive_diff(project_root, diff_text)
    parts.append("### `git diff` (truncated)")
    parts.append("")
    if redacted_diff_files:
        preview = ", ".join(redacted_diff_files[:5])
        suffix = "" if len(redacted_diff_files) <= 5 else f" (+{len(redacted_diff_files) - 5} more)"
        parts.append(f"- note: omitted diff bodies for {len(redacted_diff_files)} sensitive file(s): {preview}{suffix}")
        parts.append("")
    parts.append("```diff")
    parts.append(diff_text or "(all diff bodies omitted or no diff)")
    parts.append("```")
    parts.append("")
    return parts
