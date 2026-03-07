# `synapse ui`

Open a local **read-only** web viewer for `./.synapse/**` (prompts/outputs/patches/logs/state).

## Usage

```bash
synapse ui [--host <host>] [--port <port>] [--allow-remote] [--no-open]
```

## Notes

- The viewer serves **only** `.synapse/**` files.
- By default, non-loopback hosts are rejected. To bind `0.0.0.0` or another LAN address, pass `--allow-remote` explicitly.
- Default view: **Timeline** grouped by `slug → phase → model`. Use **Browse** to see the raw folder lists.
- The process runs until interrupted (Ctrl+C).

## Codex chat behavior (important)

`synapse ui` is a long-running local server. When the user asks for `synapse ui` in Codex chat, respond with the
copy/paste command below (print it first), instead of trying to run it inside Codex.

Recommended command (PowerShell; run from the target project root):

```powershell
$SkillDir = Join-Path $HOME ".codex\skills\synapse"
uv run --no-project python "$SkillDir\scripts\synapse.py" --project-dir . ui --port 0
```

Runs until interrupted (Ctrl+C).

Optional flags:

- If you don't want the browser to open automatically: add `--no-open`
- To bind a specific port: replace `--port 0` with `--port 8765` (default)
- To bind a non-local host such as `0.0.0.0`: add `--allow-remote`
