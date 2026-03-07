from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from .context_git import build_git_section
from .context_queries import derive_rg_queries
from .context_rg import build_rg_summary
from .context_sensitivity import filter_sensitive_diff, is_sensitive_file_candidate
from .context_snippets import select_key_files, snippet_for_file
from .file_ops import write_text
from .paths import SynapsePaths, is_git_repo, unique_path
from .safety import WriteGuard


def build_context_pack(
    *,
    paths: SynapsePaths,
    defaults: dict[str, Any],
    slug: str,
    phase: str,
    query: str,
    rg_queries: list[str] | None = None,
    include_files: list[Path] | None = None,
    guard: WriteGuard | None = None,
) -> Path:
    cfg = defaults.get("context_pack", {})
    rg_cfg = cfg.get("rg", {})
    snip_cfg = cfg.get("snippets", {})
    git_cfg = cfg.get("git", {})

    out_path = unique_path(paths.context_dir / f"{slug}-{phase}.md")
    project_root = paths.project_root
    git_ok = is_git_repo(project_root)

    parts = [
        f"# Synapse Context Pack: `{slug}` / `{phase}`",
        "",
        f"- created_at: {_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()}",
        f"- project_root: {project_root}",
        f"- git_repo: {git_ok}",
        f"- query: {query}",
        "- note: context packs may include sensitive data (git diff / file snippets); review before sharing",
        "",
    ]

    if git_ok:
        parts.extend(
            build_git_section(
                project_root=project_root,
                status_max_lines=int(git_cfg.get("status_max_lines", 300)),
                diff_max_lines=int(git_cfg.get("diff_max_lines", 2000)),
                diff_max_bytes=int(git_cfg.get("diff_max_bytes", 200000)),
            )
        )
    else:
        parts.extend(["## Git", "", "(not a git repository)", ""])

    parts.extend(
        build_rg_summary(
            project_root=project_root,
            query=query,
            rg_queries=rg_queries,
            rg_max_depth=int(rg_cfg.get("max_depth", 25)),
            rg_max_queries=int(rg_cfg.get("max_queries", 10)),
            rg_max_matches_per_query=int(rg_cfg.get("max_matches_per_query", 80)),
            rg_max_total_matches=int(rg_cfg.get("max_total_matches", 200)),
            rg_max_count_per_file=int(rg_cfg.get("max_count_per_file", 20)),
            rg_max_filesize=str(rg_cfg.get("max_filesize", "1M")),
        )
    )

    parts.extend(["## Key files (snippets)", ""])
    key_files = select_key_files(project_root, max_files=int(snip_cfg.get("max_files", 20)), extra_files=include_files)
    if not key_files:
        parts.append("(none selected)")
    for path in key_files:
        rel = path
        if path.is_absolute():
            try:
                rel = path.relative_to(project_root)
            except Exception:
                rel = path
        if rel != path and is_sensitive_file_candidate(project_root / rel):
            continue
        parts.extend(
            [
                f"### `{rel}`",
                "",
                "```",
                snippet_for_file(
                    path,
                    max_lines=int(snip_cfg.get("max_lines_per_file", 160)),
                    max_bytes=int(snip_cfg.get("max_bytes_per_file", 20000)),
                ).rstrip(),
                "```",
                "",
            ]
        )

    write_text(out_path, "\n".join(parts).rstrip() + "\n", guard=guard)
    return out_path


__all__ = [
    "build_context_pack",
    "derive_rg_queries",
    "filter_sensitive_diff",
    "is_sensitive_file_candidate",
    "select_key_files",
    "snippet_for_file",
]
