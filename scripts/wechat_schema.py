#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from wechat_common import _md5_hex, _normalize_text, _timestamp_to_iso
from wechat_privacy import redact_obj

SCHEMA_VERSION = "wechat_export_v2"
CONVERSATION_SCHEMA_VERSION = "conversation_v2"
MESSAGE_SCHEMA_VERSION = "message_v2"
FAVORITE_SCHEMA_VERSION = "favorite_v1"
SNS_SCHEMA_VERSION = "sns_v1"
ATTACHMENT_SCHEMA_VERSION = "attachment_v1"
MANIFEST_SCHEMA_VERSION = "wechat_manifest_v2"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _string(value: Any) -> str:
    return _normalize_text(value)


def _list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _raw(raw_payload: Any, *, include_raw: bool = False, redact_raw: bool = True) -> Any:
    if not include_raw:
        return {}
    return redact_obj(raw_payload) if redact_raw else raw_payload


def stable_id(*parts: Any, prefix: str = "") -> str:
    text = "|".join(_string(part) for part in parts if _string(part))
    digest = _md5_hex(text or prefix or "empty")
    return f"{prefix}_{digest}" if prefix else digest


def build_source_ref(
    *,
    provider: str,
    command: Optional[list[str] | str] = None,
    db_path: str = "",
    profile: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": provider,
        "command": command or [],
        "db_path": db_path,
        "profile": profile,
    }
    if extra:
        payload.update(extra)
    return redact_obj(payload)


def build_manifest(
    *,
    source_provider: str,
    export_root: str,
    counts: Optional[dict[str, Any]] = None,
    filters: Optional[dict[str, Any]] = None,
    coverage: Optional[dict[str, Any]] = None,
    warnings: Optional[list[str]] = None,
    privacy: Optional[dict[str, Any]] = None,
    generated_at: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": generated_at or utc_now_iso(),
        "source_provider": source_provider,
        "export_root": export_root,
        "counts": counts or {},
        "filters": filters or {},
        "coverage": coverage or {},
        "warnings": warnings or [],
        "privacy": privacy or {"raw_payload": "omitted_by_default", "redaction": "enabled"},
    }


def build_conversation_row(
    *,
    conversation_username: str = "",
    display_name: str = "",
    remark: str = "",
    nick_name: str = "",
    alias: str = "",
    conversation_type: str = "unknown",
    last_active_at: Any = "",
    message_count: int = 0,
    notification_muted: Any = None,
    notification_state: str = "unknown",
    chat_room_notify: Any = None,
    source_provider: str = "",
    raw_payload: Any = None,
    include_raw: bool = False,
    redact_raw: bool = True,
) -> dict[str, Any]:
    username = _string(conversation_username)
    name = _string(display_name) or username
    return {
        "schema_version": CONVERSATION_SCHEMA_VERSION,
        "conversation_id": stable_id(username or name, prefix="conv"),
        "conversation_username": username,
        "display_name": name,
        "remark": _string(remark),
        "nick_name": _string(nick_name),
        "alias": _string(alias),
        "conversation_type": _string(conversation_type) or "unknown",
        "last_active_at": _timestamp_to_iso(last_active_at) or _string(last_active_at),
        "message_count": int(message_count or 0),
        "notification_muted": notification_muted,
        "notification_state": _string(notification_state) or "unknown",
        "chat_room_notify": chat_room_notify,
        "source_provider": source_provider,
        "raw_payload": _raw(raw_payload, include_raw=include_raw, redact_raw=redact_raw),
    }


def build_message_row(
    *,
    conversation_id: str,
    conversation_username: str = "",
    conversation_name: str = "",
    conversation_type: str = "unknown",
    sender_id: str = "",
    sender_name: str = "",
    message_id: Any = "",
    message_svr_id: Any = "",
    timestamp: Any = "",
    direction: str = "unknown",
    message_type: str = "unknown",
    render_type: str = "unknown",
    text: str = "",
    raw_type: Any = None,
    attachment_meta: Any = None,
    entities: Optional[list[Any]] = None,
    source_provider: str = "",
    source_db: str = "",
    raw_payload: Any = None,
    include_raw: bool = False,
    redact_raw: bool = True,
) -> dict[str, Any]:
    return {
        "schema_version": MESSAGE_SCHEMA_VERSION,
        "conversation_id": _string(conversation_id),
        "conversation_username": _string(conversation_username),
        "conversation_name": _string(conversation_name),
        "conversation_type": _string(conversation_type) or "unknown",
        "sender_id": _string(sender_id),
        "sender_name": _string(sender_name),
        "message_id": _string(message_id),
        "message_svr_id": _string(message_svr_id),
        "timestamp": _timestamp_to_iso(timestamp) or _string(timestamp),
        "direction": _string(direction) or "unknown",
        "message_type": _string(message_type) or "unknown",
        "render_type": _string(render_type or message_type) or "unknown",
        "text": _string(text),
        "raw_type": raw_type or {},
        "attachment_meta": attachment_meta or {},
        "entities": entities or [],
        "source_provider": source_provider,
        "source_db": source_db,
        "raw_payload": _raw(raw_payload, include_raw=include_raw, redact_raw=redact_raw),
    }


def build_favorite_row(
    *,
    favorite_id: Any = "",
    favorite_type: str = "",
    title: str = "",
    desc: str = "",
    link: str = "",
    tags: Any = None,
    source: str = "",
    timestamp: Any = "",
    attachment_refs: Any = None,
    source_provider: str = "",
    raw_payload: Any = None,
    include_raw: bool = False,
    redact_raw: bool = True,
) -> dict[str, Any]:
    fid = _string(favorite_id) or stable_id(favorite_type, title, link, timestamp, prefix="fav")
    return {
        "schema_version": FAVORITE_SCHEMA_VERSION,
        "favorite_id": fid,
        "type": _string(favorite_type),
        "title": _string(title),
        "desc": _string(desc),
        "link": _string(link),
        "tags": _list(tags),
        "source": _string(source),
        "timestamp": _timestamp_to_iso(timestamp) or _string(timestamp),
        "attachment_refs": _list(attachment_refs),
        "source_provider": source_provider,
        "raw_payload": _raw(raw_payload, include_raw=include_raw, redact_raw=redact_raw),
    }


def build_sns_row(
    *,
    sns_id: Any = "",
    author_username: str = "",
    author_name: str = "",
    timestamp: Any = "",
    content: str = "",
    media_refs: Any = None,
    like_count: Any = 0,
    comment_count: Any = 0,
    comments: Any = None,
    source_provider: str = "",
    raw_payload: Any = None,
    include_raw: bool = False,
    redact_raw: bool = True,
) -> dict[str, Any]:
    sid = _string(sns_id) or stable_id(author_username, author_name, timestamp, content, prefix="sns")
    return {
        "schema_version": SNS_SCHEMA_VERSION,
        "sns_id": sid,
        "author_username": _string(author_username),
        "author_name": _string(author_name),
        "timestamp": _timestamp_to_iso(timestamp) or _string(timestamp),
        "content": _string(content),
        "media_refs": _list(media_refs),
        "like_count": like_count or 0,
        "comment_count": comment_count or 0,
        "comments": _list(comments),
        "source_provider": source_provider,
        "raw_payload": _raw(raw_payload, include_raw=include_raw, redact_raw=redact_raw),
    }


def build_attachment_row(
    *,
    attachment_id: Any = "",
    conversation_id: str = "",
    message_id: Any = "",
    kind: str = "",
    original_path: str = "",
    export_path: str = "",
    mime: str = "",
    size: Any = 0,
    sha256: str = "",
    status: str = "indexed",
    derived_text: str = "",
    source_provider: str = "",
    raw_payload: Any = None,
    include_raw: bool = False,
    redact_raw: bool = True,
) -> dict[str, Any]:
    aid = _string(attachment_id) or stable_id(conversation_id, message_id, kind, original_path, export_path, prefix="att")
    return {
        "schema_version": ATTACHMENT_SCHEMA_VERSION,
        "attachment_id": aid,
        "conversation_id": _string(conversation_id),
        "message_id": _string(message_id),
        "kind": _string(kind),
        "original_path": _string(redact_obj(original_path)),
        "export_path": _string(redact_obj(export_path)),
        "mime": _string(mime),
        "size": size or 0,
        "sha256": _string(sha256),
        "status": _string(status) or "indexed",
        "derived_text": _string(derived_text),
        "source_provider": source_provider,
        "raw_payload": _raw(raw_payload, include_raw=include_raw, redact_raw=redact_raw),
    }
