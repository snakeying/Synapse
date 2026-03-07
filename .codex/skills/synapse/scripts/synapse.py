from __future__ import annotations
import argparse
import sys
from _synapse.cmd_init import cmd_init
from _synapse.cmd_pack import cmd_pack
from _synapse.cmd_plan import cmd_plan
from _synapse.cmd_run import cmd_run
from _synapse.cmd_ui import cmd_ui
from _synapse.cmd_verify import cmd_verify
from _synapse.safety import SynapseError

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="synapse.py")
    p.add_argument("--project-dir", default=".", help="Directory inside the target project (default: .)")
    # Back-compat: --resume is treated as --resume-gemini.
    p.add_argument("--resume", dest="resume", default=None, help="Back-compat alias for --resume-gemini (Gemini session id)")
    p.add_argument("--resume-gemini", dest="resume_gemini", default=None, help="Gemini session id to resume")
    p.add_argument("--resume-claude", dest="resume_claude", default=None, help="Claude session id to resume")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub_init = sub.add_parser("init", help="Initialize ./.synapse artifacts + AGENTS.md/.gitignore (idempotent)")
    sub_init.set_defaults(func=cmd_init)

    sub_pack = sub.add_parser("pack", help="Build a context pack under .synapse/context/**")
    sub_pack.add_argument("--phase", default="pack", help="Context pack phase label (default: pack)")
    sub_pack.add_argument("--slug", default=None, help="Slug to use in filenames (default: derived)")
    sub_pack.add_argument("--query", default="", help="Query string used to derive default rg queries")
    sub_pack.add_argument("--rg-query", action="append", default=[], help="Override rg query (repeatable)")
    sub_pack.add_argument("--include-file", action="append", default=[], help="Always include snippet for this file (repeatable)")
    sub_pack.set_defaults(func=cmd_pack)

    sub_plan = sub.add_parser("plan", help="Create a plan stub (Gate checklist + meta JSON)")
    sub_plan.add_argument(
        "--task-type",
        choices=["frontend", "backend", "fullstack"],
        default="fullstack",
        help="Routing hint for later stages (default: fullstack)",
    )
    sub_plan.add_argument("--slug", default=None, help="Slug override (default: derived from request)")
    sub_plan.add_argument("--no-pack", action="store_true", help="Do not generate a plan context pack")
    sub_plan.add_argument("--rg-query", action="append", default=[], help="Override rg query (repeatable)")
    sub_plan.add_argument("--include-file", action="append", default=[], help="Always include snippet for this file (repeatable)")
    sub_plan.add_argument("request", nargs=argparse.REMAINDER)
    sub_plan.set_defaults(func=cmd_plan)

    sub_run = sub.add_parser("run", help="Run an external model (prompt is provided by Codex via --prompt-file)")
    sub_run.add_argument("--model", choices=["claude", "gemini"], required=True)
    sub_run.add_argument("--phase", default="run", help="Phase label for logs/artifacts (default: run)")
    sub_run.add_argument("--slug", required=True, help="Slug used to group artifacts")
    sub_run.add_argument("--prompt-file", required=True, help="Prompt template file (placeholders allowed)")
    sub_run.add_argument("--plan-path", default=None, help="Optional plan path to update sessions in meta JSON")
    sub_run.add_argument("--var", action="append", default=[], help="Template variable KEY=VALUE (repeatable)")
    sub_run.add_argument("--var-file", action="append", default=[], help="Template variable KEY=PATH (file contents; repeatable)")
    sub_run.set_defaults(func=cmd_run)

    sub_verify = sub.add_parser("verify", help="Auto-detect toolchain and run install/lint/typecheck/test")
    sub_verify.add_argument("--dry-run", action="store_true", help="Print planned commands only")
    sub_verify.add_argument("--no-install", action="store_true", help="Skip dependency installation steps")
    sub_verify.add_argument("--keep-going", action="store_true", help="Continue even if a step fails")
    sub_verify.set_defaults(func=cmd_verify)

    sub_ui = sub.add_parser("ui", help="Open a local web viewer for .synapse artifacts")
    sub_ui.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    sub_ui.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765; 0 = auto)")
    sub_ui.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding UI to a non-loopback host (disabled by default for safety)",
    )
    sub_ui.add_argument("--no-open", action="store_true", help="Do not open the browser automatically")
    sub_ui.set_defaults(func=cmd_ui)

    return p

def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    resume_alias = getattr(args, "resume", None)
    resume_gemini = getattr(args, "resume_gemini", None)
    if resume_alias and resume_gemini and resume_alias != resume_gemini:
        print("synapse error: both --resume and --resume-gemini were provided with different values", file=sys.stderr)
        return 2
    if resume_alias and not resume_gemini:
        args.resume_gemini = resume_alias
    try:
        return int(args.func(args))
    except SynapseError as e:
        print(f"synapse error: {e}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
