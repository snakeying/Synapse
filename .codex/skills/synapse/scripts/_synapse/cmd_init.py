from __future__ import annotations
import argparse
from pathlib import Path
from .agents_md import ensure_agents_md, ensure_gitignore
from .config import load_defaults
from .file_ops import utc_now_iso
from .paths import ensure_synapse_layout, find_project_root, synapse_paths
from .safety import WriteGuard
from .state import rebuild_index, update_state

def cmd_init(args: argparse.Namespace) -> int:
    defaults = load_defaults()
    project_root = find_project_root(Path(args.project_dir))
    paths = synapse_paths(project_root)
    guard = WriteGuard.from_defaults(project_root=project_root, defaults=defaults)
    ensure_synapse_layout(paths, guard=guard)
    ensure_gitignore(project_root, guard=guard)
    ensure_agents_md(project_root, guard=guard)
    rebuild_index(paths, guard=guard)
    update_state(paths, last={"command": "init", "at": utc_now_iso()}, guard=guard)

    print(f"project_root: {project_root}")
    print(f"synapse_dir: {paths.synapse_dir}")
    print(f"state: {paths.state_json}")
    print(f"index: {paths.index_json}")
    return 0
