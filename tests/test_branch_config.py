# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

from panel.settings import (
    GOPROXY_ENDPOINTS,
    GOPROXY_POOL_MODES,
    apply_local_proxy_bindings,
    build_goproxy_env,
    describe_proxy_selection,
    normalize_branch_config,
    normalize_endpoint,
    normalize_pool_mode,
    resolve_local_proxy_url,
)


class BranchConfigTests(unittest.TestCase):
    def test_pool_modes_cover_five(self):
        self.assertEqual(len(GOPROXY_POOL_MODES), 5)
        for mode in (
            "mixed",
            "mixed_custom_first",
            "free_first",
            "custom_only",
            "free_only",
        ):
            self.assertIn(normalize_pool_mode(mode), GOPROXY_POOL_MODES)

    def test_endpoints_http_and_socks(self):
        self.assertEqual(set(GOPROXY_ENDPOINTS), {
            "http_random",
            "http_stable",
            "socks5_random",
            "socks5_stable",
        })
        self.assertEqual(normalize_endpoint("socks5_lowest"), "socks5_stable")
        self.assertEqual(normalize_endpoint("http_lowest_latency"), "http_stable")

    def test_legacy_config_gets_defaults(self):
        legacy = {"proxy": "http://127.0.0.1:7890", "register_count": 3}
        cfg = normalize_branch_config(legacy)
        self.assertEqual(cfg["register_count"], 3)
        self.assertEqual(cfg["proxy"], "http://127.0.0.1:7890")
        self.assertTrue(cfg["panel_enabled"])
        self.assertEqual(cfg["goproxy_http_random_port"], 17877)
        self.assertEqual(cfg["goproxy_http_stable_port"], 17876)
        self.assertEqual(cfg["goproxy_socks5_random_port"], 17879)
        self.assertEqual(cfg["goproxy_socks5_stable_port"], 17880)
        self.assertTrue(cfg["log_cleanup_enabled"])


    def test_legacy_proxy_not_overwritten_by_default(self):
        legacy = {"proxy": "http://127.0.0.1:7890", "cpa_proxy": ""}
        cfg = normalize_branch_config(legacy)
        bound = apply_local_proxy_bindings(cfg)
        self.assertEqual(bound["proxy"], "http://127.0.0.1:7890")
        self.assertEqual(bound.get("cpa_proxy", ""), "")

    def test_local_proxy_bind_and_env(self):
        cfg = normalize_branch_config({
            "goproxy_endpoint": "socks5_stable",
            "goproxy_pool_mode": "mixed_free_first",
            "goproxy_bind_register_proxy": True,
            "goproxy_bind_cpa_proxy": True,
        })
        bound = apply_local_proxy_bindings(cfg)
        self.assertEqual(bound["proxy"], "socks5://127.0.0.1:17880")
        self.assertEqual(bound["cpa_proxy"], "socks5://127.0.0.1:17880")
        env = build_goproxy_env(cfg, base_env={})
        self.assertEqual(env["CUSTOM_PROXY_MODE"], "mixed")
        self.assertEqual(env["CUSTOM_FREE_PRIORITY"], "true")
        self.assertEqual(env["RANDOM_PORT"], "17877")
        self.assertEqual(env["STABLE_PORT"], "17876")
        self.assertEqual(env["SOCKS5_RANDOM_PORT"], "17879")
        self.assertEqual(env["SOCKS5_STABLE_PORT"], "17880")
        info = describe_proxy_selection(cfg)
        self.assertEqual(info["endpoint"], "socks5_stable")
        self.assertEqual(info["proxy_url"], resolve_local_proxy_url(cfg))

    def test_example_config_has_branch_keys(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(root, "config", "config.example.json"),
            os.path.join(root, "config.example.json"),
        ]
        example = next((p for p in candidates if os.path.isfile(p)), candidates[-1])
        with open(example, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in (
            "panel_port",
            "goproxy_endpoint",
            "goproxy_pool_mode",
            "goproxy_http_random_port",
            "goproxy_http_stable_port",
            "goproxy_socks5_random_port",
            "goproxy_socks5_stable_port",
            "log_cleanup_enabled",
        ):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
