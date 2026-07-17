# -*- coding: utf-8 -*-
"""Branch web panel server: glass UI + local APIs for monitor/proxy/credentials/logs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import json
import mimetypes
import os
import threading
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse, urljoin
from urllib.request import Request, urlopen
import urllib.error

from panel.credentials import (
    build_credentials_zip,
    delete_all_uploaded,
    delete_credentials,
    list_credentials,
    prune_local_credentials,
)
from panel.goproxy_manager import get_manager
from panel.log_cleanup import cleanup_logs, loop_status as log_cleanup_status, start_log_cleanup_loop, stop_log_cleanup_loop
from panel.local_cred_retain import (
    run_once as local_cred_retain_once,
    start_local_cred_retain_loop,
    status as local_cred_retain_status,
    stop_local_cred_retain_loop,
)
from panel.remote_live import (
    run_remote_live_patrol as remote_live_tick,
    start_remote_live_loop,
    status as remote_live_status,
    stop_remote_live_loop,
)
from panel.pool_autoreg import (
    compute_register_need,
    evaluate_and_maybe_trigger as pool_autoreg_tick,
    pool_counts,
    start_manual_registration,
    start_pool_autoreg_loop,
    status as pool_autoreg_status,
    stop_pool_autoreg_loop,
)
from panel.settings import (
    GOPROXY_ENDPOINTS,
    GOPROXY_MODE_LABELS,
    GOPROXY_POOL_MODES,
    apply_local_proxy_bindings,
    describe_proxy_selection,
    normalize_branch_config,
    normalize_endpoint,
    normalize_pool_mode,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _config_path(root: Path | None = None) -> Path:
    """Prefer config/config.json; keep legacy root config.json fallback."""
    root = root or _project_root()
    try:
        import grok_register_ttk as app

        return Path(app.resolve_config_file(root=str(root)))
    except Exception:
        pass
    env_file = (os.environ.get("GROK_CONFIG_FILE") or "").strip()
    if env_file:
        p = Path(env_file)
        return p if p.is_absolute() else (root / p)
    env_dir = (os.environ.get("GROK_CONFIG_DIR") or "").strip()
    if env_dir:
        d = Path(env_dir)
        base = d if d.is_absolute() else (root / d)
        return base / "config.json"
    folder = root / "config" / "config.json"
    legacy = root / "config.json"
    if folder.is_file():
        return folder
    if legacy.is_file():
        return legacy
    return folder


def _load_config() -> dict:
    root = _project_root()
    cfg_path = _config_path(root)
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
    path = _config_path(root)
    # keep as plain json; do not drop unknown keys
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    try:
        import grok_register_ttk as app

        app.CONFIG_FILE = str(path)
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
        self._lock = threading.RLock()

    def reload(self):
        with self._lock:
            self.config = _load_config()
            self.manager.set_config(self.config)
            return self.config


STATE = PanelState()

GOPROXY_UI_PREFIX = "/goproxy"


def _goproxy_webui_base() -> str:
    cfg = STATE.config or {}
    host = str(cfg.get("goproxy_host") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(cfg.get("goproxy_webui_port") or 17878)
    except Exception:
        port = 17878
    return f"http://{host}:{port}"


def _rewrite_goproxy_location(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    if raw.startswith(GOPROXY_UI_PREFIX):
        return raw
    if raw.startswith("/"):
        return GOPROXY_UI_PREFIX + raw
    # absolute url pointing to local webui
    try:
        u = urlparse(raw)
        base = urlparse(_goproxy_webui_base())
        if u.netloc and (u.hostname in (base.hostname, "127.0.0.1", "localhost")):
            path = u.path or "/"
            if not path.startswith(GOPROXY_UI_PREFIX):
                path = GOPROXY_UI_PREFIX + path
            q = f"?{u.query}" if u.query else ""
            f = f"#{u.fragment}" if u.fragment else ""
            return path + q + f
    except Exception:
        pass
    return raw


def _rewrite_set_cookie(value: str) -> str:
    """Keep GoProxy session cookie scoped under /goproxy to avoid root hijack."""
    parts = [p.strip() for p in str(value or "").split(";") if p.strip()]
    if not parts:
        return value
    out = [parts[0]]
    saw_path = False
    for part in parts[1:]:
        low = part.lower()
        if low.startswith("path="):
            saw_path = True
            out.append("Path=" + GOPROXY_UI_PREFIX + "/")
        else:
            out.append(part)
    if not saw_path:
        out.append("Path=" + GOPROXY_UI_PREFIX + "/")
    return "; ".join(out)


def _rewrite_goproxy_html(body: bytes) -> bytes:
    """Rewrite absolute paths in GoProxy WebUI HTML/JS so it works under /goproxy."""
    try:
        text = body.decode("utf-8")
    except Exception:
        try:
            text = body.decode("latin-1")
        except Exception:
            return body

    prefix = GOPROXY_UI_PREFIX

    # form/actions/links/redirects
    repls = [
        (r'action="/login"', f'action="{prefix}/login"'),
        (r"action='/login'", f"action='{prefix}/login'"),
        (r'href="/login"', f'href="{prefix}/login"'),
        (r"href='/login'", f"href='{prefix}/login'"),
        (r'href="/logout"', f'href="{prefix}/logout"'),
        (r"href='/logout'", f"href='{prefix}/logout'"),
        (r'href="/"', f'href="{prefix}/"'),
        (r"href='/'", f"href='{prefix}/'"),
        (r"location\.href\s*=\s*'/login'", f"location.href = '{prefix}/login'"),
        (r'location\.href\s*=\s*"/login"', f'location.href = "{prefix}/login"'),
        (r"location\.href\s*=\s*'/'", f"location.href = '{prefix}/'"),
        (r'location\.href\s*=\s*"/"', f'location.href = "{prefix}/"'),
        # fetch/api absolute paths used by dashboard
        (r"fetch\('/api/", f"fetch('{prefix}/api/"),
        (r'fetch\("/api/', f'fetch("{prefix}/api/'),
        (r"api\('/api/", f"api('{prefix}/api/"),
        (r'api\("/api/', f'api("{prefix}/api/'),
        (r"await api\('/api/", f"await api('{prefix}/api/"),
        (r'await api\("/api/', f'await api("{prefix}/api/'),
        (r"'/api/", f"'{prefix}/api/"),
        (r'"/api/', f'"{prefix}/api/'),
    ]
    for a, b in repls:
        text = re.sub(a, b, text)

    # inject base-path fetch wrapper as early as possible
    inject = (
        "<script>(function(){var P='" + prefix + "';"
        "if(window.__goproxyPrefixPatched)return;window.__goproxyPrefixPatched=1;"
        "var of=window.fetch;window.fetch=function(i,n){"
        "try{if(typeof i==='string'&&i.charAt(0)==='/'&&i.indexOf(P+'/')!==0&&i!==P){i=P+i;}"
        "else if(i&&typeof Request!=='undefined'&&i instanceof Request){"
        "var u=i.url;try{u=new URL(u,location.origin).pathname+new URL(u,location.origin).search+new URL(u,location.origin).hash;}catch(e){}"
        "if(typeof u==='string'&&u.charAt(0)==='/'&&u.indexOf(P+'/')!==0&&u!==P){"
        "i=new Request(P+u,i);}}}catch(e){}"
        "return of.call(this,i,n);};"
        "var oa=window.XMLHttpRequest&&XMLHttpRequest.prototype.open;"
        "if(oa){XMLHttpRequest.prototype.open=function(m,u){"
        "try{if(typeof u==='string'&&u.charAt(0)==='/'&&u.indexOf(P+'/')!==0&&u!==P){u=P+u;}}catch(e){}"
        "return oa.apply(this,[m,u].concat([].slice.call(arguments,2)));};}"
        "})();</script>"
    )
    low = text.lower()
    idx = low.find("<head>")
    if idx >= 0:
        ins = idx + len("<head>")
        text = text[:ins] + inject + text[ins:]
    else:
        text = inject + text

    return text.encode("utf-8")




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


PANEL_COOKIE_NAME = "grok_panel_session"
PANEL_PASSWORD_ENVS = (
    "GROK_PANEL_PASSWORD",
    "PANEL_PASSWORD",
    "PANEL_TOKEN",
    "GROK_PANEL_TOKEN",
)


def _panel_password() -> str:
    """Password from environment first; fallback to config panel_token for compatibility."""
    for key in PANEL_PASSWORD_ENVS:
        val = str(os.environ.get(key) or "").strip()
        if val:
            return val
    return str(STATE.config.get("panel_token") or "").strip()


def _auth_required() -> bool:
    return bool(_panel_password())


def _session_secret() -> str:
    # derive stable-ish secret from password so restarts keep cookies valid for same password
    pwd = _panel_password() or "dev"
    return hashlib.sha256(("grok-panel-session|" + pwd).encode("utf-8")).hexdigest()


def _make_session_value(password: str) -> str:
    raw = hmac.new(_session_secret().encode("utf-8"), password.encode("utf-8"), hashlib.sha256).hexdigest()
    return raw


def _expected_session() -> str:
    pwd = _panel_password()
    if not pwd:
        return ""
    return _make_session_value(pwd)


def _parse_cookies(handler: BaseHTTPRequestHandler) -> dict:
    raw = handler.headers.get("Cookie") or ""
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return {}
    out = {}
    for k, morsel in jar.items():
        out[k] = morsel.value
    return out


def _check_token(handler: BaseHTTPRequestHandler) -> bool:
    """Authenticate via HttpOnly cookie (preferred), header/query fallback."""
    if not _auth_required():
        return True
    expected = _expected_session()
    cookies = _parse_cookies(handler)
    got_cookie = str(cookies.get(PANEL_COOKIE_NAME) or "").strip()
    if got_cookie and hmac.compare_digest(got_cookie, expected):
        return True
    # backward compatible fallbacks
    got = handler.headers.get("X-Panel-Token") or ""
    q = parse_qs(urlparse(handler.path).query)
    if not got:
        got = (q.get("token") or [""])[0]
    pwd = _panel_password()
    if got and pwd and hmac.compare_digest(str(got).strip(), pwd):
        return True
    # also accept old style where client stored password as session equal value
    if got and expected and hmac.compare_digest(str(got).strip(), expected):
        return True
    return False


def _set_auth_cookie(handler: BaseHTTPRequestHandler, value: str, *, clear: bool = False):
    # host-only cookie for local panel
    max_age = 0 if clear else 60 * 60 * 24 * 14  # 14 days
    parts = [
        f"{PANEL_COOKIE_NAME}={value if not clear else ''}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if clear:
        parts.append("Max-Age=0")
    else:
        parts.append(f"Max-Age={max_age}")
    handler.send_header("Set-Cookie", "; ".join(parts))


STATIC_DIR = Path(__file__).resolve().parent / "static"


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "GrokPanel/1.0"

    def log_message(self, fmt, *args):
        # quieter default
        return

    def _unauthorized(self):
        _json_response(self, 401, {"ok": False, "error": "unauthorized"})

    def _api_login(self, body: dict):
        if not _auth_required():
            # no password configured: always ok, clear any cookie
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            _set_auth_cookie(self, "", clear=True)
            raw = json.dumps({"ok": True, "authenticated": True, "auth_required": False}, ensure_ascii=False).encode("utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        password = str((body or {}).get("password") or (body or {}).get("token") or "").strip()
        expected = _panel_password()
        if not password or not hmac.compare_digest(password, expected):
            return _json_response(self, 401, {"ok": False, "error": "密码错误"})
        session = _make_session_value(expected)
        payload = {"ok": True, "authenticated": True, "auth_required": True, "auth_mode": "cookie"}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        _set_auth_cookie(self, session, clear=False)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _api_logout(self):
        payload = {"ok": True, "authenticated": False}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        _set_auth_cookie(self, "", clear=True)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        # GoProxy WebUI reverse proxy under same origin path (no extra public port).
        if path == GOPROXY_UI_PREFIX or path.startswith(GOPROXY_UI_PREFIX + "/"):
            if _auth_required() and not _check_token(self):
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            return self._proxy_goproxy(parsed)
        # static assets always public (login page needs css/js)
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/") :])
        if path in ("/login", "/login.html"):
            # already logged in -> home
            if _check_token(self):
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            return self._serve_static("login.html")
        if path in ("/", "/index.html"):
            if _auth_required() and not _check_token(self):
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            return self._serve_static("index.html")
        if path.startswith("/api/"):
            # public endpoints for login page
            if path == "/api/auth/status":
                required = _auth_required()
                return _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "auth_required": required,
                        "token_required": required,  # backward compatible
                        "authenticated": (not required) or _check_token(self),
                        "auth_mode": "cookie",
                        "password_env": "GROK_PANEL_PASSWORD",
                    },
                )
            if path == "/api/auth/check":
                if not _check_token(self):
                    return self._unauthorized()
                return _json_response(self, 200, {"ok": True, "authenticated": True})
            if not _check_token(self):
                return self._unauthorized()
            if path == "/api/stream":
                return self._sse_stream()
            return self._api_get(path, parse_qs(parsed.query))
        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == GOPROXY_UI_PREFIX or path.startswith(GOPROXY_UI_PREFIX + "/"):
            if _auth_required() and not _check_token(self):
                return self._unauthorized()
            return self._proxy_goproxy(parsed)
        if not path.startswith("/api/"):
            _json_response(self, 404, {"ok": False, "error": "not found"})
            return
        body = _read_json(self)
        # public auth endpoints
        if path == "/api/auth/login":
            return self._api_login(body)
        if path == "/api/auth/logout":
            return self._api_logout()
        if not _check_token(self):
            return self._unauthorized()
        try:
            self._api_post(path, body)
        except Exception as exc:
            _json_response(
                self,
                500,
                {"ok": False, "error": str(exc), "trace": traceback.format_exc()[-1000:]},
            )

    def do_PUT(self):
        return self.do_POST()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == GOPROXY_UI_PREFIX or path.startswith(GOPROXY_UI_PREFIX + "/"):
            if _auth_required() and not _check_token(self):
                return self._unauthorized()
            return self._proxy_goproxy(parsed)
        return _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_PATCH(self):
        return self.do_POST()

    def _overview_payload(self) -> dict:
        cfg = STATE.reload()
        g = STATE.manager.status()
        if isinstance(g, dict):
            g = dict(g)
            g["webui_path"] = GOPROXY_UI_PREFIX + "/"
            g["webui_proxy"] = True
        pool = pool_counts(cfg, root=str(STATE.root))
        try:
            pool_need = compute_register_need(
                current_total=int(pool.get("total") or 0) if pool.get("ok") else 0,
                min_count=int(cfg.get("pool_autoreg_min_count") or 5),
                target_count=int(
                    cfg.get("pool_autoreg_target_count")
                    if cfg.get("pool_autoreg_target_count") is not None
                    else (cfg.get("pool_autoreg_min_count") or 5)
                ),
                batch=0,
            )
        except Exception:
            pool_need = {
                "deficit": 0,
                "target_count": int(cfg.get("pool_autoreg_target_count") or cfg.get("pool_autoreg_min_count") or 0),
                "current_total": int((pool or {}).get("total") or 0),
                "should_register": False,
            }
        pool = {**(pool or {}), "need": pool_need}
        creds = list_credentials(cfg, buckets=("uploaded", "pending"), root=STATE.root)
        return {
            "ok": True,
            "ts": time.time(),
            "proxy": describe_proxy_selection(cfg),
            "goproxy": g,
            "credentials": {
                "counts": creds.get("counts"),
                "total": creds.get("total"),
            },
            "log_cleanup": STATE.last_log_cleanup,
            "log_cleanup_loop": log_cleanup_status(),
            "pool": pool,
            "pool_need": pool_need,
            "pool_autoreg": pool_autoreg_status(),
            "remote_live": remote_live_status(),
            "local_cred_retain": local_cred_retain_status(),
            "config": {
                "panel_host": cfg.get("panel_host"),
                "panel_port": cfg.get("panel_port"),
                "proxy": cfg.get("proxy"),
                "email_provider": cfg.get("email_provider"),
                "register_count": cfg.get("register_count"),
                "concurrent_count": cfg.get("concurrent_count"),
                "enable_nsfw": cfg.get("enable_nsfw"),
                "browser_restart_every": cfg.get("browser_restart_every"),
                "log_level": cfg.get("log_level"),
                "duckmail_api_key": cfg.get("duckmail_api_key"),
                "freemail_api_base": cfg.get("freemail_api_base"),
                "freemail_jwt_token": cfg.get("freemail_jwt_token"),
                "freemail_domain": cfg.get("freemail_domain"),
                "icloud_hme_api_base": cfg.get("icloud_hme_api_base"),
                "icloud_hme_account_id": cfg.get("icloud_hme_account_id"),
                "icloud_hme_label": cfg.get("icloud_hme_label"),
                "cloudflare_api_base": cfg.get("cloudflare_api_base"),
                "cloudflare_api_key": cfg.get("cloudflare_api_key"),
                "cloudflare_auth_mode": cfg.get("cloudflare_auth_mode"),
                "cpa_export_enabled": cfg.get("cpa_export_enabled"),
                "cpa_mint_async": cfg.get("cpa_mint_async"),
                "cpa_proxy": cfg.get("cpa_proxy"),
                "cpa_remote_enabled": cfg.get("cpa_remote_enabled"),
                "cpa_remote_base": cfg.get("cpa_remote_base"),
                "cpa_remote_management_key": cfg.get("cpa_remote_management_key"),
                "goproxy_pool_mode": cfg.get("goproxy_pool_mode"),
                "goproxy_endpoint": cfg.get("goproxy_endpoint"),
                "goproxy_bind_register_proxy": cfg.get("goproxy_bind_register_proxy"),
                "goproxy_bind_cpa_proxy": cfg.get("goproxy_bind_cpa_proxy"),
                "proxy_mode": cfg.get("proxy_mode", "custom"),
                "goproxy_enabled": cfg.get("goproxy_enabled"),
                "goproxy_auto_start": cfg.get("goproxy_auto_start"),
                "goproxy_webui_password": cfg.get("goproxy_webui_password"),
                "live_inspect_enabled": cfg.get("live_inspect_enabled", True),
                "success_require_live": cfg.get("success_require_live", True),
                "pool_autoreg_enabled": cfg.get("pool_autoreg_enabled", False),
                "pool_autoreg_source": cfg.get("pool_autoreg_source", "remote"),
                "pool_autoreg_min_count": cfg.get("pool_autoreg_min_count", 5),
                "pool_autoreg_target_count": cfg.get("pool_autoreg_target_count", cfg.get("pool_autoreg_min_count", 5)),
                "pool_autoreg_batch": cfg.get("pool_autoreg_batch", 3),
                "pool_autoreg_interval_sec": cfg.get("pool_autoreg_interval_sec", 300),
                "remote_live_enabled": cfg.get("remote_live_enabled", False),
                "remote_live_interval_sec": cfg.get("remote_live_interval_sec", 7200),
                "remote_live_delete_on_fail": cfg.get("remote_live_delete_on_fail", True),
                "remote_live_model": cfg.get("remote_live_model", "grok-4.5"),
                "remote_live_max_files": cfg.get("remote_live_max_files", 0),
                "remote_live_batch": cfg.get("remote_live_batch", 6),
                "remote_live_proxy": cfg.get("remote_live_proxy", ""),
                "log_cleanup_enabled": cfg.get("log_cleanup_enabled", True),
                "log_retain_days": cfg.get("log_retain_days"),
                "local_cred_retain_enabled": cfg.get("local_cred_retain_enabled", False),
                "local_cred_retain_count": cfg.get("local_cred_retain_count", 100),
                "local_cred_retain_interval_sec": cfg.get("local_cred_retain_interval_sec", 600),
                "log_max_total_mb": cfg.get("log_max_total_mb"),
            },
            "modes": [
                {"id": m, "label": GOPROXY_MODE_LABELS.get(m, m)} for m in GOPROXY_POOL_MODES
            ],
            "endpoints": [
                {
                    "id": k,
                    "label": v.get("label", k),
                    "scheme": v.get("scheme"),
                    "port": cfg.get(v.get("port_key"), v.get("default_port")),
                }
                for k, v in GOPROXY_ENDPOINTS.items()
            ],
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


    def _proxy_goproxy(self, parsed):
        """Reverse-proxy GoProxy WebUI under /goproxy without exposing extra ports."""
        prefix = GOPROXY_UI_PREFIX
        raw_path = parsed.path or "/"
        if raw_path == prefix:
            # normalize /goproxy -> /goproxy/
            self.send_response(302)
            self.send_header("Location", prefix + "/")
            self.end_headers()
            return

        rel = raw_path[len(prefix):] or "/"
        if not rel.startswith("/"):
            rel = "/" + rel
        query = parsed.query
        target = _goproxy_webui_base().rstrip("/") + rel
        if query:
            target = target + "?" + query

        # read body if present
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        body = self.rfile.read(length) if length > 0 else None

        # forward selected headers
        hop_by_hop = {
            "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
            "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
        }
        headers = {}
        for k, v in self.headers.items():
            if k.lower() in hop_by_hop:
                continue
            # avoid leaking panel auth cookie name conflicts; still forward Cookie for goproxy session
            headers[k] = v
        headers["Host"] = urlparse(_goproxy_webui_base()).netloc
        headers["X-Forwarded-Prefix"] = prefix
        headers["X-Forwarded-Proto"] = "https" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else "http"
        headers["X-Forwarded-Host"] = self.headers.get("Host") or ""

        req = Request(target, data=body, headers=headers, method=self.command)
        try:
            with urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                status = getattr(resp, "status", 200) or 200
                resp_headers = dict(resp.headers.items())
        except urllib.error.HTTPError as exc:
            resp_body = exc.read() if hasattr(exc, "read") else b""
            status = int(getattr(exc, "code", 502) or 502)
            resp_headers = dict(exc.headers.items()) if getattr(exc, "headers", None) is not None else {}
        except Exception as exc:
            return _json_response(
                self,
                502,
                {
                    "ok": False,
                    "error": f"goproxy webui proxy failed: {exc}",
                    "target": target,
                    "hint": "请先在面板启动本地 GoProxy；管理页通过 /goproxy/ 同域访问，无需额外开放端口",
                },
            )

        ctype = str(resp_headers.get("Content-Type") or resp_headers.get("content-type") or "")
        if "text/html" in ctype.lower():
            resp_body = _rewrite_goproxy_html(resp_body)

        self.send_response(status)
        skip = {"content-length", "transfer-encoding", "connection", "content-encoding"}
        for k, v in resp_headers.items():
            lk = k.lower()
            if lk in skip:
                continue
            if lk == "location":
                self.send_header("Location", _rewrite_goproxy_location(v))
                continue
            if lk == "set-cookie":
                # may be multi; BaseHTTP one header at a time
                self.send_header("Set-Cookie", _rewrite_set_cookie(v))
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp_body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(resp_body)
        except Exception:
            pass

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
        if path == "/api/config":
            # return editable subset used by panel settings form
            keys = [
                "proxy","proxy_mode","email_provider","register_count","concurrent_count","enable_nsfw","browser_restart_every","log_level",
                "duckmail_api_key","freemail_api_base","freemail_jwt_token","freemail_domain",
                "icloud_hme_api_base","icloud_hme_account_id","icloud_hme_label",
                "cloudflare_api_base","cloudflare_api_key","cloudflare_auth_mode",
                "cpa_export_enabled","cpa_mint_async","cpa_proxy","cpa_remote_enabled","cpa_remote_base","cpa_remote_management_key",
                "goproxy_enabled","goproxy_auto_start","goproxy_pool_mode","goproxy_endpoint","goproxy_bind_register_proxy","goproxy_bind_cpa_proxy","goproxy_webui_password",
                "live_inspect_enabled","success_require_live",
                "pool_autoreg_enabled","pool_autoreg_source","pool_autoreg_min_count","pool_autoreg_target_count","pool_autoreg_batch","pool_autoreg_interval_sec","remote_live_enabled","remote_live_interval_sec","remote_live_delete_on_fail","remote_live_model","remote_live_max_files","remote_live_batch","remote_live_proxy",
                "log_cleanup_enabled","log_retain_days","log_max_total_mb","local_cred_retain_enabled","local_cred_retain_count","local_cred_retain_interval_sec","panel_token",
            ]
            data = {k: cfg.get(k) for k in keys}
            return _json_response(self, 200, {"ok": True, "config": data})

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
        if path == "/api/remote-live/check":
            res = remote_live_tick(cfg, root=str(STATE.root), force=bool(body.get("force", True)), trigger_autoreg=bool(body.get("trigger_autoreg", True)))
            return _json_response(self, 200, res)
        if path == "/api/register/start":
            # 手动注册：按 register_count 启动，与账号池补货分离
            count = body.get("count", body.get("register_count"))
            res = start_manual_registration(cfg, count=count)
            return _json_response(self, 200, res)
        if path == "/api/pool/check":
            # 默认只检查；refill/force 才触发补到目标数
            force = bool(body.get("force", False) or body.get("refill", False))
            res = pool_autoreg_tick(cfg, root=str(STATE.root), force=force)
            res["mode"] = "pool_refill" if force else "pool_check"
            return _json_response(self, 200, res)
        if path == "/api/pool/refill":
            res = pool_autoreg_tick(cfg, root=str(STATE.root), force=True)
            res["mode"] = "pool_refill"
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
        if path == "/api/credentials/prune":
            res = prune_local_credentials(
                cfg,
                root=STATE.root,
                keep=body.get("keep"),
                enabled=True if body.get("force") else None,
            )
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
                # proxy / goproxy
                "proxy",
                "goproxy_pool_mode",
                "goproxy_endpoint",
                "goproxy_bind_register_proxy",
                "goproxy_bind_cpa_proxy",
                "proxy_mode",
                "goproxy_enabled",
                "goproxy_auto_start",
                "goproxy_webui_password",
                # register core
                "email_provider",
                "register_count",
                "concurrent_count",
                "enable_nsfw",
                "browser_restart_every",
                "log_level",
                # mail providers
                "duckmail_api_key",
                "freemail_api_base",
                "freemail_jwt_token",
                "freemail_domain",
                "icloud_hme_api_base",
                "icloud_hme_account_id",
                "icloud_hme_label",
                "cloudflare_api_base",
                "cloudflare_api_key",
                "cloudflare_auth_mode",
                # cpa / live / pool
                "cpa_export_enabled",
                "cpa_mint_async",
                "cpa_proxy",
                "cpa_remote_enabled",
                "cpa_remote_base",
                "cpa_remote_management_key",
                "live_inspect_enabled",
                "success_require_live",
                "pool_autoreg_enabled",
                "pool_autoreg_min_count",
                "pool_autoreg_target_count",
                "pool_autoreg_batch",
                "pool_autoreg_interval_sec",
                # logs / panel
                "log_cleanup_enabled",
                "log_retain_days",
                "log_max_total_mb",
            }
            for k, v in body.items():
                if k in allowed:
                    cfg[k] = v
            cfg = normalize_branch_config(cfg)
            # proxy_mode is the single global switch
            if str(cfg.get("proxy_mode") or "custom").lower() == "goproxy":
                cfg["goproxy_bind_register_proxy"] = True
                cfg["goproxy_bind_cpa_proxy"] = True
                cfg = apply_local_proxy_bindings(cfg)
            else:
                cfg["goproxy_bind_register_proxy"] = False
                cfg["goproxy_bind_cpa_proxy"] = False
            _save_config(cfg)
            STATE.reload()
            return _json_response(self, 200, {"ok": True, "config": STATE.config})
        _json_response(self, 404, {"ok": False, "error": f"unknown api {path}"})


class PanelServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8787,
        start_log_cleanup: bool = True,
        start_pool_autoreg: bool = True,
    ):
        self.host = host
        self.port = int(port)
        self.start_log_cleanup = bool(start_log_cleanup)
        self.start_pool_autoreg = bool(start_pool_autoreg)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def _maybe_start_local_cred_retain(self):
        if not getattr(self, "start_local_cred_retain", True):
            return
        cfg = STATE.config or {}
        if not bool(cfg.get("local_cred_retain_enabled", False)):
            return

        def _cfg_provider():
            current = STATE.reload()
            out = dict(current)
            out["_project_root"] = str(STATE.root)
            return out

        start_local_cred_retain_loop(
            project_root=str(STATE.root),
            interval_sec=float(cfg.get("local_cred_retain_interval_sec") or 600),
            enabled=True,
            config_provider=_cfg_provider,
            run_immediately=True,
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

    def _maybe_start_remote_live(self):
        if not self.start_remote_live:
            return
        cfg = STATE.config or {}
        if not bool(cfg.get("remote_live_enabled", False)):
            return

        def _cfg_provider():
            current = STATE.reload()
            out = dict(current)
            out["_project_root"] = str(STATE.root)
            return out

        start_remote_live_loop(
            project_root=str(STATE.root),
            interval_sec=float(cfg.get("remote_live_interval_sec") or 7200),
            enabled=True,
            config_provider=_cfg_provider,
            run_immediately=False,
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
            self._maybe_start_log_cleanup()
        except Exception:
            pass
        try:
            self._maybe_start_local_cred_retain()
        except Exception:
            pass
        try:
            self._maybe_start_remote_live()
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
            stop_log_cleanup_loop()
        except Exception:
            pass
        try:
            stop_local_cred_retain_loop()
        except Exception:
            pass
        try:
            stop_remote_live_loop()
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
