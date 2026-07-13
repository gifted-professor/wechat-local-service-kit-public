#!/usr/bin/env python3

import json
import os
from pathlib import Path
from typing import Any, Optional

from wechat_privacy import redact_obj, redact_text


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_POINTER = REPO_ROOT / ".wx-cli-profile"


def _coerce_profile_dir(raw: str) -> Path:
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    else:
        path = path.resolve()
    if path.is_file() and path.name == "config.json":
        return path.parent.resolve()
    return path


def resolve_profile_dir(explicit: Optional[str] = None) -> Optional[Path]:
    candidates = [
        explicit,
        os.environ.get("WX_CLI_CONFIG_DIR", "").strip(),
    ]

    if PROFILE_POINTER.exists():
        candidates.append(PROFILE_POINTER.read_text(encoding="utf-8").strip())

    for raw in candidates:
        if not raw:
            continue
        return _coerce_profile_dir(raw)
    return None


def config_path_for(profile_dir: Path) -> Path:
    return profile_dir / "config.json"


def load_profile_config(profile_dir: Path) -> dict[str, Any]:
    if not profile_dir.exists():
        raise FileNotFoundError(f"wx-cli profile directory not found: {profile_dir}")

    config_path = config_path_for(profile_dir)
    if not config_path.exists():
        raise FileNotFoundError(f"wx-cli profile config not found: {config_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"wx-cli profile config must be a JSON object: {config_path}")
    return data


def profile_db_dir(profile_dir: Path) -> Path:
    config = load_profile_config(profile_dir)
    db_dir = str(config.get("db_dir") or "").strip()
    if not db_dir:
        raise ValueError(f"wx-cli profile is missing db_dir: {config_path_for(profile_dir)}")
    return Path(db_dir).expanduser().resolve()


def profile_keys_path(profile_dir: Path) -> Path:
    config = load_profile_config(profile_dir)
    raw = str(config.get("keys_file") or "all_keys.json").strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (profile_dir / path).resolve()
    else:
        path = path.resolve()
    return path


def load_profile_keys(profile_dir: Path) -> dict[str, str]:
    keys_path = profile_keys_path(profile_dir)
    if not keys_path.exists():
        raise FileNotFoundError(f"wx-cli profile keys file not found: {keys_path}")

    payload = json.loads(keys_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"wx-cli keys file must be a JSON object: {keys_path}")

    out: dict[str, str] = {}
    for raw_rel, raw_value in payload.items():
        rel = str(raw_rel or "").replace("\\", "/").strip()
        if not rel:
            continue
        if isinstance(raw_value, str):
            enc_key = raw_value.strip()
        elif isinstance(raw_value, dict):
            enc_key = str(raw_value.get("enc_key") or "").strip()
        else:
            continue
        if enc_key:
            out[rel] = enc_key
    return out


def profile_key_count(profile_dir: Path) -> int:
    keys_path = profile_keys_path(profile_dir)
    if not keys_path.exists():
        return 0
    try:
        payload = json.loads(keys_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(payload) if isinstance(payload, dict) else 0


def profile_summary(profile_dir: Path, *, redacted: bool = True) -> dict[str, Any]:
    config = load_profile_config(profile_dir)
    keys_path = profile_keys_path(profile_dir)
    db_dir = profile_db_dir(profile_dir)
    summary: dict[str, Any] = {
        "profile_dir": str(profile_dir),
        "config_path": str(config_path_for(profile_dir)),
        "db_dir": str(db_dir),
        "keys_path": str(keys_path),
        "key_count": profile_key_count(profile_dir),
        "has_config": config_path_for(profile_dir).exists(),
        "has_keys": keys_path.exists(),
        "has_db_dir": db_dir.exists(),
        "config_keys": sorted(str(key) for key in config.keys()),
    }
    if redacted:
        return redact_obj(summary)
    return summary


def active_profile_summary(*, explicit: Optional[str] = None, redacted: bool = True) -> dict[str, Any]:
    profile_dir = resolve_profile_dir(explicit)
    if not profile_dir:
        return {"active": False, "reason": "no wx-cli profile configured"}
    try:
        summary = profile_summary(profile_dir, redacted=redacted)
        summary["active"] = True
        return summary
    except Exception as exc:
        return {
            "active": False,
            "profile_dir": redact_text(str(profile_dir)) if redacted else str(profile_dir),
            "error": redact_text(str(exc)) if redacted else str(exc),
        }
