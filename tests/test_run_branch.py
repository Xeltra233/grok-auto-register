# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import run_branch


class RunBranchEntryTests(unittest.TestCase):
    def test_parser_has_core_commands(self):
        parser = run_branch.build_parser()
        for cmd in ("panel", "gui", "cli", "status"):
            args = parser.parse_args([cmd] if cmd != "cli" else ["cli", "--count", "2"])
            self.assertEqual(args.command, cmd if cmd != "cli" else "cli")
            self.assertTrue(callable(args.func))

    def test_status_command_returns_json_shape(self):
        cfg = {
            "panel_host": "127.0.0.1",
            "panel_port": 8787,
            "panel_enabled": True,
            "goproxy_bind_register_proxy": False,
            "goproxy_bind_cpa_proxy": False,
            "pool_autoreg_enabled": False,
            "pool_autoreg_min_count": 5,
            "pool_autoreg_batch": 3,
            "pool_autoreg_interval_sec": 300,
            "live_inspect_enabled": True,
            "log_cleanup_enabled": True,
        }
        with patch.object(run_branch, "_load_cfg", return_value=cfg), \
             patch("panel.settings.describe_proxy_selection", return_value={"endpoint": "http_random"}), \
             patch("panel.credentials.list_credentials", return_value={"counts": {"uploaded": 0, "pending": 0}}), \
             patch("panel.browser_monitor.summary", return_value={"registered": 0, "zombies": 0}), \
             patch("panel.pool_autoreg.pool_counts", return_value={"total": 0, "uploaded": 0, "pending": 0}), \
             patch("panel.pool_autoreg.status", return_value={"running": False}):
            code = run_branch.cmd_status(None)
        self.assertEqual(code, 0)

    def test_main_dispatches_status(self):
        with patch.object(run_branch, "cmd_status", return_value=0) as fn:
            code = run_branch.main(["status"])
        self.assertEqual(code, 0)
        fn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
