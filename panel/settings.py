# -*- coding: utf-8 -*-
"""Branch-panel configuration schema and helpers.

Keeps GoProxy ports/modes, web panel and log cleanup defaults
compatible with legacy flat config.json files.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, Optional


# GoProxy pool strategies (5 modes from upstream WebUI / env).
GOPROXY_POOL_MODES = (
    "mixed_equal",        # mixed + no source priority
    "mixed_custom_first", # mixed + subscription priority
    "mixed_free_first",   # mixed + free priority
    "custom_only",        # subscription only
    "free_only",          # free only
)

# Local proxy entrypoints: HTTP x2 + SOCKS5 x2.
GOPROXY_ENDPOINTS = {
    "http_random": {
        "label": "HTTP random",
        "scheme": "http",
        "port_key": "goproxy_http_random_port",
        "default_port": 7777,
        "upstream_env": "RANDOM_PORT",
    },
    "http_stable": {
        "label": "HTTP lowest latency",
        "scheme": "http",
        "port_key": "goproxy_http_stable_port",
        "default_port": 7776,
        "upstream_env": "STABLE_PORT",
    },
    "socks5_random": {
        "label": "SOCKS5 random",
        "scheme": "socks5",
        "port_key": "goproxy_socks5_random_port",
        "default_port": 7779,
        "upstream_env": "SOCKS5_RANDOM_PORT",
    },
    "socks5_stable": {
        "label": "SOCKS5 lowest latency",
        "scheme": "socks5",
        "port_key": "goproxy_socks5_stable_port",
        "default_port": 7780,
        "upstream_env": "SOCKS5_STABLE_PORT",
    },
}

# Defaults for the branch web panel + embedded GoProxy + maintenance jobs.
PANEL_DEFAULTS = {
    # Web panel (Python)
    "panel_enabled": True,
    "panel_host": "127.0.0.1",
    "panel_port": 8787,
    "panel_auto_open": False,
    "panel_token": "",
    # Embedded GoProxy process
    "goproxy_enabled": True,
    "goproxy_auto_start": True,
    "goproxy_source_dir": "third_party/goproxy",
    "goproxy_bin_path": "",  # empty => auto-detect third_party/goproxy/bin/proxygo[.exe]
    "goproxy_data_dir": "data/goproxy",
    "goproxy_workdir": "third_party/goproxy",
    "goproxy_webui_port": 7778,
    "goproxy_http_random_port": 7777,
    "goproxy_http_stable_port": 7776,
    "goproxy_socks5_random_port": 7779,
    "goproxy_socks5_stable_port": 7780,
    "goproxy_webui_password": "goproxy",
    "goproxy_proxy_auth_enabled": False,
    "goproxy_proxy_auth_username": "proxy",
    "goproxy_proxy_auth_password": "",
    "goproxy_blocked_countries": "CN",
    "goproxy_allowed_countries": "",
    # Pool mode: one of GOPROXY_POOL_MODES
    "goproxy_pool_mode": "mixed_equal",
    # Which local endpoint register/CPA should bind to
    "goproxy_endpoint": "http_random",
    # When true, rewrite runtime proxy/cpa_proxy from local endpoint selection (off by default for legacy configs)
    "goproxy_bind_register_proxy": False,
    "goproxy_bind_cpa_proxy": False,
    "goproxy_host": "127.0.0.1",
    # Browser monitor / zombie guard
    "browser_monitor_enabled": True,
    "browser_monitor_interval_sec": 15,
    "browser_zombie_cleanup_enabled": True,
    # Log auto cleanup
    "log_cleanup_enabled": True,
    "log_dir": "logs",
    "log_retain_days": 7,
    "log_max_total_mb": 512,
    "log_cleanup_interval_sec": 3600,
    "log_cleanup_globs": "*.log,*.err,live-*.log",
    # Live inspect gate (grok-inspection style)
    "live_inspect_enabled": True,
    "success_require_live": True,
    # Account-pool auto registration
    "pool_autoreg_enabled": False,
    "pool_autoreg_min_count": 5,
    "pool_autoreg_batch": 3,
    "pool_autoreg_interval_sec": 300,
}


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n", ""}:
        return False
    return default


def _as_int(value, default, minimum=None, maximum=None):
    try:
        num = int(value)
    except Exception:
        num = int(default)
    if minimum is not None:
        num = max(minimum, num)
    if maximum is not None:
        num = min(maximum, num)
    return num


def _as_str(value, default=""):
    if value is None:
        return default
    return str(value)


def normalize_pool_mode(value):
    raw = _as_str(value, "mixed_equal").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "mixed": "mixed_equal",
        "mixed_equal": "mixed_equal",
        "equal": "mixed_equal",
        "mixed_custom_first": "mixed_custom_first",
        "mixed_subscription_first": "mixed_custom_first",
        "subscription_first": "mixed_custom_first",
        "custom_first": "mixed_custom_first",
        "mixed_free_first": "mixed_free_first",
        "free_first": "mixed_free_first",
        "custom_only": "custom_only",
        "subscription_only": "custom_only",
        "free_only": "free_only",
    }
    mode = aliases.get(raw, raw)
    if mode not in GOPROXY_POOL_MODES:
        return "mixed_equal"
    return mode


def normalize_endpoint(value):
    raw = _as_str(value, "http_random").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "http": "http_random",
        "http_random": "http_random",
        "random_http": "http_random",
        "http_stable": "http_stable",
        "http_lowest": "http_stable",
        "http_lowest_latency": "http_stable",
        "stable_http": "http_stable",
        "socks": "socks5_random",
        "socks5": "socks5_random",
        "socks5_random": "socks5_random",
        "random_socks": "socks5_random",
        "socks5_stable": "socks5_stable",
        "socks5_lowest": "socks5_stable",
        "socks5_lowest_latency": "socks5_stable",
        "stable_socks": "socks5_stable",
    }
    endpoint = aliases.get(raw, raw)
    if endpoint not in GOPROXY_ENDPOINTS:
        return "http_random"
    return endpoint


def pool_mode_to_goproxy_env(mode):
    """Map branch pool mode to GoProxy CUSTOM_PROXY_MODE + priority flags."""
    mode = normalize_pool_mode(mode)
    if mode == "custom_only":
        return {
            "CUSTOM_PROXY_MODE": "custom_only",
            "CUSTOM_PRIORITY": "true",
            "CUSTOM_FREE_PRIORITY": "false",
        }
    if mode == "free_only":
        return {
            "CUSTOM_PROXY_MODE": "free_only",
            "CUSTOM_PRIORITY": "false",
            "CUSTOM_FREE_PRIORITY": "false",
        }
    if mode == "mixed_custom_first":
        return {
            "CUSTOM_PROXY_MODE": "mixed",
            "CUSTOM_PRIORITY": "true",
            "CUSTOM_FREE_PRIORITY": "false",
        }
    if mode == "mixed_free_first":
        return {
            "CUSTOM_PROXY_MODE": "mixed",
            "CUSTOM_PRIORITY": "false",
            "CUSTOM_FREE_PRIORITY": "true",
        }
    return {
        "CUSTOM_PROXY_MODE": "mixed",
        "CUSTOM_PRIORITY": "false",
        "CUSTOM_FREE_PRIORITY": "false",
    }


def endpoint_port(cfg, endpoint=None):
    endpoint = normalize_endpoint(endpoint if endpoint is not None else cfg.get("goproxy_endpoint"))
    meta = GOPROXY_ENDPOINTS[endpoint]
    return _as_int(cfg.get(meta["port_key"], meta["default_port"]), meta["default_port"], minimum=1, maximum=65535)


def resolve_local_proxy_url(cfg, endpoint=None):
    """Build local proxy URL for the selected HTTP/SOCKS endpoint."""
    endpoint = normalize_endpoint(endpoint if endpoint is not None else cfg.get("goproxy_endpoint"))
    meta = GOPROXY_ENDPOINTS[endpoint]
    host = _as_str(cfg.get("goproxy_host"), "127.0.0.1").strip() or "127.0.0.1"
    port = endpoint_port(cfg, endpoint)
    scheme = meta["scheme"]
    user = _as_str(cfg.get("goproxy_proxy_auth_username"), "").strip()
    password = _as_str(cfg.get("goproxy_proxy_auth_password"), "")
    auth_enabled = _as_bool(cfg.get("goproxy_proxy_auth_enabled"), False)
    if auth_enabled and user:
        return f"{scheme}://{user}:{password}@{host}:{port}"
    return f"{scheme}://{host}:{port}"


def apply_local_proxy_bindings(cfg):
    """Optionally rewrite proxy/cpa_proxy from local GoProxy endpoint selection."""
    out = dict(cfg)
    if not _as_bool(out.get("goproxy_enabled"), True):
        return out
    url = resolve_local_proxy_url(out)
    if _as_bool(out.get("goproxy_bind_register_proxy"), False):
        out["proxy"] = url
    if _as_bool(out.get("goproxy_bind_cpa_proxy"), False):
        out["cpa_proxy"] = url
    return out


def deep_merge_defaults(defaults, loaded):
    """Shallow-merge top-level keys; nested dict values merge one level for safety."""
    merged = copy.deepcopy(defaults)
    if not isinstance(loaded, dict):
        return merged
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            child = copy.deepcopy(merged[key])
            child.update(value)
            merged[key] = child
        else:
            merged[key] = value
    return merged


def normalize_branch_config(cfg):
    """Fill/coerce branch-panel related keys without dropping legacy fields."""
    out = deep_merge_defaults(PANEL_DEFAULTS, cfg if isinstance(cfg, dict) else {})

    out["panel_enabled"] = _as_bool(out.get("panel_enabled"), True)
    out["panel_host"] = _as_str(out.get("panel_host"), "127.0.0.1").strip() or "127.0.0.1"
    out["panel_port"] = _as_int(out.get("panel_port"), 8787, minimum=1, maximum=65535)
    out["panel_auto_open"] = _as_bool(out.get("panel_auto_open"), False)
    out["panel_token"] = _as_str(out.get("panel_token"), "")

    out["goproxy_enabled"] = _as_bool(out.get("goproxy_enabled"), True)
    out["goproxy_auto_start"] = _as_bool(out.get("goproxy_auto_start"), True)
    out["goproxy_source_dir"] = (
        _as_str(out.get("goproxy_source_dir"), "third_party/goproxy").strip() or "third_party/goproxy"
    )
    out["goproxy_bin_path"] = _as_str(out.get("goproxy_bin_path"), "").strip()
    out["goproxy_data_dir"] = _as_str(out.get("goproxy_data_dir"), "data/goproxy").strip() or "data/goproxy"
    out["goproxy_workdir"] = (
        _as_str(out.get("goproxy_workdir"), "third_party/goproxy").strip() or "third_party/goproxy"
    )
    out["goproxy_host"] = _as_str(out.get("goproxy_host"), "127.0.0.1").strip() or "127.0.0.1"
    for key, default in (
        ("goproxy_webui_port", 7778),
        ("goproxy_http_random_port", 7777),
        ("goproxy_http_stable_port", 7776),
        ("goproxy_socks5_random_port", 7779),
        ("goproxy_socks5_stable_port", 7780),
    ):
        out[key] = _as_int(out.get(key), default, minimum=1, maximum=65535)
    out["goproxy_webui_password"] = _as_str(out.get("goproxy_webui_password"), "goproxy")
    out["goproxy_proxy_auth_enabled"] = _as_bool(out.get("goproxy_proxy_auth_enabled"), False)
    out["goproxy_proxy_auth_username"] = _as_str(out.get("goproxy_proxy_auth_username"), "proxy")
    out["goproxy_proxy_auth_password"] = _as_str(out.get("goproxy_proxy_auth_password"), "")
    out["goproxy_blocked_countries"] = _as_str(out.get("goproxy_blocked_countries"), "CN")
    out["goproxy_allowed_countries"] = _as_str(out.get("goproxy_allowed_countries"), "")
    out["goproxy_pool_mode"] = normalize_pool_mode(out.get("goproxy_pool_mode"))
    out["goproxy_endpoint"] = normalize_endpoint(out.get("goproxy_endpoint"))
    out["goproxy_bind_register_proxy"] = _as_bool(out.get("goproxy_bind_register_proxy"), False)
    out["goproxy_bind_cpa_proxy"] = _as_bool(out.get("goproxy_bind_cpa_proxy"), False)

    out["browser_monitor_enabled"] = _as_bool(out.get("browser_monitor_enabled"), True)
    out["browser_monitor_interval_sec"] = _as_int(out.get("browser_monitor_interval_sec"), 15, minimum=3)
    out["browser_zombie_cleanup_enabled"] = _as_bool(out.get("browser_zombie_cleanup_enabled"), True)

    out["log_cleanup_enabled"] = _as_bool(out.get("log_cleanup_enabled"), True)
    out["log_dir"] = _as_str(out.get("log_dir"), "logs").strip() or "logs"
    out["log_retain_days"] = _as_int(out.get("log_retain_days"), 7, minimum=1)
    out["log_max_total_mb"] = _as_int(out.get("log_max_total_mb"), 512, minimum=1)
    out["log_cleanup_interval_sec"] = _as_int(out.get("log_cleanup_interval_sec"), 3600, minimum=60)
    out["log_cleanup_globs"] = _as_str(out.get("log_cleanup_globs"), "*.log,*.err,live-*.log")
    out["live_inspect_enabled"] = _as_bool(out.get("live_inspect_enabled"), True)
    out["success_require_live"] = _as_bool(out.get("success_require_live"), True)
    out["pool_autoreg_enabled"] = _as_bool(out.get("pool_autoreg_enabled"), False)
    out["pool_autoreg_min_count"] = _as_int(out.get("pool_autoreg_min_count"), 5, minimum=0)
    out["pool_autoreg_batch"] = _as_int(out.get("pool_autoreg_batch"), 3, minimum=1)
    out["pool_autoreg_interval_sec"] = _as_int(out.get("pool_autoreg_interval_sec"), 300, minimum=30)

    return out


def build_goproxy_env(cfg, base_env=None):
    """Environment variables for launching the embedded GoProxy process."""
    env = dict(os.environ if base_env is None else base_env)
    env.update(pool_mode_to_goproxy_env(cfg.get("goproxy_pool_mode")))
    env["WEBUI_PORT"] = str(_as_int(cfg.get("goproxy_webui_port"), 7778, minimum=1, maximum=65535))
    env["RANDOM_PORT"] = str(_as_int(cfg.get("goproxy_http_random_port"), 7777, minimum=1, maximum=65535))
    env["STABLE_PORT"] = str(_as_int(cfg.get("goproxy_http_stable_port"), 7776, minimum=1, maximum=65535))
    env["SOCKS5_RANDOM_PORT"] = str(
        _as_int(cfg.get("goproxy_socks5_random_port"), 7779, minimum=1, maximum=65535)
    )
    env["SOCKS5_STABLE_PORT"] = str(
        _as_int(cfg.get("goproxy_socks5_stable_port"), 7780, minimum=1, maximum=65535)
    )
    env["WEBUI_PASSWORD"] = _as_str(cfg.get("goproxy_webui_password"), "goproxy")
    env["PROXY_AUTH_ENABLED"] = "true" if _as_bool(cfg.get("goproxy_proxy_auth_enabled"), False) else "false"
    env["PROXY_AUTH_USERNAME"] = _as_str(cfg.get("goproxy_proxy_auth_username"), "proxy")
    env["PROXY_AUTH_PASSWORD"] = _as_str(cfg.get("goproxy_proxy_auth_password"), "")
    env["BLOCKED_COUNTRIES"] = _as_str(cfg.get("goproxy_blocked_countries"), "CN")
    env["ALLOWED_COUNTRIES"] = _as_str(cfg.get("goproxy_allowed_countries"), "")
    env["DATA_DIR"] = _as_str(cfg.get("goproxy_data_dir"), "data/goproxy")
    return env


def describe_proxy_selection(cfg):
    endpoint = normalize_endpoint(cfg.get("goproxy_endpoint"))
    mode = normalize_pool_mode(cfg.get("goproxy_pool_mode"))
    return {
        "endpoint": endpoint,
        "endpoint_label": GOPROXY_ENDPOINTS[endpoint]["label"],
        "pool_mode": mode,
        "proxy_url": resolve_local_proxy_url(cfg, endpoint),
        "ports": {
            "http_random": _as_int(cfg.get("goproxy_http_random_port"), 7777, minimum=1, maximum=65535),
            "http_stable": _as_int(cfg.get("goproxy_http_stable_port"), 7776, minimum=1, maximum=65535),
            "socks5_random": _as_int(cfg.get("goproxy_socks5_random_port"), 7779, minimum=1, maximum=65535),
            "socks5_stable": _as_int(cfg.get("goproxy_socks5_stable_port"), 7780, minimum=1, maximum=65535),
            "webui": _as_int(cfg.get("goproxy_webui_port"), 7778, minimum=1, maximum=65535),
        },
    }
