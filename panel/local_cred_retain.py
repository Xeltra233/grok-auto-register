# -*- coding: utf-8 -*-
"""Background patrol for local credential retain limit."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Optional

_LOCK = threading.RLock()
_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()
_LAST: Dict[str, Any] = {"ts": None, "ok": None, "result": None, "error": None}


def status() -> dict:
    with _LOCK:
        return {
            "running": bool(_THREAD and _THREAD.is_alive()),
            "last_ts": _LAST.get("ts"),
            "last_ok": _LAST.get("ok"),
            "last_error": _LAST.get("error"),
            "last_result": _LAST.get("result"),
        }


def run_once(config: Optional[dict] = None, *, root: Optional[str] = None, force: bool = False) -> dict:
    from panel.credentials import prune_local_credentials

    cfg = dict(config or {})
    if force:
        cfg = dict(cfg)
        cfg["local_cred_retain_enabled"] = True
    res = prune_local_credentials(cfg, root=root)
    with _LOCK:
        _LAST["ts"] = time.time()
        _LAST["ok"] = bool(res.get("ok"))
        _LAST["result"] = {
            "skipped": res.get("skipped"),
            "before": res.get("before"),
            "after": res.get("after"),
            "deleted_count": res.get("deleted_count"),
            "keep": res.get("keep"),
            "reason": res.get("reason"),
        }
        _LAST["error"] = None if res.get("ok") else str((res.get("errors") or res.get("error") or "") )[:300]
    return res


def start_local_cred_retain_loop(
    *,
    project_root: Optional[str] = None,
    interval_sec: float = 600,
    enabled: bool = True,
    config_provider: Optional[Callable[[], dict]] = None,
    run_immediately: bool = True,
) -> dict:
    global _THREAD
    if not enabled:
        stop_local_cred_retain_loop()
        return {"ok": True, "running": False, "reason": "disabled"}

    interval = max(float(interval_sec or 600), 60.0)
    root = os.path.abspath(project_root or os.getcwd())
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return {"ok": True, "running": True, "reason": "already-running"}
        _STOP.clear()

        def _loop():
            first = True
            while not _STOP.is_set():
                tick = interval
                cfg: Dict[str, Any] = {
                    "local_cred_retain_enabled": True,
                    "local_cred_retain_count": 100,
                    "local_cred_retain_interval_sec": interval,
                    "_project_root": root,
                }
                if config_provider is not None:
                    try:
                        cfg.update(config_provider() or {})
                    except Exception as exc:
                        with _LOCK:
                            _LAST["ts"] = time.time()
                            _LAST["ok"] = False
                            _LAST["error"] = f"config_provider: {exc}"
                        _STOP.wait(tick)
                        continue
                if not bool(cfg.get("local_cred_retain_enabled", True)):
                    _STOP.wait(tick)
                    continue
                try:
                    tick = max(float(cfg.get("local_cred_retain_interval_sec") or interval), 60.0)
                except Exception:
                    tick = interval
                if first and not run_immediately:
                    first = False
                    _STOP.wait(tick)
                    continue
                first = False
                try:
                    run_once(cfg, root=str(cfg.get("_project_root") or root), force=False)
                except Exception as exc:
                    with _LOCK:
                        _LAST["ts"] = time.time()
                        _LAST["ok"] = False
                        _LAST["error"] = str(exc)
                _STOP.wait(tick)

        _THREAD = threading.Thread(target=_loop, name="local-cred-retain", daemon=True)
        _THREAD.start()
        return {"ok": True, "running": True, "interval_sec": interval}


def stop_local_cred_retain_loop(timeout: float = 2.0) -> dict:
    global _THREAD
    with _LOCK:
        thread = _THREAD
    _STOP.set()
    if thread and thread.is_alive():
        thread.join(timeout=timeout)
    with _LOCK:
        if _THREAD is thread:
            _THREAD = None
    return {"ok": True, "running": False}
