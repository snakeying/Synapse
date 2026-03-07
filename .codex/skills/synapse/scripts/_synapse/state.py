from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Optional
from .file_ops import file_lock, read_json, read_text, utc_now_iso, write_json_atomic, write_text
from .paths import SynapsePaths
from .safety import WriteGuard
from .storage_paths import normalize_storage_paths, relative_storage_path

def extract_json_meta(markdown: str) -> dict[str, Any]:
    m = re.search(r"```json[ \t]*\r?\n(.*?)\r?\n```", markdown, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        return {}
    try:
        meta = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return {}
    return meta if isinstance(meta, dict) else {}

def upsert_plan_file(
    *,
    plan_path: Path,
    slug: str,
    request: str,
    context_pack_path: Optional[Path],
    plan_text: str,
    sessions: dict[str, Any],
    extra: Optional[dict[str, Any]] = None,
    guard: WriteGuard | None = None,
) -> None:
    now = utc_now_iso()
    existing_meta: dict[str, Any] = {}
    if plan_path.exists():
        try:
            existing_meta = extract_json_meta(read_text(plan_path))
        except Exception:
            existing_meta = {}
    created_at = now
    existing_created_at = existing_meta.get("created_at")
    if isinstance(existing_created_at, str) and existing_created_at.strip():
        created_at = existing_created_at.strip()

    existing_sessions = existing_meta.get("sessions") if isinstance(existing_meta.get("sessions"), dict) else {}
    merged_sessions = dict(existing_sessions)
    if isinstance(sessions, dict):
        merged_sessions.update(sessions)

    meta: dict[str, Any] = {
        "synapse_version": 1,
        "slug": slug,
        "created_at": created_at,
        "updated_at": now,
        "request": request,
        "context_pack": relative_storage_path(plan_path.parent.parent.parent, context_pack_path) if context_pack_path else None,
        "sessions": merged_sessions,
    }
    if extra:
        meta.update(extra)
    meta = normalize_storage_paths(meta, project_root=plan_path.parent.parent.parent)

    doc = "\n".join(
        [
            f"# Plan: `{slug}`",
            "",
            "## Synapse Meta",
            "",
            "```json",
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Request",
            "",
            request.strip(),
            "",
            "## Plan",
            "",
            plan_text.strip() or "(empty)",
            "",
        ]
    )
    write_text(plan_path, doc, guard=guard)

def _replace_json_meta(markdown: str, meta: dict[str, Any]) -> str:
    block = "\n".join(["```json", json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True), "```"])
    pattern = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n```", flags=re.DOTALL | re.IGNORECASE)
    if not pattern.search(markdown):
        raise ValueError("plan file is missing json meta block")
    return pattern.sub(lambda _m: block, markdown, count=1)

def update_plan_session(*, plan_path: Path, model: str, session_id: str, guard: WriteGuard | None = None) -> None:
    if model not in ("gemini", "claude"):
        return
    lock_path = plan_path.with_name(f"{plan_path.name}.lock")
    with file_lock(lock_path, guard=guard):
        text = read_text(plan_path)
        meta = extract_json_meta(text)
        if not meta:
            raise ValueError("plan file json meta could not be parsed")
        sessions = meta.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
        sessions[model] = session_id
        meta["sessions"] = sessions
        meta["updated_at"] = utc_now_iso()
        meta = normalize_storage_paths(meta, project_root=plan_path.parent.parent.parent)
        write_text(plan_path, _replace_json_meta(text, meta), guard=guard)

def update_state(
    paths: SynapsePaths,
    *,
    last: dict[str, Any],
    sessions_by_slug: Optional[dict[str, dict[str, str]]] = None,
    guard: WriteGuard | None = None,
) -> None:
    lock_path = paths.state_json.with_name(f"{paths.state_json.name}.lock")
    with file_lock(lock_path, guard=guard):
        state = read_json(paths.state_json)
        if not isinstance(state, dict):
            state = {}
        state.setdefault("version", 1)
        state["project_root"] = "."
        state["updated_at"] = utc_now_iso()
        state["last"] = normalize_storage_paths(last, project_root=paths.project_root)
        sessions = state.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
            state["sessions"] = sessions
        for model in ("gemini", "claude"):
            m = sessions.get(model)
            if not isinstance(m, dict):
                m = {}
                sessions[model] = m
            by_slug = m.get("by_slug")
            if not isinstance(by_slug, dict):
                m["by_slug"] = {}

        if sessions_by_slug:
            for model, mapping in sessions_by_slug.items():
                if model not in ("gemini", "claude"):
                    continue
                by_slug = sessions[model]["by_slug"]
                if isinstance(by_slug, dict) and isinstance(mapping, dict):
                    by_slug.update(mapping)

        state = normalize_storage_paths(state, project_root=paths.project_root)
        write_json_atomic(paths.state_json, state, guard=guard)

def rebuild_index(paths: SynapsePaths, *, guard: WriteGuard | None = None) -> None:
    lock_path = paths.index_json.with_name(f"{paths.index_json.name}.lock")
    with file_lock(lock_path, guard=guard):
        plans: list[dict[str, Any]] = []
        for p in sorted(paths.plan_dir.glob("*.md")):
            try:
                meta = extract_json_meta(read_text(p))
            except Exception:
                meta = {}
            slug = meta.get("slug") or p.stem
            sessions = meta.get("sessions") if isinstance(meta.get("sessions"), dict) else {}
            plans.append(
                {
                    "slug": slug,
                    "path": relative_storage_path(paths.project_root, p),
                    "created_at": meta.get("created_at"),
                    "sessions": sessions,
                }
            )
        index = {"version": 1, "updated_at": utc_now_iso(), "plans": plans}
        index = normalize_storage_paths(index, project_root=paths.project_root)
        write_json_atomic(paths.index_json, index, guard=guard)
