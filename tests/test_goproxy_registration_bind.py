# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

import grok_register_ttk as app


class PrepareGoProxyTests(unittest.TestCase):
    def setUp(self):
        self._orig = dict(app.config)
        app.config.clear()
        app.config.update(app.DEFAULT_CONFIG)
        app.config["goproxy_enabled"] = True
        app.config["goproxy_auto_start"] = True
        app.config["goproxy_endpoint"] = "http_stable"
        app.config["goproxy_bind_register_proxy"] = True
        app.config["goproxy_bind_cpa_proxy"] = True
        app.config["proxy"] = ""
        app.config["cpa_proxy"] = ""

    def tearDown(self):
        app.config.clear()
        app.config.update(self._orig)

    def test_prepare_binds_local_urls_and_starts_manager(self):
        class FakeMgr:
            def __init__(self):
                self.started = False
                self.cfg = None

            def set_config(self, cfg):
                self.cfg = dict(cfg)

            def start(self, build_if_missing=True, wait_sec=3):
                self.started = True
                return {"ok": True, "already_running": False}

            def apply_bindings_to(self, cfg):
                return {
                    **cfg,
                    "proxy": "http://127.0.0.1:7776",
                    "cpa_proxy": "http://127.0.0.1:7776",
                }

        fake = FakeMgr()
        logs = []
        with patch("panel.goproxy_manager.get_manager", return_value=fake):
            res = app.prepare_goproxy_for_registration(log_callback=logs.append)
        self.assertTrue(res["ok"])
        self.assertTrue(fake.started)
        self.assertTrue(res["bind_register"])
        self.assertTrue(res["bind_cpa"])
        self.assertEqual(app.config["proxy"], "http://127.0.0.1:7776")
        self.assertEqual(app.config["cpa_proxy"], "http://127.0.0.1:7776")
        self.assertTrue(any("register proxy bound" in x for x in logs))

    def test_prepare_skips_when_disabled(self):
        app.config["goproxy_enabled"] = False
        res = app.prepare_goproxy_for_registration()
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("skipped"))

    def test_start_registration_and_cli_call_prepare(self):
        src = open(app.__file__, "r", encoding="utf-8").read()
        self.assertIn("prepare_goproxy_for_registration(log_callback=self.log)", src)
        self.assertIn("prepare_goproxy_for_registration(log_callback=cli_log)", src)
        self.assertIn("def prepare_goproxy_for_registration", src)


if __name__ == "__main__":
    unittest.main()
