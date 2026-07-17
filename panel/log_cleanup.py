# -*- coding: utf-8 -*-
"""Automatic log cleanup for the branch panel."""

from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _parse_globs(raw: Any) -> List[str]:
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw or "*.log,*.err,live-*.log")
    parts = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts or ["*.log"]


def cleanup_logs(
    *,
    log_dir: str = "logs",
    retain_days: int = 7,
    max_total_mb: int = 512,
    globs: Any = "*.log,*.err,live-*.log",
    root: Optional[str] = None,
    extra_dirs: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    base = Path(root) if root else Path.cwd()
    dirs = [base / log_dir]
    for d in extra_dirs or []:
        p = Path(d)
        if not p.is_absolute():
            p = base / p
        dirs.append(p)

    patterns = _parse_globs(globs)
    retain_days = max(int(retain_days or 7), 1)
    max_total = max(int(max_total_mb or 512), 1) * 1024 * 1024
    cutoff = time.time() - retain_days * 86400

    candidates: List[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            name = p.name
            if any(fnmatch.fnmatch(name, pat) for pat in patterns):
                candidates.append(p)

    deleted = []
    kept = []
    # 1) age-based
    for p in candidates:
        try:
            mtime = p.stat().st_mtime
        except Exception:
            continue
        if mtime < cutoff:
            try:
                size = p.stat().st_size
                p.unlink()
                deleted.append({"path": str(p), "reason": "age", "size": size})
            except Exception as exc:
                kept.append({"path": str(p), "error": str(exc)})
        else:
            kept.append({"path": str(p)})

    # refresh remaining
    remaining = []
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
                try:
                    remaining.append((p, p.stat().st_mtime, p.stat().st_size))
                except Exception:
                    pass
    total = sum(sz for _, _, sz in remaining)
    # 2) size-based: delete oldest until under quota
    if total > max_total:
        remaining.sort(key=lambda x: x[1])  # oldest first
        for p, _, size in remaining:
            if total <= max_total:
                break
            try:
                p.unlink()
                deleted.append({"path": str(p), "reason": "size", "size": size})
                total -= size
            except Exception:
                pass

    final_total = 0
    final_count = 0
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
                try:
                    final_total += p.stat().st_size
                    final_count += 1
                except Exception:
                    pass

    return {
        "ok": True,
        "deleted_count": len(deleted),
        "deleted": deleted[:200],
        "remaining_count": final_count,
        "remaining_bytes": final_total,
        "retain_days": retain_days,
        "max_total_mb": max_total // (1024 * 1024),
        "ts": time.time(),
    }
