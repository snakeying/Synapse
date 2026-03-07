from __future__ import annotations

import re
from pathlib import Path


SENSITIVE_RG_GLOBS: tuple[str, ...] = (
    "!**/credentials.json",
    "!**/credentials.yaml",
    "!**/credentials.yml",
    "!**/credentials.toml",
    "!**/secret.json",
    "!**/secret.yaml",
    "!**/secret.yml",
    "!**/secret.toml",
    "!**/secrets.json",
    "!**/secrets.yaml",
    "!**/secrets.yml",
    "!**/secrets.toml",
    "!**/appsettings*.json",
    "!**/terraform.tfvars",
    "!**/terraform.tfvars.json",
    "!**/*.tfvars",
    "!**/*.tfstate",
    "!**/kubeconfig",
    "!**/*.mobileprovision",
    "!**/*.p8",
    "!**/*.cer",
    "!**/*.crt",
    "!**/*.der",
)


def is_sensitive_file_candidate(path: Path) -> bool:
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


def filter_sensitive_diff(project_root: Path, diff_text: str) -> tuple[str, list[str]]:
    if not diff_text.strip():
        return diff_text, []
    kept: list[str] = []
    redacted: list[str] = []
    for chunk in _split_diff_chunks(diff_text):
        rel = _chunk_relpath(chunk)
        if rel and is_sensitive_file_candidate(project_root / rel):
            redacted.append(rel.replace("\\", "/"))
            continue
        kept.extend(chunk)
    return "\n".join(kept).strip("\n"), redacted
