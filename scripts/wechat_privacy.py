#!/usr/bin/env python3

import re
from pathlib import Path
from typing import Any

MAX_TEXT_PREVIEW = 2000

SENSITIVE_KEY_NAMES = {
    "key",
    "keys",
    "enc_key",
    "key_hex",
    "raw_key",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "api_key",
    "apikey",
    "authorization",
}

WXID_RE = re.compile(r"\bwxid_[A-Za-z0-9_\-]{6,}\b")
LONG_HEX_RE = re.compile(r"\b(?:0x)?[0-9a-fA-F]{64,}\b")
SQLCIPHER_LITERAL_RE = re.compile(r"x'[0-9a-fA-F]{32,}'", re.IGNORECASE)
LOCAL_PATH_RE = re.compile(
    r"(?P<path>(?:/Users|/Volumes|/private|/tmp|/var/folders)/[^\s\"'`<>]+)"
)
SENSITIVE_FILE_RE = re.compile(
    r"(?i)(all_keys\.json|config\.json|wechat_frida_keys\.log|frida_keys\.log|\.db(?:-wal|-shm)?|decrypted|chat-export|customer-memory)"
)


def truncate_text(value: Any, max_chars: int = MAX_TEXT_PREVIEW) -> str:
    text = "" if value is None else str(value)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"…<truncated {len(text) - max_chars} chars>"


def _redact_path_match(match: re.Match[str]) -> str:
    raw = match.group("path")
    clean = raw.rstrip(".,;:)]}")
    suffix = raw[len(clean) :]
    name = Path(clean).name
    if SENSITIVE_FILE_RE.search(clean):
        return f"<local_sensitive_path:{name}>{suffix}"
    return f"<local_path:{name}>{suffix}"


def redact_text(value: Any, max_chars: int = MAX_TEXT_PREVIEW) -> str:
    text = truncate_text(value, max_chars=max_chars)
    text = SQLCIPHER_LITERAL_RE.sub("<redacted_sqlcipher_literal>", text)
    text = LONG_HEX_RE.sub("<redacted_hex>", text)
    text = WXID_RE.sub("<redacted_wxid>", text)
    text = LOCAL_PATH_RE.sub(_redact_path_match, text)
    return text


def is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    if normalized in SENSITIVE_KEY_NAMES:
        return True
    return any(part in normalized for part in ("key_hex", "enc_key", "secret", "token", "password"))


def redact_obj(value: Any, max_text_chars: int = MAX_TEXT_PREVIEW) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = redact_obj(item, max_text_chars=max_text_chars)
        return out
    if isinstance(value, list):
        return [redact_obj(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, tuple):
        return [redact_obj(item, max_text_chars=max_text_chars) for item in value]
    if isinstance(value, Path):
        return redact_text(str(value), max_chars=max_text_chars)
    if isinstance(value, str):
        return redact_text(value, max_chars=max_text_chars)
    return value


def redacted_error_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not details:
        return {}
    redacted = redact_obj(details)
    return redacted if isinstance(redacted, dict) else {"details": redacted}
