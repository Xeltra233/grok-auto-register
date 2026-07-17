# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from panel.pool_autoreg import (
    compute_register_need,
    evaluate_and_maybe_trigger,
    pool_counts,
    start_pool_autoreg_loop,
    status,
    stop_pool_autoreg_loop,
)


class PoolAutoRegTests(unittest.TestCase):
    def setUp(self):
        stop_pool_autoreg_loop()

    def tearDown(self):
        stop_pool_autoreg_loop()

    def test_compute_register_need(self):
        need = compute_register_need(current_total=2, min_count=5, batch=3)
        self.assertTrue(need["should_register"])
        self.assertEqual(need["deficit"], 3)
        self.assertEqual(need["register_count"], 3)
        need2 = compute_register_need(current_total=8, min_count=5, batch=3)
        self.assertFalse(need2["should_register"])
        self.assertEqual(need2["register_count"], 0)

    def test_pool_counts_uploaded_pending(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            up = auth / "uploaded"
            pe = auth / "pending"
            up.mkdir(parents=True)
            pe.mkdir(parents=True)
            (up / "xai-a@example.com.json").write_text("{}", encoding="utf-8")
            (up / "xai-b@example.com.json").write_text("{}", encoding="utf-8")
            (pe / "xai-c@example.com.json").write_text("{}", encoding="utf-8")
            counts = pool_counts({"cpa_auth_dir": "cpa_auths"}, root=str(root))
            self.assertEqual(counts["uploaded"], 2)
            self.assertEqual(counts["pending"], 1)
            self.assertEqual(counts["total"], 3)

    def test_evaluate_triggers_once_and_is_non_reentrant(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths" / "uploaded"
            auth.mkdir(parents=True)
            (auth / "xai-only@example.com.json").write_text("{}", encoding="utf-8")
            calls = []

            def fake_register(count, cfg):
                calls.append((count, cfg.get("pool_autoreg_min_count")))
                time.sleep(0.2)
                return True

            cfg = {
                "pool_autoreg_enabled": True,
                "pool_autoreg_min_count": 5,
                "pool_autoreg_batch": 3,
                "cpa_auth_dir": "cpa_auths",
            }
            res1 = evaluate_and_maybe_trigger(cfg, root=str(root), register_fn=fake_register)
            self.assertTrue(res1["triggered"])
            self.assertEqual(res1["register_count"], 3)
            res2 = evaluate_and_maybe_trigger(cfg, root=str(root), register_fn=fake_register)
            self.assertFalse(res2["triggered"])
            self.assertEqual(res2["reason"], "registration_in_progress")
            deadline = time.time() + 2
            while time.time() < deadline and status().get("registration_running"):
                time.sleep(0.05)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 3)

    def test_loop_can_start_and_stop(self):
        cfg = {
            "pool_autoreg_enabled": True,
            "pool_autoreg_min_count": 1,
            "pool_autoreg_batch": 1,
            "pool_autoreg_interval_sec": 60,
            "cpa_auth_dir": "cpa_auths",
            "_project_root": os.getcwd(),
        }
        with patch("panel.pool_autoreg.evaluate_and_maybe_trigger", return_value={"ok": True}) as tick:
            started = start_pool_autoreg_loop(
                project_root=os.getcwd(),
                interval_sec=60,
                enabled=True,
                config_provider=lambda: cfg,
                run_immediately=True,
            )
            self.assertTrue(started["running"])
            deadline = time.time() + 2
            while time.time() < deadline and tick.call_count < 1:
                time.sleep(0.05)
            self.assertGreaterEqual(tick.call_count, 1)
            self.assertTrue(status()["running"])
        stop_pool_autoreg_loop()
        self.assertFalse(status()["running"])


if __name__ == "__main__":
    unittest.main()
