from __future__ import annotations
import argparse
import datetime as _dt
from pathlib import Path
from .config import load_defaults
from .file_ops import utc_now_iso
from .paths import ensure_synapse_layout, find_project_root, slugify, synapse_paths
from .safety import SynapseError, WriteGuard
from .storage_paths import resolve_path_within_root
from .context_pack import build_context_pack
from .state import rebuild_index, update_state

def cmd_pack(args: argparse.Namespace) -> int:
    defaults = load_defaults()
    project_root = find_project_root(Path(args.project_dir))
    paths = synapse_paths(project_root)

    guard = WriteGuard.from_defaults(project_root=project_root, defaults=defaults)
    ensure_synapse_layout(paths, guard=guard)

    phase = (getattr(args, "phase", None) or "pack").strip()
    if not phase:
        raise SynapseError("pack requires a non-empty --phase")
    phase = slugify(phase, max_len=24)

    query = getattr(args, "query", None)
    query = str(query) if query is not None else ""

    slug = getattr(args, "slug", None)
    slug = str(slug).strip() if isinstance(slug, str) else ""
    if not slug:
        if query.strip():
            slug = slugify(query)
        else:
            slug = f"{phase}-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    else:
        slug = slugify(slug)

    rg_queries = list(getattr(args, "rg_query", []) or [])
    include_files_raw = list(getattr(args, "include_file", []) or [])
    include_files: list[Path] = []
    for p in include_files_raw:
        include_files.append(resolve_path_within_root(project_root, Path(p)))

    context_pack = build_context_pack(
        paths=paths,
        defaults=defaults,
        slug=slug,
        phase=phase,
        query=query,
        rg_queries=rg_queries if rg_queries else None,
        include_files=include_files if include_files else None,
        guard=guard,
    )

    rebuild_index(paths, guard=guard)
    update_state(
        paths,
        last={
            "command": "pack",
            "slug": slug,
            "phase": phase,
            "query": query,
            "context_pack": str(context_pack),
            "at": utc_now_iso(),
        },
        guard=guard,
    )

    print(f"slug: {slug}")
    print(f"phase: {phase}")
    if query.strip():
        print(f"query: {query.strip()}")
    print(f"context_pack: {context_pack}")
    return 0
