# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from panel.browser_monitor import (
    cleanup_zombies,
    list_registered,
    monitor_status,
    prune_dead_registry,
    register_browser,
    reset_registry,
    scan_browser_processes,
    start_monitor_loop,
    stop_monitor_loop,
    summary,
    unregister_browser,
)
from panel.log_cleanup import cleanup_logs, loop_status as log_loop_status, start_log_cleanup_loop, stop_log_cleanup_loop


class DummyBrowser:
    def __init__(self, pid=None, profile=""):
        self.process_id = pid
        self.user_data_path = profile


class BrowserMonitorTests(unittest.TestCase):
    def setUp(self):
        reset_registry()
        stop_monitor_loop()

    def tearDown(self):
        stop_monitor_loop()
        reset_registry()

    def test_register_unregister(self):
        b = DummyBrowser(pid=12345, profile="x")
        key = register_browser(b, purpose="register", worker_id=1)
        items = list_registered()
        self.assertTrue(any(i["key"] == key for i in items))
        n = unregister_browser(b)
        self.assertGreaterEqual(n, 1)
        items2 = list_registered()
        self.assertFalse(any(i["key"] == key for i in items2))

    def test_summary_shape(self):
        s = summary(project_root=os.getcwd())
        self.assertIn("registered", s)
        self.assertIn("zombies", s)
        self.assertIn("monitor", s)

    def test_scan_classifies_project_zombie_and_keeps_registered_active(self):
        root = os.path.abspath(os.getcwd())
        marker = os.path.join(root, ".browser_profiles", "worker-1").replace("\\", "/")
        active_browser = DummyBrowser(pid=1001, profile=marker)
        register_browser(active_browser, purpose="register", worker_id=1)

        def fake_iter():
            return [
                {
                    "pid": 1001,
                    "ppid": 1,
                    "name": "chrome.exe",
                    "cmdline": f"chrome --user-data-dir={marker}",
                    "create_time": time.time(),
                },
                {
                    "pid": 2002,
                    "ppid": 1,
                    "name": "chrome.exe",
                    "cmdline": f"chrome --user-data-dir={marker}-zombie",
                    "create_time": time.time(),
                },
                {
                    "pid": 3003,
                    "ppid": 1,
                    "name": "chrome.exe",
                    "cmdline": "chrome --user-data-dir=C:/OtherApp/profile",
                    "create_time": time.time(),
                },
                {
                    "pid": 4004,
                    "ppid": 1001,
                    "name": "chrome.exe",
                    "cmdline": f"chrome --type=renderer --user-data-dir={marker}",
                    "create_time": time.time(),
                },
            ]

        # Fake PIDs are not real OS processes; keep registered PID "alive" for prune.
        with patch("panel.browser_monitor._pid_is_running", side_effect=lambda pid: int(pid) == 1001):
            snap = scan_browser_processes(project_root=root, process_iter=fake_iter)
            self.assertEqual([p["pid"] for p in snap["active"]], [1001])
            self.assertEqual([p["pid"] for p in snap["zombies"]], [2002])
            self.assertEqual(snap["foreign_root_browsers"], 1)
            self.assertEqual(snap["total_chrome_processes"], 4)

            killed = []

            def kill_fn(pid):
                killed.append(pid)

            res = cleanup_zombies(
                project_root=root,
                kill=True,
                process_iter=fake_iter,
                kill_fn=kill_fn,
            )
            self.assertEqual(killed, [2002])
            self.assertEqual(res["killed_count"], 1)
            self.assertEqual(res["before_zombies"], 1)

    def test_prune_dead_registry_drops_missing_pid(self):
        b = DummyBrowser(pid=999001, profile="dead")
        register_browser(b, purpose="register", worker_id=9)
        with patch("panel.browser_monitor._pid_is_running", return_value=False):
            pruned = prune_dead_registry()
        self.assertEqual(pruned["removed_count"], 1)
        self.assertEqual(list_registered(), [])

    def test_monitor_loop_records_tick(self):
        root = os.path.abspath(os.getcwd())
        with patch("panel.browser_monitor._monitor_tick", return_value={"ok": True}) as tick:
            started = start_monitor_loop(
                project_root=root,
                interval_sec=3,
                enabled=True,
                cleanup_enabled=False,
            )
            self.assertTrue(started["running"])
            # wait briefly for first tick
            deadline = time.time() + 2.0
            while time.time() < deadline and tick.call_count < 1:
                time.sleep(0.05)
            self.assertGreaterEqual(tick.call_count, 1)
            st = monitor_status()
            self.assertTrue(st["running"])
        stop_monitor_loop()
        self.assertFalse(monitor_status()["running"])


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


if __name__ == "__main__":
    unittest.main()


class LogCleanupLoopTests(unittest.TestCase):
    def setUp(self):
        stop_log_cleanup_loop()

    def tearDown(self):
        stop_log_cleanup_loop()

    def test_loop_runs_and_records_status(self):
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
            self.assertTrue(started["running"])
            deadline = time.time() + 2.0
            while time.time() < deadline and not log_loop_status().get("last_ts"):
                time.sleep(0.05)
            st = log_loop_status()
            self.assertTrue(st["running"])
            self.assertIsNotNone(st["last_ts"])
            self.assertTrue(st["last_ok"])
            self.assertFalse(old.exists())
            stop_log_cleanup_loop()
            self.assertFalse(log_loop_status()["running"])
