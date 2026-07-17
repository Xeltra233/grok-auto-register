"""High-level: mint CPA xai-*.json for one free registered account."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .auth_code import has_cli_referrer, mint_tokens_from_sso
from .browser_confirm import mint_with_browser
from .inspect import inspect_access_token, is_live_pass
from .probe import probe_mini_response, probe_models
from .proxyutil import proxy_log_label, resolve_proxy, set_runtime_proxy
from .schema import DEFAULT_BASE_URL, build_cpa_xai_auth
from .writer import write_cpa_xai_auth

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def quarantine_auth_file(
    path: str | Path,
    *,
    reason: str = "",
    quarantine_dir: str | Path | None = None,
    log: LogFn | None = None,
) -> Path | None:
    """Move a written auth file out of pending so remote upload will not pick it up."""
    log = log or _noop
    src = Path(path)
    if not src.is_file():
        return None
    qdir = Path(quarantine_dir) if quarantine_dir else (src.parent.parent / "quarantine")
    if not qdir.is_absolute() and quarantine_dir is None:
        # pending/xai-*.json -> <auth_root>/quarantine
        qdir = src.parent.parent / "quarantine"
        if src.parent.name not in {"pending", "uploaded"}:
            qdir = src.parent / "quarantine"
    qdir = qdir.expanduser().resolve()
    qdir.mkdir(parents=True, exist_ok=True)
    dest = qdir / src.name
    if dest.exists():
        dest = qdir / f"{src.stem}-{int(time.time())}{src.suffix}"
    try:
        os.replace(src, dest)
    except OSError:
        shutil.move(str(src), str(dest))
    note = qdir / f"{dest.stem}.reason.txt"
    try:
        note.write_text((reason or "quarantined").strip() + "\n", encoding="utf-8")
    except OSError:
        pass
    log(f"quarantined {src.name} -> {dest} ({reason})")
    return dest


def mint_and_export(
    *,
    email: str,
    password: str,
    auth_dir: str | Path,
    page: Any | None = None,
    proxy: str | None = None,
    headless: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    headers: dict[str, str] | None = None,
    probe: bool = True,
    probe_chat: bool = False,
    probe_strict: bool = False,
    live_inspect: bool = True,
    browser_timeout_sec: float = 240.0,
    force_standalone: bool = False,
    cookies: Any | None = None,
    sso: str | None = None,
    prefer_auth_code: bool = True,
    require_cli_referrer: bool = True,
    allow_device_fallback: bool = True,
    reuse_browser: bool = True,
    recycle_every: int = 15,
    log: LogFn | None = None,
    cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Full pipeline: SSO auth-code (preferred) or device-auth -> write CPA file -> probe.

    Authorization-code + PKCE injects JWT referrer=grok-build required by
    cli-chat-proxy. Device-code tokens usually lack referrer and get 403.
    """
    log = log or _noop
    email = (email or "").strip()
    sso_val = (sso or "").strip()
    if not email or (not password and not sso_val):
        return {"ok": False, "email": email, "error": "missing email/password (or sso)"}

    resolved = resolve_proxy(proxy)
    set_runtime_proxy(resolved or None)
    log(f"mint start: {email} proxy={proxy_log_label(resolved) or '(none)'}")

    tokens: dict[str, Any] | None = None
    flow_err = ""

    if prefer_auth_code and sso_val:
        try:
            log("mint via auth-code + PKCE (referrer=grok-build)")
            tokens = mint_tokens_from_sso(
                sso_val,
                proxy=resolved or None,
                log=log,
                require_referrer=require_cli_referrer,
            )
        except Exception as e:  # noqa: BLE001
            flow_err = str(e)
            log(f"auth-code mint failed: {e}")
            if not allow_device_fallback:
                return {"ok": False, "email": email, "error": f"auth-code: {e}", "flow": "auth_code_pkce"}

    if tokens is None:
        if not password:
            return {
                "ok": False,
                "email": email,
                "error": flow_err or "auth-code failed and no password for device fallback",
                "flow": "auth_code_pkce",
            }
        try:
            if flow_err:
                log("falling back to browser device-code flow")
            tokens = mint_with_browser(
                email=email,
                password=password,
                page=page,
                proxy=resolved or None,
                headless=headless,
                browser_timeout_sec=browser_timeout_sec,
                force_standalone=force_standalone,
                cookies=cookies,
                reuse_browser=reuse_browser,
                recycle_every=recycle_every,
                poll_log=log,
                cancel=cancel,
            )
            tokens = dict(tokens)
            tokens.setdefault("flow", "device_code")
            tokens.setdefault("referrer", None)
        except Exception as e:  # noqa: BLE001
            log(f"mint failed: {e}")
            err = str(e)
            if flow_err:
                err = f"auth-code: {flow_err}; device: {err}"
            return {"ok": False, "email": email, "error": err}

    access = str(tokens.get("access_token") or "").strip()
    refresh = str(tokens.get("refresh_token") or "").strip()
    if not access or not refresh:
        return {"ok": False, "email": email, "error": "mint returned empty tokens"}

    ref = tokens.get("referrer")
    if ref is None:
        try:
            from .auth_code import access_token_referrer

            ref = access_token_referrer(access)
            tokens["referrer"] = ref
        except Exception:
            ref = None

    if require_cli_referrer and not has_cli_referrer(access):
        msg = f"access_token missing CLI referrer={ref!r} (need grok-build)"
        log(msg)
        return {
            "ok": False,
            "email": email,
            "error": msg,
            "flow": tokens.get("flow"),
            "referrer": ref,
        }

    payload = build_cpa_xai_auth(
        email=email,
        access_token=access,
        refresh_token=refresh,
        id_token=tokens.get("id_token"),
        expires_in=tokens.get("expires_in"),
        base_url=base_url,
        headers=headers,
        extra={
            "mint_flow": tokens.get("flow") or "unknown",
            "referrer": ref,
        },
    )
    path = write_cpa_xai_auth(auth_dir, payload)
    log(f"wrote {path} flow={tokens.get('flow')} referrer={ref!r}")

    result: dict[str, Any] = {
        "ok": True,
        "email": email,
        "path": str(path),
        "user_code": tokens.get("user_code"),
        "base_url": base_url,
        "proxy": proxy_log_label(resolved),
        "flow": tokens.get("flow"),
        "referrer": ref,
    }

    if probe:
        pr = probe_models(access, base_url=base_url, proxy=resolved or None)
        result["probe_models"] = pr
        log(
            f"probe models: ok={pr.get('ok')} has_grok_45={pr.get('has_grok_45')} "
            f"ids={pr.get('model_ids')} err={pr.get('error')}"
        )
        if not pr.get("has_grok_45"):
            err_detail = pr.get("error") or "grok-4.5 not listed"
            msg = f"token ok but grok-4.5 not listed ({err_detail})"
            # Local IP/CF often 403 even with valid grok-build tokens; remote CPA
            # may still work. Only hard-fail when probe_strict=True and live_inspect is off.
            if (probe_strict and not live_inspect) or not has_cli_referrer(access):
                result["ok"] = False
                result["error"] = msg
            else:
                result["probe_warning"] = msg
                log(f"WARN soft probe fail (referrer ok, not strict): {msg}")
        if probe_chat and pr.get("has_grok_45"):
            ch = probe_mini_response(
                access, base_url=base_url, proxy=resolved or None
            )
            result["probe_chat"] = ch
            log(f"probe chat: ok={ch.get('ok')} model={ch.get('model')} text={ch.get('text')!r}")
            if not ch.get("ok"):
                msg = f"chat probe failed: {ch.get('error') or ch.get('status')}"
                if probe_strict and not live_inspect:
                    result["ok"] = False
                    result["error"] = msg
                else:
                    result["probe_warning"] = msg
                    log(f"WARN soft chat probe fail: {msg}")

    # grok-inspection style conversation live-check is the hard gate for keep/push.
    if live_inspect and result.get("ok"):
        live = inspect_access_token(
            access,
            base_url=base_url,
            proxy=resolved or None,
        )
        result["live_inspect"] = live
        log(
            f"live inspect: healthy={live.get('healthy')} class={live.get('classification')} "
            f"reason={live.get('reason')} endpoint={live.get('endpoint')}"
        )
        if not is_live_pass(live):
            result["ok"] = False
            result["error"] = (
                f"live inspect failed: {live.get('classification')}: {live.get('reason')}"
            )

    if not result.get("ok") and result.get("path"):
        q = quarantine_auth_file(
            result["path"],
            reason=str(result.get("error") or "probe/cli gate failed"),
            log=log,
        )
        if q is not None:
            result["path"] = str(q)
            result["quarantined"] = True

    return result
