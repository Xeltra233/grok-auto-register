#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud/Docker entrypoint.

Starts the web panel (not GUI). Registration can be triggered via the panel
or: python run_branch.py cli --start --count N
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)


def _ensure_config() -> Path:
    cfg = ROOT / "config.json"
    example = ROOT / "config.example.json"
    if not cfg.is_file() and example.is_file():
        cfg.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print("[main] created config.json from config.example.json", flush=True)
    return cfg


def _cloud_host_port() -> tuple[str, int]:
    host = (
        os.environ.get("PANEL_HOST")
        or os.environ.get("HOST")
        or "0.0.0.0"
    ).strip() or "0.0.0.0"
    port_raw = os.environ.get("PORT") or os.environ.get("PANEL_PORT") or "8787"
    try:
        port = int(port_raw)
    except Exception:
        port = 8787
    return host, port


def _patch_runtime_config(host: str, port: int) -> None:
    try:
        import json

        cfg_path = _ensure_config()
        data = {}
        if cfg_path.is_file():
            data = json.loads(cfg_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            data = {}

        data["panel_enabled"] = True
        data["panel_host"] = host
        data["panel_port"] = port
        data["panel_auto_open"] = False
        if os.environ.get("GOPROXY_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
            data["goproxy_enabled"] = True
            data["goproxy_auto_start"] = True
        else:
            data["goproxy_enabled"] = False
            data["goproxy_auto_start"] = False

        if "cpa_headless" not in data:
            data["cpa_headless"] = True
        if os.environ.get("CPA_HEADLESS", "").strip().lower() in ("1", "true", "yes", "on"):
            data["cpa_headless"] = True

        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"[main] warn: config patch failed: {exc}", flush=True)


def main() -> int:
    host, port = _cloud_host_port()
    _patch_runtime_config(host, port)

    if os.environ.get("GROK_PANEL_PASSWORD"):
        print("[main] GROK_PANEL_PASSWORD is set", flush=True)

    from panel.server import run_panel

    auto_goproxy = os.environ.get("GOPROXY_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    print(f"[main] starting panel host={host} port={port} goproxy={auto_goproxy}", flush=True)
    server = run_panel(host=host, port=port, auto_start_goproxy=auto_goproxy)
    print(f"[main] panel listening on {server.url}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[main] stopping...", flush=True)
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())