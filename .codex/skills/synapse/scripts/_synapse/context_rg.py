from __future__ import annotations

from pathlib import Path

from .file_ops import truncate_bytes
from .process_utils import CmdResult, run_cmd
from .context_queries import derive_rg_queries
from .context_sensitivity import SENSITIVE_RG_GLOBS


def _base_rg_globs() -> list[str]:
    return [
        "!**/.synapse/**",
        "!**/node_modules/**",
        "!**/.venv/**",
        "!**/venv/**",
        "!**/__pycache__/**",
        "!**/.env",
        "!**/.env.*",
        "!**/.npmrc",
        "!**/.pypirc",
        "!**/.netrc",
        "!**/.git-credentials",
        "!**/id_rsa",
        "!**/id_dsa",
        "!**/id_ecdsa",
        "!**/id_ed25519",
        "!**/*.pem",
        "!**/*.key",
        "!**/*.p12",
        "!**/*.pfx",
        "!**/*.kdbx",
        *SENSITIVE_RG_GLOBS,
        "!**/dist/**",
        "!**/build/**",
        "!**/out/**",
    ]


def build_rg_summary(
    *,
    project_root: Path,
    query: str,
    rg_queries: list[str] | None,
    rg_max_depth: int,
    rg_max_queries: int,
    rg_max_matches_per_query: int,
    rg_max_total_matches: int,
    rg_max_count_per_file: int,
    rg_max_filesize: str,
) -> list[str]:
    parts: list[str] = []
    parts.append("## ripgrep (summary)")
    parts.append("")
    derived_queries = not rg_queries
    queries = rg_queries[:] if rg_queries else derive_rg_queries(query, max_queries=rg_max_queries)
    queries = [q for q in queries if isinstance(q, str) and q.strip()]
    if len(queries) > rg_max_queries:
        queries = queries[:rg_max_queries]
    total_hits = 0

    for q in queries:
        if total_hits >= rg_max_total_matches:
            break
        parts.append(f"### `rg -n {q!r}`")
        parts.append("")
        try:
            rg_argv = ["rg", "-n", "--max-depth", str(rg_max_depth), "--max-filesize", rg_max_filesize, "--max-count", str(max(1, rg_max_count_per_file))]
            for glob in _base_rg_globs():
                rg_argv += ["--glob", glob]
            if derived_queries:
                rg_argv.append("-F")
            rg_argv += ["--", q]
            rg = run_cmd(rg_argv, cwd=project_root, timeout_seconds=60)
            if rg.code == 2 and "No files were searched" in (rg.stderr or ""):
                rg = CmdResult(code=1, stdout="", stderr=rg.stderr)
            if rg.code not in (0, 1):
                parts.append("```")
                msg = f"(rg failed: exit_code={rg.code})"
                if rg.stderr.strip():
                    msg += "\n" + truncate_bytes(rg.stderr.strip(), 4000).rstrip()
                parts.append(msg)
                parts.append("```")
                parts.append("")
                continue
            lines = [ln for ln in rg.stdout.splitlines() if ln.strip()]
            if not lines:
                parts.append("```")
                parts.append("(no matches)")
                parts.append("```")
                parts.append("")
                continue
            remaining = rg_max_total_matches - total_hits
            take = min(len(lines), min(rg_max_matches_per_query, remaining))
            total_hits += take
            parts.append("```")
            parts.append("\n".join(lines[:take]))
            if len(lines) > take:
                parts.append("…(truncated)")
            parts.append("```")
            parts.append("")
        except Exception as e:
            parts.append("```")
            parts.append(f"(rg failed: {type(e).__name__}: {e})")
            parts.append("```")
            parts.append("")
    return parts
