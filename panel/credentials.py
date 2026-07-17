# -*- coding: utf-8 -*-
"""Local CPA credential vault helpers for the branch panel.

Supports:
- list credentials by bucket (uploaded/pending/all)
- multi-select / select-all download as zip bytes
- delete uploaded credentials (default scope) and scrub remote-upload state
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional


SAFE_NAME_RE = re.compile(r"^[\w.@+=\-]+\.json$", re.UNICODE)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_auth_dir(config: Optional[dict] = None, root: Optional[Path] = None) -> Path:
    root = Path(root) if root else _project_root()
    cfg = config or {}
    raw = str(cfg.get("cpa_auth_dir") or "cpa_auths").strip() or "cpa_auths"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def resolve_bucket_dir(auth_dir: Path, config: Optional[dict], bucket: str) -> Path:
    cfg = config or {}
    bucket = (bucket or "").strip().lower()
    if bucket in {"", "root", "all"}:
        return auth_dir
    if bucket == "uploaded":
        name = str(cfg.get("cpa_remote_uploaded_dir") or "uploaded").strip() or "uploaded"
    elif bucket == "pending":
        name = str(cfg.get("cpa_remote_pending_dir") or "pending").strip() or "pending"
    elif bucket == "quarantine":
        name = "quarantine"
    else:
        name = bucket
    path = Path(name).expanduser()
    if not path.is_absolute():
        path = auth_dir / path
    return path.resolve()


def _state_path(auth_dir: Path, config: Optional[dict] = None) -> Path:
    cfg = config or {}
    raw = str(cfg.get("cpa_remote_state_file") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = auth_dir / path
        return path
    return auth_dir / ".cpa_remote_upload_state.json"


def _safe_name(name: str) -> str:
    base = os.path.basename(str(name or "").strip())
    if not base or not SAFE_NAME_RE.match(base):
        raise ValueError(f"unsafe credential name: {name!r}")
    if ".." in base or "/" in base or "\\" in base:
        raise ValueError(f"unsafe credential name: {name!r}")
    return base


def _file_meta(path: Path, bucket: str) -> dict[str, Any]:
    st = path.stat()
    email = ""
    try:
        # xai-email.json
        stem = path.stem
        if stem.startswith("xai-"):
            email = stem[4:]
    except Exception:
        email = ""
    return {
        "name": path.name,
        "bucket": bucket,
        "path": str(path),
        "size": int(st.st_size),
        "mtime": int(st.st_mtime),
        "email": email,
    }


def list_credentials(
    config: Optional[dict] = None,
    *,
    buckets: Optional[Iterable[str]] = None,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """List local credential JSON files.

    Default buckets: uploaded + pending.
    """
    auth_dir = resolve_auth_dir(config, root=root)
    wanted = [b.strip().lower() for b in (buckets or ("uploaded", "pending")) if str(b).strip()]
    if not wanted:
        wanted = ["uploaded", "pending"]

    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for bucket in wanted:
        if bucket == "all":
            # root-level xai-*.json plus known subdirs
            for sub in ("uploaded", "pending", "quarantine", "root"):
                d = resolve_bucket_dir(auth_dir, config, sub)
                if not d.is_dir():
                    continue
                files = sorted(d.glob("xai-*.json"))
                label = "root" if sub == "root" else sub
                counts[label] = counts.get(label, 0) + len(files)
                for f in files:
                    items.append(_file_meta(f, label))
            continue
        d = resolve_bucket_dir(auth_dir, config, bucket)
        if not d.is_dir():
            counts[bucket] = 0
            continue
        files = sorted(d.glob("xai-*.json"))
        counts[bucket] = len(files)
        for f in files:
            items.append(_file_meta(f, bucket))

    # de-dup by absolute path
    seen = set()
    unique = []
    for it in items:
        key = it["path"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    unique.sort(key=lambda x: (-int(x.get("mtime") or 0), x.get("name") or ""))
    return {
        "ok": True,
        "auth_dir": str(auth_dir),
        "counts": counts,
        "total": len(unique),
        "items": unique,
    }


def _resolve_named_files(
    names: Iterable[str],
    *,
    config: Optional[dict],
    buckets: Optional[Iterable[str]],
    root: Optional[Path],
) -> list[Path]:
    listing = list_credentials(config, buckets=buckets or ("uploaded", "pending", "quarantine", "root"), root=root)
    by_name: dict[str, list[Path]] = {}
    for item in listing["items"]:
        by_name.setdefault(item["name"], []).append(Path(item["path"]))

    resolved: list[Path] = []
    missing: list[str] = []
    for raw in names:
        name = _safe_name(raw)
        cands = by_name.get(name) or []
        if not cands:
            missing.append(name)
            continue
        # Prefer uploaded when duplicates exist.
        preferred = None
        for p in cands:
            if p.parent.name == "uploaded":
                preferred = p
                break
        resolved.append(preferred or cands[0])
    if missing:
        raise FileNotFoundError(f"credentials not found: {', '.join(missing)}")
    return resolved


def build_credentials_zip(
    names: Optional[Iterable[str]] = None,
    *,
    config: Optional[dict] = None,
    buckets: Optional[Iterable[str]] = None,
    select_all: bool = False,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Build an in-memory zip for selected credentials.

    select_all=True uses the provided buckets (default uploaded only for safety
    when downloading from "uploaded management" UI).
    """
    if select_all:
        use_buckets = list(buckets or ("uploaded",))
        listing = list_credentials(config, buckets=use_buckets, root=root)
        paths = [Path(it["path"]) for it in listing["items"]]
        names = [it["name"] for it in listing["items"]]
    else:
        if not names:
            raise ValueError("names is empty (or pass select_all=True)")
        use_buckets = list(buckets or ("uploaded", "pending", "quarantine", "root"))
        paths = _resolve_named_files(names, config=config, buckets=use_buckets, root=root)

    if not paths:
        raise FileNotFoundError("no credentials selected")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        used = set()
        for p in paths:
            arc = p.name
            if arc in used:
                arc = f"{p.parent.name}_{p.name}"
            used.add(arc)
            zf.write(p, arcname=arc)
    data = buf.getvalue()
    ts = time.strftime("%Y%m%d_%H%M%S")
    return {
        "ok": True,
        "filename": f"cpa_credentials_{ts}.zip",
        "count": len(paths),
        "names": [p.name for p in paths],
        "content": data,
        "size": len(data),
    }


def delete_credentials(
    names: Optional[Iterable[str]] = None,
    *,
    config: Optional[dict] = None,
    bucket: str = "uploaded",
    select_all: bool = False,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Delete credential files.

    Default bucket is uploaded only, so pending files are not removed by accident.
    """
    bucket = (bucket or "uploaded").strip().lower()
    if bucket not in {"uploaded", "pending", "quarantine", "root"}:
        raise ValueError(f"unsupported bucket: {bucket}")

    auth_dir = resolve_auth_dir(config, root=root)
    target_dir = resolve_bucket_dir(auth_dir, config, bucket)
    if select_all:
        paths = sorted(target_dir.glob("xai-*.json")) if target_dir.is_dir() else []
        names = [p.name for p in paths]
    else:
        if not names:
            raise ValueError("names is empty (or pass select_all=True)")
        paths = []
        for raw in names:
            name = _safe_name(raw)
            p = (target_dir / name).resolve()
            if target_dir not in p.parents and p.parent != target_dir:
                raise ValueError(f"path escapes bucket: {name}")
            if not p.is_file():
                raise FileNotFoundError(name)
            paths.append(p)

    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    for p in paths:
        try:
            p.unlink()
            deleted.append(p.name)
        except Exception as exc:
            errors.append({"name": p.name, "error": str(exc)})

    # scrub upload state entries for deleted names
    state_file = _state_path(auth_dir, config)
    scrubbed = 0
    if deleted and state_file.is_file():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            files = state.get("files") if isinstance(state, dict) else None
            if isinstance(files, dict):
                for name in deleted:
                    if name in files:
                        files.pop(name, None)
                        scrubbed += 1
                    # also pop path-keyed variants
                    for key in list(files.keys()):
                        if key.endswith("/" + name) or key.endswith("\\" + name):
                            files.pop(key, None)
                            scrubbed += 1
                state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    return {
        "ok": not errors,
        "bucket": bucket,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "errors": errors,
        "state_scrubbed": scrubbed,
        "auth_dir": str(auth_dir),
    }


def delete_all_uploaded(config: Optional[dict] = None, root: Optional[Path] = None) -> dict[str, Any]:
    return delete_credentials(select_all=True, config=config, bucket="uploaded", root=root)


def prune_local_credentials(
    config: Optional[dict] = None,
    *,
    root: Optional[Path] = None,
    keep: Optional[int] = None,
    buckets: Optional[Iterable[str]] = None,
    enabled: Optional[bool] = None,
) -> dict[str, Any]:
    """Keep only the newest N local credential files; delete older ones.

    Default off. When enabled, sorts by mtime desc and retains `keep` files
    across selected buckets (default: uploaded + pending).
    """
    cfg = dict(config or {})
    if enabled is None:
        enabled = bool(cfg.get("local_cred_retain_enabled", False))
    if not enabled:
        listing = list_credentials(cfg, buckets=buckets or ("uploaded", "pending"), root=root)
        return {
            "ok": True,
            "skipped": True,
            "reason": "disabled",
            "enabled": False,
            "keep": int(cfg.get("local_cred_retain_count") or 0),
            "before": int(listing.get("total") or 0),
            "after": int(listing.get("total") or 0),
            "deleted": [],
            "deleted_count": 0,
        }

    try:
        keep_n = int(keep if keep is not None else (cfg.get("local_cred_retain_count") or 0))
    except Exception:
        keep_n = 0
    keep_n = max(keep_n, 0)

    use_buckets = [b.strip().lower() for b in (buckets or ("uploaded", "pending")) if str(b).strip()]
    if not use_buckets:
        use_buckets = ["uploaded", "pending"]

    listing = list_credentials(cfg, buckets=use_buckets, root=root)
    items = list(listing.get("items") or [])
    # newest first
    items.sort(key=lambda it: int(it.get("mtime") or 0), reverse=True)
    before = len(items)
    if keep_n <= 0:
        # keep 0 means delete all selected local credentials
        victims = items
    else:
        victims = items[keep_n:]

    deleted: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    # delete per-bucket to reuse path safety
    by_bucket: dict[str, list[str]] = {}
    for it in victims:
        b = str(it.get("bucket") or "uploaded")
        n = str(it.get("name") or "")
        if not n:
            continue
        by_bucket.setdefault(b, []).append(n)

    for bkt, names in by_bucket.items():
        try:
            res = delete_credentials(names, config=cfg, bucket=bkt, root=root)
            for name in res.get("deleted") or []:
                deleted.append({"name": name, "bucket": bkt})
            for err in res.get("errors") or []:
                errors.append({"bucket": bkt, **(err if isinstance(err, dict) else {"error": str(err)})})
        except Exception as exc:
            errors.append({"bucket": bkt, "error": str(exc)[:300]})

    after_listing = list_credentials(cfg, buckets=use_buckets, root=root)
    return {
        "ok": not errors,
        "skipped": False,
        "enabled": True,
        "keep": keep_n,
        "before": before,
        "after": int(after_listing.get("total") or 0),
        "deleted": deleted,
        "deleted_count": len(deleted),
        "errors": errors,
        "buckets": use_buckets,
        "auth_dir": after_listing.get("auth_dir") or listing.get("auth_dir"),
    }

