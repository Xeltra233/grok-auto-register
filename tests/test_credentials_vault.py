# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from panel.credentials import (
    build_credentials_zip,
    delete_all_uploaded,
    delete_credentials,
    list_credentials,
)


class CredentialVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.auth = self.root / "cpa_auths"
        self.uploaded = self.auth / "uploaded"
        self.pending = self.auth / "pending"
        self.uploaded.mkdir(parents=True)
        self.pending.mkdir(parents=True)
        (self.uploaded / "xai-a@example.com.json").write_text('{"email":"a@example.com"}', encoding="utf-8")
        (self.uploaded / "xai-b@example.com.json").write_text('{"email":"b@example.com"}', encoding="utf-8")
        (self.pending / "xai-c@example.com.json").write_text('{"email":"c@example.com"}', encoding="utf-8")
        state = {
            "version": 1,
            "files": {
                "xai-a@example.com.json": {"status": "uploaded"},
                "xai-b@example.com.json": {"status": "uploaded"},
                "xai-c@example.com.json": {"status": "pending"},
            },
        }
        (self.auth / ".cpa_remote_upload_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.cfg = {
            "cpa_auth_dir": str(self.auth),
            "cpa_remote_uploaded_dir": "uploaded",
            "cpa_remote_pending_dir": "pending",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_buckets(self):
        data = list_credentials(self.cfg, buckets=("uploaded", "pending"), root=self.root)
        self.assertEqual(data["counts"]["uploaded"], 2)
        self.assertEqual(data["counts"]["pending"], 1)
        self.assertEqual(data["total"], 3)

    def test_multi_select_zip(self):
        z = build_credentials_zip(
            ["xai-a@example.com.json", "xai-b@example.com.json"],
            config=self.cfg,
            root=self.root,
        )
        self.assertTrue(z["ok"])
        self.assertEqual(z["count"], 2)
        with zipfile.ZipFile(io_bytes := __import__("io").BytesIO(z["content"])) as zf:
            names = set(zf.namelist())
        self.assertIn("xai-a@example.com.json", names)
        self.assertIn("xai-b@example.com.json", names)

    def test_select_all_uploaded_zip(self):
        z = build_credentials_zip(select_all=True, config=self.cfg, buckets=("uploaded",), root=self.root)
        self.assertEqual(z["count"], 2)

    def test_delete_uploaded_only(self):
        res = delete_credentials(
            ["xai-a@example.com.json"],
            config=self.cfg,
            bucket="uploaded",
            root=self.root,
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["deleted_count"], 1)
        self.assertFalse((self.uploaded / "xai-a@example.com.json").exists())
        self.assertTrue((self.pending / "xai-c@example.com.json").exists())

    def test_delete_all_uploaded(self):
        res = delete_all_uploaded(self.cfg, root=self.root)
        self.assertEqual(res["deleted_count"], 2)
        self.assertEqual(len(list(self.uploaded.glob("xai-*.json"))), 0)
        self.assertEqual(len(list(self.pending.glob("xai-*.json"))), 1)
        state = json.loads((self.auth / ".cpa_remote_upload_state.json").read_text(encoding="utf-8"))
        self.assertNotIn("xai-a@example.com.json", state["files"])
        self.assertNotIn("xai-b@example.com.json", state["files"])
        self.assertIn("xai-c@example.com.json", state["files"])


if __name__ == "__main__":
    unittest.main()
