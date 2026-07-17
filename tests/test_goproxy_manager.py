# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from unittest import mock

from panel.goproxy_manager import GoProxyManager, get_manager
from panel.settings import GOPROXY_POOL_MODES


class GoProxyManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        # minimal fake source tree
        src = os.path.join(self.root, "third_party", "goproxy")
        os.makedirs(os.path.join(src, "bin"), exist_ok=True)
        with open(os.path.join(src, "main.go"), "w", encoding="utf-8") as f:
            f.write("package main\n")
        self.cfg = {
            "goproxy_enabled": True,
            "goproxy_source_dir": "third_party/goproxy",
            "goproxy_workdir": "third_party/goproxy",
            "goproxy_data_dir": "data/goproxy",
            "goproxy_pool_mode": "mixed_equal",
            "goproxy_endpoint": "http_random",
            "goproxy_bind_register_proxy": False,
        }
        self.mgr = GoProxyManager(config=self.cfg, root=self.root)

    def tearDown(self):
        try:
            self.mgr.stop()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_list_modes_and_endpoints(self):
        modes = self.mgr.list_modes()
        self.assertEqual(len(modes), 5)
        self.assertEqual(set(modes), set(GOPROXY_POOL_MODES))
        endpoints = self.mgr.list_endpoints()
        ids = {e["id"] for e in endpoints}
        self.assertEqual(ids, {"http_random", "http_stable", "socks5_random", "socks5_stable"})
        ports = {e["id"]: e["port"] for e in endpoints}
        self.assertEqual(ports["http_random"], 7777)
        self.assertEqual(ports["http_stable"], 7776)
        self.assertEqual(ports["socks5_random"], 7779)
        self.assertEqual(ports["socks5_stable"], 7780)

    def test_set_pool_mode_writes_config(self):
        res = self.mgr.set_pool_mode("mixed_custom_first", restart_if_running=False)
        self.assertTrue(res["ok"])
        path = res["config_path"]
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["custom_proxy_mode"], "mixed")
        self.assertTrue(data["custom_priority"])
        self.assertFalse(data["custom_free_priority"])
        res2 = self.mgr.set_pool_mode("free_only", restart_if_running=False)
        with open(res2["config_path"], "r", encoding="utf-8") as f:
            data2 = json.load(f)
        self.assertEqual(data2["custom_proxy_mode"], "free_only")

    def test_set_endpoint_and_bind(self):
        res = self.mgr.set_endpoint("socks5_stable", bind=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["endpoint"], "socks5_stable")
        self.assertEqual(res["proxy_url"], "socks5://127.0.0.1:7780")
        self.assertEqual(res["bound_proxy"], "socks5://127.0.0.1:7780")
        self.assertEqual(res["bound_cpa_proxy"], "socks5://127.0.0.1:7780")
        bound = self.mgr.apply_bindings_to({"register_count": 1})
        self.assertEqual(bound["proxy"], "socks5://127.0.0.1:7780")

    def test_status_shape(self):
        st = self.mgr.status()
        for key in (
            "enabled",
            "running",
            "pool_mode",
            "endpoint",
            "proxy_url",
            "ports",
            "modes",
            "endpoints",
            "port_status",
        ):
            self.assertIn(key, st)
        self.assertEqual(st["ports"]["http_random"], 7777)
        self.assertEqual(st["ports"]["socks5_stable"], 7780)

    def test_start_without_binary_fails_cleanly(self):
        # no runnable binary in fake tree
        res = self.mgr.start(build_if_missing=False, wait_sec=0.2)
        self.assertFalse(res["ok"])
        self.assertIn("binary", res.get("error", "").lower() + res["status"].get("last_error", "").lower())

    def test_get_manager_singleton(self):
        a = get_manager(self.cfg, root=self.root)
        b = get_manager(root=self.root)
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main()
