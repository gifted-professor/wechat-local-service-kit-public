#!/usr/bin/env python3

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


MUTED_CHAT_ROOM_NOTIFY_VALUE = 1
UNMUTED_CHAT_ROOM_NOTIFY_VALUE = 0


def _to_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def notification_muted_from_chat_room_notify(value: Any) -> Optional[bool]:
    raw = _to_int(value)
    if raw == MUTED_CHAT_ROOM_NOTIFY_VALUE:
        return True
    if raw == UNMUTED_CHAT_ROOM_NOTIFY_VALUE:
        return False
    return None


def notification_state_from_chat_room_notify(value: Any) -> str:
    muted = notification_muted_from_chat_room_notify(value)
    if muted is True:
        return "muted"
    if muted is False:
        return "unmuted"
    return "unknown"


def infer_conversation_type(username: str, verify_flag: Any = None) -> str:
    username = str(username or "").strip()
    if username.endswith("@chatroom"):
        return "group"
    if username.startswith("gh_") or (_to_int(verify_flag) or 0):
        return "official"
    if username:
        return "friend"
    return "unknown"


def normalize_private_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"private", "friend"}:
        return "private"
    if text in {"group", "chatroom"}:
        return "group"
    if text in {"official", "official_account", "mp"}:
        return "official"
    return text or "unknown"


def is_private_target(target: dict[str, Any]) -> bool:
    chat_type = normalize_private_type(target.get("chat_type") or target.get("conversation_type"))
    if chat_type == "private":
        return True
    if chat_type in {"group", "official"}:
        return False
    return normalize_private_type(infer_conversation_type(str(target.get("conversation_username") or ""))) == "private"


def _project_roots() -> list[Path]:
    roots = [Path.cwd(), Path(__file__).resolve().parents[1]]
    deduped = []
    seen = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def _json_path_value(path: Path, keys: list[str]) -> Optional[Path]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if not value:
        return None
    return Path(str(value)).expanduser()


def default_contact_db_candidates() -> list[Path]:
    candidates: list[Path] = []
    for root in _project_roots():
        candidates.extend(
            [
                root / "out/chat-export/workspace/db_storage/contact/contact.db",
                root / ".cache-auto-reply/contact/contact.db",
                root / ".cache-watch/contact/contact.db",
            ]
        )
        manifest_contact = _json_path_value(root / "out/chat-export/export/manifest.json", ["source_dbs", "contact"])
        prepared_contact = _json_path_value(root / "out/chat-export/workspace/prepared_dbs.json", ["prepared", "contact"])
        for path in [manifest_contact, prepared_contact]:
            if path:
                candidates.append(path)

    deduped = []
    seen = set()
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def resolve_contact_db(path: Optional[str | Path] = None) -> Optional[Path]:
    if path:
        candidate = Path(path).expanduser().resolve()
        return candidate if candidate.exists() else None
    for candidate in default_contact_db_candidates():
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except Exception:
        return set()
    return {str(row[1]).lower() for row in rows if len(row) >= 2 and row[1]}


def _select_part(columns: set[str], name: str, fallback: str = "NULL") -> str:
    return name if name in columns else f"{fallback} AS {name}"


def load_contact_policies(contact_db_path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    conn = sqlite3.connect(str(contact_db_path))
    conn.row_factory = sqlite3.Row
    try:
        for table in ("contact", "stranger", "Contact", "Stranger"):
            columns = _table_columns(conn, table)
            if not columns or "username" not in columns:
                continue
            select_sql = ", ".join(
                [
                    _select_part(columns, "username", "''"),
                    _select_part(columns, "verify_flag", "0"),
                    _select_part(columns, "chat_room_notify", "NULL"),
                    _select_part(columns, "flag", "0"),
                    _select_part(columns, "delete_flag", "0"),
                ]
            )
            rows = conn.execute(f'SELECT {select_sql} FROM "{table}"').fetchall()
            for row in rows:
                username = str(row["username"] or "").strip()
                if not username or username in out:
                    continue
                chat_room_notify = row["chat_room_notify"]
                out[username] = {
                    "conversation_username": username,
                    "conversation_type": infer_conversation_type(username, row["verify_flag"]),
                    "chat_room_notify": _to_int(chat_room_notify),
                    "notification_muted": notification_muted_from_chat_room_notify(chat_room_notify),
                    "notification_state": notification_state_from_chat_room_notify(chat_room_notify),
                    "notification_policy_source": "contact.chat_room_notify",
                    "flag": _to_int(row["flag"]) or 0,
                    "delete_flag": _to_int(row["delete_flag"]) or 0,
                }
        return out
    finally:
        conn.close()


def load_contact_policy(username: str, contact_db: Optional[str | Path] = None) -> Optional[dict[str, Any]]:
    username = str(username or "").strip()
    if not username:
        return None
    contact_db_path = resolve_contact_db(contact_db)
    if not contact_db_path:
        return None
    try:
        return load_contact_policies(contact_db_path).get(username)
    except sqlite3.DatabaseError:
        return None


def enrich_target_with_contact_policy(
    target: dict[str, Any],
    contact_db: Optional[str | Path] = None,
) -> dict[str, Any]:
    enriched = dict(target)
    username = str(enriched.get("conversation_username") or enriched.get("username") or "").strip()
    policy = load_contact_policy(username, contact_db)
    if policy:
        for key in [
            "chat_room_notify",
            "notification_muted",
            "notification_state",
            "notification_policy_source",
            "flag",
            "delete_flag",
        ]:
            enriched[key] = policy.get(key)
        if not enriched.get("conversation_type"):
            enriched["conversation_type"] = policy.get("conversation_type") or "unknown"
        if not enriched.get("chat_type"):
            enriched["chat_type"] = normalize_private_type(policy.get("conversation_type"))
        return enriched

    if "notification_muted" not in enriched:
        enriched["notification_muted"] = None
    if "notification_state" not in enriched:
        enriched["notification_state"] = "unknown"
    if "notification_policy_source" not in enriched:
        enriched["notification_policy_source"] = "unavailable"
    if not enriched.get("conversation_type"):
        enriched["conversation_type"] = infer_conversation_type(username)
    if not enriched.get("chat_type"):
        enriched["chat_type"] = normalize_private_type(enriched.get("conversation_type"))
    return enriched


def contact_policy_block(
    target: dict[str, Any],
    *,
    allow_non_private: bool = False,
    include_muted: bool = False,
    allow_unknown_notification_state: bool = False,
) -> Optional[dict[str, Any]]:
    if not allow_non_private and not is_private_target(target):
        return {
            "reason": "non_private_conversation",
            "chat_type": target.get("chat_type") or target.get("conversation_type") or "unknown",
        }

    if include_muted:
        return None

    muted = target.get("notification_muted")
    if muted is True:
        return {
            "reason": "notification_muted",
            "notification_state": target.get("notification_state") or "muted",
            "notification_policy_source": target.get("notification_policy_source") or "unknown",
        }
    if muted is False:
        return None
    if not allow_unknown_notification_state:
        return {
            "reason": "notification_state_unknown",
            "notification_state": target.get("notification_state") or "unknown",
            "notification_policy_source": target.get("notification_policy_source") or "unknown",
        }
    return None
