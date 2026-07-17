# -*- coding: utf-8 -*-
"""Browser instance registry and zombie Chromium cleanup."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional


_LOCK = threading.RLock()
_REGISTRY: Dict[str, Dict[str, Any]] = {}

_MONITOR_LOCK = threading.RLock()
_MONITOR_THREAD: Optional[threading.Thread] = None
_MONITOR_STOP = threading.Event()
_MONITOR_LAST: Dict[str, Any] = {
    "ts": None,
    "ok": None,
    "result": None,
    "error": None,
}


def _now() -> float:
    return time.time()


def _browser_pid(browser: Any) -> Optional[int]:
    if browser is None:
        return None
    try:
        pid = getattr(browser, "process_id", None)
        if callable(pid):
            pid = pid()
        if pid is not None:
            return int(pid)
    except Exception:
        return None
    return None


def _profile_path(browser: Any) -> str:
    try:
        p = getattr(browser, "user_data_path", None)
        return str(p or "")
    except Exception:
        return ""


def _pid_is_running(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(int(pid)))
    except Exception:
        return False


def reset_registry() -> None:
    """Test helper: wipe in-memory registry state."""
    with _LOCK:
        _REGISTRY.clear()


def register_browser(
    browser: Any,
    *,
    purpose: str = "register",
    worker_id: Any = None,
    extra: Optional[dict] = None,
) -> str:
    key = f"{purpose}:{id(browser)}"
    with _LOCK:
        _REGISTRY[key] = {
            "key": key,
            "purpose": purpose,
            "worker_id": worker_id,
            "pid": _browser_pid(browser),
            "profile": _profile_path(browser),
            "started_at": _now(),
            "last_heartbeat": _now(),
            "object_id": id(browser),
            "extra": dict(extra or {}),
        }
    return key


def unregister_browser(
    browser: Any = None,
    *,
    key: Optional[str] = None,
    purpose: Optional[str] = None,
) -> int:
    removed = 0
    with _LOCK:
        if key and key in _REGISTRY:
            _REGISTRY.pop(key, None)
            return 1
        drop = []
        for k, item in _REGISTRY.items():
            if browser is not None and item.get("object_id") == id(browser):
                drop.append(k)
            elif purpose and browser is None and item.get("purpose") == purpose:
                drop.append(k)
        for k in drop:
            _REGISTRY.pop(k, None)
            removed += 1
    return removed


def heartbeat(browser: Any = None, *, key: Optional[str] = None) -> None:
    with _LOCK:
        if key and key in _REGISTRY:
            _REGISTRY[key]["last_heartbeat"] = _now()
            _REGISTRY[key]["pid"] = _browser_pid(browser) or _REGISTRY[key].get("pid")
            return
        if browser is None:
            return
        oid = id(browser)
        for item in _REGISTRY.values():
            if item.get("object_id") == oid:
                item["last_heartbeat"] = _now()
                item["pid"] = _browser_pid(browser) or item.get("pid")


def list_registered() -> List[dict]:
    with _LOCK:
        items = [dict(v) for v in _REGISTRY.values()]
    for it in items:
        it["age_sec"] = round(max(_now() - float(it.get("started_at") or _now()), 0), 1)
        pid = it.get("pid")
        it["alive"] = _pid_is_running(pid) if pid else None
    items.sort(key=lambda x: (str(x.get("purpose")), str(x.get("worker_id"))))
    return items


def prune_dead_registry() -> dict:
    """Drop registry rows whose recorded PID no longer exists."""
    removed = []
    kept = 0
    with _LOCK:
        drop = []
        for k, item in _REGISTRY.items():
            pid = item.get("pid")
            if pid and not _pid_is_running(pid):
                drop.append((k, int(pid)))
        for k, pid in drop:
            _REGISTRY.pop(k, None)
            removed.append({"key": k, "pid": pid})
        kept = len(_REGISTRY)
    return {"removed": removed, "removed_count": len(removed), "kept": kept}


def expected_pids() -> set:
    pids = set()
    for item in list_registered():
        pid = item.get("pid")
        if pid:
            try:
                pids.add(int(pid))
            except Exception:
                pass
    # include mint browsers if available
    try:
        from cpa_xai import browser_confirm as bc

        with bc._mint_browsers_lock:  # type: ignore[attr-defined]
            for browser in list(getattr(bc, "_mint_browser_objs", {}).values()):
                pid = _browser_pid(browser)
                if pid:
                    pids.add(int(pid))
    except Exception:
        pass
    return pids


def _iter_chrome_processes():
    try:
        import psutil
    except Exception as exc:
        raise RuntimeError(f"psutil unavailable: {exc}") from exc
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time", "ppid"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "chrome" not in name and "chromium" not in name:
                continue
            cmd = proc.info.get("cmdline") or []
            yield {
                "pid": int(proc.info["pid"]),
                "ppid": int(proc.info.get("ppid") or 0),
                "name": proc.info.get("name") or "",
                "cmdline": " ".join(str(x) for x in cmd),
                "create_time": float(proc.info.get("create_time") or 0),
            }
        except Exception:
            continue


def _classify_browser_roots(
    roots: List[dict],
    *,
    project_root: str,
    expected: set,
) -> dict:
    """Split root Chromium processes into project/active/zombie/foreign."""
    profile_marker = os.path.join(project_root, ".browser_profiles").replace("\\", "/").lower()
    project_roots = []
    foreign_roots = []
    for p in roots:
        cmd_norm = str(p.get("cmdline") or "").replace("\\", "/").lower()
        if profile_marker in cmd_norm or ".browser_profiles" in cmd_norm:
            project_roots.append(p)
        else:
            foreign_roots.append(p)

    zombies = []
    active = []
    expected_strs = {str(ep) for ep in expected}
    for p in project_roots:
        pid = int(p.get("pid") or 0)
        cmd = str(p.get("cmdline") or "")
        if pid in expected:
            active.append(p)
            continue
        # rare: child root still carries parent pid marker in args
        if any(ep in cmd for ep in expected_strs):
            active.append(p)
        else:
            zombies.append(p)

    return {
        "profile_marker": profile_marker,
        "project_root_browsers": project_roots,
        "active": active,
        "zombies": zombies,
        "foreign_root_browsers": foreign_roots,
    }


def scan_browser_processes(
    project_root: Optional[str] = None,
    *,
    process_iter: Optional[Callable[[], Any]] = None,
) -> dict:
    root = os.path.abspath(project_root or os.getcwd())
    expected = expected_pids()
    if process_iter is None:
        procs = list(_iter_chrome_processes())
    else:
        procs = list(process_iter())

    # root processes only (no --type=)
    roots = []
    for p in procs:
        cmd = str(p.get("cmdline") or "")
        if "--type=" in cmd:
            continue
        roots.append(p)

    classified = _classify_browser_roots(roots, project_root=root, expected=expected)
    return {
        "ok": True,
        "expected_count": len(expected),
        "expected_pids": sorted(expected),
        "registered": list_registered(),
        "project_root_browsers": classified["project_root_browsers"],
        "active": classified["active"],
        "zombies": classified["zombies"],
        "foreign_root_browsers": len(classified["foreign_root_browsers"]),
        "total_chrome_processes": len(procs),
        "profile_marker": classified["profile_marker"],
    }


def cleanup_zombies(
    project_root: Optional[str] = None,
    kill: bool = True,
    *,
    process_iter: Optional[Callable[[], Any]] = None,
    kill_fn: Optional[Callable[[int], None]] = None,
) -> dict:
    prune = prune_dead_registry()
    snap = scan_browser_processes(project_root=project_root, process_iter=process_iter)
    killed = []
    errors = []
    if not kill:
        return {
            **snap,
            "killed": [],
            "killed_count": 0,
            "pruned": prune,
        }

    def _default_kill(pid: int) -> None:
        import psutil

        root_proc = psutil.Process(pid)
        victims = list(root_proc.children(recursive=True)) + [root_proc]
        for proc in victims:
            try:
                proc.kill()
            except Exception:
                pass
        psutil.wait_procs(victims, timeout=2)

    killer = kill_fn or _default_kill
    if kill_fn is None:
        try:
            import psutil  # noqa: F401
        except Exception as exc:
            return {
                **snap,
                "ok": False,
                "error": f"psutil unavailable: {exc}",
                "killed": [],
                "killed_count": 0,
                "pruned": prune,
            }

    for z in snap.get("zombies") or []:
        pid = int(z.get("pid") or 0)
        if not pid:
            continue
        try:
            killer(pid)
            killed.append(pid)
        except Exception as exc:
            errors.append({"pid": pid, "error": str(exc)})

    after = scan_browser_processes(project_root=project_root, process_iter=process_iter)
    return {
        "ok": not errors,
        "killed": killed,
        "killed_count": len(killed),
        "errors": errors,
        "before_zombies": len(snap.get("zombies") or []),
        "after_zombies": len(after.get("zombies") or []),
        "registered_count": len(after.get("registered") or []),
        "pruned": prune,
        "scan": after,
    }


def summary(
    project_root: Optional[str] = None,
    *,
    process_iter: Optional[Callable[[], Any]] = None,
) -> dict:
    try:
        scan = scan_browser_processes(project_root=project_root, process_iter=process_iter)
        return {
            "ok": True,
            "registered": len(scan.get("registered") or []),
            "expected_pids": len(scan.get("expected_pids") or []),
            "project_browsers": len(scan.get("project_root_browsers") or []),
            "zombies": len(scan.get("zombies") or []),
            "active": len(scan.get("active") or []),
            "monitor": monitor_status(),
            "details": scan,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "registered": len(list_registered()),
            "zombies": None,
            "monitor": monitor_status(),
        }


def monitor_status() -> dict:
    with _MONITOR_LOCK:
        running = bool(_MONITOR_THREAD and _MONITOR_THREAD.is_alive())
        last = dict(_MONITOR_LAST)
    return {
        "running": running,
        "last_ts": last.get("ts"),
        "last_ok": last.get("ok"),
        "last_error": last.get("error"),
        "last_result": last.get("result"),
    }


def _monitor_tick(project_root: Optional[str], *, kill: bool) -> dict:
    prune = prune_dead_registry()
    if kill:
        res = cleanup_zombies(project_root=project_root, kill=True)
    else:
        res = scan_browser_processes(project_root=project_root)
        res = {
            **res,
            "killed": [],
            "killed_count": 0,
            "before_zombies": len(res.get("zombies") or []),
            "after_zombies": len(res.get("zombies") or []),
        }
    compact = {
        "pruned_count": prune.get("removed_count", 0),
        "registered": len(list_registered()),
        "zombies_before": res.get("before_zombies"),
        "zombies_after": res.get("after_zombies"),
        "killed_count": res.get("killed_count", 0),
        "killed": res.get("killed") or [],
    }
    with _MONITOR_LOCK:
        _MONITOR_LAST["ts"] = _now()
        _MONITOR_LAST["ok"] = bool(res.get("ok", True))
        _MONITOR_LAST["result"] = compact
        _MONITOR_LAST["error"] = res.get("error")
    return compact


def start_monitor_loop(
    *,
    project_root: Optional[str] = None,
    interval_sec: float = 15,
    enabled: bool = True,
    cleanup_enabled: bool = True,
    config_provider: Optional[Callable[[], dict]] = None,
) -> dict:
    """Start daemon patrol that prunes dead registry rows and kills project zombies."""
    global _MONITOR_THREAD
    if not enabled:
        stop_monitor_loop()
        return {"ok": True, "running": False, "reason": "disabled"}

    interval = max(float(interval_sec or 15), 3.0)
    root = os.path.abspath(project_root or os.getcwd())

    with _MONITOR_LOCK:
        if _MONITOR_THREAD and _MONITOR_THREAD.is_alive():
            return {"ok": True, "running": True, "reason": "already-running"}
        _MONITOR_STOP.clear()

        def _loop():
            while not _MONITOR_STOP.is_set():
                kill = cleanup_enabled
                tick_root = root
                tick_interval = interval
                if config_provider is not None:
                    try:
                        cfg = config_provider() or {}
                        if not bool(cfg.get("browser_monitor_enabled", True)):
                            _MONITOR_STOP.wait(tick_interval)
                            continue
                        kill = bool(cfg.get("browser_zombie_cleanup_enabled", True))
                        tick_interval = max(
                            float(cfg.get("browser_monitor_interval_sec") or interval),
                            3.0,
                        )
                        tick_root = os.path.abspath(str(cfg.get("_project_root") or root))
                    except Exception:
                        pass
                try:
                    _monitor_tick(tick_root, kill=kill)
                except Exception as exc:
                    with _MONITOR_LOCK:
                        _MONITOR_LAST["ts"] = _now()
                        _MONITOR_LAST["ok"] = False
                        _MONITOR_LAST["error"] = str(exc)
                _MONITOR_STOP.wait(tick_interval)

        _MONITOR_THREAD = threading.Thread(target=_loop, name="browser-monitor", daemon=True)
        _MONITOR_THREAD.start()
        return {
            "ok": True,
            "running": True,
            "interval_sec": interval,
            "cleanup_enabled": cleanup_enabled,
        }


def stop_monitor_loop(timeout: float = 2.0) -> dict:
    global _MONITOR_THREAD
    with _MONITOR_LOCK:
        thread = _MONITOR_THREAD
    _MONITOR_STOP.set()
    if thread and thread.is_alive():
        thread.join(timeout=timeout)
    with _MONITOR_LOCK:
        if _MONITOR_THREAD is thread:
            _MONITOR_THREAD = None
    return {"ok": True, "running": False}
