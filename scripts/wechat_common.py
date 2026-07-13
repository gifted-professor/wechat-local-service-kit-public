#!/usr/bin/env python3

import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _decode_xml_entities(s: str) -> str:
    s = html.unescape(s)
    s = re.sub(r'&#x([0-9a-fA-F]+);', lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), s)
    return s


def _extract_xml_field(xml_str: str, tag: str) -> str:
    if not xml_str:
        return ""
    m = re.search(rf'<{re.escape(tag)}>(.*?)</{re.escape(tag)}>', xml_str, re.DOTALL | re.IGNORECASE)
    return _decode_xml_entities(m.group(1).strip()) if m else ""


def _extract_xml_attr(xml_str: str, tag: str, attr: Optional[str] = None) -> str:
    if not xml_str:
        return ""
    if attr is None:
        attr_name = re.escape(tag)
        m = re.search(rf'{attr_name}="([^"]*)"', xml_str, re.IGNORECASE)
        return _decode_xml_entities(m.group(1).strip()) if m else ""
    tag_name = re.escape(tag)
    attr_name = re.escape(attr)
    m = re.search(rf'<{tag_name}\s[^>]*{attr_name}="([^"]*)"', xml_str, re.IGNORECASE)
    return _decode_xml_entities(m.group(1).strip()) if m else ""


def _extract_xml_tag_text(xml_str: str, tag: str) -> str:
    return _extract_xml_field(xml_str, tag)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, memoryview):
        value = bytes(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="ignore").strip()
        except Exception:
            return ""
    return str(value).strip()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _to_optional_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _timestamp_to_iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        raw = int(value)
    except Exception:
        text = _normalize_text(value)
        if not text:
            return None
        try:
            return datetime.fromisoformat(text).isoformat()
        except Exception:
            return None

    abs_raw = abs(raw)
    if abs_raw >= 10**15:
        ts = raw / 1_000_000
    elif abs_raw >= 10**12:
        ts = raw / 1_000
    else:
        ts = raw

    try:
        return datetime.fromtimestamp(ts).isoformat()
    except Exception:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return None


def _md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, data: Any) -> None:
    _ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    _ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def _safe_slug(value: str) -> str:
    text = _normalize_text(value)
    if not text:
        return "untitled"
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text).strip("._")
    return text[:80] or "untitled"
