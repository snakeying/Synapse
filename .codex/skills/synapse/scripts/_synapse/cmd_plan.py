from __future__ import annotations
import argparse
from pathlib import Path
from typing import Optional
from .config import load_defaults
from .file_ops import utc_now_iso, write_text
from .paths import ensure_synapse_layout, find_project_root, slugify, synapse_paths, unique_path
from .safety import SynapseError, WriteGuard
from .storage_paths import resolve_path_within_root
from .context_pack import build_context_pack
from .state import rebuild_index, update_state, upsert_plan_file

def _gate_stub() -> str:
    return "\n".join(
        [
            "## Gate (Codex) — single confirmation point",
            "",
            "Confirm these once after reviewing the gate_prep output (clarifications + decisions + side effects).",
            "",
            "### Required confirmations",
            "",
            "- [ ] Clarification checklist is resolved (single-round): unanswered items use the recommended defaults.",
            "- [ ] Scope and acceptance criteria are correct (no missing requirements).",
            "- [ ] `task_type` selection is confirmed:",
            "  - Options: `frontend` / `backend` / `fullstack`",
            "  - Codex recommendation: (fill in)  |  User choice: (fill in)",
            "- [ ] Stack/toolchain choice is confirmed (or explicitly TBD).",
            "- [ ] Allowed side effects are confirmed:",
            "  - [ ] Install dependencies (project-local).",
            "  - [ ] Generate/update lockfiles (project-local).",
            "  - [ ] Create toolchain artifacts (e.g., `.venv/`, `node_modules/`, build outputs).",
            "- [ ] Git/review setup is confirmed:",
            "  - [ ] Ensure this is a git repo (`git init` if needed).",
            "  - [ ] Before review: run `git add -N .` so new files appear in `git diff`.",
            "- [ ] Verification plan is confirmed (what `synapse verify` will run after applying code).",
            "",
            "### Open questions (only if truly blocking; keep short)",
            "",
            "- Q1: (if any)",
            "",
            "### Next steps (after Gate is confirmed)",
            "",
            "- Generate draft diffs via `synapse run ...` (Claude/Gemini).",
            "- Codex applies final code changes (rewrite drafts into production quality).",
            "- Run: `synapse verify`",
            "- Build a review context pack via `synapse pack --phase review ...`, then run audits via `synapse run ...`.",
            "",
        ]
    ).strip()

def cmd_plan(args: argparse.Namespace) -> int:
    defaults = load_defaults()
    project_root = find_project_root(Path(args.project_dir))
    paths = synapse_paths(project_root)

    guard = WriteGuard.from_defaults(project_root=project_root, defaults=defaults)
    ensure_synapse_layout(paths, guard=guard)

    request = " ".join(args.request).strip()
    if not request:
        raise SynapseError("plan requires a non-empty request")

    task_type = getattr(args, "task_type", None) or "fullstack"
    if task_type not in ("frontend", "backend", "fullstack"):
        task_type = "fullstack"

    slug_raw = getattr(args, "slug", None)
    slug = slugify(str(slug_raw)) if slug_raw else slugify(request)
    plan_path = paths.plan_dir / f"{slug}.md"
    backup_path: Optional[Path] = None
    if plan_path.exists():
        backup_path = unique_path(plan_path.with_suffix(plan_path.suffix + ".bak"))
        try:
            write_text(backup_path, plan_path.read_text(encoding="utf-8", errors="replace"), guard=guard)
        except Exception as e:
            raise SynapseError(f"Failed to backup existing plan file: {type(e).__name__}: {e}") from e
        print(f"backup: {backup_path}")

    rg_queries = list(getattr(args, "rg_query", []) or [])
    include_files_raw = list(getattr(args, "include_file", []) or [])
    include_files: list[Path] = []
    for p in include_files_raw:
        include_files.append(resolve_path_within_root(project_root, Path(p)))

    context_pack: Optional[Path] = None
    if not bool(getattr(args, "no_pack", False)):
        context_pack = build_context_pack(
            paths=paths,
            defaults=defaults,
            slug=slug,
            phase="plan",
            query=request,
            rg_queries=rg_queries if rg_queries else None,
            include_files=include_files if include_files else None,
            guard=guard,
        )

    plan_text = "\n".join(
        [
            "## gate_prep (Claude) — clarification checklist + acceptance criteria",
            "",
            "(TBD: required for workflow. Run `synapse run --model claude --phase gate_prep ...` and paste/summarize here.)",
            "",
            "## gate_prep (Gemini) — optional (frontend/fullstack only)",
            "",
            "(TBD: optional. Run `synapse run --model gemini --phase gate_prep ...` and paste/summarize here; skip if backend-only.)",
            "",
            _gate_stub(),
        ]
    ).strip()

    upsert_plan_file(
        plan_path=plan_path,
        slug=str(slug),
        request=request,
        context_pack_path=context_pack,
        plan_text=plan_text,
        sessions={},
        extra={"task_type": task_type},
        guard=guard,
    )

    rebuild_index(paths, guard=guard)
    update_state(
        paths,
        last={
            "command": "plan",
            "slug": slug,
            "task_type": task_type,
            "plan_path": str(plan_path),
            "context_pack": str(context_pack) if context_pack else None,
            "backup_plan": str(backup_path) if backup_path else None,
            "at": utc_now_iso(),
        },
        guard=guard,
    )

    print(f"slug: {slug}")
    print(f"task_type: {task_type}")
    print(f"plan: {plan_path}")
    if context_pack:
        print(f"context_pack: {context_pack}")
    return 0
