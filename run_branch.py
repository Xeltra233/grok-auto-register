# -*- coding: utf-8 -*-
"""Unified entry for the web-panel + GoProxy branch.

Examples:
  python run_branch.py panel
  python run_branch.py panel --no-goproxy
  python run_branch.py gui
  python run_branch.py cli
  python run_branch.py status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_cfg() -> dict:
    import grok_register_ttk as app

    return app.load_config()


def cmd_panel(args: argparse.Namespace) -> int:
    from panel.server import run_panel

    cfg = _load_cfg()
    host = args.host or str(cfg.get("panel_host") or "127.0.0.1")
    port = int(args.port or cfg.get("panel_port") or 8787)
    auto_goproxy = not bool(args.no_goproxy)
    if args.goproxy:
        auto_goproxy = True
    server = run_panel(host=host, port=port, auto_start_goproxy=auto_goproxy)
    print(f"[branch] panel listening on {server.url}")
    print("[branch] features: log cleanup / pool autoreg / credentials")
    if auto_goproxy:
        print("[branch] GoProxy auto-start requested (if enabled in config)")
    try:
        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[branch] stopping panel...")
        server.stop()
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    import grok_register_ttk as app

    # Ensure GUI path, not cli
    sys.argv = [sys.argv[0]]
    app.main()
    return 0


def cmd_cli(args: argparse.Namespace) -> int:
    import grok_register_ttk as app

    app.load_config()
    if args.count is not None:
        app.config["register_count"] = int(args.count)
    # Direct CLI registration without interactive prompt when --start
    if args.start:
        count = int(app.config.get("register_count", 1) or 1)
        app.run_registration_cli(count)
        return 0
    app.main_cli()
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    cfg = _load_cfg()
    from panel.settings import describe_proxy_selection, normalize_branch_config
    from panel.credentials import list_credentials
    from panel.pool_autoreg import pool_counts, status as pool_status

    cfg = normalize_branch_config(cfg)
    payload = {
        "panel": {
            "host": cfg.get("panel_host"),
            "port": cfg.get("panel_port"),
            "enabled": cfg.get("panel_enabled"),
        },
        "goproxy": describe_proxy_selection(cfg),
        "bind": {
            "register": cfg.get("goproxy_bind_register_proxy"),
            "cpa": cfg.get("goproxy_bind_cpa_proxy"),
        },
        "pool": pool_counts(cfg, root=str(ROOT)),
        "pool_autoreg": {
            "enabled": cfg.get("pool_autoreg_enabled"),
            "min_count": cfg.get("pool_autoreg_min_count"),
            "batch": cfg.get("pool_autoreg_batch"),
            "interval_sec": cfg.get("pool_autoreg_interval_sec"),
            "status": pool_status(),
        },
        "credentials": list_credentials(cfg, buckets=("uploaded", "pending"), root=ROOT).get("counts"),
        "live_inspect_enabled": cfg.get("live_inspect_enabled"),
        "log_cleanup_enabled": cfg.get("log_cleanup_enabled"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_branch.py",
        description="Unified launcher for grok-auto-register branch (panel + GoProxy + register)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_panel = sub.add_parser("panel", help="Start glass web panel (and optional GoProxy)")
    p_panel.add_argument("--host", default=None)
    p_panel.add_argument("--port", type=int, default=None)
    p_panel.add_argument("--no-goproxy", action="store_true", help="Do not auto-start GoProxy")
    p_panel.add_argument("--goproxy", action="store_true", help="Force GoProxy auto-start")
    p_panel.set_defaults(func=cmd_panel)

    p_gui = sub.add_parser("gui", help="Start Tk GUI registration app")
    p_gui.set_defaults(func=cmd_gui)

    p_cli = sub.add_parser("cli", help="Start CLI registration")
    p_cli.add_argument("--start", action="store_true", help="Skip prompt and start immediately")
    p_cli.add_argument("--count", type=int, default=None, help="Override register_count")
    p_cli.set_defaults(func=cmd_cli)

    p_status = sub.add_parser("status", help="Print branch config/runtime snapshot as JSON")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
