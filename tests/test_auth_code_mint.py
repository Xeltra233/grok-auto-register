"""Unit tests for auth-code mint path and CLI referrer gate."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cpa_xai.auth_code import (
    GROK_REFERRER,
    _parse_consent_code,
    has_cli_referrer,
)
from cpa_xai import mint as mint_mod


def _fake_jwt(payload: dict) -> str:
    def b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{b64({'alg': 'none'})}.{b64(payload)}.sig"


class AuthCodeParseTests(unittest.TestCase):
    def test_parse_consent_code_from_rsc_line(self):
        body = '0:{"success":true,"code":"abc123"}\n'
        self.assertEqual(_parse_consent_code(body), "abc123")

    def test_parse_consent_code_rejects_failed(self):
        body = '1:{"success":false,"code":"nope"}\n'
        self.assertIsNone(_parse_consent_code(body))

    def test_has_cli_referrer_grok_build(self):
        tok = _fake_jwt({"referrer": GROK_REFERRER, "exp": 9999999999, "iat": 1})
        self.assertTrue(has_cli_referrer(tok))

    def test_has_cli_referrer_missing(self):
        tok = _fake_jwt({"exp": 9999999999, "iat": 1})
        self.assertFalse(has_cli_referrer(tok))


class MintPrefersAuthCodeTests(unittest.TestCase):
    def test_mint_uses_sso_auth_code_when_available(self):
        access = _fake_jwt(
            {
                "referrer": GROK_REFERRER,
                "exp": 9999999999,
                "iat": 9999990000,
                "sub": "user-1",
            }
        )
        tokens = {
            "access_token": access,
            "refresh_token": "refresh-xyz",
            "id_token": None,
            "expires_in": 9999,
            "token_type": "Bearer",
            "user_code": None,
            "flow": "auth_code_pkce",
            "referrer": GROK_REFERRER,
            "raw": {},
        }

        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            pending.mkdir()
            with patch.object(mint_mod, "mint_tokens_from_sso", return_value=tokens) as m_sso, \
                    patch.object(mint_mod, "mint_with_browser") as m_browser, \
                    patch.object(mint_mod, "probe_models", return_value={
                        "ok": True, "has_grok_45": True, "model_ids": ["grok-4.5"],
                    }), \
                    patch.object(mint_mod, "inspect_access_token", return_value={
                        "ok": True,
                        "healthy": True,
                        "classification": "healthy",
                        "action": "keep",
                        "reason": "mock live pass",
                    }), \
                    patch.object(mint_mod, "is_live_pass", return_value=True):
                result = mint_mod.mint_and_export(
                    email="a@example.com",
                    password="pw",
                    auth_dir=pending,
                    sso="sso-cookie-value",
                    prefer_auth_code=True,
                    require_cli_referrer=True,
                    probe=True,
                    proxy=None,
                )

            m_sso.assert_called_once()
            m_browser.assert_not_called()
            self.assertTrue(result["ok"])
            self.assertEqual(result.get("flow"), "auth_code_pkce")
            self.assertEqual(result.get("referrer"), GROK_REFERRER)
            self.assertTrue(Path(result["path"]).is_file())
            payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            # Aaron-style pure oauth JSON: no forced headers/extra metadata.
            self.assertNotIn("headers", payload)
            self.assertNotIn("referrer", payload)
            self.assertEqual(payload.get("type"), "xai")
            self.assertEqual(payload.get("auth_kind"), "oauth")
            self.assertTrue(payload.get("access_token"))
            self.assertTrue(payload.get("refresh_token"))

    def test_missing_referrer_does_not_write_pending(self):
        access = _fake_jwt({"exp": 9999999999, "iat": 1, "sub": "u"})
        tokens = {
            "access_token": access,
            "refresh_token": "r",
            "flow": "device_code",
            "referrer": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            pending.mkdir()
            with patch.object(mint_mod, "mint_tokens_from_sso", side_effect=RuntimeError("boom")), \
                    patch.object(mint_mod, "mint_with_browser", return_value=tokens):
                result = mint_mod.mint_and_export(
                    email="b@example.com",
                    password="pw",
                    auth_dir=pending,
                    sso="sso",
                    prefer_auth_code=True,
                    require_cli_referrer=True,
                    allow_device_fallback=True,
                    probe=False,
                )
            self.assertFalse(result["ok"])
            self.assertIn("referrer", result.get("error", "").lower())
            self.assertEqual(list(pending.glob("xai-*.json")), [])

    def test_probe_fail_quarantines_file(self):
        access = _fake_jwt(
            {
                "referrer": GROK_REFERRER,
                "exp": 9999999999,
                "iat": 9999990000,
                "sub": "user-2",
            }
        )
        tokens = {
            "access_token": access,
            "refresh_token": "refresh-xyz",
            "flow": "auth_code_pkce",
            "referrer": GROK_REFERRER,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / "pending"
            pending.mkdir()
            with patch.object(mint_mod, "mint_tokens_from_sso", return_value=tokens), \
                    patch.object(mint_mod, "probe_models", return_value={
                        "ok": False, "has_grok_45": False, "model_ids": [], "error": "Access denied",
                    }):
                result = mint_mod.mint_and_export(
                    email="c@example.com",
                    password="pw",
                    auth_dir=pending,
                    sso="sso",
                    prefer_auth_code=True,
                    require_cli_referrer=True,
                    probe=True,
                    probe_strict=True,
                    live_inspect=False,
                )
            self.assertFalse(result["ok"])
            self.assertTrue(result.get("quarantined"))
            self.assertEqual(list(pending.glob("xai-*.json")), [])
            self.assertTrue((root / "quarantine").exists())
            self.assertTrue(list((root / "quarantine").glob("xai-*.json")))



    def test_probe_soft_fail_keeps_pending_when_referrer_ok(self):
        access = _fake_jwt(
            {
                "referrer": GROK_REFERRER,
                "exp": 9999999999,
                "iat": 9999990000,
                "sub": "user-3",
            }
        )
        tokens = {
            "access_token": access,
            "refresh_token": "refresh-xyz",
            "flow": "auth_code_pkce",
            "referrer": GROK_REFERRER,
        }
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            pending.mkdir()
            with patch.object(mint_mod, "mint_tokens_from_sso", return_value=tokens), \
                    patch.object(mint_mod, "probe_models", return_value={
                        "ok": False, "has_grok_45": False, "model_ids": [], "error": "Access denied",
                    }), \
                    patch.object(mint_mod, "inspect_access_token", return_value={
                        "ok": True,
                        "healthy": True,
                        "classification": "healthy",
                        "action": "keep",
                        "reason": "mock live pass",
                    }), \
                    patch.object(mint_mod, "is_live_pass", return_value=True):
                result = mint_mod.mint_and_export(
                    email="d@example.com",
                    password="pw",
                    auth_dir=pending,
                    sso="sso",
                    prefer_auth_code=True,
                    require_cli_referrer=False,
                    probe=True,
                    probe_strict=False,
                    live_inspect=False,
                )
            self.assertTrue(result["ok"])
            self.assertIn("probe_warning", result)
            self.assertTrue(list(pending.glob("xai-*.json")))




class MintDefaultsToDeviceCodeTests(unittest.TestCase):
    def test_default_prefers_device_code_without_auth_code_flag(self):
        access = _fake_jwt(
            {
                "exp": 9999999999,
                "iat": 9999990000,
                "sub": "user-2",
            }
        )
        tokens = {
            "access_token": access,
            "refresh_token": "refresh-device",
            "id_token": None,
            "expires_in": 9999,
            "token_type": "Bearer",
            "user_code": "ABCD",
            "flow": "device_code",
            "referrer": None,
            "raw": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            pending = Path(tmp) / "pending"
            pending.mkdir()
            with patch.object(mint_mod, "mint_tokens_from_sso") as m_sso, \
                    patch.object(mint_mod, "mint_with_browser", return_value=tokens) as m_browser, \
                    patch.object(mint_mod, "probe_models", return_value={
                        "ok": True, "has_grok_45": True, "model_ids": ["grok-4.5"],
                    }), \
                    patch.object(mint_mod, "inspect_access_token", return_value={
                        "ok": True,
                        "healthy": True,
                        "classification": "healthy",
                        "action": "keep",
                        "reason": "mock live pass",
                    }), \
                    patch.object(mint_mod, "is_live_pass", return_value=True):
                result = mint_mod.mint_and_export(
                    email="b@example.com",
                    password="pw",
                    auth_dir=pending,
                    sso="sso-cookie-value",
                    # defaults: prefer_auth_code=False, require_cli_referrer=False
                    probe=False,
                    live_inspect=False,
                    proxy=None,
                )

            m_sso.assert_not_called()
            m_browser.assert_called_once()
            self.assertTrue(result["ok"])
            self.assertEqual(result.get("flow"), "device_code")


if __name__ == "__main__":
    unittest.main()
