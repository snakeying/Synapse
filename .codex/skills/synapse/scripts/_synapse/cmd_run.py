from __future__ import annotations
import argparse
import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Optional
from .config import load_defaults
from .file_ops import utc_now_iso, write_text
from .paths import ensure_synapse_layout, find_project_root, slugify, synapse_paths, unique_path
from .safety import SynapseError, WriteGuard
from .llm import extract_unified_diff, run_model_with_retries
from .state import rebuild_index, update_plan_session, update_state

def _parse_kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            raise SynapseError(f"Expected KEY=VALUE, got: {raw!r}")
        k, v = raw.split("=", 1)
        k = k.strip()
        if not k:
            raise SynapseError(f"Empty KEY in {raw!r}")
        out[k] = v
    return out

def _render_prompt(template: str, *, vars: dict[str, str]) -> str:
    out = template
    for k, v in vars.items():
        out = out.replace("{{" + k + "}}", v)
    return out

def cmd_run(args: argparse.Namespace) -> int:
    defaults = load_defaults()
    project_root = find_project_root(Path(args.project_dir))
    paths = synapse_paths(project_root)

    guard = WriteGuard.from_defaults(project_root=project_root, defaults=defaults)
    ensure_synapse_layout(paths, guard=guard)

    model = getattr(args, "model", None)
    if model not in ("claude", "gemini"):
        raise SynapseError("run requires --model {claude|gemini}")

    phase = (getattr(args, "phase", None) or "run").strip()
    if not phase:
        raise SynapseError("run requires a non-empty --phase")
    phase = slugify(phase, max_len=24)

    slug = (getattr(args, "slug", None) or "").strip()
    if not slug:
        raise SynapseError("run requires --slug")
    slug = slugify(slug)

    prompt_file_raw = getattr(args, "prompt_file", None)
    if not isinstance(prompt_file_raw, str) or not prompt_file_raw.strip():
        raise SynapseError("run requires --prompt-file <path>")
    prompt_file = Path(prompt_file_raw)
    if prompt_file.anchor and not prompt_file.is_absolute():
        raise SynapseError(f"Invalid --prompt-file path (drive-relative): {prompt_file_raw!r}")
    prompt_file = prompt_file if prompt_file.is_absolute() else (project_root / prompt_file).resolve()
    if not prompt_file.exists() or not prompt_file.is_file():
        raise SynapseError(f"Prompt file not found: {prompt_file}")

    template = prompt_file.read_text(encoding="utf-8", errors="replace")

    vars = _parse_kv(list(getattr(args, "var", []) or []))
    var_files = _parse_kv(list(getattr(args, "var_file", []) or []))
    if var_files:
        print(
            "synapse run note: --var-file contents are inlined into the rendered prompt and saved under .synapse/prompts/**; "
            "avoid passing secrets unless necessary.",
            file=sys.stderr,
        )
    for k, p in var_files.items():
        pp = Path(p)
        if pp.anchor and not pp.is_absolute():
            raise SynapseError(f"Invalid --var-file path (drive-relative) for {k}: {p!r}")
        pp = pp if pp.is_absolute() else (project_root / pp).resolve()
        if not pp.exists():
            raise SynapseError(f"Var file not found for {k}: {pp}")
        vars[k] = pp.read_text(encoding="utf-8", errors="replace")

    rendered = _render_prompt(template, vars=vars)
    placeholders = sorted(set(re.findall(r"{{([A-Za-z0-9_]+)}}", template)))
    if placeholders:
        missing = [k for k in placeholders if ("{{" + k + "}}") in rendered]
        if missing:
            print(f"synapse run warning: unreplaced template variables: {', '.join(missing)}", file=sys.stderr)
            print("synapse run hint: pass them via --var KEY=VALUE or --var-file KEY=PATH", file=sys.stderr)

    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    prompt_out = unique_path(paths.prompts_dir / f"{ts}-{slug}-{phase}-{model}.prompt.md")
    write_text(prompt_out, rendered.rstrip() + "\n", guard=guard)

    resume: Optional[str] = None
    if model == "gemini":
        resume = getattr(args, "resume_gemini", None)
    if model == "claude":
        resume = getattr(args, "resume_claude", None)

    run = run_model_with_retries(
        model=model,
        prompt=rendered,
        project_root=project_root,
        resume=resume,
        defaults=defaults,
        slug=slug,
        phase=phase,
        run_ts=ts,
    )
    if run.exit_code == 0 and (run.output_text or "").strip() == "":
        print(
            "synapse run warning: model returned exit_code=0 but no assistant output was parsed from stream-json; "
            f"inspect log: {run.log_path}",
            file=sys.stderr,
        )

    out_md = unique_path(paths.patches_dir / f"{ts}-{slug}-{phase}-{model}.md")
    write_text(out_md, (run.output_text or "").rstrip("\n") + "\n", guard=guard)

    out_diff = None
    diff = extract_unified_diff(run.output_text or "")
    if diff:
        out_diff = unique_path(paths.patches_dir / f"{ts}-{slug}-{phase}-{model}.diff")
        write_text(out_diff, diff, guard=guard)

    rebuild_index(paths, guard=guard)
    sessions_by_slug = {model: {slug: run.session_id}} if run.session_id else None
    update_state(
        paths,
        last={
            "command": "run",
            "model": model,
            "slug": slug,
            "phase": phase,
            "prompt": str(prompt_out),
            "output_md": str(out_md),
            "output_diff": str(out_diff) if out_diff else None,
            "session_id": run.session_id,
            "log": str(run.log_path),
            "exit_code": run.exit_code,
            "error": run.error,
            "duration_seconds": run.duration_seconds,
            "at": utc_now_iso(),
        },
        sessions_by_slug=sessions_by_slug,
        guard=guard,
    )

    plan_path_raw = getattr(args, "plan_path", None)
    if plan_path_raw and run.session_id:
        plan_path = Path(plan_path_raw)
        if plan_path.anchor and not plan_path.is_absolute():
            print(f"plan_session_update: skipped (drive-relative plan_path): {plan_path_raw!r}")
            plan_path = None
        else:
            plan_path = plan_path if plan_path.is_absolute() else (project_root / plan_path).resolve()
        if plan_path is not None and plan_path.exists():
            try:
                update_plan_session(plan_path=plan_path, model=model, session_id=run.session_id, guard=guard)
            except Exception as e:
                print(f"plan_session_update: failed: {type(e).__name__}: {e}")

    print(f"model: {model}")
    print(f"slug: {slug}")
    print(f"phase: {phase}")
    print(f"prompt: {prompt_out}")
    print(f"output: {out_md}")
    print(f"patch: {out_diff or '(not extracted)'}")
    print(f"session_id: {run.session_id or 'TBD'}")
    print(f"log: {run.log_path}")
    return 0 if run.exit_code == 0 and run.output_text.strip() else 2
