# -*- coding: utf-8 -*-
"""CPA/Grok account pool auto-registration when count falls below threshold.

默认按远端 CPA 管理接口的账号数量判断是否补货；本地 pending/uploaded 仅作对照。
"""

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

SOURCE_REMOTE = "remote"
SOURCE_LOCAL = "local"
SOURCE_REMOTE_THEN_LOCAL = "remote_then_local"
VALID_SOURCES = (SOURCE_REMOTE, SOURCE_LOCAL, SOURCE_REMOTE_THEN_LOCAL)


def normalize_pool_source(value: Any) -> str:
    raw = str(value or SOURCE_REMOTE).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "remote": SOURCE_REMOTE,
        "cpa": SOURCE_REMOTE,
        "cpa_remote": SOURCE_REMOTE,
        "remote_only": SOURCE_REMOTE,
        "local": SOURCE_LOCAL,
        "local_only": SOURCE_LOCAL,
        "remote_then_local": SOURCE_REMOTE_THEN_LOCAL,
        "remote_fallback_local": SOURCE_REMOTE_THEN_LOCAL,
        "auto": SOURCE_REMOTE_THEN_LOCAL,
    }
    if raw in VALID_SOURCES:
        return raw
    return aliases.get(raw, SOURCE_REMOTE)


def local_pool_counts(config: Optional[dict] = None, root: Optional[str] = None) -> dict:
    from panel.credentials import list_credentials

    cfg = dict(config or {})
    listing = list_credentials(cfg, buckets=("uploaded", "pending"), root=root)
    counts = listing.get("counts") or {}
    uploaded = int(counts.get("uploaded") or 0)
    pending = int(counts.get("pending") or 0)
    total = int(listing.get("total") or (uploaded + pending))
    return {
        "ok": True,
        "source": SOURCE_LOCAL,
        "uploaded": uploaded,
        "pending": pending,
        "total": total,
        "auth_dir": listing.get("auth_dir"),
        "error": None,
    }


def remote_pool_counts(config: Optional[dict] = None) -> dict:
    cfg = dict(config or {})
    enabled = bool(cfg.get("cpa_remote_enabled", False))
    base = str(cfg.get("cpa_remote_base") or "").strip().rstrip("/")
    key = str(cfg.get("cpa_remote_management_key") or "").strip()
    timeout = max(float(cfg.get("cpa_remote_timeout_sec") or 30), 5.0)

    if not enabled:
        return {"ok": False, "source": SOURCE_REMOTE, "total": 0, "files": 0, "error": "cpa_remote_enabled is false"}
    if not base or not key:
        return {
            "ok": False,
            "source": SOURCE_REMOTE,
            "total": 0,
            "files": 0,
            "error": "missing cpa_remote_base or cpa_remote_management_key",
        }

    try:
        from cpa_remote import list_remote_auth_files

        files = list_remote_auth_files(base, key, timeout=timeout)
        total = len(files) if isinstance(files, list) else 0
        return {"ok": True, "source": SOURCE_REMOTE, "total": total, "files": total, "base": base, "error": None}
    except Exception as exc:
        return {
            "ok": False,
            "source": SOURCE_REMOTE,
            "total": 0,
            "files": 0,
            "base": base,
            "error": str(exc)[:500],
        }


def pool_counts(config: Optional[dict] = None, root: Optional[str] = None, *, source: Optional[str] = None) -> dict:
    """Resolve account-pool size. Default source=remote."""
    cfg = dict(config or {})
    preferred = normalize_pool_source(source if source is not None else cfg.get("pool_autoreg_source"))
    local = local_pool_counts(cfg, root=root)

    if preferred == SOURCE_LOCAL:
        return {
            **local,
            "preferred_source": preferred,
            "used_source": SOURCE_LOCAL,
            "local": local,
            "remote": None,
            "fallback": False,
        }

    remote = remote_pool_counts(cfg)
    if remote.get("ok"):
        return {
            "ok": True,
            "preferred_source": preferred,
            "used_source": SOURCE_REMOTE,
            "source": SOURCE_REMOTE,
            "total": int(remote.get("total") or 0),
            "files": int(remote.get("files") or 0),
            "base": remote.get("base"),
            "local": local,
            "remote": remote,
            "fallback": False,
            "error": None,
        }

    if preferred == SOURCE_REMOTE_THEN_LOCAL:
        return {
            "ok": True,
            "preferred_source": preferred,
            "used_source": SOURCE_LOCAL,
            "source": SOURCE_LOCAL,
            "total": int(local.get("total") or 0),
            "uploaded": local.get("uploaded"),
            "pending": local.get("pending"),
            "auth_dir": local.get("auth_dir"),
            "local": local,
            "remote": remote,
            "fallback": True,
            "error": remote.get("error"),
        }

    return {
        "ok": False,
        "preferred_source": preferred,
        "used_source": None,
        "source": SOURCE_REMOTE,
        "total": 0,
        "local": local,
        "remote": remote,
        "fallback": False,
        "error": remote.get("error") or "remote pool unavailable",
    }


def compute_register_need(
    *,
    current_total: int,
    min_count: int,
    target_count: int | None = None,
    batch: int = 0,
) -> dict:
    """Compute how many accounts to register.

    触发线 min_count：当前数量 < min_count 才触发。
    终点 target_count：一次补到这个数后停止。
    中间缺口 = target_count - current_total，一次开满，便于多线程。

    batch:
      - 0/负数：不截断，整缺口一次补
      - >0：单次最多 batch（一般不建议，会削弱多线程）
    """
    min_count = max(int(min_count or 0), 0)
    current_total = max(int(current_total or 0), 0)
    try:
        if target_count is None:
            target = min_count
        else:
            target = int(target_count)
    except Exception:
        target = min_count
    # 终点不能低于触发线，否则触发后马上又不够
    target = max(target, min_count, 0)
    try:
        batch = int(batch if batch is not None else 0)
    except Exception:
        batch = 0

    should = current_total < min_count
    fill_gap = max(target - current_total, 0)
    if not should:
        count = 0
    elif batch > 0:
        count = min(batch, fill_gap)
    else:
        count = fill_gap

    return {
        "should_register": should,
        "deficit": fill_gap,  # 到终点还差多少
        "trigger_gap": max(min_count - current_total, 0),
        "register_count": count,
        "min_count": min_count,
        "trigger_count": min_count,
        "target_count": target,
        "batch": batch,
        "batch_limited": bool(batch > 0 and should and count < fill_gap),
        "current_total": current_total,
        "fill_to_target": bool(should and (batch <= 0 or count == fill_gap)),
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
    cfg = dict(config or {})
    enabled = bool(cfg.get("pool_autoreg_enabled", False))
    source = normalize_pool_source(cfg.get("pool_autoreg_source"))
    counts = pool_counts(cfg, root=root, source=source)
    need = compute_register_need(
        current_total=int(counts.get("total") or 0) if counts.get("ok") else 0,
        min_count=int(cfg.get("pool_autoreg_min_count") or 5),
        target_count=int(cfg.get("pool_autoreg_target_count") if cfg.get("pool_autoreg_target_count") is not None else (cfg.get("pool_autoreg_min_count") or 5)),
        batch=0,  # 一次补满到终点；多线程靠 concurrent_count
    )

    if not enabled and not force:
        result = {
            "ok": True,
            "enabled": False,
            "skipped": True,
            "reason": "disabled",
            "counts": counts,
            "need": need,
            "triggered": False,
            "source": counts.get("used_source") or source,
        }
        with _LOCK:
            _LAST["ts"] = time.time()
            _LAST["ok"] = True
            _LAST["result"] = {"enabled": False, "triggered": False, "counts": counts, "need": need}
            _LAST["error"] = None
        return result

    if not counts.get("ok"):
        result = {
            "ok": False,
            "enabled": True,
            "skipped": True,
            "reason": "pool_count_unavailable",
            "error": counts.get("error") or "pool count unavailable",
            "counts": counts,
            "need": need,
            "triggered": False,
            "source": source,
        }
        with _LOCK:
            _LAST["ts"] = time.time()
            _LAST["ok"] = False
            _LAST["result"] = {
                "enabled": True,
                "triggered": False,
                "reason": "pool_count_unavailable",
                "counts": counts,
                "need": need,
            }
            _LAST["error"] = result["error"]
        return result

    if not need["should_register"]:
        result = {
            "ok": True,
            "enabled": True,
            "skipped": True,
            "reason": "above_threshold",
            "counts": counts,
            "need": need,
            "triggered": False,
            "source": counts.get("used_source") or source,
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
            "source": counts.get("used_source") or source,
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
            "source": counts.get("used_source") or source,
        }

    def _default_register(count: int, runtime_cfg: dict) -> Any:
        import grok_register_ttk as app

        # 账号池补货路径：一次按缺口数量启动，方便 concurrent 多线程。
        # 过程中持续重查远端/本地，达到停止目标立即停，避免远端变化后仍按旧缺口超补。
        app.config.clear()
        app.config.update(runtime_cfg)
        app.config["register_count"] = int(count)
        try:
            conc = int(app.config.get("concurrent_count") or 1)
        except Exception:
            conc = 1
        app.config["concurrent_count"] = max(1, min(conc, int(count)))
        target = int(
            runtime_cfg.get("pool_autoreg_target_count")
            if runtime_cfg.get("pool_autoreg_target_count") is not None
            else (runtime_cfg.get("pool_autoreg_min_count") or count)
        )
        watch = {
            "enabled": True,
            "source": runtime_cfg.get("pool_autoreg_source") or "remote",
            "target_count": target,
            "root": root or runtime_cfg.get("_project_root"),
            "interval_sec": float(runtime_cfg.get("pool_watch_interval_sec") or 15),
            "pool_autoreg_source": runtime_cfg.get("pool_autoreg_source"),
            "pool_autoreg_min_count": runtime_cfg.get("pool_autoreg_min_count"),
            "pool_autoreg_target_count": runtime_cfg.get("pool_autoreg_target_count"),
            "cpa_remote_enabled": runtime_cfg.get("cpa_remote_enabled"),
            "cpa_remote_base": runtime_cfg.get("cpa_remote_base"),
            "cpa_remote_management_key": runtime_cfg.get("cpa_remote_management_key"),
            "cpa_auth_dir": runtime_cfg.get("cpa_auth_dir"),
        }
        return app.run_registration_cli(int(count), pool_watch=watch)

    runner = register_fn or _default_register
    _set_registration_running(True)
    planned_count = int(register_count)
    trigger_meta = {
        "ts": time.time(),
        "register_count_planned": planned_count,
        "counts_before": counts,
        "need_before": need,
        "source": counts.get("used_source") or source,
        "refresh_before_start": True,
    }

    def _job():
        final_count = planned_count
        refresh_counts = None
        refresh_need = None
        skipped_reason = None
        try:
            # 真正开跑前再查一次远端/本地，避免用过期缺口
            refresh_counts = pool_counts(cfg, root=root, source=source)
            if not refresh_counts.get("ok"):
                skipped_reason = "pool_count_unavailable_on_refresh"
                raise RuntimeError(refresh_counts.get("error") or skipped_reason)
            refresh_need = compute_register_need(
                current_total=int(refresh_counts.get("total") or 0),
                min_count=int(cfg.get("pool_autoreg_min_count") or 5),
                target_count=int(
                    cfg.get("pool_autoreg_target_count")
                    if cfg.get("pool_autoreg_target_count") is not None
                    else (cfg.get("pool_autoreg_min_count") or 5)
                ),
                batch=0,
            )
            if not refresh_need.get("should_register"):
                skipped_reason = "above_threshold_on_refresh"
                with _LOCK:
                    _LAST["last_trigger"] = {
                        **trigger_meta,
                        "ok": True,
                        "skipped": True,
                        "reason": skipped_reason,
                        "register_count_final": 0,
                        "counts_refresh": refresh_counts,
                        "need_refresh": refresh_need,
                        "finished_ts": time.time(),
                    }
                    _LAST["result"] = {
                        "enabled": True,
                        "triggered": False,
                        "skipped": True,
                        "reason": skipped_reason,
                        "counts": refresh_counts,
                        "need": refresh_need,
                    }
                    _LAST["ok"] = True
                    _LAST["error"] = None
                return

            final_count = int(refresh_need.get("register_count") or 0)
            if final_count <= 0:
                skipped_reason = "zero_batch_on_refresh"
                with _LOCK:
                    _LAST["last_trigger"] = {
                        **trigger_meta,
                        "ok": True,
                        "skipped": True,
                        "reason": skipped_reason,
                        "register_count_final": 0,
                        "counts_refresh": refresh_counts,
                        "need_refresh": refresh_need,
                        "finished_ts": time.time(),
                    }
                    _LAST["ok"] = True
                    _LAST["error"] = None
                return

            # 若远端已回升，只补最新缺口，不沿用旧计划数
            runner(final_count, dict(cfg))
            with _LOCK:
                _LAST["last_trigger"] = {
                    **trigger_meta,
                    "ok": True,
                    "skipped": False,
                    "register_count_final": final_count,
                    "counts_refresh": refresh_counts,
                    "need_refresh": refresh_need,
                    "finished_ts": time.time(),
                }
                _LAST["result"] = {
                    "enabled": True,
                    "triggered": True,
                    "register_count": final_count,
                    "register_count_planned": planned_count,
                    "counts": refresh_counts,
                    "need": refresh_need,
                    "source": refresh_counts.get("used_source") or source,
                }
                _LAST["ok"] = True
                _LAST["error"] = None
        except Exception as exc:
            with _LOCK:
                _LAST["last_trigger"] = {
                    **trigger_meta,
                    "ok": False,
                    "error": str(exc),
                    "skipped_reason": skipped_reason,
                    "register_count_final": final_count,
                    "counts_refresh": refresh_counts,
                    "need_refresh": refresh_need,
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

    result = {
        "ok": True,
        "enabled": True,
        "skipped": False,
        "reason": "triggered",
        "counts": counts,
        "need": need,
        "triggered": True,
        "register_count": planned_count,
        "register_count_planned": planned_count,
        "refresh_before_start": True,
        "source": counts.get("used_source") or source,
        "note": "实际注册数会在开跑前按最新远端/本地数量重算",
    }
    # Publish planned trigger state before starting the worker so a fast
    # refresh/skip path cannot be overwritten by this outer write.
    with _LOCK:
        _LAST["ts"] = time.time()
        _LAST["ok"] = True
        _LAST["result"] = {
            "enabled": True,
            "triggered": True,
            "register_count_planned": planned_count,
            "counts": counts,
            "need": need,
            "source": counts.get("used_source") or source,
            "refresh_before_start": True,
        }
        _LAST["last_trigger"] = trigger_meta
        _LAST["error"] = None
    threading.Thread(target=_job, name="pool-autoreg-register", daemon=True).start()
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
                    "pool_autoreg_target_count": 5,
                    "pool_autoreg_batch": 0,
                    "pool_autoreg_interval_sec": interval,
                    "pool_autoreg_source": SOURCE_REMOTE,
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



def start_manual_registration(
    config: Optional[dict] = None,
    *,
    count: Optional[int] = None,
    register_fn: Optional[Callable[[int, dict], Any]] = None,
) -> dict:
    """Manual registration path, separate from pool refill.

    Uses config.register_count (or explicit count), not pool deficit.
    """
    cfg = dict(config or {})
    try:
        register_count = int(count if count is not None else (cfg.get("register_count") or 1))
    except Exception:
        register_count = 1
    register_count = max(register_count, 1)

    if is_registration_running() or not _REG_LOCK.acquire(blocking=False):
        return {
            "ok": True,
            "mode": "manual",
            "skipped": True,
            "reason": "registration_in_progress",
            "triggered": False,
            "register_count": register_count,
        }

    def _default_register(n: int, runtime_cfg: dict) -> Any:
        import grok_register_ttk as app

        app.config.clear()
        app.config.update(runtime_cfg)
        app.config["register_count"] = int(n)
        try:
            conc = int(app.config.get("concurrent_count") or 1)
        except Exception:
            conc = 1
        app.config["concurrent_count"] = max(1, min(conc, int(n)))
        return app.run_registration_cli(int(n))

    runner = register_fn or _default_register
    _set_registration_running(True)
    trigger_meta = {
        "ts": time.time(),
        "mode": "manual",
        "register_count": register_count,
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

    threading.Thread(target=_job, name="manual-register", daemon=True).start()
    result = {
        "ok": True,
        "mode": "manual",
        "skipped": False,
        "reason": "triggered",
        "triggered": True,
        "register_count": register_count,
        "concurrent_count": max(1, min(int(cfg.get("concurrent_count") or 1), register_count)),
    }
    with _LOCK:
        _LAST["ts"] = time.time()
        _LAST["ok"] = True
        _LAST["result"] = result
        _LAST["last_trigger"] = trigger_meta
        _LAST["error"] = None
    return result
