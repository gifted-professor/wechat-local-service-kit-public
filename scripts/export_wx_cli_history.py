#!/usr/bin/env python3

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wechat_common import _ensure_dir, _md5_hex, _safe_slug, _timestamp_to_iso, _write_json, _write_jsonl
from wechat_privacy import redact_obj
from wx_cli_adapter import (
    WxCliError,
    get_history,
    get_sns_feed,
    get_sns_notifications,
    get_stats,
    list_contacts,
    list_favorites,
    list_group_members,
    list_sessions,
)
from wx_cli_profile import active_profile_summary


SESSION_SCHEMA_VERSION = "wx_cli_history_export_v1"

CONVERSATION_TYPE_MAP = {
    "private": "friend",
    "group": "group",
    "official_account": "official",
}

MESSAGE_TYPE_MAP = {
    "文本": "text",
    "图片": "image",
    "表情": "emoji",
    "语音": "voice",
    "视频": "video",
    "位置": "location",
    "链接": "link",
    "链接/文件": "file",
    "文件": "file",
    "通话": "call",
    "系统": "system",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export WeChat sessions and paged history through wx-cli into local JSONL files")
    parser.add_argument("--output", required=True, help="output directory; export files are written under <output>/export")
    parser.add_argument("--wx-cli-profile", help="optional wx-cli profile directory or config.json path")
    parser.add_argument("--conversation", default="", help="optional conversation filter by username or display name")
    parser.add_argument("--private-only", action="store_true", help="only export private/friend conversations")
    parser.add_argument("--session-limit", type=int, default=10000, help="maximum sessions to ask wx-cli for")
    parser.add_argument("--history-batch-size", type=int, default=200, help="history page size per wx-cli request")
    parser.add_argument("--max-messages-per-conversation", type=int, help="optional cap for each conversation")
    parser.add_argument("--since", default="", help="optional start date passed to wx history, YYYY-MM-DD")
    parser.add_argument("--until", default="", help="optional end date passed to wx history, YYYY-MM-DD")
    parser.add_argument("--include-contacts", action="store_true", help="also export wx contacts into contacts.json")
    parser.add_argument("--include-members", action="store_true", help="also export group members under export/members")
    parser.add_argument("--include-favorites", action="store_true", help="also export wx favorites into favorites.jsonl")
    parser.add_argument("--include-sns", action="store_true", help="also export local SNS feed/notifications into sns_*.jsonl")
    parser.add_argument("--include-stats", action="store_true", help="also export per-conversation stats into stats.json")
    parser.add_argument("--include-raw", action="store_true", help="include raw wx-cli payloads in exported rows")
    parser.add_argument("--raw-redaction", choices=("safe", "full", "none"), default="safe", help="raw payload handling when --include-raw is set")
    parser.add_argument("--contact-limit", type=int, default=1000, help="maximum contacts to fetch when --include-contacts is set")
    parser.add_argument("--member-limit", type=int, default=500, help="maximum group members per conversation when --include-members is set")
    parser.add_argument("--favorite-limit", type=int, default=1000, help="maximum favorites to fetch when --include-favorites is set")
    parser.add_argument("--sns-limit", type=int, default=200, help="maximum SNS feed/notification items when --include-sns is set")
    return parser.parse_args()


def _conversation_type(session: dict[str, Any]) -> str:
    chat_type = str(session.get("chat_type") or "").strip()
    return CONVERSATION_TYPE_MAP.get(chat_type, chat_type or "unknown")


def _message_type(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return MESSAGE_TYPE_MAP.get(text, text.lower())


def _message_timestamp_iso(message: dict[str, Any]) -> str:
    raw = message.get("timestamp")
    iso = _timestamp_to_iso(raw)
    if iso:
        return iso

    raw_payload = message.get("raw_payload")
    if isinstance(raw_payload, dict):
        for key in ("timestamp", "create_time", "time"):
            iso = _timestamp_to_iso(raw_payload.get(key))
            if iso:
                return iso

    return str(raw or "")


def _set_profile_env(profile: str) -> str:
    previous = os.environ.get("WX_CLI_CONFIG_DIR", "")
    if profile:
        os.environ["WX_CLI_CONFIG_DIR"] = profile
    return previous


def _restore_profile_env(previous: str, profile_was_set: bool) -> None:
    if not profile_was_set:
        return
    if previous:
        os.environ["WX_CLI_CONFIG_DIR"] = previous
    else:
        os.environ.pop("WX_CLI_CONFIG_DIR", None)


def _matches_conversation(session: dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    target = needle.strip().lower()
    fields = [
        session.get("conversation_username"),
        session.get("display_name"),
        session.get("remark"),
        session.get("alias"),
    ]
    return any(target in str(value or "").lower() for value in fields)


def _dedupe_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for session in sessions:
        username = str(session.get("conversation_username") or "").strip()
        if not username or username in seen:
            continue
        seen.add(username)
        out.append(session)
    return out


def _select_sessions(args: argparse.Namespace) -> list[dict[str, Any]]:
    sessions = _dedupe_sessions(list_sessions(limit=args.session_limit))
    selected = []
    for session in sessions:
        if args.private_only and _conversation_type(session) != "friend":
            continue
        if args.conversation and not _matches_conversation(session, args.conversation):
            continue
        selected.append(session)
    return selected


def _raw_payload(raw: Any, args: argparse.Namespace) -> Any:
    if not args.include_raw or args.raw_redaction == "none":
        return {}
    if args.raw_redaction == "safe":
        return redact_obj(raw)
    return raw


def _dedupe_contacts(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for contact in contacts:
        key = str(contact.get("username") or contact.get("conversation_username") or contact.get("display_name") or "").strip()
        if not key:
            key = json.dumps(contact, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(contact)
    return out


def _contact_export_row(contact: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    raw = contact.get("raw_payload") if isinstance(contact.get("raw_payload"), dict) else {}
    username = str(contact.get("username") or contact.get("conversation_username") or "").strip()
    return {
        "username": username,
        "display_name": str(contact.get("display_name") or username),
        "remark": str(contact.get("remark") or ""),
        "nick_name": str(raw.get("nick_name") or raw.get("nickname") or contact.get("display_name") or ""),
        "alias": str(contact.get("alias") or raw.get("alias") or ""),
        "conversation_type": CONVERSATION_TYPE_MAP.get(str(contact.get("chat_type") or ""), str(contact.get("chat_type") or "unknown")),
        "source_provider": contact.get("source_provider") or "wx_cli",
        "raw_payload": _raw_payload(raw, args),
    }


def _load_full_history(session: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    chat = str(session.get("conversation_username") or session.get("display_name") or "").strip()
    if not chat:
        return []

    offset = 0
    page_size = max(int(args.history_batch_size or 200), 1)
    total_loaded = 0
    batches: list[list[dict[str, Any]]] = []

    while True:
        remaining = None
        if args.max_messages_per_conversation is not None:
            remaining = max(args.max_messages_per_conversation - total_loaded, 0)
            if remaining <= 0:
                break

        limit = min(page_size, remaining) if remaining is not None else page_size
        batch = get_history(chat, limit, offset=offset, since=args.since, until=args.until)
        if not batch:
            break

        batches.append(batch)
        batch_count = len(batch)
        total_loaded += batch_count
        offset += batch_count

        if batch_count < limit:
            break

    # wx history returns the newest window first; reverse the windows so the
    # final JSONL stays oldest -> newest like the DB export path.
    ordered: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for batch in reversed(batches):
        for message in batch:
            key = (
                str(message.get("message_id") or ""),
                str(message.get("timestamp") or ""),
                str(message.get("text") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            ordered.append(message)
    return ordered


def _infer_direction(session: dict[str, Any], message: dict[str, Any]) -> str:
    direction = str(message.get("direction") or "").strip().lower()
    if direction in {"sent", "received"}:
        return direction

    sender = str(message.get("sender_id") or "").strip()
    conversation_type = _conversation_type(session)
    if conversation_type in {"friend", "official"}:
        return "received" if not sender else "sent"
    if conversation_type == "group":
        return "sent" if not sender else "received"
    return "unknown"


def _sender_name(session: dict[str, Any], message: dict[str, Any], direction: str) -> str:
    sender = str(message.get("sender_id") or "").strip()
    if sender:
        return sender
    if direction == "received" and _conversation_type(session) != "group":
        return str(session.get("display_name") or session.get("conversation_username") or "")
    return ""


def _session_base_row(session: dict[str, Any]) -> dict[str, Any]:
    raw = session.get("raw_payload") if isinstance(session.get("raw_payload"), dict) else {}
    username = str(session.get("conversation_username") or "").strip()
    display_name = str(session.get("display_name") or username).strip()
    return {
        "conversation_id": _md5_hex(username) if username else _md5_hex(display_name),
        "conversation_username": username,
        "display_name": display_name or username,
        "remark": str(session.get("remark") or ""),
        "nick_name": str(raw.get("nick_name") or raw.get("nickname") or session.get("display_name") or ""),
        "alias": str(session.get("alias") or raw.get("alias") or ""),
        "conversation_type": _conversation_type(session),
        "last_active_at": _timestamp_to_iso(raw.get("timestamp")) or str(raw.get("time") or ""),
        "message_count": 0,
        "chat_room_notify": session.get("chat_room_notify"),
        "notification_muted": session.get("notification_muted"),
        "notification_state": session.get("notification_state") or "unknown",
    }


def export_history(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output).expanduser().resolve()
    export_dir = output_dir / "export"
    conversations_dir = export_dir / "conversations"
    members_dir = export_dir / "members"
    _ensure_dir(conversations_dir)
    if args.include_members:
        _ensure_dir(members_dir)

    previous_profile = _set_profile_env(args.wx_cli_profile or "")
    profile_was_set = bool(args.wx_cli_profile)
    try:
        sessions = _select_sessions(args)
    finally:
        _restore_profile_env(previous_profile, profile_was_set)

    contacts: list[dict[str, Any]] = []
    session_rows: list[dict[str, Any]] = []
    conversation_index: list[dict[str, Any]] = []
    total_messages = 0
    type_counts: Counter[str] = Counter()
    warnings: list[str] = []
    member_index: dict[str, dict[str, Any]] = {}
    stats_index: dict[str, Any] = {}
    favorites_rows: list[dict[str, Any]] = []
    sns_feed_rows: list[dict[str, Any]] = []
    sns_notification_rows: list[dict[str, Any]] = []

    previous_profile = _set_profile_env(args.wx_cli_profile or "")
    profile_was_set = bool(args.wx_cli_profile)
    try:
        for session in sessions:
            session_row = _session_base_row(session)
            conversation_id = session_row["conversation_id"]
            conversation_path = conversations_dir / f"{conversation_id}.jsonl"

            try:
                messages = _load_full_history(session, args)
            except WxCliError as exc:
                warnings.append(
                    f"{session_row['conversation_username'] or session_row['display_name']}: {exc.code}: {exc.message}"
                )
                messages = []

            with conversation_path.open("w", encoding="utf-8") as f:
                for message in messages:
                    direction = _infer_direction(session, message)
                    message_type = _message_type(message.get("message_type"))
                    sender_id = str(message.get("sender_id") or "")
                    row = {
                        "conversation_id": conversation_id,
                        "conversation_username": session_row["conversation_username"],
                        "conversation_name": session_row["display_name"],
                        "conversation_type": session_row["conversation_type"],
                        "notification_muted": session_row.get("notification_muted"),
                        "notification_state": session_row.get("notification_state") or "unknown",
                        "chat_room_notify": session_row.get("chat_room_notify"),
                        "sender_id": sender_id,
                        "sender_name": _sender_name(session, message, direction),
                        "message_id": message.get("message_id") or "",
                        "message_svr_id": "",
                        "timestamp": _message_timestamp_iso(message),
                        "direction": direction,
                        "message_type": message_type,
                        "render_type": message_type,
                        "text": str(message.get("text") or ""),
                        "raw_type": {"wx_cli_type": str(message.get("message_type") or "")},
                        "attachment_meta": {},
                        "raw_payload": _raw_payload(message.get("raw_payload") if isinstance(message.get("raw_payload"), dict) else {}, args),
                        "source_db": "wx_cli",
                        "source_provider": "wx_cli",
                    }
                    f.write(json.dumps(row, ensure_ascii=False))
                    f.write("\n")
                    total_messages += 1
                    type_counts[row["render_type"]] += 1

            session_row["message_count"] = len(messages)
            if messages:
                session_row["last_active_at"] = _message_timestamp_iso(messages[-1]) or str(session_row["last_active_at"] or "")

            conversation_index.append(
                {
                    "conversation_id": conversation_id,
                    "conversation_username": session_row["conversation_username"],
                    "display_name": session_row["display_name"],
                    "remark": session_row["remark"],
                    "nick_name": session_row["nick_name"],
                    "alias": session_row["alias"],
                    "conversation_type": session_row["conversation_type"],
                    "notification_muted": session_row["notification_muted"],
                    "notification_state": session_row["notification_state"],
                    "chat_room_notify": session_row["chat_room_notify"],
                    "last_active_at": session_row["last_active_at"],
                    "message_count": session_row["message_count"],
                    "file": f"conversations/{conversation_id}.jsonl",
                    "file_label": _safe_slug(session_row["display_name"]),
                }
            )
            session_rows.append(session_row)
            contacts.append(
                {
                    "username": session_row["conversation_username"],
                    "display_name": session_row["display_name"],
                    "remark": session_row["remark"],
                    "nick_name": session_row["nick_name"],
                    "alias": session_row["alias"],
                    "conversation_type": session_row["conversation_type"],
                    "source_provider": "wx_cli_session",
                    "raw_payload": {},
                }
            )

            if args.include_members and session_row["conversation_type"] == "group":
                try:
                    members = list_group_members(session_row["conversation_username"] or session_row["display_name"], limit=args.member_limit)
                except WxCliError as exc:
                    warnings.append(
                        f"members:{session_row['conversation_username'] or session_row['display_name']}: {exc.code}: {exc.message}"
                    )
                    members = []
                member_rows = []
                for member in members:
                    row = {
                        "room_username": member.get("room_username") or session_row["conversation_username"],
                        "conversation_id": conversation_id,
                        "member_username": member.get("member_username") or "",
                        "display_name": member.get("display_name") or "",
                        "room_nickname": member.get("room_nickname") or "",
                        "contact_remark": member.get("contact_remark") or "",
                        "alias": member.get("alias") or "",
                        "source_provider": "wx_cli",
                        "raw_payload": _raw_payload(member.get("raw_payload") if isinstance(member.get("raw_payload"), dict) else {}, args),
                    }
                    member_rows.append(row)
                member_index[conversation_id] = {
                    "conversation_id": conversation_id,
                    "conversation_username": session_row["conversation_username"],
                    "display_name": session_row["display_name"],
                    "member_count": len(member_rows),
                    "file": f"members/{conversation_id}.json",
                }
                _write_json(members_dir / f"{conversation_id}.json", member_rows)

            if args.include_stats:
                try:
                    stats_index[conversation_id] = get_stats(
                        session_row["conversation_username"] or session_row["display_name"],
                        since=args.since,
                        until=args.until,
                    )
                except WxCliError as exc:
                    warnings.append(
                        f"stats:{session_row['conversation_username'] or session_row['display_name']}: {exc.code}: {exc.message}"
                    )
    finally:
        _restore_profile_env(previous_profile, profile_was_set)

    previous_profile = _set_profile_env(args.wx_cli_profile or "")
    profile_was_set = bool(args.wx_cli_profile)
    try:
        if args.include_contacts:
            try:
                contacts.extend(_contact_export_row(contact, args) for contact in list_contacts(limit=args.contact_limit))
            except WxCliError as exc:
                warnings.append(f"contacts: {exc.code}: {exc.message}")
        if args.include_favorites:
            try:
                favorites_rows = [
                    {
                        **favorite,
                        "raw_payload": _raw_payload(
                            favorite.get("raw_payload") if isinstance(favorite.get("raw_payload"), dict) else {}, args
                        ),
                    }
                    for favorite in list_favorites(limit=args.favorite_limit)
                ]
            except WxCliError as exc:
                warnings.append(f"favorites: {exc.code}: {exc.message}")
        if args.include_sns:
            try:
                sns_feed_rows = [
                    {
                        **item,
                        "raw_payload": _raw_payload(item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {}, args),
                    }
                    for item in get_sns_feed(limit=args.sns_limit, since=args.since, until=args.until)
                ]
            except WxCliError as exc:
                warnings.append(f"sns-feed: {exc.code}: {exc.message}")
            try:
                sns_notification_rows = [
                    {
                        **item,
                        "raw_payload": _raw_payload(item.get("raw_payload") if isinstance(item.get("raw_payload"), dict) else {}, args),
                    }
                    for item in get_sns_notifications(limit=args.sns_limit, since=args.since, until=args.until)
                ]
            except WxCliError as exc:
                warnings.append(f"sns-notifications: {exc.code}: {exc.message}")
    finally:
        _restore_profile_env(previous_profile, profile_was_set)

    contacts = _dedupe_contacts(contacts)
    _write_json(export_dir / "contacts.json", sorted(contacts, key=lambda item: item["username"]))
    _write_json(
        export_dir / "sessions.json",
        sorted(session_rows, key=lambda item: (item.get("display_name") or "", item.get("conversation_username") or "")),
    )
    _write_json(export_dir / "conversation_index.json", sorted(conversation_index, key=lambda item: item["display_name"]))

    with (export_dir / "messages_summary.csv").open("w", encoding="utf-8", newline="") as f:
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
        writer.writerows(conversation_index)

    if member_index:
        _write_json(export_dir / "members_index.json", sorted(member_index.values(), key=lambda item: item["display_name"]))
    if favorites_rows:
        _write_jsonl(export_dir / "favorites.jsonl", favorites_rows)
    if sns_feed_rows:
        _write_jsonl(export_dir / "sns_feed.jsonl", sns_feed_rows)
    if sns_notification_rows:
        _write_jsonl(export_dir / "sns_notifications.jsonl", sns_notification_rows)
    if stats_index:
        _write_json(export_dir / "stats.json", stats_index)

    profile_summary = active_profile_summary(explicit=args.wx_cli_profile or None, redacted=True)
    coverage = {
        "schema_version": "wx_cli_export_coverage_v1",
        "source_provider": "wx_cli",
        "included": {
            "contacts": True,
            "members": bool(args.include_members),
            "favorites": bool(args.include_favorites),
            "sns": bool(args.include_sns),
            "stats": bool(args.include_stats),
            "raw_payload": bool(args.include_raw),
        },
        "counts": {
            "sessions": len(session_rows),
            "conversations": len(conversation_index),
            "messages": total_messages,
            "contacts": len(contacts),
            "member_groups": len(member_index),
            "favorites": len(favorites_rows),
            "sns_feed": len(sns_feed_rows),
            "sns_notifications": len(sns_notification_rows),
            "stats": len(stats_index),
        },
        "warnings": warnings,
        "privacy": {
            "raw_payload": "included" if args.include_raw else "omitted",
            "raw_redaction": args.raw_redaction,
        },
    }
    _write_json(export_dir / "coverage.json", coverage)
    _write_json(export_dir / "profile_summary.json", profile_summary)

    return {
        "schema_version": SESSION_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "wx_cli_profile": str(args.wx_cli_profile or ""),
        "source_provider": "wx_cli",
        "export": str(export_dir),
        "filter": {
            "conversation": args.conversation,
            "private_only": bool(args.private_only),
            "since": args.since,
            "until": args.until,
            "session_limit": args.session_limit,
            "history_batch_size": args.history_batch_size,
            "max_messages_per_conversation": args.max_messages_per_conversation,
            "include_contacts": bool(args.include_contacts),
            "include_members": bool(args.include_members),
            "include_favorites": bool(args.include_favorites),
            "include_sns": bool(args.include_sns),
            "include_stats": bool(args.include_stats),
            "include_raw": bool(args.include_raw),
            "raw_redaction": args.raw_redaction,
        },
        "profile_summary": profile_summary,
        "coverage": coverage,
        "manifest": {
            "total_conversations": len(conversation_index),
            "total_messages": total_messages,
            "type_counts": dict(sorted(type_counts.items())),
            "warnings": warnings,
        },
    }


def main() -> int:
    args = parse_args()
    try:
        summary = export_history(args)
    except WxCliError as exc:
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
