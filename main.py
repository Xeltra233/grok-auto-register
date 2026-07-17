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


def _config_candidates() -> list[Path]:
    env_file = (os.environ.get("GROK_CONFIG_FILE") or "").strip()
    if env_file:
        p = Path(env_file)
        return [p if p.is_absolute() else (ROOT / p)]
    env_dir = (os.environ.get("GROK_CONFIG_DIR") or "").strip()
    if env_dir:
        d = Path(env_dir)
        base = d if d.is_absolute() else (ROOT / d)
        return [base / "config.json"]
    return [ROOT / "config" / "config.json", ROOT / "config.json"]


def _example_candidates() -> list[Path]:
    return [
        ROOT / "config" / "config.example.json",
        ROOT / "config.example.json",
    ]


def _ensure_config() -> Path:
    """Ensure a writable config file under config/ by default."""
    for path in _config_candidates():
        if path.is_file():
            return path

    example = next((p for p in _example_candidates() if p.is_file()), None)
    target = _config_candidates()[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    if example is not None:
        target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[main] created {target} from {example.name}", flush=True)
    else:
        target.write_text("{}\n", encoding="utf-8")
        print(f"[main] created empty {target}", flush=True)
    return target


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


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on")


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

        # Default: enable GoProxy only when explicitly requested.
        # Image ships a prebuilt Linux binary under third_party/goproxy/bin/proxygo.
        if _truthy(os.environ.get("GOPROXY_ENABLED")):
            data["goproxy_enabled"] = True
            data["goproxy_auto_start"] = True
            bin_path = ROOT / "third_party" / "goproxy" / "bin" / "proxygo"
            if bin_path.is_file():
                data.setdefault("goproxy_bin_path", str(bin_path))
        else:
            data["goproxy_enabled"] = False
            data["goproxy_auto_start"] = False

        if "cpa_headless" not in data:
            data["cpa_headless"] = True
        if _truthy(os.environ.get("CPA_HEADLESS")):
            data["cpa_headless"] = True

        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.environ.setdefault("GROK_CONFIG_FILE", str(cfg_path))
    except Exception as exc:
        print(f"[main] warn: config patch failed: {exc}", flush=True)


def main() -> int:
    host, port = _cloud_host_port()
    _patch_runtime_config(host, port)

    if os.environ.get("GROK_PANEL_PASSWORD"):
        print("[main] GROK_PANEL_PASSWORD is set", flush=True)

    from panel.server import run_panel

    auto_goproxy = _truthy(os.environ.get("GOPROXY_ENABLED"))
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
