from __future__ import annotations
import datetime as _dt
import re
from pathlib import Path
from typing import Any
from .common import (
    SynapsePaths,
    WriteGuard,
    is_git_repo,
    read_text,
    run_cmd,
    truncate_bytes,
    unique_path,
    write_text,
)

def derive_rg_queries(query: str, *, max_queries: int) -> list[str]:
    stop = {
        "the",
        "and",
        "or",
        "to",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "be",
        "as",
        "at",
        "by",
        "from",
        "this",
        "that",
        "these",
        "those",
        "it",
        "we",
        "you",
        "i",
        "our",
        "your",
        "实现",
        "功能",
        "支持",
        "增加",
        "优化",
        "修复",
        "问题",
    }

    def _has_cjk(s: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", s))

    def _min_len(s: str) -> int:
        return 2 if _has_cjk(s) else 3

    tokens = re.findall(r"[A-Za-z0-9_][A-Za-z0-9_./:\-\\]{1,}", query)
    cjk_seqs = re.findall(r"[\u4e00-\u9fff]{2,}", query)
    seen: set[str] = set()
    out: list[str] = []

    def _add(tok: str) -> None:
        if len(out) >= max_queries:
            return
        t = tok.strip()
        if not t:
            return
        t_norm = t.lower()
        if len(t_norm) < _min_len(t_norm):
            return
        if t_norm in stop:
            return
        if t_norm in seen:
            return
        seen.add(t_norm)
        out.append(t)

    for tok in tokens:
        _add(tok)
    if cjk_seqs and len(out) < max_queries:
        for seq in cjk_seqs:
            s = seq.strip()
            if not s:
                continue
            for n in (4, 3, 2):
                if len(out) >= max_queries:
                    break
                if len(s) < n:
                    continue
                for i in range(0, len(s) - n + 1):
                    if len(out) >= max_queries:
                        break
                    _add(s[i : i + n])
            if len(out) >= max_queries:
                break

    if not out and query.strip():
        q = query.strip()
        out.append(q[: (16 if _has_cjk(q) else 32)])
    return out[:max_queries]

def _is_sensitive_file_candidate(path: Path) -> bool:
    """
    Best-effort filter to avoid accidentally including obvious secret files in
    auto-generated context packs.

    Explicit `--include-file` entries are still allowed (handled by caller).
    """
    name = path.name.lower()
    if name in {".env", ".npmrc", ".pypirc", ".netrc", ".git-credentials"}:
        return True
    if name.startswith(".env."):
        return True
    if name.startswith("appsettings.") and name.endswith(".json"):
        return True
    if name in {
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "credentials.toml",
        "secret.json",
        "secret.yaml",
        "secret.yml",
        "secret.toml",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "secrets.toml",
        "terraform.tfvars",
        "terraform.tfvars.json",
        "kubeconfig",
    }:
        return True
    if name in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
        return True
    ext = path.suffix.lower()
    if ext in {".pem", ".key", ".p12", ".pfx", ".kdbx", ".tfvars", ".tfstate", ".mobileprovision", ".p8", ".cer", ".crt", ".der"}:
        return True
    return False
def _split_diff_chunks(diff_text: str) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git ") and current:
            chunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(current)
    return chunks


def _chunk_relpath(lines: list[str]) -> str | None:
    if not lines:
        return None
    m = re.match(r"diff --git a/(.*?) b/(.*?)$", lines[0])
    if m:
        return m.group(2)
    for line in lines:
        if line.startswith("+++ b/"):
            return line[6:]
    return None


def _filter_sensitive_diff(project_root: Path, diff_text: str) -> tuple[str, list[str]]:
    if not diff_text.strip():
        return diff_text, []
    kept: list[str] = []
    redacted: list[str] = []
    for chunk in _split_diff_chunks(diff_text):
        rel = _chunk_relpath(chunk)
        if rel and _is_sensitive_file_candidate(project_root / rel):
            redacted.append(rel.replace("\\", "/"))
            continue
        kept.extend(chunk)
    return "\n".join(kept).strip("\n"), redacted

def select_key_files(project_root: Path, *, max_files: int, extra_files: list[Path] | None = None) -> list[Path]:
    candidates: list[Path] = []
    explicit: set[Path] = set()
    if extra_files:
        for p in extra_files:
            try:
                if p.exists() and p.is_file():
                    candidates.append(p)
                    explicit.add(p.resolve())
            except Exception:
                continue
    preferred_names = [
        "AGENTS.md",
        ".gitignore",
        "README.md",
        "README.txt",
        "README",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "Dockerfile",
    ]
    for name in preferred_names:
        p = project_root / name
        if p.exists() and p.is_file():
            if p.resolve() not in explicit and _is_sensitive_file_candidate(p):
                continue
            candidates.append(p)
    if is_git_repo(project_root):
        try:
            st = run_cmd(["git", "status", "--porcelain", "-z"], cwd=project_root, timeout_seconds=20)
            if st.code == 0:
                items = st.stdout.split("\0")
                i = 0
                while i < len(items):
                    entry = items[i]
                    i += 1
                    if not entry:
                        continue
                    if len(entry) < 4:
                        continue
                    status = entry[:2]
                    path_part = entry[3:]
                    if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
                        if i < len(items) and items[i]:
                            path_part = items[i]
                        i += 1
                    if not path_part:
                        continue
                    p = project_root / path_part
                    if p.exists() and p.is_file():
                        if p.resolve() not in explicit and _is_sensitive_file_candidate(p):
                            continue
                        candidates.append(p)
        except Exception:
            pass

    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(p)
        if len(unique) >= max_files:
            break
    return unique

def snippet_for_file(path: Path, *, max_lines: int, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
        if size > max(256_000, max_bytes * 4):
            return f"(skipped: file too large: {size} bytes)"
        text = read_text(path)
    except Exception as e:
        return f"(skipped: {type(e).__name__}: {e})"

    lines = text.splitlines()
    head = lines[:max_lines]
    out = "\n".join(head)
    out = truncate_bytes(out, max_bytes)
    if len(lines) > max_lines:
        out += "\n…(truncated)\n"
    return out

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

    rg_max_depth = int(rg_cfg.get("max_depth", 25))
    rg_max_queries = int(rg_cfg.get("max_queries", 10))
    rg_max_matches_per_query = int(rg_cfg.get("max_matches_per_query", 80))
    rg_max_total_matches = int(rg_cfg.get("max_total_matches", 200))
    rg_max_count_per_file = int(rg_cfg.get("max_count_per_file", 20))
    rg_max_filesize = str(rg_cfg.get("max_filesize", "1M"))

    max_files = int(snip_cfg.get("max_files", 20))
    max_lines_per_file = int(snip_cfg.get("max_lines_per_file", 160))
    max_bytes_per_file = int(snip_cfg.get("max_bytes_per_file", 20000))

    diff_max_bytes = int(git_cfg.get("diff_max_bytes", 200000))
    diff_max_lines = int(git_cfg.get("diff_max_lines", 2000))
    status_max_lines = int(git_cfg.get("status_max_lines", 300))

    out_path = unique_path(paths.context_dir / f"{slug}-{phase}.md")
    project_root = paths.project_root
    git_ok = is_git_repo(project_root)

    parts: list[str] = []
    parts.append(f"# Synapse Context Pack: `{slug}` / `{phase}`")
    parts.append("")
    parts.append(f"- created_at: {_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()}")
    parts.append(f"- project_root: {project_root}")
    parts.append(f"- git_repo: {git_ok}")
    parts.append(f"- query: {query}")
    parts.append("- note: context packs may include sensitive data (git diff / file snippets); review before sharing")
    parts.append("")

    if git_ok:
        parts.append("## Git")
        parts.append("")
        head = run_cmd(["git", "rev-parse", "HEAD"], cwd=project_root, timeout_seconds=10)
        branch = run_cmd(["git", "branch", "--show-current"], cwd=project_root, timeout_seconds=10)
        parts.append(f"- branch: {branch.stdout.strip() or '(detached)'}")
        parts.append(f"- head: {head.stdout.strip() if head.code == 0 else '(no commits yet)'}")
        parts.append("")

        status = run_cmd(["git", "status", "--porcelain", "-b"], cwd=project_root, timeout_seconds=20)
        status_lines = status.stdout.splitlines()[:status_max_lines]
        parts.append("### `git status --porcelain -b`")
        parts.append("")
        parts.append("```")
        parts.append("\n".join(status_lines) if status_lines else "(clean)")
        parts.append("```")
        parts.append("")

        diff_stat = run_cmd(["git", "diff", "--stat"], cwd=project_root, timeout_seconds=30)
        parts.append("### `git diff --stat`")
        parts.append("")
        parts.append("```")
        parts.append(diff_stat.stdout.strip() or "(no diff)")
        parts.append("```")
        parts.append("")

        diff = run_cmd(["git", "diff"], cwd=project_root, timeout_seconds=60)
        diff_text = "\n".join(diff.stdout.splitlines()[:diff_max_lines])
        diff_text = truncate_bytes(diff_text, diff_max_bytes).rstrip()
        diff_text, redacted_diff_files = _filter_sensitive_diff(project_root, diff_text)
        parts.append("### `git diff` (truncated)")
        parts.append("")
        if redacted_diff_files:
            preview = ", ".join(redacted_diff_files[:5])
            suffix = "" if len(redacted_diff_files) <= 5 else f" (+{len(redacted_diff_files) - 5} more)"
            parts.append(f"- note: omitted diff bodies for {len(redacted_diff_files)} sensitive file(s): {preview}{suffix}")
            parts.append("")
        parts.append("```diff")
        parts.append(diff_text or "(all diff bodies omitted or no diff)")
        parts.append("```")
        parts.append("")
    else:
        parts.append("## Git")
        parts.append("")
        parts.append("(not a git repository)")
        parts.append("")

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
            rg_argv = [
                "rg",
                "-n",
                "--max-depth",
                str(rg_max_depth),
                "--max-filesize",
                rg_max_filesize,
                "--max-count",
                str(max(1, rg_max_count_per_file)),
                "--glob",
                "!**/.synapse/**",
                "--glob",
                "!**/node_modules/**",
                "--glob",
                "!**/.venv/**",
                "--glob",
                "!**/venv/**",
                "--glob",
                "!**/__pycache__/**",
                "--glob",
                "!**/.env",
                "--glob",
                "!**/.env.*",
                "--glob",
                "!**/.npmrc",
                "--glob",
                "!**/.pypirc",
                "--glob",
                "!**/.netrc",
                "--glob",
                "!**/.git-credentials",
                "--glob",
                "!**/id_rsa",
                "--glob",
                "!**/id_dsa",
                "--glob",
                "!**/id_ecdsa",
                "--glob",
                "!**/id_ed25519",
                "--glob",
                "!**/*.pem",
                "--glob",
                "!**/*.key",
                "--glob",
                "!**/*.p12",
                "--glob",
                "!**/*.pfx",
                "--glob",
                "!**/*.kdbx",
                "--glob",
                "!**/credentials.json",
                "--glob",
                "!**/credentials.yaml",
                "--glob",
                "!**/credentials.yml",
                "--glob",
                "!**/credentials.toml",
                "--glob",
                "!**/secret.json",
                "--glob",
                "!**/secret.yaml",
                "--glob",
                "!**/secret.yml",
                "--glob",
                "!**/secret.toml",
                "--glob",
                "!**/secrets.json",
                "--glob",
                "!**/secrets.yaml",
                "--glob",
                "!**/secrets.yml",
                "--glob",
                "!**/secrets.toml",
                "--glob",
                "!**/appsettings*.json",
                "--glob",
                "!**/terraform.tfvars",
                "--glob",
                "!**/terraform.tfvars.json",
                "--glob",
                "!**/*.tfvars",
                "--glob",
                "!**/*.tfstate",
                "--glob",
                "!**/kubeconfig",
                "--glob",
                "!**/*.mobileprovision",
                "--glob",
                "!**/*.p8",
                "--glob",
                "!**/*.cer",
                "--glob",
                "!**/*.crt",
                "--glob",
                "!**/*.der",
                "--glob",
                "!**/dist/**",
                "--glob",
                "!**/build/**",
                "--glob",
                "!**/out/**",
            ]
            if derived_queries:
                rg_argv.append("-F")
            rg_argv += ["--", q]
            rg = run_cmd(
                rg_argv,
                cwd=project_root,
                timeout_seconds=60,
            )
            if rg.code == 2 and "No files were searched" in (rg.stderr or ""):
                rg = type(rg)(code=1, stdout="", stderr=rg.stderr)
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

    parts.append("## Key files (snippets)")
    parts.append("")
    key_files = select_key_files(project_root, max_files=max_files, extra_files=include_files)
    if not key_files:
        parts.append("(none selected)")
    for p in key_files:
        if p.is_absolute():
            try:
                rel = p.relative_to(project_root)
            except Exception:
                rel = p
        else:
            rel = p
        parts.append(f"### `{rel}`")
        parts.append("")
        parts.append("```")
        parts.append(snippet_for_file(p, max_lines=max_lines_per_file, max_bytes=max_bytes_per_file).rstrip())
        parts.append("```")
        parts.append("")

    write_text(out_path, "\n".join(parts).rstrip() + "\n", guard=guard)
    return out_path
