from __future__ import annotations

import re


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
        if t_norm in stop or t_norm in seen:
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
