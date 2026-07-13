#!/usr/bin/env python3

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from wechat_common import _ensure_dir, _write_json
from wx_cli_adapter import WxCliError, check_wx_cli_ready, get_history, get_new_messages, list_unread


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def load_reply_ready_contacts(manifest_path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    contacts = data.get("contacts")
    if not isinstance(contacts, list):
        raise ValueError(f"contact wiki manifest must contain contacts list: {manifest_path}")

    out = {}
    for record in contacts:
        if not isinstance(record, dict) or not record.get("reply_ready"):
            continue
        username = str(record.get("conversation_username") or "").strip()
        if not username:
            continue
        out[username] = record
    return out


def fallback_contact(username: str, *, display_name: str = "", notification_state: str = "unknown") -> dict[str, Any]:
    name = str(display_name or username or "").strip()
    return {
        "contact_id": username,
        "conversation_username": username,
        "display_name": name,
        "tier": "personal_private",
        "score": 0,
        "selection_reasons": ["allow_all_private"],
        "activity": {
            "notification_state": notification_state or "unknown",
            "notification_muted": None,
        },
    }


def message_key(message: dict[str, Any], username: str = "") -> str:
    return "|".join(
        [
            str(username or message.get("conversation_username") or ""),
            str(message.get("message_id") or ""),
            str(message.get("timestamp") or ""),
            str(message.get("text") or ""),
        ]
    )


def message_timestamp_seconds(message: dict[str, Any]) -> Optional[float]:
    try:
        return float(message.get("timestamp") or 0)
    except (TypeError, ValueError):
        return None


def message_is_fresh(message: dict[str, Any], fresh_within_seconds: float) -> bool:
    if fresh_within_seconds <= 0:
        return True
    timestamp = message_timestamp_seconds(message)
    if not timestamp:
        return False
    age = time.time() - timestamp
    return -300 <= age <= fresh_within_seconds


def load_seen_event_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            keys.add(message_key(message, str(event.get("conversation_username") or "")))
    return keys


def message_username(message: dict[str, Any]) -> str:
    values = [
        message.get("conversation_username"),
    ]
    raw = message.get("raw_payload") if isinstance(message.get("raw_payload"), dict) else {}
    for key in ["chat", "username", "conversation_username", "talker", "room_id", "chat_name"]:
        values.append(raw.get(key))
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def contact_matches_message(
    message: dict[str, Any],
    ready_contacts: dict[str, dict[str, Any]],
    *,
    allow_all_private: bool = False,
) -> tuple[Optional[str], dict[str, Any]]:
    values = {
        str(value or "").strip()
        for value in [
            message.get("conversation_username"),
            message.get("display_name"),
            message.get("sender_id"),
        ]
        if str(value or "").strip()
    }
    raw = message.get("raw_payload") if isinstance(message.get("raw_payload"), dict) else {}
    for key in ["chat", "username", "conversation_username", "talker", "room_id", "chat_name"]:
        value = str(raw.get(key) or "").strip()
        if value:
            values.add(value)
    for username, contact in ready_contacts.items():
        if username in values:
            return username, contact
        search_values = {
            str(value or "").strip()
            for value in [
                contact.get("display_name"),
                *(contact.get("selection_reasons") or []),
            ]
            if str(value or "").strip()
        }
        if values & search_values:
            return username, contact

    if allow_all_private:
        username = message_username(message)
        if username:
            return username, fallback_contact(
                username,
                display_name=str(message.get("display_name") or ""),
                notification_state=str(message.get("notification_state") or "unknown"),
            )
    return None, {}


def is_received_private_message(message: dict[str, Any]) -> bool:
    if message.get("chat_type") and message.get("chat_type") != "private":
        return False
    if message.get("direction") == "sent":
        return False
    if message.get("direction") == "received":
        return True
    return not str(message.get("sender_id") or "").strip()


def compact_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": message.get("message_id") or "",
        "timestamp": message.get("timestamp") or "",
        "direction": message.get("direction") or "",
        "message_type": message.get("message_type") or "",
        "text": str(message.get("text") or ""),
        "sender_id": message.get("sender_id") or "",
        "source_provider": message.get("source_provider") or "wx-cli",
    }


def write_jsonl(path: Path, row: dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        f.write("\n")


def make_event(username: str, contact: dict[str, Any], source: str, message: dict[str, Any]) -> dict[str, Any]:
    activity = contact.get("activity") if isinstance(contact.get("activity"), dict) else {}
    return {
        "schema_version": "reply_candidate_event_v1",
        "detected_at": utc_now_iso(),
        "source": source,
        "contact_id": contact.get("contact_id") or "",
        "conversation_username": username,
        "display_name": contact.get("display_name") or "",
        "tier": contact.get("tier") or "",
        "score": contact.get("score") or 0,
        "notification_state": activity.get("notification_state") or "unknown",
        "message": compact_message(message),
        "draft_status": "not_generated",
        "ui_status": "not_touched",
        "send_status": "not_sent",
    }


def latest_received_history(chat: str, limit: int) -> Optional[dict[str, Any]]:
    try:
        messages = get_history(chat, limit)
    except WxCliError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        stderr = str(details.get("stderr") or "")
        if "找不到联系人" in stderr:
            return None
        raise
    for message in reversed(messages):
        if is_received_private_message(message):
            return message
    return None


def scan_unread(
    ready_contacts: dict[str, dict[str, Any]],
    seen_keys: set[str],
    events_path: Path,
    *,
    history_limit: int,
    fresh_within_seconds: float,
    allow_all_private: bool,
) -> int:
    count = 0
    for session in list_unread(chat_type_filter="private"):
        username = str(session.get("conversation_username") or "").strip()
        contact = ready_contacts.get(username)
        if not contact and allow_all_private and username:
            contact = fallback_contact(
                username,
                display_name=str(session.get("display_name") or ""),
                notification_state=str(session.get("notification_state") or "unknown"),
            )
        if not contact:
            continue
        message = latest_received_history(username, history_limit)
        if not message:
            continue
        if not message_is_fresh(message, fresh_within_seconds):
            continue
        key = message_key(message, username)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        event = make_event(username, contact, "unread_history", message)
        write_jsonl(events_path, event)
        count += 1
    return count


def scan_new_messages(
    ready_contacts: dict[str, dict[str, Any]],
    seen_keys: set[str],
    events_path: Path,
    limit: Optional[int],
    *,
    fresh_within_seconds: float,
    allow_all_private: bool,
) -> int:
    count = 0
    for message in get_new_messages(limit=limit):
        if not is_received_private_message(message):
            continue
        if not message_is_fresh(message, fresh_within_seconds):
            continue
        username, contact = contact_matches_message(message, ready_contacts, allow_all_private=allow_all_private)
        if not username:
            continue
        key = message_key(message, username)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        event = make_event(username, contact, "new_messages", message)
        write_jsonl(events_path, event)
        count += 1
    return count


def write_state(path: Path, state: dict[str, Any]) -> None:
    _write_json(path, state)


def count_existing_events(path: Path) -> int:
    return len(load_seen_event_keys(path))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor reply-ready private non-muted chats and write local candidate events.")
    parser.add_argument("--manifest", default="out/contact-wiki/manifest.json", help="Contact wiki manifest path.")
    parser.add_argument("--out-root", default="out/dry-run-replies/monitor", help="Local output root.")
    parser.add_argument("--duration", type=float, default=1800.0, help="Seconds to monitor.")
    parser.add_argument("--interval", type=float, default=10.0, help="Polling interval in seconds.")
    parser.add_argument("--history-limit", type=int, default=12, help="History messages to inspect for unread sessions.")
    parser.add_argument("--new-message-limit", type=int, default=100, help="Max new messages per wx-cli poll; 0 means wx-cli default.")
    parser.add_argument("--max-events", type=int, default=0, help="Stop after this many events; 0 means unlimited.")
    parser.add_argument("--skip-initial-unread", action="store_true", help="Do not scan current unread sessions at startup.")
    parser.add_argument("--allow-all-private", action="store_true", help="Record fresh private chats even without a contact wiki manifest.")
    parser.add_argument(
        "--message-fresh-within-seconds",
        type=float,
        default=1800.0,
        help="Only record messages whose message timestamp is this recent; 0 disables the freshness gate.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    out_root = _ensure_dir(Path(args.out_root).expanduser().resolve())
    events_path = out_root / "events.jsonl"
    state_path = out_root / "state.json"

    if args.allow_all_private and not manifest_path.exists():
        ready = {}
    else:
        ready = load_reply_ready_contacts(manifest_path)
    status = check_wx_cli_ready()
    if not status.get("ready"):
        existing_event_count = count_existing_events(events_path)
        write_state(
            state_path,
            {
                "schema_version": "reply_candidate_monitor_state_v1",
                "status": "wx_cli_not_ready",
                "updated_at": utc_now_iso(),
                "ready_contact_count": len(ready),
                "existing_event_count": existing_event_count,
                "new_event_count": 0,
                "event_count": existing_event_count,
                "message_fresh_within_seconds": max(args.message_fresh_within_seconds, 0),
                "events_path": str(events_path),
                "model_api_touched": False,
                "ui_touched": False,
                "sent": False,
                "wx_cli": status,
            },
        )
        print(
            json.dumps(
                {
                    "status": "wx_cli_not_ready",
                    "ready_contact_count": len(ready),
                    "existing_event_count": existing_event_count,
                    "new_event_count": 0,
                    "event_count": existing_event_count,
                    "message_fresh_within_seconds": max(args.message_fresh_within_seconds, 0),
                    "wx_cli": status,
                },
                ensure_ascii=False,
            )
        )
        return 2

    seen_keys = load_seen_event_keys(events_path)
    existing_event_count = len(seen_keys)
    new_event_count = 0
    started_at = utc_now_iso()
    deadline = time.time() + max(args.duration, 0)
    write_state(
        state_path,
        {
            "schema_version": "reply_candidate_monitor_state_v1",
            "status": "starting",
            "started_at": started_at,
            "updated_at": utc_now_iso(),
            "ready_contact_count": len(ready),
            "existing_event_count": existing_event_count,
            "new_event_count": new_event_count,
            "event_count": existing_event_count + new_event_count,
            "message_fresh_within_seconds": max(args.message_fresh_within_seconds, 0),
            "events_path": str(events_path),
            "model_api_touched": False,
            "ui_touched": False,
            "sent": False,
        },
    )

    try:
        if not args.skip_initial_unread:
            new_event_count += scan_unread(
                ready,
                seen_keys,
                events_path,
                history_limit=max(args.history_limit, 1),
                fresh_within_seconds=max(args.message_fresh_within_seconds, 0),
                allow_all_private=bool(args.allow_all_private),
            )

        while time.time() < deadline:
            new_event_count += scan_new_messages(
                ready,
                seen_keys,
                events_path,
                None if args.new_message_limit <= 0 else args.new_message_limit,
                fresh_within_seconds=max(args.message_fresh_within_seconds, 0),
                allow_all_private=bool(args.allow_all_private),
            )
            write_state(
                state_path,
                {
                    "schema_version": "reply_candidate_monitor_state_v1",
                    "status": "running",
                    "started_at": started_at,
                    "updated_at": utc_now_iso(),
                    "ready_contact_count": len(ready),
                    "existing_event_count": existing_event_count,
                    "new_event_count": new_event_count,
                    "event_count": existing_event_count + new_event_count,
                    "message_fresh_within_seconds": max(args.message_fresh_within_seconds, 0),
                    "events_path": str(events_path),
                    "model_api_touched": False,
                    "ui_touched": False,
                    "sent": False,
                },
            )
            if args.max_events > 0 and new_event_count >= args.max_events:
                break
            time.sleep(max(args.interval, 0.5))

        final_status = "done_max_events" if args.max_events > 0 and new_event_count >= args.max_events else "done"
        write_state(
            state_path,
            {
                "schema_version": "reply_candidate_monitor_state_v1",
                "status": final_status,
                "started_at": started_at,
                "updated_at": utc_now_iso(),
                "ready_contact_count": len(ready),
                "existing_event_count": existing_event_count,
                "new_event_count": new_event_count,
                "event_count": existing_event_count + new_event_count,
                "message_fresh_within_seconds": max(args.message_fresh_within_seconds, 0),
                "events_path": str(events_path),
                "model_api_touched": False,
                "ui_touched": False,
                "sent": False,
            },
        )
        print(
            json.dumps(
                {
                    "status": final_status,
                    "ready_contact_count": len(ready),
                    "existing_event_count": existing_event_count,
                    "new_event_count": new_event_count,
                    "event_count": existing_event_count + new_event_count,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except WxCliError as exc:
        write_state(
            state_path,
            {
                "schema_version": "reply_candidate_monitor_state_v1",
                "status": "wx_cli_error",
                "updated_at": utc_now_iso(),
                "ready_contact_count": len(ready),
                "existing_event_count": existing_event_count,
                "new_event_count": new_event_count,
                "event_count": existing_event_count + new_event_count,
                "message_fresh_within_seconds": max(args.message_fresh_within_seconds, 0),
                "error": exc.to_dict(),
                "model_api_touched": False,
                "ui_touched": False,
                "sent": False,
            },
        )
        print(json.dumps({"status": "wx_cli_error", "error": exc.to_dict()}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
