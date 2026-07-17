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
from pathlib import Path
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


def _probe_host(host: Optional[str] = None) -> str:
    """Host used for local readiness probes / reverse-proxy.

    Bind address 0.0.0.0 cannot be used as connect target.
    """
    h = str(host or "127.0.0.1").strip() or "127.0.0.1"
    if h in ("0.0.0.0", "::", "[::]", "*"):
        return "127.0.0.1"
    return h


def _file_magic(path: str) -> str:
    try:
        with open(path, "rb") as f:
            b = f.read(4)
        if b[:2] == b"MZ":
            return "pe"
        if b[:4] == b"\x7fELF":
            return "elf"
        if b in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
            return "macho"
        return b.hex() or "empty"
    except Exception as exc:
        return f"unreadable:{exc}"


def _tail_text(path: str, max_chars: int = 1200) -> str:
    try:
        if not path or not os.path.isfile(path):
            return ""
        data = Path(path).read_text(encoding="utf-8", errors="replace")
        return data[-max_chars:]
    except Exception:
        return ""


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
        # Only trust real PE headers; wrong-arch .exe leftovers must be rejected.
        return _is_windows_pe(path)
    # reject Mach-O / PE when running on *nix
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
        if magic[:2] == b"MZ":
            return False
        if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
            # still ok if native, but check executable bit below
            pass
    except OSError:
        return False
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

    def preferred_binary_path(self) -> str:
        src = self.source_dir()
        if os.name == "nt":
            return os.path.join(src, "bin", "proxygo-windows.exe")
        return os.path.join(src, "bin", "proxygo")

    def bin_candidates(self):
        """Yield candidate binaries; wrong-arch files are filtered by _is_runnable_binary."""
        configured = (self.config.get("goproxy_bin_path") or "").strip()
        names = []
        if configured:
            names.append(_abs(configured, self.root))
        src = self.source_dir()
        if os.name == "nt":
            names.extend(
                [
                    # Prefer explicit Windows artifact first.
                    os.path.join(src, "bin", "proxygo-windows.exe"),
                    os.path.join(src, "bin", "proxygo.exe"),
                    os.path.join(src, "bin", "goproxy.exe"),
                    os.path.join(src, "bin", "proxy-pool.exe"),
                    os.path.join(src, "proxygo.exe"),
                    os.path.join(src, "proxy-pool.exe"),
                ]
            )
        else:
            names.extend(
                [
                    os.path.join(src, "bin", "proxygo"),
                    os.path.join(src, "bin", "proxy-pool"),
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
        return self.preferred_binary_path()

    def _find_c_compiler(self) -> str:
        """Locate gcc/clang for CGO (mattn/go-sqlite3 needs it)."""
        for name in ("gcc", "clang", "x86_64-w64-mingw32-gcc"):
            found = shutil.which(name)
            if found:
                return found
        roots = [
            os.path.join(self.root, "third_party", "toolchains"),
            os.path.join(self.root, "third_party", "toolchains", "mingw64"),
            os.path.join(self.root, "third_party", "toolchains", "winlibs"),
        ]
        for root in roots:
            if not os.path.isdir(root):
                continue
            depth_root = root.count(os.sep)
            for dirpath, dirnames, filenames in os.walk(root):
                for fname in ("gcc.exe", "gcc"):
                    if fname in filenames:
                        candidate = os.path.join(dirpath, fname)
                        if os.path.isfile(candidate):
                            return candidate
                if dirpath.count(os.sep) - depth_root > 6:
                    dirnames[:] = []
        return ""

    def _prepare_build_env(self) -> dict:
        """Prefer pure-Go build (modernc sqlite). Fall back to CGO if forced."""
        env = os.environ.copy()
        force_cgo = str(env.get("GOPROXY_FORCE_CGO") or self.config.get("goproxy_force_cgo") or "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if force_cgo:
            env["CGO_ENABLED"] = "1"
            gcc = self._find_c_compiler()
            if gcc:
                env["CC"] = gcc
                gcc_dir = os.path.dirname(gcc)
                path_parts = env.get("PATH", "").split(os.pathsep)
                if gcc_dir and gcc_dir not in path_parts:
                    env["PATH"] = gcc_dir + os.pathsep + env.get("PATH", "")
        else:
            # Vendored GoProxy uses modernc.org/sqlite so host deploy can self-build without gcc.
            env["CGO_ENABLED"] = "0"
            env.pop("CC", None)
        return env

    def build_binary(self) -> str:
        """Compile vendored GoProxy source for the current platform."""
        go = shutil.which("go")
        if not go:
            self._last_error = "未找到 go 工具链，无法从源码编译 GoProxy"
            return ""
        src = self.source_dir()
        if not os.path.isfile(os.path.join(src, "main.go")):
            self._last_error = f"GoProxy 源码缺失: {src}"
            return ""

        force_cgo = str(os.environ.get("GOPROXY_FORCE_CGO") or self.config.get("goproxy_force_cgo") or "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if force_cgo and not self._find_c_compiler():
            self._last_error = (
                "已强制 CGO，但未找到 gcc。请安装 MinGW-w64 / build-essential，"
                "或取消 goproxy_force_cgo / GOPROXY_FORCE_CGO。"
            )
            return ""

        out_dir = os.path.join(src, "bin")
        os.makedirs(out_dir, exist_ok=True)
        if os.name == "nt":
            out = os.path.join(out_dir, "proxygo-windows.exe")
        else:
            out = os.path.join(out_dir, "proxygo")

        env = self._prepare_build_env()
        env.pop("GOOS", None)
        env.pop("GOARCH", None)

        log_path = os.path.join(self.data_dir(), "goproxy.build.log")
        cmd = [go, "build", "-o", out, "."]
        try:
            with open(log_path, "a", encoding="utf-8", errors="replace") as logf:
                logf.write(
                    f"\n--- build {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"go={go} cc={env.get('CC')} out={out} ---\n"
                )
                logf.flush()
                proc = subprocess.run(
                    cmd,
                    cwd=src,
                    env=env,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=900,
                )
        except Exception as exc:
            self._last_error = f"go build failed: {exc}"
            return ""
        if proc.returncode != 0 or not _is_runnable_binary(out):
            err_tail = ""
            try:
                if os.path.isfile(log_path):
                    err_tail = Path(log_path).read_text(encoding="utf-8", errors="replace")[-800:]
            except Exception:
                err_tail = ""
            self._last_error = (
                f"从源码编译 GoProxy 失败 (code={proc.returncode})。"
                f" 日志: {log_path}"
                + (f"\n{err_tail}" if err_tail else "")
            )
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
            host = _probe_host(self.config.get("goproxy_host"))
            webui = int(self.config.get("goproxy_webui_port") or 17878)
            if _port_open(host, webui, timeout=0.05):
                return True
            http_random = int(self.config.get("goproxy_http_random_port") or 17877)
            return _port_open(host, http_random, timeout=0.05)

    def managed_pid(self) -> Optional[int]:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return int(self._proc.pid)
            return None

    def port_status(self) -> Dict[str, bool]:
        host = _probe_host(self.config.get("goproxy_host"))
        ports = {
            "http_random": int(self.config.get("goproxy_http_random_port") or 17877),
            "http_stable": int(self.config.get("goproxy_http_stable_port") or 17876),
            "socks5_random": int(self.config.get("goproxy_socks5_random_port") or 17879),
            "socks5_stable": int(self.config.get("goproxy_socks5_stable_port") or 17880),
            "webui": int(self.config.get("goproxy_webui_port") or 17878),
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
                "log_path": os.path.join(self.data_dir(), "goproxy.manager.log"),
                "log_tail": _tail_text(os.path.join(self.data_dir(), "goproxy.manager.log"), 800) if self._last_error else "",
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
            # absolute DATA_DIR for child (critical in Docker / reverse-proxy)
            os.makedirs(data_dir, exist_ok=True)
            env["DATA_DIR"] = data_dir
            # force connect-friendly host for child-facing config consumers
            env["WEBUI_PORT"] = str(int(self.config.get("goproxy_webui_port") or 17878))
            env["RANDOM_PORT"] = str(int(self.config.get("goproxy_http_random_port") or 17877))
            env["STABLE_PORT"] = str(int(self.config.get("goproxy_http_stable_port") or 17876))
            env["SOCKS5_RANDOM_PORT"] = str(int(self.config.get("goproxy_socks5_random_port") or 17879))
            env["SOCKS5_STABLE_PORT"] = str(int(self.config.get("goproxy_socks5_stable_port") or 17880))

            # On Linux/container, refuse PE leftovers and ensure +x.
            magic = _file_magic(binary)
            if os.name != "nt" and magic == "pe":
                self._last_error = (
                    f"GoProxy binary is Windows PE, not runnable in Linux container: {binary}. "
                    "Rebuild image so third_party/goproxy/bin/proxygo is Linux ELF."
                )
                return {"ok": False, "error": self._last_error, "status": self.status()}
            if os.name != "nt":
                try:
                    mode = os.stat(binary).st_mode
                    if not (mode & 0o111):
                        os.chmod(binary, mode | 0o755)
                except Exception:
                    pass

            log_path = os.path.join(data_dir, "goproxy.manager.log")
            try:
                if self._log_fp:
                    try:
                        self._log_fp.close()
                    except Exception:
                        pass
                self._log_fp = open(log_path, "a", encoding="utf-8", errors="replace")
                self._log_fp.write(
                    f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"bin={binary} magic={magic} size={os.path.getsize(binary) if os.path.isfile(binary) else -1} "
                    f"cwd={workdir} DATA_DIR={data_dir} "
                    f"WEBUI_PORT={env.get('WEBUI_PORT')} RANDOM_PORT={env.get('RANDOM_PORT')} ---\n"
                )
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
                code = self._proc.poll() if self._proc is not None else None
                if code is not None:
                    # flush log and include tail so panel/UI can show real reason
                    try:
                        if self._log_fp:
                            self._log_fp.flush()
                    except Exception:
                        pass
                    tail = _tail_text(log_path, 1500)
                    self._last_error = (
                        f"GoProxy exited early with code {code}; log={log_path}"
                        + (f"\n---- log tail ----\n{tail}" if tail else "")
                    )
                    return {
                        "ok": False,
                        "error": self._last_error,
                        "log_path": log_path,
                        "log_tail": tail,
                        "binary": binary,
                        "magic": magic,
                        "status": self.status(),
                    }
                self._last_error = f"GoProxy started pid={self._proc.pid} but ports not ready yet; log={log_path}"
                return {
                    "ok": True,
                    "warming_up": True,
                    "warning": self._last_error,
                    "status": self.status(),
                }
            return {"ok": True, "status": self.status()}

    def _wait_ready(self, wait_sec: float = 8.0) -> bool:
        host = _probe_host(self.config.get("goproxy_host"))
        # webui or any proxy port is enough signal
        ports = [
            int(self.config.get("goproxy_webui_port") or 17878),
            int(self.config.get("goproxy_http_random_port") or 17877),
            int(self.config.get("goproxy_http_stable_port") or 17876),
            int(self.config.get("goproxy_socks5_random_port") or 17879),
            int(self.config.get("goproxy_socks5_stable_port") or 17880),
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
