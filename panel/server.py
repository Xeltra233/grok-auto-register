# -*- coding: utf-8 -*-
"""Branch web panel server: glass UI + local APIs for monitor/proxy/credentials/logs."""

from __future__ import annotations

import base64
import time
import json
import mimetypes
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from panel.browser_monitor import cleanup_zombies, start_monitor_loop, stop_monitor_loop, summary as browser_summary
from panel.credentials import (
    build_credentials_zip,
    delete_all_uploaded,
    delete_credentials,
    list_credentials,
)
from panel.goproxy_manager import get_manager
from panel.log_cleanup import cleanup_logs, loop_status as log_cleanup_status, start_log_cleanup_loop, stop_log_cleanup_loop
from panel.pool_autoreg import (
    evaluate_and_maybe_trigger as pool_autoreg_tick,
    pool_counts,
    start_pool_autoreg_loop,
    status as pool_autoreg_status,
    stop_pool_autoreg_loop,
)
from panel.settings import (
    GOPROXY_ENDPOINTS,
    GOPROXY_POOL_MODES,
    apply_local_proxy_bindings,
    describe_proxy_selection,
    normalize_branch_config,
    normalize_endpoint,
    normalize_pool_mode,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_config() -> dict:
    root = _project_root()
    cfg_path = root / "config.json"
    data = {}
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    # merge defaults via normalize
    try:
        import grok_register_ttk as app

        merged = {**getattr(app, "DEFAULT_CONFIG", {}), **(data or {})}
    except Exception:
        merged = dict(data or {})
    return normalize_branch_config(merged)


def _save_config(cfg: dict) -> None:
    root = _project_root()
    path = root / "config.json"
    # keep as plain json; do not drop unknown keys
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    try:
        import grok_register_ttk as app

        app.config.clear()
        app.config.update(cfg)
    except Exception:
        pass


class PanelState:
    def __init__(self):
        self.root = _project_root()
        self.config = _load_config()
        self.manager = get_manager(self.config, root=str(self.root))
        self.last_log_cleanup = None
        self.last_browser_cleanup = None
        self._lock = threading.RLock()

    def reload(self):
        with self._lock:
            self.config = _load_config()
            self.manager.set_config(self.config)
            return self.config


STATE = PanelState()


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: Any):
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _check_token(handler: BaseHTTPRequestHandler) -> bool:
    token = str(STATE.config.get("panel_token") or "").strip()
    if not token:
        return True
    got = handler.headers.get("X-Panel-Token") or ""
    q = parse_qs(urlparse(handler.path).query)
    if not got:
        got = (q.get("token") or [""])[0]
    return got == token


STATIC_DIR = Path(__file__).resolve().parent / "static"


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "GrokPanel/1.0"

    def log_message(self, fmt, *args):
        # quieter default
        return

    def _unauthorized(self):
        _json_response(self, 401, {"ok": False, "error": "unauthorized"})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/") :])
        if path.startswith("/api/"):
            if not _check_token(self):
                return self._unauthorized()
            if path == "/api/stream":
                return self._sse_stream()
            return self._api_get(path, parse_qs(parsed.query))
        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/"):
            _json_response(self, 404, {"ok": False, "error": "not found"})
            return
        if not _check_token(self):
            return self._unauthorized()
        body = _read_json(self)
        try:
            self._api_post(path, body)
        except Exception as exc:
            _json_response(
                self,
                500,
                {"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1000:]},
            )

    def _overview_payload(self) -> dict:
        cfg = STATE.reload()
        g = STATE.manager.status()
        b = browser_summary(project_root=str(STATE.root))
        creds = list_credentials(cfg, buckets=("uploaded", "pending"), root=STATE.root)
        return {
            "ok": True,
            "ts": time.time(),
            "proxy": describe_proxy_selection(cfg),
            "goproxy": g,
            "browsers": b,
            "credentials": {
                "counts": creds.get("counts"),
                "total": creds.get("total"),
            },
            "log_cleanup": STATE.last_log_cleanup,
            "log_cleanup_loop": log_cleanup_status(),
            "browser_cleanup": STATE.last_browser_cleanup,
            "pool": pool_counts(cfg, root=str(STATE.root)),
            "pool_autoreg": pool_autoreg_status(),
            "config": {
                "panel_host": cfg.get("panel_host"),
                "panel_port": cfg.get("panel_port"),
                "goproxy_pool_mode": cfg.get("goproxy_pool_mode"),
                "goproxy_endpoint": cfg.get("goproxy_endpoint"),
                "goproxy_bind_register_proxy": cfg.get("goproxy_bind_register_proxy"),
                "live_inspect_enabled": cfg.get("live_inspect_enabled", True),
                "success_require_live": cfg.get("success_require_live", True),
                "pool_autoreg_enabled": cfg.get("pool_autoreg_enabled", False),
                "pool_autoreg_min_count": cfg.get("pool_autoreg_min_count", 5),
                "pool_autoreg_batch": cfg.get("pool_autoreg_batch", 3),
                "pool_autoreg_interval_sec": cfg.get("pool_autoreg_interval_sec", 300),
            },
            "modes": list(GOPROXY_POOL_MODES),
            "endpoints": list(GOPROXY_ENDPOINTS.keys()),
        }

    def _sse_stream(self):
        """Push backend overview snapshots so the UI stays live without manual refresh."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        # first event immediately
        try:
            interval = 2.0
            while True:
                payload = self._overview_payload()
                data = json.dumps(payload, ensure_ascii=False, default=str)
                chunk = f"event: overview\ndata: {data}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Exception:
            return

    def _serve_static(self, rel: str):

        rel = rel.replace("\\", "/").lstrip("/")
        if not rel:
            rel = "index.html"
        target = (STATIC_DIR / rel).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api_get(self, path: str, query: dict):
        cfg = STATE.reload()
        if path == "/api/health":
            return _json_response(self, 200, {"ok": True, "service": "panel"})
        if path == "/api/overview":
            return _json_response(self, 200, self._overview_payload())
        if path == "/api/browsers":
            return _json_response(self, 200, browser_summary(project_root=str(STATE.root)))
        if path == "/api/pool":
            counts = pool_counts(cfg, root=str(STATE.root))
            return _json_response(self, 200, {"ok": True, "counts": counts, "autoreg": pool_autoreg_status()})

        if path == "/api/goproxy/status":
            return _json_response(self, 200, STATE.manager.status())
        if path == "/api/credentials":
            buckets = query.get("buckets") or ["uploaded", "pending"]
            if isinstance(buckets, list) and buckets and "," in buckets[0]:
                buckets = [x.strip() for x in buckets[0].split(",") if x.strip()]
            data = list_credentials(cfg, buckets=buckets, root=STATE.root)
            return _json_response(self, 200, data)
        if path == "/api/credentials/download":
            # GET download via query names=a,b or all=1
            names = []
            if query.get("names"):
                raw = query.get("names")[0]
                names = [x.strip() for x in raw.split(",") if x.strip()]
            select_all = (query.get("all") or ["0"])[0] in ("1", "true", "yes")
            buckets = query.get("buckets") or ["uploaded"]
            if isinstance(buckets, list) and buckets and "," in str(buckets[0]):
                buckets = [x.strip() for x in str(buckets[0]).split(",") if x.strip()]
            z = build_credentials_zip(
                names=names or None,
                select_all=select_all,
                config=cfg,
                buckets=buckets,
                root=STATE.root,
            )
            data = z["content"]
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{z["filename"]}"'
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        _json_response(self, 404, {"ok": False, "error": f"unknown api {path}"})

    def _api_post(self, path: str, body: dict):
        cfg = STATE.reload()
        if path == "/api/pool/check":
            res = pool_autoreg_tick(cfg, root=str(STATE.root), force=bool(body.get("force", False)))
            return _json_response(self, 200, res)
        if path == "/api/browsers/cleanup":
            res = cleanup_zombies(project_root=str(STATE.root), kill=bool(body.get("kill", True)))
            STATE.last_browser_cleanup = {
                "ts": time.time(),
                "killed_count": res.get("killed_count"),
                "before_zombies": res.get("before_zombies"),
                "after_zombies": res.get("after_zombies"),
                "ok": res.get("ok"),
            }
            return _json_response(self, 200, res)
        if path == "/api/goproxy/start":
            return _json_response(self, 200, STATE.manager.start(build_if_missing=bool(body.get("build", True))))
        if path == "/api/goproxy/stop":
            return _json_response(self, 200, STATE.manager.stop())
        if path == "/api/goproxy/restart":
            return _json_response(self, 200, STATE.manager.restart(build_if_missing=bool(body.get("build", True))))
        if path == "/api/goproxy/mode":
            mode = normalize_pool_mode(body.get("mode") or body.get("goproxy_pool_mode"))
            res = STATE.manager.set_pool_mode(mode, restart_if_running=bool(body.get("restart", True)))
            cfg["goproxy_pool_mode"] = mode
            _save_config(cfg)
            STATE.reload()
            return _json_response(self, 200, res)
        if path == "/api/goproxy/endpoint":
            endpoint = normalize_endpoint(body.get("endpoint") or body.get("goproxy_endpoint"))
            bind = body.get("bind")
            res = STATE.manager.set_endpoint(endpoint, bind=bind if bind is None else bool(bind))
            cfg["goproxy_endpoint"] = endpoint
            if bind is not None:
                cfg["goproxy_bind_register_proxy"] = bool(bind)
                cfg["goproxy_bind_cpa_proxy"] = bool(bind)
            if bool(cfg.get("goproxy_bind_register_proxy")):
                cfg = apply_local_proxy_bindings(cfg)
            _save_config(cfg)
            STATE.reload()
            return _json_response(self, 200, {**res, "selection": describe_proxy_selection(STATE.config)})
        if path == "/api/logs/cleanup":
            res = cleanup_logs(
                log_dir=str(cfg.get("log_dir") or "logs"),
                retain_days=int(cfg.get("log_retain_days") or 7),
                max_total_mb=int(cfg.get("log_max_total_mb") or 512),
                globs=cfg.get("log_cleanup_globs"),
                root=str(STATE.root),
            )
            STATE.last_log_cleanup = res
            return _json_response(self, 200, res)
        if path == "/api/credentials/delete":
            if body.get("all"):
                res = delete_all_uploaded(cfg, root=STATE.root)
            else:
                res = delete_credentials(
                    body.get("names") or [],
                    config=cfg,
                    bucket=str(body.get("bucket") or "uploaded"),
                    root=STATE.root,
                )
            return _json_response(self, 200, res)
        if path == "/api/credentials/download":
            z = build_credentials_zip(
                names=body.get("names"),
                select_all=bool(body.get("all")),
                config=cfg,
                buckets=body.get("buckets") or ("uploaded",),
                root=STATE.root,
            )
            # return base64 for XHR convenience
            return _json_response(
                self,
                200,
                {
                    "ok": True,
                    "filename": z["filename"],
                    "count": z["count"],
                    "names": z["names"],
                    "size": z["size"],
                    "base64": base64.b64encode(z["content"]).decode("ascii"),
                },
            )
        if path == "/api/config":
            # shallow update selected keys only
            allowed = {
                "goproxy_pool_mode",
                "goproxy_endpoint",
                "goproxy_bind_register_proxy",
                "goproxy_bind_cpa_proxy",
                "goproxy_enabled",
                "goproxy_auto_start",
                "live_inspect_enabled",
                "success_require_live",
                "log_retain_days",
                "log_max_total_mb",
                "panel_token",
            }
            for k, v in body.items():
                if k in allowed:
                    cfg[k] = v
            cfg = normalize_branch_config(cfg)
            if cfg.get("goproxy_bind_register_proxy"):
                cfg = apply_local_proxy_bindings(cfg)
            _save_config(cfg)
            STATE.reload()
            return _json_response(self, 200, {"ok": True, "config": STATE.config})
        _json_response(self, 404, {"ok": False, "error": f"unknown api {path}"})


class PanelServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8787,
        start_browser_monitor: bool = True,
        start_log_cleanup: bool = True,
        start_pool_autoreg: bool = True,
    ):
        self.host = host
        self.port = int(port)
        self.start_browser_monitor = bool(start_browser_monitor)
        self.start_log_cleanup = bool(start_log_cleanup)
        self.start_pool_autoreg = bool(start_pool_autoreg)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _maybe_start_browser_monitor(self):
        if not self.start_browser_monitor:
            return
        cfg = STATE.config or {}
        if not bool(cfg.get("browser_monitor_enabled", True)):
            return

        def _cfg_provider():
            current = STATE.reload()
            out = dict(current)
            out["_project_root"] = str(STATE.root)
            return out

        start_monitor_loop(
            project_root=str(STATE.root),
            interval_sec=float(cfg.get("browser_monitor_interval_sec") or 15),
            enabled=True,
            cleanup_enabled=bool(cfg.get("browser_zombie_cleanup_enabled", True)),
            config_provider=_cfg_provider,
        )

    def _maybe_start_log_cleanup(self):
        if not self.start_log_cleanup:
            return
        cfg = STATE.config or {}
        if not bool(cfg.get("log_cleanup_enabled", True)):
            return

        def _cfg_provider():
            current = STATE.reload()
            out = dict(current)
            out["_project_root"] = str(STATE.root)
            return out

        def _on_result(res: dict):
            STATE.last_log_cleanup = res

        start_log_cleanup_loop(
            project_root=str(STATE.root),
            interval_sec=float(cfg.get("log_cleanup_interval_sec") or 3600),
            enabled=True,
            config_provider=_cfg_provider,
            on_result=_on_result,
            run_immediately=True,
        )

    def _maybe_start_pool_autoreg(self):
        if not self.start_pool_autoreg:
            return
        cfg = STATE.config or {}
        if not bool(cfg.get("pool_autoreg_enabled", False)):
            return

        def _cfg_provider():
            current = STATE.reload()
            out = dict(current)
            out["_project_root"] = str(STATE.root)
            return out

        start_pool_autoreg_loop(
            project_root=str(STATE.root),
            interval_sec=float(cfg.get("pool_autoreg_interval_sec") or 300),
            enabled=True,
            config_provider=_cfg_provider,
            run_immediately=True,
        )


    def start(self):
        if self._httpd is not None:
            return
        STATE.reload()
        self._httpd = ThreadingHTTPServer((self.host, self.port), PanelHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="panel-http", daemon=True)
        self._thread.start()
        try:
            self._maybe_start_browser_monitor()
        except Exception:
            pass
        try:
            self._maybe_start_log_cleanup()
        except Exception:
            pass
        try:
            self._maybe_start_pool_autoreg()
        except Exception:
            pass

    def stop(self):
        if self._httpd is None:
            return
        try:
            stop_monitor_loop()
        except Exception:
            pass
        try:
            stop_log_cleanup_loop()
        except Exception:
            pass
        try:
            stop_pool_autoreg_loop()
        except Exception:
            pass
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"


def run_panel(host: Optional[str] = None, port: Optional[int] = None, auto_start_goproxy: Optional[bool] = None):
    cfg = STATE.reload()
    host = host or str(cfg.get("panel_host") or "127.0.0.1")
    port = int(port or cfg.get("panel_port") or 8787)
    server = PanelServer(host=host, port=port)
    if auto_start_goproxy is None:
        auto_start_goproxy = bool(cfg.get("goproxy_enabled", True)) and bool(cfg.get("goproxy_auto_start", True))
    if auto_start_goproxy:
        try:
            STATE.manager.start(build_if_missing=True, wait_sec=3)
        except Exception:
            pass
    server.start()
    return server


def main():
    import argparse
    import time as _time

    parser = argparse.ArgumentParser(description="Grok branch web panel")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-goproxy", action="store_true")
    args = parser.parse_args()
    server = run_panel(host=args.host, port=args.port, auto_start_goproxy=(not args.no_goproxy))
    print(f"[panel] listening on {server.url}")
    try:
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        print("[panel] stopping...")
        server.stop()


if __name__ == "__main__":
    main()
