from __future__ import annotations
import json
import shutil
from pathlib import Path
from .types import VerifyStep

def _read_package_json_scripts(project_root: Path) -> dict[str, str]:
    p = project_root / "package.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    scripts = data.get("scripts")
    return scripts if isinstance(scripts, dict) else {}

def _pm_base_argv(pm: str) -> list[str]:
    if shutil.which(pm):
        return [pm]
    if pm in {"pnpm", "yarn"} and shutil.which("corepack"):
        return ["corepack", pm]
    return [pm]

def _pick_node_pm(project_root: Path) -> tuple[str, list[str], list[str]]:
    if (project_root / "pnpm-lock.yaml").exists():
        base = _pm_base_argv("pnpm")
        return ("pnpm", base, base + ["install", "--frozen-lockfile"])
    if (project_root / "yarn.lock").exists():
        base = _pm_base_argv("yarn")
        if (project_root / ".yarnrc.yml").exists() or (project_root / ".yarn").exists():
            return ("yarn", base, base + ["install", "--immutable"])
        return ("yarn", base, base + ["install", "--frozen-lockfile"])
    base = _pm_base_argv("npm")
    if (project_root / "package-lock.json").exists():
        return ("npm", base, base + ["ci"])
    return ("npm", base, base + ["install"])

def _node_run_argv(pm_base_argv: list[str], script: str) -> list[str]:
    return pm_base_argv + ["run", script]

def detect_node_steps(project_root: Path, *, timeout_seconds: int, no_install: bool) -> list[VerifyStep]:
    if not (project_root / "package.json").exists():
        return []
    steps: list[VerifyStep] = []
    pm, pm_base_argv, install_argv = _pick_node_pm(project_root)
    if not no_install:
        steps.append(VerifyStep(name=f"node:{pm}:install", argv=install_argv, cwd=project_root, timeout_seconds=timeout_seconds, kind="install"))
    scripts = _read_package_json_scripts(project_root)
    for s in ("lint", "typecheck", "test"):
        if s in scripts:
            steps.append(
                VerifyStep(
                    name=f"node:{pm}:{s}",
                    argv=_node_run_argv(pm_base_argv, s),
                    cwd=project_root,
                    timeout_seconds=timeout_seconds,
                    kind="test" if s == "test" else s,
                )
            )
    return steps
