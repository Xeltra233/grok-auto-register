# -*- coding: utf-8 -*-
"""Automatic log cleanup for the branch panel."""

from __future__ import annotations

import fnmatch
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


_LOOP_LOCK = threading.RLock()
_LOOP_THREAD: Optional[threading.Thread] = None
_LOOP_STOP = threading.Event()
_LOOP_LAST: Dict[str, Any] = {
    "ts": None,
    "ok": None,
    "result": None,
    "error": None,
}


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


def loop_status() -> dict:
    with _LOOP_LOCK:
        running = bool(_LOOP_THREAD and _LOOP_THREAD.is_alive())
        last = dict(_LOOP_LAST)
    return {
        "running": running,
        "last_ts": last.get("ts"),
        "last_ok": last.get("ok"),
        "last_error": last.get("error"),
        "last_result": last.get("result"),
    }


def _run_cleanup_from_config(cfg: dict, *, root: Optional[str] = None) -> dict:
    return cleanup_logs(
        log_dir=str(cfg.get("log_dir") or "logs"),
        retain_days=int(cfg.get("log_retain_days") or 7),
        max_total_mb=int(cfg.get("log_max_total_mb") or 512),
        globs=cfg.get("log_cleanup_globs"),
        root=root or str(cfg.get("_project_root") or os.getcwd()),
    )


def start_log_cleanup_loop(
    *,
    project_root: Optional[str] = None,
    interval_sec: float = 3600,
    enabled: bool = True,
    config_provider: Optional[Callable[[], dict]] = None,
    on_result: Optional[Callable[[dict], None]] = None,
    run_immediately: bool = True,
) -> dict:
    """Daemon loop that periodically cleans logs by age and total size."""
    global _LOOP_THREAD
    if not enabled:
        stop_log_cleanup_loop()
        return {"ok": True, "running": False, "reason": "disabled"}

    interval = max(float(interval_sec or 3600), 60.0)
    root = os.path.abspath(project_root or os.getcwd())

    with _LOOP_LOCK:
        if _LOOP_THREAD and _LOOP_THREAD.is_alive():
            return {"ok": True, "running": True, "reason": "already-running"}
        _LOOP_STOP.clear()

        def _loop():
            first = True
            while not _LOOP_STOP.is_set():
                tick_interval = interval
                cfg = {
                    "log_dir": "logs",
                    "log_retain_days": 7,
                    "log_max_total_mb": 512,
                    "log_cleanup_globs": "*.log,*.err,live-*.log",
                    "log_cleanup_enabled": True,
                    "_project_root": root,
                }
                if config_provider is not None:
                    try:
                        provided = config_provider() or {}
                        cfg.update(provided)
                    except Exception as exc:
                        with _LOOP_LOCK:
                            _LOOP_LAST["ts"] = time.time()
                            _LOOP_LAST["ok"] = False
                            _LOOP_LAST["error"] = f"config_provider: {exc}"
                        _LOOP_STOP.wait(tick_interval)
                        continue

                if not bool(cfg.get("log_cleanup_enabled", True)):
                    _LOOP_STOP.wait(tick_interval)
                    continue

                try:
                    tick_interval = max(float(cfg.get("log_cleanup_interval_sec") or interval), 60.0)
                except Exception:
                    tick_interval = interval

                if first and not run_immediately:
                    first = False
                    _LOOP_STOP.wait(tick_interval)
                    continue
                first = False

                try:
                    res = _run_cleanup_from_config(cfg, root=str(cfg.get("_project_root") or root))
                    compact = {
                        "deleted_count": res.get("deleted_count", 0),
                        "remaining_count": res.get("remaining_count", 0),
                        "remaining_bytes": res.get("remaining_bytes", 0),
                        "retain_days": res.get("retain_days"),
                        "max_total_mb": res.get("max_total_mb"),
                        "ts": res.get("ts"),
                    }
                    with _LOOP_LOCK:
                        _LOOP_LAST["ts"] = time.time()
                        _LOOP_LAST["ok"] = bool(res.get("ok", True))
                        _LOOP_LAST["result"] = compact
                        _LOOP_LAST["error"] = res.get("error")
                    if on_result is not None:
                        try:
                            on_result(res)
                        except Exception:
                            pass
                except Exception as exc:
                    with _LOOP_LOCK:
                        _LOOP_LAST["ts"] = time.time()
                        _LOOP_LAST["ok"] = False
                        _LOOP_LAST["error"] = str(exc)
                _LOOP_STOP.wait(tick_interval)

        _LOOP_THREAD = threading.Thread(target=_loop, name="log-cleanup", daemon=True)
        _LOOP_THREAD.start()
        return {
            "ok": True,
            "running": True,
            "interval_sec": interval,
            "run_immediately": bool(run_immediately),
        }


def stop_log_cleanup_loop(timeout: float = 2.0) -> dict:
    global _LOOP_THREAD
    with _LOOP_LOCK:
        thread = _LOOP_THREAD
    _LOOP_STOP.set()
    if thread and thread.is_alive():
        thread.join(timeout=timeout)
    with _LOOP_LOCK:
        if _LOOP_THREAD is thread:
            _LOOP_THREAD = None
    return {"ok": True, "running": False}
