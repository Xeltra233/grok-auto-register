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
    def test_register_uses_save_first_then_cpa(self):
        src = open("grok_register_ttk.py", "r", encoding="utf-8").read()
        self.assertIn("def run_success_live_gate(", src)
        self.assertIn("def persist_successful_account(", src)
        self.assertIn("success_require_live", src)
        self.assertIn("from account_outputs import append_account_line", src)
        self.assertIn("queue_unsaved_account", src)
        # Aaron-style: persist appears before gate in both success paths
        gui_idx = src.find("self.accounts_output_file")
        self.assertGreater(gui_idx, 0)
        # save-first marker
        self.assertIn("Aaron-style: persist account first", src)
        self.assertIn("CPA mint + 凭证转换（失败不阻断账号保存）", src)
        # defaults no longer hard-require live
        self.assertIn('"success_require_live": False', src)
        self.assertIn('"cpa_prefer_auth_code": False', src)
        self.assertIn('"cpa_force_standalone": True', src)
        mint = open("cpa_xai/mint.py", "r", encoding="utf-8").read()
        self.assertIn("prefer_auth_code: bool = False", mint)
        self.assertIn("force_standalone: bool = True", mint)


class AccountOutputsTests(unittest.TestCase):
    def test_append_and_pending(self):
        import os
        import tempfile
        from account_outputs import append_account_line, queue_unsaved_account, retry_pending_file

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "accounts.txt")
            append_account_line(out, "a@x.com", "pw", "sso1")
            with open(out, "r", encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "a@x.com----pw----sso1")
            queue_unsaved_account(out, {"email": "b@x.com", "password": "p2", "sso": "sso2"}, "disk full")
            pending = out + ".pending.jsonl"
            self.assertTrue(os.path.isfile(pending))
            res = retry_pending_file(pending, output_path=out)
            self.assertEqual(res["restored"], 1)
            with open(out, "r", encoding="utf-8") as f:
                body = f.read()
            self.assertIn("b@x.com----p2----sso2", body)


if __name__ == "__main__":
    unittest.main()
