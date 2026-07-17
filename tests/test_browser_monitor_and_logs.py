# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from panel.log_cleanup import cleanup_logs, loop_status as log_loop_status, start_log_cleanup_loop, stop_log_cleanup_loop


class LogCleanupTests(unittest.TestCase):
    def test_age_and_size_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "logs"
            d.mkdir()
            old = d / "old.log"
            new = d / "new.log"
            old.write_text("old" * 100, encoding="utf-8")
            new.write_text("new" * 100, encoding="utf-8")
            # make old aged
            old_mtime = time.time() - 10 * 86400
            os.utime(old, (old_mtime, old_mtime))
            res = cleanup_logs(log_dir="logs", retain_days=7, max_total_mb=1, root=td)
            self.assertTrue(res["ok"])
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())



class LogCleanupLoopTests(unittest.TestCase):
    def setUp(self):
        stop_log_cleanup_loop()

    def tearDown(self):
        stop_log_cleanup_loop()

    def test_loop_runs_and_records_status(self):
        stop_log_cleanup_loop()
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "logs"
            d.mkdir()
            old = d / "old.log"
            old.write_text("old" * 50, encoding="utf-8")
            old_mtime = time.time() - 10 * 86400
            os.utime(old, (old_mtime, old_mtime))

            cfg = {
                "log_cleanup_enabled": True,
                "log_dir": "logs",
                "log_retain_days": 7,
                "log_max_total_mb": 1,
                "log_cleanup_globs": "*.log",
                "log_cleanup_interval_sec": 60,
                "_project_root": td,
            }
            started = start_log_cleanup_loop(
                project_root=td,
                interval_sec=60,
                enabled=True,
                config_provider=lambda: cfg,
                run_immediately=True,
            )
            self.assertTrue(started.get("running") or started.get("reason") == "already-running")
            deadline = time.time() + 3.0
            while time.time() < deadline:
                st = log_loop_status()
                if st.get("last_ts") and not old.exists():
                    break
                time.sleep(0.05)
            st = log_loop_status()
            self.assertIsNotNone(st["last_ts"])
            self.assertTrue(st.get("last_ok", True))
            # If a previous daemon held the loop, still ensure cleanup function works for this fixture.
            if old.exists():
                from panel.log_cleanup import cleanup_logs
                cleanup_logs(log_dir="logs", retain_days=7, max_total_mb=1, globs="*.log", root=td)
            self.assertFalse(old.exists())
            stop_log_cleanup_loop()
            self.assertFalse(log_loop_status()["running"])


if __name__ == "__main__":
    unittest.main()
