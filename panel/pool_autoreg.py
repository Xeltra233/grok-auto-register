# -*- coding: utf-8 -*-
"""CPA/Grok account pool auto-registration when count falls below threshold."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Optional


_LOCK = threading.RLock()
_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()
_REG_LOCK = threading.Lock()
_REG_RUNNING = False
_LAST: Dict[str, Any] = {
    "ts": None,
    "ok": None,
    "result": None,
    "error": None,
    "last_trigger": None,
}


def pool_counts(config: Optional[dict] = None, root: Optional[str] = None) -> dict:
    """Count local CPA credentials used as the Grok account pool."""
    from panel.credentials import list_credentials

    cfg = dict(config or {})
    listing = list_credentials(cfg, buckets=("uploaded", "pending"), root=root)
    counts = listing.get("counts") or {}
    uploaded = int(counts.get("uploaded") or 0)
    pending = int(counts.get("pending") or 0)
    total = int(listing.get("total") or (uploaded + pending))
    return {
        "ok": True,
        "uploaded": uploaded,
        "pending": pending,
        "total": total,
        "auth_dir": listing.get("auth_dir"),
    }


def compute_register_need(
    *,
    current_total: int,
    min_count: int,
    batch: int,
) -> dict:
    """How many accounts to register to approach the threshold."""
    min_count = max(int(min_count or 0), 0)
    batch = max(int(batch or 1), 1)
    current_total = max(int(current_total or 0), 0)
    deficit = max(min_count - current_total, 0)
    should = deficit > 0
    count = min(batch, deficit) if should else 0
    return {
        "should_register": should,
        "deficit": deficit,
        "register_count": count,
        "min_count": min_count,
        "batch": batch,
        "current_total": current_total,
    }


def status() -> dict:
    with _LOCK:
        running = bool(_THREAD and _THREAD.is_alive())
        last = dict(_LAST)
        reg_running = bool(_REG_RUNNING)
    return {
        "running": running,
        "registration_running": reg_running,
        "last_ts": last.get("ts"),
        "last_ok": last.get("ok"),
        "last_error": last.get("error"),
        "last_result": last.get("result"),
        "last_trigger": last.get("last_trigger"),
    }


def is_registration_running() -> bool:
    with _LOCK:
        return bool(_REG_RUNNING)


def _set_registration_running(value: bool) -> None:
    global _REG_RUNNING
    with _LOCK:
        _REG_RUNNING = bool(value)


def evaluate_and_maybe_trigger(
    config: Optional[dict] = None,
    *,
    root: Optional[str] = None,
    register_fn: Optional[Callable[[int, dict], Any]] = None,
    force: bool = False,
) -> dict:
    """Check pool size and optionally start a non-reentrant registration job."""
    cfg = dict(config or {})
    enabled = bool(cfg.get("pool_autoreg_enabled", False))
    if not enabled and not force:
        counts = pool_counts(cfg, root=root)
        need = compute_register_need(
            current_total=counts["total"],
            min_count=int(cfg.get("pool_autoreg_min_count") or 5),
            batch=int(cfg.get("pool_autoreg_batch") or 3),
        )
        result = {
            "ok": True,
            "enabled": False,
            "skipped": True,
            "reason": "disabled",
            "counts": counts,
            "need": need,
            "triggered": False,
        }
        with _LOCK:
            _LAST["ts"] = time.time()
            _LAST["ok"] = True
            _LAST["result"] = {
                "enabled": False,
                "triggered": False,
                "counts": counts,
                "need": need,
            }
            _LAST["error"] = None
        return result

    counts = pool_counts(cfg, root=root)
    need = compute_register_need(
        current_total=counts["total"],
        min_count=int(cfg.get("pool_autoreg_min_count") or 5),
        batch=int(cfg.get("pool_autoreg_batch") or 3),
    )
    if not need["should_register"]:
        result = {
            "ok": True,
            "enabled": True,
            "skipped": True,
            "reason": "above_threshold",
            "counts": counts,
            "need": need,
            "triggered": False,
        }
        with _LOCK:
            _LAST["ts"] = time.time()
            _LAST["ok"] = True
            _LAST["result"] = {
                "enabled": True,
                "triggered": False,
                "reason": "above_threshold",
                "counts": counts,
                "need": need,
            }
            _LAST["error"] = None
        return result

    if is_registration_running() or not _REG_LOCK.acquire(blocking=False):
        result = {
            "ok": True,
            "enabled": True,
            "skipped": True,
            "reason": "registration_in_progress",
            "counts": counts,
            "need": need,
            "triggered": False,
        }
        with _LOCK:
            _LAST["ts"] = time.time()
            _LAST["ok"] = True
            _LAST["result"] = {
                "enabled": True,
                "triggered": False,
                "reason": "registration_in_progress",
                "counts": counts,
                "need": need,
            }
            _LAST["error"] = None
        return result

    register_count = int(need["register_count"] or 0)
    if register_count <= 0:
        _REG_LOCK.release()
        return {
            "ok": True,
            "enabled": True,
            "skipped": True,
            "reason": "zero_batch",
            "counts": counts,
            "need": need,
            "triggered": False,
        }

    def _default_register(count: int, runtime_cfg: dict) -> Any:
        import grok_register_ttk as app

        # Update live module config then run CLI registration path.
        app.config.clear()
        app.config.update(runtime_cfg)
        app.config["register_count"] = int(count)
        return app.run_registration_cli(int(count))

    runner = register_fn or _default_register
    _set_registration_running(True)
    trigger_meta = {
        "ts": time.time(),
        "register_count": register_count,
        "counts_before": counts,
        "need": need,
    }

    def _job():
        try:
            runner(register_count, dict(cfg))
            with _LOCK:
                _LAST["last_trigger"] = {**trigger_meta, "ok": True, "finished_ts": time.time()}
        except Exception as exc:
            with _LOCK:
                _LAST["last_trigger"] = {
                    **trigger_meta,
                    "ok": False,
                    "error": str(exc),
                    "finished_ts": time.time(),
                }
                _LAST["error"] = str(exc)
                _LAST["ok"] = False
        finally:
            _set_registration_running(False)
            try:
                _REG_LOCK.release()
            except Exception:
                pass

    threading.Thread(target=_job, name="pool-autoreg-register", daemon=True).start()
    result = {
        "ok": True,
        "enabled": True,
        "skipped": False,
        "reason": "triggered",
        "counts": counts,
        "need": need,
        "triggered": True,
        "register_count": register_count,
    }
    with _LOCK:
        _LAST["ts"] = time.time()
        _LAST["ok"] = True
        _LAST["result"] = {
            "enabled": True,
            "triggered": True,
            "register_count": register_count,
            "counts": counts,
            "need": need,
        }
        _LAST["last_trigger"] = trigger_meta
        _LAST["error"] = None
    return result


def start_pool_autoreg_loop(
    *,
    project_root: Optional[str] = None,
    interval_sec: float = 300,
    enabled: bool = True,
    config_provider: Optional[Callable[[], dict]] = None,
    register_fn: Optional[Callable[[int, dict], Any]] = None,
    run_immediately: bool = True,
) -> dict:
    """Daemon patrol: if pool < threshold, trigger registration once at a time."""
    global _THREAD
    if not enabled:
        stop_pool_autoreg_loop()
        return {"ok": True, "running": False, "reason": "disabled"}

    interval = max(float(interval_sec or 300), 30.0)
    root = os.path.abspath(project_root or os.getcwd())

    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return {"ok": True, "running": True, "reason": "already-running"}
        _STOP.clear()

        def _loop():
            first = True
            while not _STOP.is_set():
                tick_interval = interval
                cfg: Dict[str, Any] = {
                    "pool_autoreg_enabled": True,
                    "pool_autoreg_min_count": 5,
                    "pool_autoreg_batch": 3,
                    "pool_autoreg_interval_sec": interval,
                    "_project_root": root,
                }
                if config_provider is not None:
                    try:
                        provided = config_provider() or {}
                        cfg.update(provided)
                    except Exception as exc:
                        with _LOCK:
                            _LAST["ts"] = time.time()
                            _LAST["ok"] = False
                            _LAST["error"] = f"config_provider: {exc}"
                        _STOP.wait(tick_interval)
                        continue

                if not bool(cfg.get("pool_autoreg_enabled", True)):
                    _STOP.wait(tick_interval)
                    continue
                try:
                    tick_interval = max(float(cfg.get("pool_autoreg_interval_sec") or interval), 30.0)
                except Exception:
                    tick_interval = interval

                if first and not run_immediately:
                    first = False
                    _STOP.wait(tick_interval)
                    continue
                first = False

                try:
                    evaluate_and_maybe_trigger(
                        cfg,
                        root=str(cfg.get("_project_root") or root),
                        register_fn=register_fn,
                        force=False,
                    )
                except Exception as exc:
                    with _LOCK:
                        _LAST["ts"] = time.time()
                        _LAST["ok"] = False
                        _LAST["error"] = str(exc)
                _STOP.wait(tick_interval)

        _THREAD = threading.Thread(target=_loop, name="pool-autoreg", daemon=True)
        _THREAD.start()
        return {"ok": True, "running": True, "interval_sec": interval}


def stop_pool_autoreg_loop(timeout: float = 2.0) -> dict:
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
