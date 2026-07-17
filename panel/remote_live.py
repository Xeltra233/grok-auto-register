# -*- coding: utf-8 -*-
"""Remote CPA Grok live-inspection patrol.

Default every 2 hours:
1) list remote CPA auth files
2) probe each access_token (grok-inspection style)
3) delete dead accounts from remote
4) optionally trigger pool auto-register after deletions
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


_LOCK = threading.RLock()
_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()
_RUN_LOCK = threading.Lock()
_RUNNING = False
_LAST: Dict[str, Any] = {
    "ts": None,
    "ok": None,
    "result": None,
    "error": None,
}


def status() -> dict:
    with _LOCK:
        return {
            "running": bool(_THREAD and _THREAD.is_alive()),
            "patrol_running": bool(_RUNNING),
            "last_ts": _LAST.get("ts"),
            "last_ok": _LAST.get("ok"),
            "last_error": _LAST.get("error"),
            "last_result": _LAST.get("result"),
        }


def _set_running(value: bool) -> None:
    global _RUNNING
    with _LOCK:
        _RUNNING = bool(value)


def _extract_name(item: Any) -> str:
    if isinstance(item, str):
        return Path(item).name
    if not isinstance(item, dict):
        return ""
    for key in ("name", "filename", "file", "path", "id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value.strip()).name
    return ""


def _extract_token(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("access_token", "token", "accessToken"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # nested
    for key in ("auth", "data", "content", "json"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            tok = _extract_token(nested)
            if tok:
                return tok
        if isinstance(nested, str) and nested.strip().startswith("{"):
            try:
                obj = json.loads(nested)
                tok = _extract_token(obj)
                if tok:
                    return tok
            except Exception:
                pass
    return ""


def _should_delete(live: dict, *, delete_on_fail: bool) -> bool:
    """Whether remote account should be deleted after live inspect.

    删除：
    - reauth / permission_denied（失效）
    - quota_exhausted（额度用尽）
    - action=delete
    保留：
    - healthy / probe_error / model_unavailable 等瞬时问题
    """
    if not delete_on_fail:
        return False
    action = str((live or {}).get("action") or "").strip().lower()
    classification = str((live or {}).get("classification") or "").strip().lower()
    if action == "delete":
        return True
    if classification in {"reauth", "permission_denied", "quota_exhausted"}:
        return True
    # some inspect paths may mark quota as disable
    if action == "disable" and classification in {"quota_exhausted", "permission_denied"}:
        return True
    return False


def run_remote_live_patrol(
    config: Optional[dict] = None,
    *,
    root: Optional[str] = None,
    force: bool = False,
    trigger_autoreg: bool = True,
    log_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """One-shot remote live patrol."""
    cfg = dict(config or {})
    enabled = bool(cfg.get("remote_live_enabled", False))
    if not enabled and not force:
        result = {"ok": True, "enabled": False, "skipped": True, "reason": "disabled"}
        with _LOCK:
            _LAST["ts"] = time.time()
            _LAST["ok"] = True
            _LAST["result"] = result
            _LAST["error"] = None
        return result

    if _RUNNING or not _RUN_LOCK.acquire(blocking=False):
        return {"ok": True, "skipped": True, "reason": "patrol_in_progress"}

    _set_running(True)
    try:
        from cpa_remote import (
            delete_remote_auth_file,
            get_remote_auth_file,
            list_remote_auth_files,
            _remote_config,
        )
        from cpa_xai.inspect import inspect_access_token

        enabled_remote, base, key, timeout = _remote_config(cfg)
        if not enabled_remote:
            result = {
                "ok": False,
                "enabled": True,
                "skipped": True,
                "reason": "cpa_remote_disabled",
                "error": "cpa_remote_enabled is false",
            }
            with _LOCK:
                _LAST["ts"] = time.time()
                _LAST["ok"] = False
                _LAST["result"] = result
                _LAST["error"] = result["error"]
            return result
        if not base or not key:
            result = {
                "ok": False,
                "enabled": True,
                "skipped": True,
                "reason": "missing_remote_config",
                "error": "missing cpa_remote_base or cpa_remote_management_key",
            }
            with _LOCK:
                _LAST["ts"] = time.time()
                _LAST["ok"] = False
                _LAST["result"] = result
                _LAST["error"] = result["error"]
            return result

        delete_on_fail = bool(cfg.get("remote_live_delete_on_fail", True))
        # follow global proxy: cpa_proxy/proxy (remote_live_proxy optional override)
        proxy = str(cfg.get("remote_live_proxy") or cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None
        model = str(cfg.get("remote_live_model") or "grok-4.5").strip() or "grok-4.5"
        max_files = max(int(cfg.get("remote_live_max_files") or 0), 0)  # 0 = all
        # batch concurrency: default 6, never flood remote/API
        try:
            batch = int(cfg.get("remote_live_batch") if cfg.get("remote_live_batch") is not None else 6)
        except Exception:
            batch = 6
        batch = max(1, min(batch, 32))

        files = list_remote_auth_files(base, key, timeout=timeout)
        names: List[str] = []
        for item in files:
            name = _extract_name(item)
            if name:
                names.append(name)
        # de-dup keep order
        seen = set()
        ordered = []
        for n in names:
            if n not in seen:
                seen.add(n)
                ordered.append(n)
        if max_files > 0:
            ordered = ordered[:max_files]

        checked = 0
        healthy = 0
        deleted: List[str] = []
        kept: List[dict] = []
        errors: Dict[str, str] = {}
        details: List[dict] = []
        details_lock = threading.Lock()

        def _log(msg: str) -> None:
            if log_callback:
                log_callback(msg)

        def _resolve_payload(name: str):
            payload = None
            for item in files:
                if _extract_name(item) == name and isinstance(item, dict):
                    tok = _extract_token(item)
                    if tok:
                        return item
                    for key_name in ("content", "data", "file", "json", "auth"):
                        nested = item.get(key_name)
                        if isinstance(nested, dict) and _extract_token(nested):
                            return nested
                        if isinstance(nested, str) and nested.strip().startswith("{"):
                            try:
                                obj = json.loads(nested)
                                if isinstance(obj, dict) and _extract_token(obj):
                                    return obj
                            except Exception:
                                pass
            return get_remote_auth_file(base, key, name, timeout=timeout)

        def _process_one(name: str) -> dict:
            try:
                payload = _resolve_payload(name)
                token = _extract_token(payload or {})
                if not token:
                    raise RuntimeError("remote auth missing access_token")
                live = inspect_access_token(token, model=model, proxy=proxy, timeout=max(float(timeout), 15.0))
                item_res = {
                    "name": name,
                    "classification": live.get("classification"),
                    "action": live.get("action"),
                    "healthy": bool(live.get("healthy")),
                    "reason": live.get("reason"),
                    "deleted": False,
                }
                if bool(live.get("healthy")):
                    item_res["status"] = "healthy"
                elif _should_delete(live, delete_on_fail=delete_on_fail):
                    delete_remote_auth_file(base, key, name, timeout=timeout)
                    item_res["deleted"] = True
                    item_res["status"] = "deleted"
                    _log(f"[remote-live] deleted dead/exhausted account: {name} ({live.get('classification')})")
                    try:
                        from panel.credentials import delete_credentials

                        delete_credentials([name], config=cfg, bucket="uploaded", root=root)
                    except Exception:
                        pass
                else:
                    item_res["status"] = "kept"
                return item_res
            except Exception as exc:
                return {"name": name, "error": str(exc)[:300], "status": "error", "deleted": False, "healthy": False}

        # process in concurrent batches of size `batch`
        for i in range(0, len(ordered), batch):
            chunk = ordered[i : i + batch]
            with ThreadPoolExecutor(max_workers=min(batch, len(chunk))) as ex:
                futs = {ex.submit(_process_one, name): name for name in chunk}
                for fut in as_completed(futs):
                    item_res = fut.result()
                    with details_lock:
                        checked += 1
                        details.append(item_res)
                        if item_res.get("error"):
                            errors[item_res.get("name") or "?"] = item_res["error"]
                        elif item_res.get("deleted"):
                            deleted.append(item_res["name"])
                        elif item_res.get("healthy"):
                            healthy += 1
                            kept.append(item_res)
                        else:
                            kept.append(item_res)

        result = {
            "ok": True,
            "enabled": True,
            "checked": checked,
            "healthy": healthy,
            "deleted": deleted,
            "deleted_count": len(deleted),
            "kept_count": len(kept),
            "errors": errors,
            "error_count": len(errors),
            "details": details[:200],
            "remote_total_before": len(ordered),
            "remote_total_after": max(len(ordered) - len(deleted), 0),
        }

        # chain auto-register when pool may drop below threshold
        if trigger_autoreg and (deleted or bool(cfg.get("pool_autoreg_enabled", False))):
            try:
                from panel.pool_autoreg import evaluate_and_maybe_trigger

                auto = evaluate_and_maybe_trigger(cfg, root=root, force=False)
                result["pool_autoreg"] = {
                    "triggered": bool(auto.get("triggered")),
                    "reason": auto.get("reason"),
                    "register_count": auto.get("register_count"),
                    "counts": auto.get("counts"),
                    "need": auto.get("need"),
                }
            except Exception as exc:
                result["pool_autoreg_error"] = str(exc)[:300]

        with _LOCK:
            _LAST["ts"] = time.time()
            _LAST["ok"] = True
            _LAST["result"] = {
                "checked": checked,
                "healthy": healthy,
                "deleted_count": len(deleted),
                "deleted": deleted[:50],
                "error_count": len(errors),
                "pool_autoreg": result.get("pool_autoreg"),
            }
            _LAST["error"] = None
        return result
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:500]}
        with _LOCK:
            _LAST["ts"] = time.time()
            _LAST["ok"] = False
            _LAST["result"] = result
            _LAST["error"] = result["error"]
        return result
    finally:
        _set_running(False)
        try:
            _RUN_LOCK.release()
        except Exception:
            pass


def start_remote_live_loop(
    *,
    project_root: Optional[str] = None,
    interval_sec: float = 7200,
    enabled: bool = True,
    config_provider: Optional[Callable[[], dict]] = None,
    run_immediately: bool = False,
) -> dict:
    global _THREAD
    if not enabled:
        stop_remote_live_loop()
        return {"ok": True, "running": False, "reason": "disabled"}

    interval = max(float(interval_sec or 7200), 60.0)
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
                    "remote_live_enabled": True,
                    "remote_live_interval_sec": interval,
                    "remote_live_delete_on_fail": True,
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
                        _STOP.wait(tick)
                        continue

                if not bool(cfg.get("remote_live_enabled", True)):
                    _STOP.wait(tick)
                    continue
                try:
                    tick = max(float(cfg.get("remote_live_interval_sec") or interval), 60.0)
                except Exception:
                    tick = interval

                if first and not run_immediately:
                    first = False
                    _STOP.wait(tick)
                    continue
                first = False

                try:
                    run_remote_live_patrol(
                        cfg,
                        root=str(cfg.get("_project_root") or root),
                        force=False,
                        trigger_autoreg=True,
                    )
                except Exception as exc:
                    with _LOCK:
                        _LAST["ts"] = time.time()
                        _LAST["ok"] = False
                        _LAST["error"] = str(exc)
                _STOP.wait(tick)

        _THREAD = threading.Thread(target=_loop, name="remote-live", daemon=True)
        _THREAD.start()
        return {"ok": True, "running": True, "interval_sec": interval}


def stop_remote_live_loop(timeout: float = 2.0) -> dict:
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
