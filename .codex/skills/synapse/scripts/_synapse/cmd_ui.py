from __future__ import annotations
import argparse
import http.server
import ipaddress
import json
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from .paths import find_project_root, synapse_paths
from .safety import SynapseError

def _load_ui_html() -> bytes:
    skill_root = Path(__file__).resolve().parents[2]
    ui_path = skill_root / "assets" / "ui" / "index.html"
    try:
        return ui_path.read_bytes()
    except Exception as e:
        msg = f"Synapse UI asset not found: {ui_path}\n{type(e).__name__}: {e}\n"
        html = (
            "<!doctype html><html><head><meta charset='utf-8'/>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
            "<title>Synapse Viewer</title></head><body style='font-family:system-ui;margin:24px;'>"
            "<h1>Synapse Viewer</h1>"
            "<p>Failed to load UI asset.</p>"
            f"<pre>{msg}</pre>"
            "</body></html>"
        )
        return html.encode("utf-8")

_UI_HTML_BYTES = _load_ui_html()

def _list_rel_files(*, project_root: Path, root: Path) -> list[str]:
    if not root.exists():
        return []
    out: list[str] = []
    try:
        paths = sorted(root.rglob("*"))
    except Exception:
        return []
    for p in paths:
        try:
            if not p.is_file():
                continue
            rel = p.relative_to(project_root)
        except Exception:
            continue
        out.append(str(rel).replace("\\", "/"))
    return out

def _within_synapse(synapse_root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(synapse_root.resolve())
        return True
    except Exception:
        return False


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip()
    if not h:
        return False
    if h.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False

def cmd_ui(args: argparse.Namespace) -> int:
    project_root = find_project_root(Path(args.project_dir))
    paths = synapse_paths(project_root)
    syn_root = paths.synapse_dir
    if not syn_root.exists():
        raise SynapseError(f".synapse not found under: {project_root} (run synapse init first)")
    if not syn_root.is_dir():
        raise SynapseError(f".synapse is not a directory: {syn_root} (delete/rename it and run synapse init)")
    try:
        proj_resolved = project_root.resolve()
    except Exception:
        proj_resolved = project_root.absolute()
    try:
        syn_resolved = syn_root.resolve()
    except Exception:
        syn_resolved = syn_root.absolute()
    try:
        syn_resolved.relative_to(proj_resolved)
    except Exception as e:
        raise SynapseError(f".synapse resolves outside project root: {syn_resolved} (project_root: {proj_resolved})") from e

    host = str(getattr(args, "host", "127.0.0.1"))
    port = int(getattr(args, "port", 8765))
    allow_remote = bool(getattr(args, "allow_remote", False))
    open_browser = not bool(getattr(args, "no_open", False))
    if not _is_loopback_host(host) and not allow_remote:
        raise SynapseError(
            f"Refusing to bind UI to non-loopback host without --allow-remote: {host!r}. "
            "This server exposes .synapse artifacts, including prompts/logs that may contain sensitive data."
        )

    state_files: list[str] = []
    for p in (paths.state_json, paths.index_json):
        if p.exists():
            state_files.append(str(p.relative_to(project_root)).replace("\\", "/"))

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, *, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                return self._send(200, _UI_HTML_BYTES, content_type="text/html; charset=utf-8")
            if parsed.path == "/favicon.ico":
                return self._send(204, b"", content_type="text/plain; charset=utf-8")
            if parsed.path == "/api/tree":
                payload = {
                    "state_files": state_files,
                    "plan_files": _list_rel_files(project_root=project_root, root=paths.plan_dir),
                    "prompt_files": _list_rel_files(project_root=project_root, root=paths.prompts_dir),
                    "patch_files": _list_rel_files(project_root=project_root, root=paths.patches_dir),
                    "context_files": _list_rel_files(project_root=project_root, root=paths.context_dir),
                    "log_files": _list_rel_files(project_root=project_root, root=paths.logs_dir),
                }
                return self._send(
                    200,
                    (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                    content_type="application/json; charset=utf-8",
                )
            if parsed.path == "/api/file":
                qs = urllib.parse.parse_qs(parsed.query)
                raw = (qs.get("path") or [""])[0]
                rel = Path(raw)
                if rel.is_absolute() or rel.anchor:
                    return self._send(400, b"absolute path not allowed\n", content_type="text/plain; charset=utf-8")
                full = (project_root / rel).resolve()
                if not _within_synapse(syn_root, full):
                    return self._send(403, b"only .synapse/** is accessible\n", content_type="text/plain; charset=utf-8")
                if not full.exists() or not full.is_file():
                    return self._send(404, b"not found\n", content_type="text/plain; charset=utf-8")
                data = full.read_bytes()
                max_bytes = 2_000_000
                if len(data) > max_bytes:
                    data = data[:max_bytes] + b"\n\n...(truncated)\n"
                return self._send(200, data, content_type="text/plain; charset=utf-8")
            return self._send(404, b"not found\n", content_type="text/plain; charset=utf-8")

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    httpd = http.server.ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"ui: {url}")
    if not _is_loopback_host(host):
        print("ui warning: non-loopback host enabled; anyone who can reach this address can read .synapse artifacts.")
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
