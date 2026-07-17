# -*- coding: utf-8 -*-
"""Local GoProxy process manager for the merged branch.

Starts/stops the vendored GoProxy as a subprocess on localhost ports:
- HTTP random: 7777
- HTTP stable: 7776
- SOCKS5 random: 7779
- SOCKS5 stable: 7780
- WebUI: 7778

Pool modes (5) and endpoint selection are controlled through branch config +
GoProxy data/config.json.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

from panel.settings import (
    GOPROXY_ENDPOINTS,
    GOPROXY_POOL_MODES,
    apply_local_proxy_bindings,
    build_goproxy_env,
    describe_proxy_selection,
    normalize_branch_config,
    normalize_endpoint,
    normalize_pool_mode,
    pool_mode_to_goproxy_env,
    resolve_local_proxy_url,
)


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _abs(path: str, root: Optional[str] = None) -> str:
    root = root or _project_root()
    if not path:
        return root
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(root, path))


def _port_open(host: str, port: int, timeout: float = 0.05) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _is_windows_pe(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def _is_runnable_binary(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    if os.name == "nt":
        return _is_windows_pe(path) or path.lower().endswith(".exe")
    return os.access(path, os.X_OK)


class GoProxyManager:
    """Manage one embedded GoProxy child process."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, root: Optional[str] = None):
        self.root = root or _project_root()
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._log_fp = None
        self._last_error = ""
        self._started_at = 0.0
        self.set_config(config or {})

    # ------------------------------------------------------------------ config
    def set_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self.config = normalize_branch_config(dict(config or {}))
            return dict(self.config)

    def update_config(self, **kwargs) -> Dict[str, Any]:
        with self._lock:
            cfg = dict(self.config)
            cfg.update(kwargs)
            if "goproxy_pool_mode" in kwargs:
                cfg["goproxy_pool_mode"] = normalize_pool_mode(kwargs["goproxy_pool_mode"])
            if "goproxy_endpoint" in kwargs:
                cfg["goproxy_endpoint"] = normalize_endpoint(kwargs["goproxy_endpoint"])
            self.config = normalize_branch_config(cfg)
            return dict(self.config)

    def selection(self) -> Dict[str, Any]:
        with self._lock:
            return describe_proxy_selection(self.config)

    def local_proxy_url(self, endpoint: Optional[str] = None) -> str:
        with self._lock:
            return resolve_local_proxy_url(self.config, endpoint)

    def apply_bindings_to(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            base = dict(config if config is not None else self.config)
            # ensure current manager selection wins
            base["goproxy_endpoint"] = self.config.get("goproxy_endpoint")
            base["goproxy_pool_mode"] = self.config.get("goproxy_pool_mode")
            for key in (
                "goproxy_host",
                "goproxy_http_random_port",
                "goproxy_http_stable_port",
                "goproxy_socks5_random_port",
                "goproxy_socks5_stable_port",
                "goproxy_proxy_auth_enabled",
                "goproxy_proxy_auth_username",
                "goproxy_proxy_auth_password",
                "goproxy_bind_register_proxy",
                "goproxy_bind_cpa_proxy",
                "goproxy_enabled",
            ):
                base[key] = self.config.get(key)
            return apply_local_proxy_bindings(normalize_branch_config(base))

    # -------------------------------------------------------------- path/build
    def source_dir(self) -> str:
        return _abs(self.config.get("goproxy_source_dir") or "third_party/goproxy", self.root)

    def workdir(self) -> str:
        return _abs(self.config.get("goproxy_workdir") or "third_party/goproxy", self.root)

    def data_dir(self) -> str:
        path = _abs(self.config.get("goproxy_data_dir") or "data/goproxy", self.root)
        os.makedirs(path, exist_ok=True)
        return path

    def bin_candidates(self):
        configured = (self.config.get("goproxy_bin_path") or "").strip()
        names = []
        if configured:
            names.append(_abs(configured, self.root))
        src = self.source_dir()
        if os.name == "nt":
            names.extend(
                [
                    os.path.join(src, "bin", "proxygo.exe"),
                    os.path.join(src, "bin", "goproxy.exe"),
                    os.path.join(src, "proxygo.exe"),
                    os.path.join(src, "proxy-pool.exe"),
                ]
            )
        names.extend(
            [
                os.path.join(src, "bin", "proxygo"),
                os.path.join(src, "bin", "goproxy"),
                os.path.join(src, "proxygo"),
                os.path.join(src, "proxy-pool"),
            ]
        )
        # de-dup preserve order
        seen = set()
        for item in names:
            if item not in seen:
                seen.add(item)
                yield item

    def resolve_binary(self, build_if_missing: bool = False) -> str:
        for path in self.bin_candidates():
            if _is_runnable_binary(path):
                return path
        if build_if_missing:
            built = self.build_binary()
            if built:
                return built
        # return preferred path even if missing (for error messages)
        preferred = (
            os.path.join(self.source_dir(), "bin", "proxygo.exe")
            if os.name == "nt"
            else os.path.join(self.source_dir(), "bin", "proxygo")
        )
        return preferred

    def build_binary(self) -> str:
        go = shutil.which("go")
        if not go:
            self._last_error = "go toolchain not found; cannot build GoProxy"
            return ""
        src = self.source_dir()
        if not os.path.isfile(os.path.join(src, "main.go")):
            self._last_error = f"GoProxy source missing: {src}"
            return ""
        out_dir = os.path.join(src, "bin")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "proxygo.exe" if os.name == "nt" else "proxygo")
        env = os.environ.copy()
        # sqlite driver needs cgo on many platforms
        env.setdefault("CGO_ENABLED", "1")
        cmd = [go, "build", "-o", out, "."]
        try:
            proc = subprocess.run(
                cmd,
                cwd=src,
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except Exception as exc:
            self._last_error = f"go build failed: {exc}"
            return ""
        if proc.returncode != 0 or not os.path.isfile(out):
            err = (proc.stderr or proc.stdout or "").strip()
            self._last_error = f"go build failed ({proc.returncode}): {err[:800]}"
            return ""
        self._last_error = ""
        return out

    # ----------------------------------------------------------- mode/config IO
    def goproxy_config_path(self) -> str:
        return os.path.join(self.data_dir(), "config.json")

    def write_pool_mode_config(self, mode: Optional[str] = None) -> str:
        mode = normalize_pool_mode(mode if mode is not None else self.config.get("goproxy_pool_mode"))
        env_map = pool_mode_to_goproxy_env(mode)
        custom_mode = env_map["CUSTOM_PROXY_MODE"]
        custom_priority = env_map["CUSTOM_PRIORITY"].lower() == "true"
        free_priority = env_map["CUSTOM_FREE_PRIORITY"].lower() == "true"
        path = self.goproxy_config_path()
        data = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        data["custom_proxy_mode"] = custom_mode
        data["custom_priority"] = custom_priority
        data["custom_free_priority"] = free_priority
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.update_config(goproxy_pool_mode=mode)
        return path

    def set_pool_mode(self, mode: str, restart_if_running: bool = True) -> Dict[str, Any]:
        mode = normalize_pool_mode(mode)
        path = self.write_pool_mode_config(mode)
        result = {
            "ok": True,
            "pool_mode": mode,
            "config_path": path,
            "restarted": False,
        }
        if restart_if_running and self.is_running():
            self.restart()
            result["restarted"] = True
        return result

    def set_endpoint(self, endpoint: str, bind: Optional[bool] = None) -> Dict[str, Any]:
        endpoint = normalize_endpoint(endpoint)
        updates = {"goproxy_endpoint": endpoint}
        if bind is not None:
            updates["goproxy_bind_register_proxy"] = bool(bind)
            updates["goproxy_bind_cpa_proxy"] = bool(bind)
        self.update_config(**updates)
        info = self.selection()
        bound = self.apply_bindings_to()
        return {
            "ok": True,
            "endpoint": endpoint,
            "proxy_url": info["proxy_url"],
            "bound_proxy": bound.get("proxy"),
            "bound_cpa_proxy": bound.get("cpa_proxy"),
            "bind_enabled": bool(self.config.get("goproxy_bind_register_proxy")),
        }

    def list_modes(self):
        return list(GOPROXY_POOL_MODES)

    def list_endpoints(self):
        out = []
        for key, meta in GOPROXY_ENDPOINTS.items():
            out.append(
                {
                    "id": key,
                    "label": meta["label"],
                    "scheme": meta["scheme"],
                    "port": int(self.config.get(meta["port_key"], meta["default_port"])),
                    "url": resolve_local_proxy_url(self.config, key),
                }
            )
        return out

    # -------------------------------------------------------------- process ops
    def _pid_alive(self, pid: int) -> bool:
        if not pid:
            return False
        try:
            import psutil

            return psutil.pid_exists(int(pid))
        except Exception:
            if os.name == "nt":
                # tasklist fallback
                try:
                    out = subprocess.check_output(
                        ["tasklist", "/FI", f"PID eq {int(pid)}"],
                        text=True,
                        stderr=subprocess.DEVNULL,
                    )
                    return str(pid) in out
                except Exception:
                    return False
            try:
                os.kill(int(pid), 0)
                return True
            except OSError:
                return False

    def is_running(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            # treat listening webui/http random as running (externally started)
            host = self.config.get("goproxy_host") or "127.0.0.1"
            webui = int(self.config.get("goproxy_webui_port") or 7778)
            if _port_open(host, webui, timeout=0.05):
                return True
            http_random = int(self.config.get("goproxy_http_random_port") or 7777)
            return _port_open(host, http_random, timeout=0.05)

    def managed_pid(self) -> Optional[int]:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return int(self._proc.pid)
            return None

    def port_status(self) -> Dict[str, bool]:
        host = self.config.get("goproxy_host") or "127.0.0.1"
        ports = {
            "http_random": int(self.config.get("goproxy_http_random_port") or 7777),
            "http_stable": int(self.config.get("goproxy_http_stable_port") or 7776),
            "socks5_random": int(self.config.get("goproxy_socks5_random_port") or 7779),
            "socks5_stable": int(self.config.get("goproxy_socks5_stable_port") or 7780),
            "webui": int(self.config.get("goproxy_webui_port") or 7778),
        }
        return {name: _port_open(host, port, timeout=0.05) for name, port in ports.items()}

    def status(self) -> Dict[str, Any]:
        with self._lock:
            binary = self.resolve_binary(build_if_missing=False)
            sel = describe_proxy_selection(self.config)
            return {
                "enabled": bool(self.config.get("goproxy_enabled")),
                "running": self.is_running(),
                "managed": self._proc is not None and self._proc.poll() is None,
                "pid": self.managed_pid(),
                "started_at": self._started_at or None,
                "binary": binary,
                "binary_runnable": _is_runnable_binary(binary),
                "source_dir": self.source_dir(),
                "data_dir": self.data_dir(),
                "pool_mode": self.config.get("goproxy_pool_mode"),
                "endpoint": self.config.get("goproxy_endpoint"),
                "proxy_url": sel["proxy_url"],
                "ports": sel["ports"],
                "port_status": self.port_status(),
                "last_error": self._last_error,
                "modes": self.list_modes(),
                "endpoints": self.list_endpoints(),
            }

    def start(self, build_if_missing: bool = True, wait_sec: float = 8.0) -> Dict[str, Any]:
        with self._lock:
            if not self.config.get("goproxy_enabled", True):
                return {"ok": False, "error": "goproxy_enabled is false", "status": self.status()}
            if self._proc is not None and self._proc.poll() is None:
                return {"ok": True, "already_running": True, "status": self.status()}

            # if ports already live, treat as ready
            if self.is_running() and self._proc is None:
                return {
                    "ok": True,
                    "already_running": True,
                    "external": True,
                    "status": self.status(),
                }

            binary = self.resolve_binary(build_if_missing=build_if_missing)
            if not _is_runnable_binary(binary):
                err = self._last_error or f"GoProxy binary not runnable: {binary}"
                self._last_error = err
                return {"ok": False, "error": err, "status": self.status()}

            self.write_pool_mode_config()
            data_dir = self.data_dir()
            workdir = self.workdir()
            os.makedirs(workdir, exist_ok=True)

            env = build_goproxy_env(self.config, base_env=os.environ.copy())
            # absolute DATA_DIR for child
            env["DATA_DIR"] = data_dir
            # ensure child cwd finds relative paths under source/data
            log_path = os.path.join(data_dir, "goproxy.manager.log")
            try:
                if self._log_fp:
                    try:
                        self._log_fp.close()
                    except Exception:
                        pass
                self._log_fp = open(log_path, "a", encoding="utf-8", errors="replace")
                self._log_fp.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} bin={binary} ---\n")
                self._log_fp.flush()
                creationflags = 0
                if os.name == "nt":
                    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                self._proc = subprocess.Popen(
                    [binary],
                    cwd=workdir,
                    env=env,
                    stdout=self._log_fp,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            except Exception as exc:
                self._last_error = f"failed to start GoProxy: {exc}"
                self._proc = None
                return {"ok": False, "error": self._last_error, "status": self.status()}

            self._started_at = time.time()
            self._last_error = ""
            ready = self._wait_ready(wait_sec=wait_sec)
            if not ready:
                # process may still be warming up; report partial
                if self._proc.poll() is not None:
                    self._last_error = f"GoProxy exited early with code {self._proc.returncode}; see {log_path}"
                    return {"ok": False, "error": self._last_error, "status": self.status()}
                self._last_error = f"GoProxy started pid={self._proc.pid} but ports not ready yet; log={log_path}"
                return {
                    "ok": True,
                    "warming_up": True,
                    "warning": self._last_error,
                    "status": self.status(),
                }
            return {"ok": True, "status": self.status()}

    def _wait_ready(self, wait_sec: float = 8.0) -> bool:
        host = self.config.get("goproxy_host") or "127.0.0.1"
        # webui or any proxy port is enough signal
        ports = [
            int(self.config.get("goproxy_webui_port") or 7778),
            int(self.config.get("goproxy_http_random_port") or 7777),
            int(self.config.get("goproxy_http_stable_port") or 7776),
            int(self.config.get("goproxy_socks5_random_port") or 7779),
            int(self.config.get("goproxy_socks5_stable_port") or 7780),
        ]
        deadline = time.time() + max(float(wait_sec), 0.5)
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if any(_port_open(host, p) for p in ports):
                return True
            time.sleep(0.25)
        return any(_port_open(host, p) for p in ports)

    def stop(self, timeout: float = 5.0) -> Dict[str, Any]:
        with self._lock:
            proc = self._proc
            if proc is None:
                return {"ok": True, "stopped": False, "reason": "not managed", "status": self.status()}
            if proc.poll() is not None:
                self._proc = None
                return {"ok": True, "stopped": True, "already_exited": True, "status": self.status()}
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT) if hasattr(signal, "CTRL_BREAK_EVENT") else proc.terminate()
                else:
                    proc.terminate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=timeout)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
            self._proc = None
            return {"ok": True, "stopped": True, "status": self.status()}

    def restart(self, build_if_missing: bool = True, wait_sec: float = 8.0) -> Dict[str, Any]:
        stop_res = self.stop()
        start_res = self.start(build_if_missing=build_if_missing, wait_sec=wait_sec)
        return {
            "ok": bool(start_res.get("ok")),
            "stop": stop_res,
            "start": start_res,
            "status": self.status(),
        }


# module-level singleton for panel/register integration
_manager: Optional[GoProxyManager] = None
_manager_lock = threading.Lock()


def get_manager(config: Optional[Dict[str, Any]] = None, root: Optional[str] = None) -> GoProxyManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = GoProxyManager(config=config, root=root)
        elif config is not None:
            _manager.set_config(config)
        return _manager
