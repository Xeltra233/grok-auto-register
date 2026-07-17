"""Upload CPA auth JSON files to a remote CLIProxyAPI management API."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from curl_cffi import CurlMime, requests


_STATE_LOCK = threading.RLock()


def _log(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(message)


def _remote_config(config: dict) -> tuple[bool, str, str, float]:
    enabled = bool(config.get("cpa_remote_enabled", False))
    base = str(config.get("cpa_remote_base", "") or "").strip().rstrip("/")
    key = str(config.get("cpa_remote_management_key", "") or "").strip()
    timeout = max(float(config.get("cpa_remote_timeout_sec", 30) or 30), 5.0)
    return enabled, base, key, timeout


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }


def _state_path(auth_dir: Path, config: dict) -> Path:
    raw = str(config.get("cpa_remote_state_file", "") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = auth_dir / path
        return path
    return auth_dir / ".cpa_remote_upload_state.json"


def _subdir(auth_dir: Path, config: dict, key: str, default: str) -> Path:
    raw = str(config.get(key, default) or default).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = auth_dir / path
    return path.resolve()


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except Exception:
        pass
    return {"version": 1, "files": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _response_error(response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("error") or payload.get("message") or payload)[:500]
    except Exception:
        pass
    return str(getattr(response, "text", "") or "")[:500]


def list_remote_auth_files(base: str, key: str, timeout: float = 30) -> list[dict]:
    response = requests.get(
        f"{base.rstrip('/')}/v0/management/auth-files",
        headers=_headers(key),
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}: {_response_error(response)}")
    payload = response.json()
    files = payload.get("files", []) if isinstance(payload, dict) else []
    return files if isinstance(files, list) else []




def get_remote_auth_file(base: str, key: str, name: str, timeout: float = 30) -> dict:
    """Fetch one remote auth file payload (JSON) by name."""
    name = Path(str(name or "")).name
    if not name:
        raise RuntimeError("missing remote auth file name")
    # try common management API shapes
    candidates = [
        f"{base.rstrip('/')}/v0/management/auth-files/{name}",
        f"{base.rstrip('/')}/v0/management/auth-files?name={name}",
        f"{base.rstrip('/')}/v0/management/auth-file?name={name}",
    ]
    last_err = ""
    for url in candidates:
        response = requests.get(url, headers=_headers(key), timeout=timeout)
        if response.status_code >= 400:
            last_err = f"HTTP {response.status_code}: {_response_error(response)}"
            continue
        try:
            payload = response.json()
        except Exception:
            text = str(getattr(response, "text", "") or "")
            try:
                payload = json.loads(text)
            except Exception as exc:
                last_err = f"invalid json from {url}: {exc}"
                continue
        if isinstance(payload, dict):
            # unwrap common envelopes
            for key_name in ("file", "data", "content", "auth", "json"):
                nested = payload.get(key_name)
                if isinstance(nested, dict) and ("access_token" in nested or nested.get("type") == "xai"):
                    return nested
                if isinstance(nested, str) and nested.strip().startswith("{"):
                    try:
                        obj = json.loads(nested)
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        pass
            if "access_token" in payload or payload.get("type") == "xai":
                return payload
        last_err = f"unexpected payload shape from {url}"
    raise RuntimeError(last_err or f"unable to fetch remote auth file: {name}")


def delete_remote_auth_file(base: str, key: str, name: str, timeout: float = 30) -> dict:
    """Delete one remote auth file by name."""
    name = Path(str(name or "")).name
    if not name:
        raise RuntimeError("missing remote auth file name")
    candidates = [
        ("DELETE", f"{base.rstrip('/')}/v0/management/auth-files/{name}", None),
        ("DELETE", f"{base.rstrip('/')}/v0/management/auth-files?name={name}", None),
        ("POST", f"{base.rstrip('/')}/v0/management/auth-files/delete", {"name": name}),
        ("POST", f"{base.rstrip('/')}/v0/management/delete-auth-file", {"name": name}),
    ]
    last_err = ""
    for method, url, body in candidates:
        if method == "DELETE":
            response = requests.delete(url, headers=_headers(key), timeout=timeout)
        else:
            headers = dict(_headers(key))
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json=body or {}, timeout=timeout)
        if response.status_code >= 400:
            last_err = f"HTTP {response.status_code}: {_response_error(response)}"
            continue
        return {"ok": True, "name": name, "status_code": response.status_code, "url": url}
    raise RuntimeError(last_err or f"unable to delete remote auth file: {name}")


def _remote_names(files: list) -> set[str]:
    names: set[str] = set()
    for item in files:
        if isinstance(item, str):
            names.add(Path(item).name)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("name", "filename", "file", "path"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                names.add(Path(value.strip()).name)
    return names


def upload_and_confirm(path: Path, base: str, key: str, timeout: float = 30) -> dict:
    multipart = CurlMime()
    multipart.addpart(
        "file",
        filename=path.name,
        content_type="application/json",
        local_path=path,
    )
    try:
        response = requests.post(
            f"{base.rstrip('/')}/v0/management/auth-files",
            headers=_headers(key),
            multipart=multipart,
            timeout=timeout,
        )
    finally:
        multipart.close()
    if response.status_code >= 400:
        raise RuntimeError(f"upload HTTP {response.status_code}: {_response_error(response)}")
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if isinstance(payload, dict) and payload.get("status") not in (None, "ok"):
        raise RuntimeError(f"upload rejected: {payload}")

    remote_names = _remote_names(list_remote_auth_files(base, key, timeout=timeout))
    if path.name not in remote_names:
        raise RuntimeError("upload returned success but remote list did not contain the file")
    return {"ok": True, "name": path.name, "confirmed": True}


def _move_to_uploaded(path: Path, uploaded_dir: Path) -> Path:
    uploaded_dir.mkdir(parents=True, exist_ok=True)
    target = uploaded_dir / path.name
    if path.resolve() == target.resolve():
        return target
    if target.exists():
        if _sha256(target) == _sha256(path):
            path.unlink()
            return target
        target = uploaded_dir / f"{path.stem}-{int(time.time())}{path.suffix}"
    os.replace(path, target)
    return target


def retry_pending_auth_files(
    auth_dir: str | Path,
    config: dict,
    log_callback: Callable[[str], None] | None = None,
) -> dict:
    enabled, base, key, timeout = _remote_config(config)
    if not enabled:
        return {"ok": True, "skipped": True, "reason": "disabled", "uploaded": [], "pending": []}
    if not base or not key:
        reason = "missing cpa_remote_base or cpa_remote_management_key"
        _log(log_callback, f"[cpa-remote] {reason}")
        return {"ok": False, "error": reason, "uploaded": [], "pending": []}

    directory = Path(auth_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    pending_dir = _subdir(directory, config, "cpa_remote_pending_dir", "pending")
    uploaded_dir = _subdir(directory, config, "cpa_remote_uploaded_dir", "uploaded")
    pending_dir.mkdir(parents=True, exist_ok=True)
    uploaded_dir.mkdir(parents=True, exist_ok=True)
    candidates = sorted({*pending_dir.glob("xai-*.json"), *directory.glob("xai-*.json")})
    state_path = _state_path(directory, config)

    with _STATE_LOCK:
        state = _load_state(state_path)
        records = state.setdefault("files", {})
        uploaded: list[str] = []
        pending: list[str] = []
        errors: dict[str, str] = {}
        moved: dict[str, str] = {}

        remote_names: set[str] | None = None
        list_error = ""
        try:
            remote_names = _remote_names(list_remote_auth_files(base, key, timeout=timeout))
        except Exception as exc:
            list_error = str(exc)[:500]
            _log(log_callback, f"[cpa-remote] 远端列表读取失败，将保留待重试: {exc}")

        for path in candidates:
            digest = _sha256(path)
            record = records.get(path.name, {}) if isinstance(records.get(path.name), dict) else {}
            if record.get("status") == "confirmed" and record.get("sha256") == digest:
                target = _move_to_uploaded(path, uploaded_dir)
                moved[path.name] = str(target)
                continue
            if remote_names is not None and path.name in remote_names:
                target = _move_to_uploaded(path, uploaded_dir)
                records[path.name] = {
                    "status": "confirmed",
                    "sha256": digest,
                    "confirmed_at": int(time.time()),
                    "attempts": int(record.get("attempts", 0)),
                    "local_path": str(target),
                }
                uploaded.append(path.name)
                moved[path.name] = str(target)
                _log(log_callback, f"[cpa-remote] 远端已存在，确认成功并归档: {path.name}")
                continue

            attempts = int(record.get("attempts", 0)) + 1
            try:
                upload_and_confirm(path, base, key, timeout=timeout)
                target = _move_to_uploaded(path, uploaded_dir)
                records[path.name] = {
                    "status": "confirmed",
                    "sha256": digest,
                    "confirmed_at": int(time.time()),
                    "attempts": attempts,
                    "local_path": str(target),
                }
                uploaded.append(path.name)
                moved[path.name] = str(target)
                _log(log_callback, f"[cpa-remote] 上传并确认成功，已移入 uploaded: {path.name}")
                if remote_names is not None:
                    remote_names.add(path.name)
            except Exception as exc:
                error = str(exc)[:500]
                records[path.name] = {
                    "status": "pending",
                    "sha256": digest,
                    "last_attempt": int(time.time()),
                    "attempts": attempts,
                    "last_error": error,
                    "local_path": str(path),
                }
                pending.append(path.name)
                errors[path.name] = error
                _log(log_callback, f"[cpa-remote] 上传失败，已留待下次重试: {path.name}: {error}")

        state["updated_at"] = int(time.time())
        _save_state(state_path, state)
        return {
            "ok": not pending and not (list_error and not candidates),
            "uploaded": uploaded,
            "pending": pending,
            "errors": errors,
            "moved": moved,
            "list_error": list_error,
            "remote_count": len(remote_names) if remote_names is not None else None,
            "state_path": str(state_path),
        }
