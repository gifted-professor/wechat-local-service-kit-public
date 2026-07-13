#!/usr/bin/env python3

import argparse
import base64
import csv
import hashlib
import html
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, Optional

from wechat_common import (
    _extract_xml_attr,
    _extract_xml_field,
    _extract_xml_tag_text,
    _md5_hex,
    _normalize_text,
    _safe_slug,
    _timestamp_to_iso,
    _to_int,
    _write_json,
)
from wechat_contact_policy import notification_muted_from_chat_room_notify, notification_state_from_chat_room_notify

try:
    import zstandard as zstd
except Exception:
    zstd = None


MESSAGE_TYPE_MAP = {
    1: "text",
    3: "image",
    34: "voice",
    43: "video",
    47: "emoji",
    48: "location",
    49: "link",
    10000: "system",
    1048625: "file",
    1090519089: "voip",
    16777265: "mini_program",
    244813135921: "quote",
    25769803825: "file",
}

APP_MESSAGE_TYPES = {49, 244813135921, 1048625, 25769803825}


def _message_real_type(local_type: int) -> int:
    """Return the low 32-bit WeChat type while preserving simple values."""
    if not local_type:
        return 0
    if local_type <= 0xFFFFFFFF:
        return local_type
    return local_type & 0xFFFFFFFF


def _message_type_label(local_type: int) -> str:
    real_type = _message_real_type(local_type)
    return MESSAGE_TYPE_MAP.get(local_type) or MESSAGE_TYPE_MAP.get(real_type, "unknown")


def _is_app_message_type(local_type: int) -> bool:
    return local_type in APP_MESSAGE_TYPES or _message_real_type(local_type) in APP_MESSAGE_TYPES


def _get_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except Exception:
        return set()
    return {str(row[1]).lower() for row in rows if len(row) >= 2 and row[1]}



def _decode_sqlite_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, memoryview):
        value = bytes(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    return str(value)



def _is_mostly_printable_text(text: str) -> bool:
    sample = text[:600]
    if not sample:
        return False
    printable = sum(1 for ch in sample if ch.isprintable() or ch in {"\n", "\r", "\t"})
    return printable / len(sample) >= 0.85



def _looks_like_xml(text: str) -> bool:
    probe = str(text or "").strip().strip('"')
    return probe.startswith("<")



def _try_decode_text_blob(text: str) -> Optional[str]:
    t = str(text or "").strip()
    if not t:
        return None

    zstd_magic = b"\x28\xb5\x2f\xfd"

    if len(t) >= 16 and len(t) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", t):
        try:
            raw = bytes.fromhex(t)
            if raw.startswith(zstd_magic) and zstd is not None:
                out = zstd.decompress(raw)
                s2 = html.unescape(out.decode("utf-8", errors="ignore").strip())
                if _looks_like_xml(s2) or _is_mostly_printable_text(s2):
                    return s2
            s2 = html.unescape(raw.decode("utf-8", errors="ignore").strip())
            lower = s2.lower()
            if _looks_like_xml(s2) or ("<msg" in lower and "</msg>" in lower) or "<appmsg" in lower:
                return s2
        except Exception:
            return None

    if len(t) >= 24 and len(t) % 4 == 0 and re.fullmatch(r"[A-Za-z0-9+/=]+", t):
        try:
            raw = base64.b64decode(t, validate=True)
            if raw.startswith(zstd_magic) and zstd is not None:
                out = zstd.decompress(raw)
                s2 = html.unescape(out.decode("utf-8", errors="ignore").strip())
                if _looks_like_xml(s2) or _is_mostly_printable_text(s2):
                    return s2
            s2 = html.unescape(raw.decode("utf-8", errors="ignore").strip())
            lower = s2.lower()
            if _looks_like_xml(s2) or ("<msg" in lower and "</msg>" in lower) or "<appmsg" in lower:
                return s2
        except Exception:
            return None

    return None



def _decode_message_content(compress_value: Any, message_value: Any) -> str:
    msg_text = html.unescape(_decode_sqlite_text(message_value).strip())
    blob_text = _try_decode_text_blob(msg_text)
    if blob_text:
        msg_text = blob_text

    if isinstance(message_value, (bytes, bytearray, memoryview)):
        raw = bytes(message_value)
        if raw.startswith(b"\x28\xb5\x2f\xfd") and zstd is not None:
            try:
                out = zstd.decompress(raw)
                s = html.unescape(out.decode("utf-8", errors="ignore").strip())
                if _looks_like_xml(s) or _is_mostly_printable_text(s):
                    msg_text = s
            except Exception:
                pass

    if compress_value is None:
        return msg_text

    if isinstance(compress_value, str):
        s = html.unescape(compress_value.strip())
        s2 = _try_decode_text_blob(s)
        if s2:
            return s2
        if _looks_like_xml(s) or _is_mostly_printable_text(s):
            return s
        return msg_text

    data = None
    if isinstance(compress_value, memoryview):
        data = bytes(compress_value)
    elif isinstance(compress_value, (bytes, bytearray)):
        data = bytes(compress_value)

    if not data:
        return msg_text

    if data.startswith(b"\x28\xb5\x2f\xfd") and zstd is not None:
        try:
            out = zstd.decompress(data)
            s = html.unescape(out.decode("utf-8", errors="ignore").strip())
            if _looks_like_xml(s) or _is_mostly_printable_text(s):
                return s
        except Exception:
            pass

    try:
        s = html.unescape(data.decode("utf-8", errors="ignore").strip())
        s2 = _try_decode_text_blob(s)
        if s2:
            return s2
        if _looks_like_xml(s) or _is_mostly_printable_text(s):
            return s
    except Exception:
        pass

    return msg_text



def _parse_location_message(text: str) -> dict[str, Any]:
    return {
        "renderType": "location",
        "content": _extract_xml_field(text, "label") or _extract_xml_field(text, "poiname") or text,
        "locationPoiname": _extract_xml_field(text, "poiname"),
        "locationLabel": _extract_xml_field(text, "label") or _extract_xml_field(text, "poiaddress"),
        "longitude": _extract_xml_field(text, "x") or _extract_xml_attr(text, "location", "x"),
        "latitude": _extract_xml_field(text, "y") or _extract_xml_attr(text, "location", "y"),
    }



def _parse_system_message_content(text: str) -> str:
    content = _normalize_text(text)
    if not content:
        return "[系统消息]"
    if "拍了拍" in content:
        return "[拍一拍]"
    return content



def _parse_app_message(text: str) -> dict[str, Any]:
    content = _normalize_text(text)
    lower = content.lower()
    title = _extract_xml_field(content, "title") or _extract_xml_field(content, "des") or _extract_xml_field(content, "filename")
    description = _extract_xml_field(content, "des")
    url = _extract_xml_field(content, "url") or _extract_xml_field(content, "link")
    app_type = _extract_xml_field(content, "type")

    if "<refermsg" in lower:
        return {
            "renderType": "quote",
            "content": description or title or "[引用消息]",
            "quoteType": app_type,
            "quoteTitle": _extract_xml_field(content, "displayname") or title,
            "quoteContent": _extract_xml_field(content, "content") or description,
        }

    if "<appmsg" in lower:
        if any(token in lower for token in ["<location", "poiname", "poiaddress"]):
            return _parse_location_message(content)
        if any(token in lower for token in ["<type>6</type>", "<type>74</type>", "filename", "filesize"]):
            return {
                "renderType": "file",
                "content": title or description or "[文件]",
                "fileName": title,
                "fileSize": _extract_xml_field(content, "filesize"),
                "url": url,
            }
        if any(token in lower for token in ["pay_memo", "paysubtype", "transcationid", "transferid"]):
            return {
                "renderType": "transfer",
                "content": title or description or "转账",
                "paySubType": _extract_xml_field(content, "paysubtype"),
                "receiveStatus": _extract_xml_field(content, "receivestatus"),
            }
        return {
            "renderType": "link",
            "content": title or description or "[应用消息]",
            "title": title,
            "description": description,
            "url": url,
            "linkType": "mini_program" if "weappinfo" in lower else "link",
        }

    return {"renderType": "text", "content": content or "[应用消息]"}



def _split_group_sender_prefix(text: str) -> tuple[str, str]:
    if not text:
        return "", text
    sep = text.find(":\n")
    if sep <= 0:
        sep = text.find(": ")
    if sep <= 0:
        return "", text
    prefix = text[:sep].strip()
    body = text[sep + 2 :].lstrip()
    strong_hint = prefix.startswith("wxid_") or prefix.endswith("@chatroom") or "@" in prefix
    body_is_xml = body.startswith("<") or body.startswith('"<')
    if strong_hint or body_is_xml:
        return prefix, body
    return "", text



def _extract_sender_from_group_xml(xml_text: str) -> str:
    if not xml_text:
        return ""
    probe_text = re.sub(r"(<refermsg[^>]*>.*?</refermsg>)", "", xml_text, flags=re.IGNORECASE | re.DOTALL)
    value = _extract_xml_tag_text(probe_text, "fromusername")
    if value:
        return value
    return _extract_xml_attr(probe_text, "fromusername")



def _pick_message_text(local_type: int, raw_text: str, parsed: dict[str, Any]) -> str:
    real_type = _message_real_type(local_type)
    if local_type == 10000:
        return _parse_system_message_content(raw_text)
    if _is_app_message_type(local_type):
        return _normalize_text(parsed.get("content")) or _normalize_text(parsed.get("title")) or "[应用消息]"
    if real_type == 48:
        return _normalize_text(parsed.get("content")) or "[位置]"
    if real_type == 3:
        return "[图片]"
    if real_type == 34:
        return "[语音]"
    if real_type == 43:
        return "[视频]"
    if real_type == 47:
        return "[表情]"
    if raw_text and not raw_text.startswith("<") and not raw_text.startswith('"<'):
        return raw_text
    return _normalize_text(parsed.get("content")) or "[消息]"



def _table_rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql).fetchall()
    except Exception:
        return []



def load_contacts(contact_db_path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    conn = sqlite3.connect(str(contact_db_path))
    conn.row_factory = sqlite3.Row
    try:
        for table in ("contact", "stranger", "Contact", "Stranger"):
            columns = _get_table_columns(conn, table)
            if not columns or "username" not in columns:
                continue
            select_parts = []
            for key, fallback in [
                ("username", "''"),
                ("remark", "''"),
                ("nick_name", "''"),
                ("alias", "''"),
                ("flag", "0"),
                ("delete_flag", "0"),
                ("local_type", "0"),
                ("verify_flag", "0"),
                ("big_head_url", "''"),
                ("small_head_url", "''"),
                ("chat_room_notify", "NULL"),
            ]:
                select_parts.append(key if key in columns else f"{fallback} AS {key}")
            sql = f"SELECT {', '.join(select_parts)} FROM {table}"
            for row in _table_rows(conn, sql):
                username = _normalize_text(row["username"])
                if not username or username in out:
                    continue
                out[username] = {
                    "username": username,
                    "remark": _normalize_text(row["remark"]),
                    "nick_name": _normalize_text(row["nick_name"]),
                    "alias": _normalize_text(row["alias"]),
                    "flag": _to_int(row["flag"]),
                    "delete_flag": _to_int(row["delete_flag"]),
                    "local_type": _to_int(row["local_type"]),
                    "verify_flag": _to_int(row["verify_flag"]),
                    "big_head_url": _normalize_text(row["big_head_url"]),
                    "small_head_url": _normalize_text(row["small_head_url"]),
                    "chat_room_notify": _to_int(row["chat_room_notify"]),
                    "notification_muted": notification_muted_from_chat_room_notify(row["chat_room_notify"]),
                    "notification_state": notification_state_from_chat_room_notify(row["chat_room_notify"]),
                }
        return out
    finally:
        conn.close()



def _pick_display_name(contact: dict[str, Any], username: str) -> str:
    return (
        _normalize_text(contact.get("remark"))
        or _normalize_text(contact.get("nick_name"))
        or _normalize_text(contact.get("alias"))
        or username
    )



def load_sessions(session_db_path: Path, contacts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    conn = sqlite3.connect(str(session_db_path))
    conn.row_factory = sqlite3.Row
    try:
        queries = [
            "SELECT username, COALESCE(sort_timestamp, 0) AS ts FROM SessionTable",
            "SELECT username, COALESCE(last_timestamp, 0) AS ts FROM SessionTable",
            "SELECT username, COALESCE(sort_timestamp, 0) AS ts FROM sessiontable",
            "SELECT username, COALESCE(last_timestamp, 0) AS ts FROM sessiontable",
        ]
        rows: list[sqlite3.Row] = []
        for sql in queries:
            rows = _table_rows(conn, sql)
            if rows:
                break
        for row in rows:
            username = _normalize_text(row["username"])
            if not username:
                continue
            contact = contacts.get(username, {})
            out[username] = {
                "conversation_id": _md5_hex(username),
                "conversation_username": username,
                "display_name": _pick_display_name(contact, username),
                "remark": _normalize_text(contact.get("remark")),
                "nick_name": _normalize_text(contact.get("nick_name")),
                "alias": _normalize_text(contact.get("alias")),
                "conversation_type": "group" if username.endswith("@chatroom") else "official" if username.startswith("gh_") or _to_int(contact.get("verify_flag")) else "friend",
                "last_active_at": _timestamp_to_iso(row["ts"]),
                "message_count": 0,
                "chat_room_notify": contact.get("chat_room_notify"),
                "notification_muted": contact.get("notification_muted"),
                "notification_state": contact.get("notification_state") or "unknown",
            }
        return out
    finally:
        conn.close()



def _iter_message_db_paths(message_dir: Path) -> list[Path]:
    candidates = []
    for path in sorted(message_dir.glob("*.db")):
        name = path.name.lower()
        if re.match(r"^message(_\d+)?\.db$", name):
            candidates.append(path)
    return candidates



def _resolve_prepared_db_path(account_root: Path, *relative_candidates: str) -> Path:
    for relative in relative_candidates:
        path = account_root / relative
        if path.exists() and path.stat().st_size > 0:
            return path
    return account_root / relative_candidates[0]



def _resolve_msg_table_name(conn: sqlite3.Connection, username: str) -> Optional[str]:
    if not username:
        return None
    md5_hex = hashlib.md5(username.encode("utf-8")).hexdigest()
    expected = f"msg_{md5_hex}".lower()
    expected_chat = f"chat_{md5_hex}".lower()
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = [str(row[0]) for row in rows if row and row[0]]
    lower_to_actual = {name.lower(): name for name in names}
    return lower_to_actual.get(expected) or lower_to_actual.get(expected_chat)



def _iter_messages_for_conversation(db_path: Path, username: str) -> Iterator[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        table = _resolve_msg_table_name(conn, username)
        if not table:
            return
        columns = _get_table_columns(conn, table)
        wanted = [
            ("local_id", ["local_id", "localid"]),
            ("message_svr_id", ["message_svr_id", "msgsvrid", "msg_svr_id"]),
            ("create_time", ["create_time", "createtime"]),
            ("is_sender", ["is_sender", "issender"]),
            ("local_type", ["local_type", "type"]),
            ("sub_type", ["sub_type", "subtype"]),
            ("message_content", ["message_content", "content"]),
            ("compress_content", ["compress_content", "compresscontent"]),
            ("bytes_extra", ["bytes_extra", "bytesextra"]),
            ("status", ["status"]),
        ]
        select_parts = []
        for alias, names in wanted:
            actual = next((name for name in names if name in columns), None)
            if actual:
                select_parts.append(f'"{actual}" AS "{alias}"')
            else:
                select_parts.append(f"NULL AS \"{alias}\"")
        select_sql = ', '.join(select_parts)
        query = f'SELECT {select_sql} FROM "{table}" ORDER BY create_time ASC, local_id ASC'
        rows = conn.execute(query).fetchall()
        for row in rows:
            raw_text = _decode_message_content(row["compress_content"], row["message_content"])
            local_type = _to_int(row["local_type"])
            real_type = _message_real_type(local_type)
            if real_type == 48:
                parsed = _parse_location_message(raw_text)
            elif _is_app_message_type(local_type):
                parsed = _parse_app_message(raw_text)
            elif real_type == 10000:
                parsed = {"renderType": "system", "content": _parse_system_message_content(raw_text)}
            else:
                parsed = {"renderType": _message_type_label(local_type) if real_type else "text", "content": raw_text}

            sender_username = ""
            body_text = raw_text
            prefix, body = _split_group_sender_prefix(raw_text)
            if prefix:
                sender_username = prefix
                body_text = body
            xml_sender = _extract_sender_from_group_xml(raw_text)
            if xml_sender:
                sender_username = xml_sender

            yield {
                "message_id": _to_int(row["local_id"]),
                "message_svr_id": _normalize_text(row["message_svr_id"]),
                "timestamp": _timestamp_to_iso(row["create_time"]),
                "direction": "sent" if _to_int(row["is_sender"]) else "received",
                "message_type": _message_type_label(local_type),
                "render_type": _normalize_text(parsed.get("renderType")) or _message_type_label(local_type),
                "text": _pick_message_text(local_type, body_text, parsed),
                "sender_id": sender_username,
                "raw_type": {"local_type": local_type, "real_type": real_type, "sub_type": _to_int(row["sub_type"])},
                "attachment_meta": {k: v for k, v in parsed.items() if k not in {"renderType", "content"} and v not in (None, "", [])},
                "raw_payload": {
                    "message_content": _normalize_text(row["message_content"]),
                    "compress_content": _normalize_text(row["compress_content"]),
                    "bytes_extra": _normalize_text(row["bytes_extra"]),
                    "status": _to_int(row["status"]),
                },
                "source_db": db_path.name,
            }
    finally:
        conn.close()



def export_chat_history(account_root: Path, output_dir: Path, conversation_filter: Optional[str] = None) -> dict[str, Any]:
    contact_db = _resolve_prepared_db_path(account_root, "contact/contact.db", "contact.db")
    session_db = _resolve_prepared_db_path(account_root, "session/session.db", "session.db")
    message_dir = account_root / "message"

    contacts = load_contacts(contact_db)
    sessions = load_sessions(session_db, contacts)
    message_db_paths = _iter_message_db_paths(message_dir)

    target_sessions = {}
    for username, session in sessions.items():
        if conversation_filter and conversation_filter not in username and conversation_filter not in session["display_name"]:
            continue
        target_sessions[username] = session

    conversations_dir = output_dir / "conversations"
    conversations_dir.mkdir(parents=True, exist_ok=True)

    conversation_index = []
    total_messages = 0
    type_counter: Counter[str] = Counter()

    for username, session in target_sessions.items():
        display_name = session["display_name"]
        conversation_id = session["conversation_id"]
        conversation_path = conversations_dir / f"{conversation_id}.jsonl"
        count = 0
        with conversation_path.open("w", encoding="utf-8") as f:
            for db_path in message_db_paths:
                for message in _iter_messages_for_conversation(db_path, username):
                    sender_contact = contacts.get(message["sender_id"], {}) if message["sender_id"] else {}
                    sender_name = (
                        _pick_display_name(sender_contact, message["sender_id"])
                        if message["sender_id"]
                        else (display_name if message["direction"] == "received" and not username.endswith("@chatroom") else "")
                    )
                    row = {
                        "conversation_id": conversation_id,
                        "conversation_username": username,
                        "conversation_name": display_name,
                        "conversation_type": session["conversation_type"],
                        "notification_muted": session.get("notification_muted"),
                        "notification_state": session.get("notification_state") or "unknown",
                        "chat_room_notify": session.get("chat_room_notify"),
                        "sender_id": message["sender_id"],
                        "sender_name": sender_name,
                        **message,
                    }
                    f.write(json.dumps(row, ensure_ascii=False))
                    f.write("\n")
                    count += 1
                    total_messages += 1
                    type_counter[row["render_type"]] += 1
        session["message_count"] = count
        conversation_index.append(
            {
                "conversation_id": conversation_id,
                "conversation_username": username,
                "display_name": display_name,
                "remark": session.get("remark") or "",
                "nick_name": session.get("nick_name") or "",
                "alias": session.get("alias") or "",
                "conversation_type": session["conversation_type"],
                "notification_muted": session.get("notification_muted"),
                "notification_state": session.get("notification_state") or "unknown",
                "chat_room_notify": session.get("chat_room_notify"),
                "last_active_at": session["last_active_at"],
                "message_count": count,
                "file": f"conversations/{conversation_id}.jsonl",
                "file_label": _safe_slug(display_name),
            }
        )

    _write_json(output_dir / "contacts.json", sorted(contacts.values(), key=lambda x: x["username"]))
    _write_json(output_dir / "sessions.json", sorted(target_sessions.values(), key=lambda x: (x.get("display_name") or x.get("conversation_username") or "")))
    _write_json(output_dir / "conversation_index.json", sorted(conversation_index, key=lambda x: x["display_name"]))

    with (output_dir / "messages_summary.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "conversation_id",
                "conversation_username",
                "display_name",
                "remark",
                "nick_name",
                "alias",
                "conversation_type",
                "notification_muted",
                "notification_state",
                "chat_room_notify",
                "last_active_at",
                "message_count",
                "file",
                "file_label",
            ],
        )
        writer.writeheader()
        for row in conversation_index:
            writer.writerow(row)

    manifest = {
        "account_root": str(account_root),
        "source_dbs": {
            "contact": str(contact_db),
            "session": str(session_db),
            "message": [str(path) for path in message_db_paths],
        },
        "total_conversations": len(conversation_index),
        "total_messages": total_messages,
        "type_counts": dict(type_counter),
        "filter": {"conversation": conversation_filter or ""},
        "warnings": [],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest



def main() -> None:
    parser = argparse.ArgumentParser(description="Parse decrypted WeChat chat history and export structured files")
    parser.add_argument("--account-root", required=True, help="Path to decrypted db_storage root containing contact.db and session.db")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--conversation", help="Optional conversation username or display name filter")
    args = parser.parse_args()

    account_root = Path(args.account_root).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    manifest = export_chat_history(account_root, output_dir, args.conversation)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
