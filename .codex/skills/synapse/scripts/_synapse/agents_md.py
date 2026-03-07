from __future__ import annotations
import re
from pathlib import Path
from .file_ops import read_text, write_text
from .safety import WriteGuard

SYNAPSE_BLOCK_BEGIN = "<!-- SYNAPSE-BEGIN -->"
SYNAPSE_BLOCK_END = "<!-- SYNAPSE-END -->"

def ensure_gitignore(project_root: Path, *, guard: WriteGuard | None = None) -> None:
    gitignore = project_root / ".gitignore"
    line = "/.synapse/"
    if gitignore.exists():
        text = read_text(gitignore)
        lines = [ln.rstrip("\n\r") for ln in text.splitlines()]
        if any(ln.strip() == line or ln.strip() == ".synapse/" or ln.strip() == "/.synapse" for ln in lines):
            return
        new_text = text
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_text += line + "\n"
        write_text(gitignore, new_text, guard=guard)
        return

    write_text(gitignore, line + "\n", guard=guard)

def render_synapse_block() -> str:
    return "\n".join(
        [
            SYNAPSE_BLOCK_BEGIN,
            "## Synapse",
            "",
            "This project uses **Synapse** (Codex skill) for workflow metadata and external model sessions.",
            "",
            "- Artifacts: `./.synapse/**` (plans, context packs, logs, patches, state)",
            "- Git ignore: `/.synapse/` is appended to `.gitignore` (idempotent)",
            "- Session resume: relies on `.synapse/state.json` and session ids stored in plan files",
            "",
            "**Safety**",
            "- `synapse init` only writes: `AGENTS.md`, `.gitignore`, `./.synapse/**`",
            "- `synapse verify` may create project-local toolchain artifacts (e.g. lockfiles, `.venv/`, `node_modules/`, build outputs) as a result of running project commands",
            "- External models never receive direct filesystem/tool access; Codex applies final code changes",
            "",
            "**Common commands**",
            "- `synapse init`",
            "- `synapse plan --task-type <frontend|backend|fullstack> <request>`",
            "- `synapse pack --phase <phase> --slug <slug> --query <text>`",
            "- `synapse run --model <claude|gemini> --phase <phase> --slug <slug> --prompt-file <path>`",
            "- `synapse verify`",
            "- `synapse ui`",
            "- `synapse workflow <request>`",
            "",
            SYNAPSE_BLOCK_END,
            "",
        ]
    )

def ensure_agents_md(project_root: Path, *, guard: WriteGuard | None = None) -> None:
    agents = project_root / "AGENTS.md"
    block = render_synapse_block()

    if not agents.exists():
        base = "\n".join(
            [
                "# Agent Notes",
                "",
                "## Overview",
                "- Keep changes focused and minimal.",
                "- Prefer project conventions (lint/test scripts, formatting).",
                "",
                "## Testing",
                "- Prefer running the narrowest relevant tests first.",
                "- If unsure, run the project's default test command (if any).",
                "",
                block,
            ]
        )
        write_text(agents, base, guard=guard)
        return

    text = read_text(agents)
    if SYNAPSE_BLOCK_BEGIN in text and SYNAPSE_BLOCK_END in text:
        pattern = re.compile(
            re.escape(SYNAPSE_BLOCK_BEGIN) + r".*?" + re.escape(SYNAPSE_BLOCK_END) + r"\s*",
            flags=re.DOTALL,
        )
        new_text = pattern.sub(block, text).rstrip() + "\n"
        write_text(agents, new_text, guard=guard)
        return

    write_text(agents, text.rstrip() + "\n\n" + block, guard=guard)
