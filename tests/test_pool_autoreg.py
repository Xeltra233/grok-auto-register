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
        # 当前 2，触发线 5，终点 10 => 触发，一次补 8
        need = compute_register_need(current_total=2, min_count=5, target_count=10, batch=0)
        self.assertTrue(need["should_register"])
        self.assertEqual(need["deficit"], 8)
        self.assertEqual(need["register_count"], 8)
        self.assertEqual(need["target_count"], 10)
        # 当前 6 >= 触发线 5 => 不触发，即使还没到终点 10
        need2 = compute_register_need(current_total=6, min_count=5, target_count=10, batch=0)
        self.assertFalse(need2["should_register"])
        self.assertEqual(need2["register_count"], 0)
        # batch 限制仍可用
        need3 = compute_register_need(current_total=2, min_count=5, target_count=10, batch=3)
        self.assertEqual(need3["register_count"], 3)

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
            counts = pool_counts({"cpa_auth_dir": "cpa_auths", "pool_autoreg_source": "local"}, root=str(root))
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
                "pool_autoreg_source": "local",
                "pool_autoreg_min_count": 5,
                "pool_autoreg_batch": 0,
                "cpa_auth_dir": "cpa_auths",
            }
            res1 = evaluate_and_maybe_trigger(cfg, root=str(root), register_fn=fake_register)
            self.assertTrue(res1["triggered"])
            self.assertEqual(res1["register_count"], 4)
            res2 = evaluate_and_maybe_trigger(cfg, root=str(root), register_fn=fake_register)
            self.assertFalse(res2["triggered"])
            self.assertEqual(res2["reason"], "registration_in_progress")
            deadline = time.time() + 2
            while time.time() < deadline and status().get("registration_running"):
                time.sleep(0.05)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], 4)

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



    def test_pool_counts_local_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            auth = root / "cpa_auths"
            up = auth / "uploaded"
            pe = auth / "pending"
            up.mkdir(parents=True)
            pe.mkdir(parents=True)
            (up / "xai-a@example.com.json").write_text("{}", encoding="utf-8")
            (pe / "xai-c@example.com.json").write_text("{}", encoding="utf-8")
            counts = pool_counts(
                {"cpa_auth_dir": "cpa_auths", "pool_autoreg_source": "local"},
                root=str(root),
            )
            self.assertTrue(counts["ok"])
            self.assertEqual(counts["used_source"], "local")
            self.assertEqual(counts["total"], 2)

    def test_remote_source_failure_does_not_fake_local(self):
        cfg = {
            "pool_autoreg_source": "remote",
            "cpa_remote_enabled": True,
            "cpa_remote_base": "https://example.invalid",
            "cpa_remote_management_key": "k",
            "cpa_auth_dir": "cpa_auths",
        }
        with patch("panel.pool_autoreg.remote_pool_counts", return_value={"ok": False, "source": "remote", "total": 0, "error": "boom"}):
            counts = pool_counts(cfg, root=".")
            self.assertFalse(counts["ok"])
            self.assertIsNone(counts.get("used_source"))
            self.assertEqual(counts.get("error"), "boom")

    def test_evaluate_uses_remote_total(self):
        calls = []
        def fake_register(count, cfg):
            calls.append(count)
            return True
        cfg = {
            "pool_autoreg_enabled": True,
            "pool_autoreg_source": "remote",
            "pool_autoreg_min_count": 5,
            "pool_autoreg_batch": 0,
            "cpa_auth_dir": "cpa_auths",
        }
        fake_counts = {
            "ok": True,
            "used_source": "remote",
            "source": "remote",
            "total": 1,
            "local": {"total": 100},
            "remote": {"total": 1},
            "fallback": False,
            "error": None,
        }
        with patch("panel.pool_autoreg.pool_counts", return_value=fake_counts):
            res = evaluate_and_maybe_trigger(cfg, root=".", register_fn=fake_register)
        self.assertTrue(res["triggered"])
        self.assertEqual(res["register_count"], 4)
        deadline = time.time() + 2
        while time.time() < deadline and status().get("registration_running"):
            time.sleep(0.05)
        self.assertEqual(calls, [4])


    def test_trigger_and_target_split(self):
        calls = []
        def fake_register(count, cfg):
            calls.append(count)
            return True
        cfg = {
            "pool_autoreg_enabled": True,
            "pool_autoreg_source": "local",
            "pool_autoreg_min_count": 5,      # 触发线
            "pool_autoreg_target_count": 12,  # 终点
            "pool_autoreg_batch": 0,
            "concurrent_count": 8,
            "cpa_auth_dir": "cpa_auths",
        }
        fake_counts = {
            "ok": True,
            "used_source": "local",
            "source": "local",
            "total": 3,
            "fallback": False,
            "error": None,
        }
        with patch("panel.pool_autoreg.pool_counts", return_value=fake_counts):
            res = evaluate_and_maybe_trigger(cfg, root=".", register_fn=fake_register)
        self.assertTrue(res["triggered"])
        # 3 -> 12 = 补 9，一次开满
        self.assertEqual(res["register_count"], 9)
        self.assertEqual(res["need"]["min_count"], 5)
        self.assertEqual(res["need"]["target_count"], 12)
        deadline = time.time() + 2
        while time.time() < deadline and status().get("registration_running"):
            time.sleep(0.05)
        self.assertEqual(calls, [9])


    def test_refresh_gap_before_start(self):
        """开跑前重查：若远端已变，按新缺口注册，不沿用旧计划数。"""
        calls = []
        seq = {
            "n": 0,
            "values": [
                # first evaluate sees low pool
                {"ok": True, "used_source": "remote", "source": "remote", "total": 2, "fallback": False, "error": None},
                # refresh before start sees higher pool
                {"ok": True, "used_source": "remote", "source": "remote", "total": 8, "fallback": False, "error": None},
            ],
        }

        def fake_counts(config=None, root=None, source=None):
            i = min(seq["n"], len(seq["values"]) - 1)
            seq["n"] += 1
            return seq["values"][i]

        def fake_register(count, cfg):
            calls.append(count)
            return True

        cfg = {
            "pool_autoreg_enabled": True,
            "pool_autoreg_source": "remote",
            "pool_autoreg_min_count": 5,
            "pool_autoreg_target_count": 12,
            "pool_autoreg_batch": 0,
            "cpa_auth_dir": "cpa_auths",
        }
        with patch("panel.pool_autoreg.pool_counts", side_effect=fake_counts):
            res = evaluate_and_maybe_trigger(cfg, root=".", register_fn=fake_register)
            self.assertTrue(res["triggered"])
            # planned from first snapshot: 12-2=10
            self.assertEqual(res["register_count_planned"], 10)
            deadline = time.time() + 2
            while time.time() < deadline and status().get("registration_running"):
                time.sleep(0.05)
        # refresh snapshot total=8, still < trigger 5? no 8>=5 so should skip
        # wait: trigger is min_count=5, 8 >= 5 => should_register False => no call
        self.assertEqual(calls, [])
        last = status().get("last_trigger") or {}
        self.assertTrue(last.get("skipped"))
        self.assertEqual(last.get("reason"), "above_threshold_on_refresh")

    def test_refresh_uses_latest_deficit(self):
        calls = []
        seq = {"n": 0, "values": [
            {"ok": True, "used_source": "remote", "source": "remote", "total": 2, "fallback": False, "error": None},
            {"ok": True, "used_source": "remote", "source": "remote", "total": 4, "fallback": False, "error": None},
        ]}
        def fake_counts(config=None, root=None, source=None):
            i = min(seq["n"], len(seq["values"]) - 1)
            seq["n"] += 1
            return seq["values"][i]
        def fake_register(count, cfg):
            calls.append(count)
            return True
        cfg = {
            "pool_autoreg_enabled": True,
            "pool_autoreg_source": "remote",
            "pool_autoreg_min_count": 5,
            "pool_autoreg_target_count": 12,
            "pool_autoreg_batch": 0,
        }
        with patch("panel.pool_autoreg.pool_counts", side_effect=fake_counts):
            res = evaluate_and_maybe_trigger(cfg, root=".", register_fn=fake_register)
            self.assertTrue(res["triggered"])
            self.assertEqual(res["register_count_planned"], 10)  # 12-2
            deadline = time.time() + 2
            while time.time() < deadline and status().get("registration_running"):
                time.sleep(0.05)
        # refresh total=4 < 5, still trigger, gap to target 12-4=8
        self.assertEqual(calls, [8])
        last = status().get("last_trigger") or {}
        self.assertEqual(last.get("register_count_final"), 8)

if __name__ == "__main__":
    unittest.main()
