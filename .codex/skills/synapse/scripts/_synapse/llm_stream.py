from __future__ import annotations

import json
import queue


def read_pipe(pipe, q: queue.Queue[object], sentinel: object) -> None:
    if pipe is None:
        q.put(sentinel)
        return
    try:
        for line in pipe:
            q.put(line)
    finally:
        q.put(sentinel)


def write_stderr_line(logf, line: str) -> None:
    logf.write(json.dumps({"type": "stderr", "content": line.rstrip("\r\n")}, ensure_ascii=False) + "\n")


def write_truncated_marker(logf, *, stream: str, original_bytes: int, limit_bytes: int, prefix: str) -> None:
    logf.write(
        json.dumps(
            {
                "type": "synapse",
                "subtype": f"{stream}_truncated",
                "original_bytes": original_bytes,
                "limit_bytes": limit_bytes,
                "prefix": prefix,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
