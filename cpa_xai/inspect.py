# -*- coding: utf-8 -*-
"""Live inspection helpers inspired by ywddd/grok-inspection.

Probe xAI/Grok credentials via cli-chat-proxy and classify:
healthy / permission_denied / quota_exhausted / reauth / model_unavailable / probe_error.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .proxyutil import resolve_proxy
from .schema import DEFAULT_BASE_URL, DEFAULT_CLIENT_HEADERS

DEFAULT_PROBE_MODEL = "grok-4.5"
XAI_RESPONSES_URL_SUFFIX = "/responses"
XAI_CHAT_URL_SUFFIX = "/chat/completions"


def _lower(value: str) -> str:
    return str(value or "").strip().lower()


def _contains_any(text: str, *needles: str) -> bool:
    value = _lower(text)
    for needle in needles:
        n = _lower(needle)
        if n and n in value:
            return True
    return False


def is_free_usage_exhausted(code: str = "", message: str = "") -> bool:
    blob = f"{code} {message}"
    return _contains_any(
        blob,
        "free-usage-exhausted",
        "used all the included free usage",
        "included free usage has been exhausted",
    )


def extract_error(body: str) -> dict[str, str]:
    body = (body or "").strip()
    if not body:
        return {"code": "", "message": ""}
    try:
        data = json.loads(body)
    except Exception:
        return {"code": "", "message": body[:300]}
    if not isinstance(data, dict):
        return {"code": "", "message": str(data)[:300]}
    err = data.get("error")
    code = str(data.get("code") or "")
    message = ""
    if isinstance(err, dict):
        code = str(err.get("code") or code or "")
        message = str(err.get("message") or err.get("error") or "")
    elif isinstance(err, str):
        message = err
    if not message:
        message = str(data.get("message") or data.get("msg") or "")[:300]
    return {"code": code, "message": message}


def classify_probe(
    *,
    chat_status: int = 0,
    chat_code: str = "",
    chat_error: str = "",
    request_error: str = "",
) -> dict[str, str]:
    """Mirror grok-inspection classifyProbe outcomes for local gating."""
    if request_error:
        low = _lower(request_error)
        if _contains_any(low, "unauthorized", "401", "invalid_token", "token expired", "re-login", "relogin"):
            return {
                "classification": "reauth",
                "action": "delete",
                "reason": f"request auth failure: {request_error[:200]}",
            }
        return {
            "classification": "probe_error",
            "action": "keep",
            "reason": f"request error: {request_error[:200]}",
        }

    code = chat_code or ""
    message = chat_error or ""
    blob = f"{code} {message}"

    if is_free_usage_exhausted(code, message):
        return {
            "classification": "quota_exhausted",
            "action": "disable",
            "reason": "free usage exhausted",
        }

    if chat_status in (401, 403) or _contains_any(
        blob,
        "permission",
        "forbidden",
        "not allowed",
        "access denied",
        "unauthorized",
        "invalid_grant",
        "invalid_token",
        "token has been revoked",
        "re-authenticate",
        "login required",
    ):
        # 401-ish auth death vs permission
        if chat_status == 401 or _contains_any(
            blob,
            "invalid_token",
            "token has been revoked",
            "re-authenticate",
            "login required",
            "unauthorized",
        ):
            return {
                "classification": "reauth",
                "action": "delete",
                "reason": message or code or f"http {chat_status}",
            }
        return {
            "classification": "permission_denied",
            "action": "disable",
            "reason": message or code or f"http {chat_status}",
        }

    if chat_status == 404 or _contains_any(blob, "model_not_found", "does not exist", "unknown model"):
        return {
            "classification": "model_unavailable",
            "action": "keep",
            "reason": message or code or f"http {chat_status}",
        }

    if 200 <= int(chat_status or 0) < 300:
        return {
            "classification": "healthy",
            "action": "keep",
            "reason": "conversation probe success",
        }

    if chat_status == 429 and not is_free_usage_exhausted(code, message):
        return {
            "classification": "probe_error",
            "action": "keep",
            "reason": message or "rate limited",
        }

    if chat_status:
        return {
            "classification": "probe_error",
            "action": "keep",
            "reason": message or code or f"http {chat_status}",
        }

    return {
        "classification": "probe_error",
        "action": "keep",
        "reason": message or "empty probe result",
    }


def _opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    p = resolve_proxy(proxy)
    handlers: list[Any] = []
    if p:
        handlers.append(urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    access_token: str,
    timeout: float,
    proxy: str | None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **DEFAULT_CLIENT_HEADERS,
    }
    opener = _opener(proxy)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "body": raw,
            }
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": e.code,
            "body": raw,
            "error": raw[:800],
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "body": "",
            "error": str(e),
        }


def inspect_access_token(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_PROBE_MODEL,
    timeout: float = 25.0,
    proxy: str | None = None,
    retry_on_429: bool = True,
) -> dict[str, Any]:
    """Probe one access_token like grok-inspection (responses, then chat fallback)."""
    token = (access_token or "").strip()
    if not token:
        classified = classify_probe(request_error="missing access_token")
        return {
            "ok": False,
            "healthy": False,
            "classification": classified["classification"],
            "action": classified["action"],
            "reason": classified["reason"],
            "model": model,
        }

    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    model = (model or DEFAULT_PROBE_MODEL).strip() or DEFAULT_PROBE_MODEL
    responses_url = f"{base}{XAI_RESPONSES_URL_SUFFIX}"
    chat_url = f"{base}{XAI_CHAT_URL_SUFFIX}"

    chat_body = {"model": model, "input": "ping", "stream": False}
    primary = _post_json(responses_url, chat_body, access_token=token, timeout=timeout, proxy=proxy)

    if primary.get("status") == 429 and retry_on_429:
        err = extract_error(primary.get("body") or "")
        if not is_free_usage_exhausted(err.get("code", ""), err.get("message", "")):
            time.sleep(0.35)
            primary = _post_json(responses_url, chat_body, access_token=token, timeout=timeout, proxy=proxy)

    if primary.get("ok"):
        classified = classify_probe(chat_status=int(primary.get("status") or 200))
        return {
            "ok": True,
            "healthy": True,
            "classification": classified["classification"],
            "action": classified["action"],
            "reason": classified["reason"],
            "model": model,
            "endpoint": "responses",
            "status": primary.get("status"),
        }

    err = extract_error(primary.get("body") or "")
    classified = classify_probe(
        chat_status=int(primary.get("status") or 0),
        chat_code=err.get("code", ""),
        chat_error=err.get("message") or primary.get("error") or "",
        request_error="" if primary.get("status") else (primary.get("error") or ""),
    )

    # Fallback to chat/completions when primary is ambiguous/transient.
    if classified["classification"] in {"probe_error", "model_unavailable"}:
        fallback_body = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        }
        fallback = _post_json(chat_url, fallback_body, access_token=token, timeout=timeout, proxy=proxy)
        if fallback.get("ok"):
            classified = classify_probe(chat_status=int(fallback.get("status") or 200))
            return {
                "ok": True,
                "healthy": True,
                "classification": classified["classification"],
                "action": classified["action"],
                "reason": classified["reason"],
                "model": model,
                "endpoint": "chat_completions",
                "status": fallback.get("status"),
                "primary_error": primary.get("error") or err.get("message"),
            }
        ferr = extract_error(fallback.get("body") or "")
        classified = classify_probe(
            chat_status=int(fallback.get("status") or 0),
            chat_code=ferr.get("code", "") or err.get("code", ""),
            chat_error=ferr.get("message") or fallback.get("error") or err.get("message") or "",
            request_error="" if fallback.get("status") else (fallback.get("error") or ""),
        )
        return {
            "ok": False,
            "healthy": False,
            "classification": classified["classification"],
            "action": classified["action"],
            "reason": classified["reason"],
            "model": model,
            "endpoint": "chat_completions",
            "status": fallback.get("status"),
            "primary_status": primary.get("status"),
            "error": fallback.get("error") or primary.get("error"),
        }

    return {
        "ok": False,
        "healthy": False,
        "classification": classified["classification"],
        "action": classified["action"],
        "reason": classified["reason"],
        "model": model,
        "endpoint": "responses",
        "status": primary.get("status"),
        "error": primary.get("error") or err.get("message"),
    }


def is_live_pass(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("healthy") is True:
        return True
    return str(result.get("classification") or "") == "healthy"
