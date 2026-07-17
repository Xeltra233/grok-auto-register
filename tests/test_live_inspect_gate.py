# -*- coding: utf-8 -*-
import unittest
from cpa_xai.inspect import classify_probe, is_live_pass, is_free_usage_exhausted


class InspectClassifyTests(unittest.TestCase):
    def test_healthy(self):
        r = classify_probe(chat_status=200)
        self.assertEqual(r["classification"], "healthy")
        self.assertTrue(is_live_pass({"healthy": True, "classification": "healthy"}))

    def test_quota(self):
        self.assertTrue(is_free_usage_exhausted("subscription:free-usage-exhausted", "used all free"))
        r = classify_probe(chat_status=429, chat_code="subscription:free-usage-exhausted", chat_error="used all")
        self.assertEqual(r["classification"], "quota_exhausted")

    def test_reauth(self):
        r = classify_probe(chat_status=401, chat_error="invalid_token")
        self.assertEqual(r["classification"], "reauth")
        self.assertFalse(is_live_pass({"healthy": False, "classification": "reauth"}))

    def test_permission(self):
        r = classify_probe(chat_status=403, chat_error="permission denied")
        self.assertEqual(r["classification"], "permission_denied")


class SuccessGateSourceTests(unittest.TestCase):
    def test_register_uses_gate_before_persist(self):
        src = open("grok_register_ttk.py", "r", encoding="utf-8").read()
        self.assertIn("def run_success_live_gate(", src)
        self.assertIn("def persist_successful_account(", src)
        self.assertIn("success_require_live", src)
        self.assertIn("live_inspect_enabled", src)
        # no old async export path before save
        self.assertNotIn("CPA xAI 导出 (异步)", src)
        self.assertGreaterEqual(src.count("run_success_live_gate("), 3)
        self.assertIn("live_inspect", open("cpa_xai/mint.py", "r", encoding="utf-8").read())
        self.assertIn("inspect_access_token", open("cpa_xai/mint.py", "r", encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
