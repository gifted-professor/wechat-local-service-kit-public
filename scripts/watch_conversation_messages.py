#!/usr/bin/env python3

import argparse
import json
import time
from pathlib import Path
from typing import Any, Optional

from chat_crypto import ChatCryptoError, prepare_readable_db
from parse_chat_history import _iter_message_db_paths, _iter_messages_for_conversation, load_contacts, load_sessions
from wechat_contact_policy import contact_policy_block, enrich_target_with_contact_policy, normalize_private_type
from wechat_common import _ensure_dir
from wx_cli_adapter import WxCliError, get_history, get_new_messages, resolve_conversation


def _resolve_db_storage_root(root: Path) -> Path:
    root = root.expanduser().resolve()
    if (root / "message").exists() and (root / "contact" / "contact.db").exists():
        return root
    db_storage = root / "db_storage"
    if (db_storage / "message").exists() and (db_storage / "contact" / "contact.db").exists():
        return db_storage
    raise FileNotFoundError(f"could not locate db_storage under: {root}")


def _load_target_session(db_storage_root: Path, cache_root: Path, frida_log: Path, needle: str) -> dict[str, Any]:
    contact_copy = prepare_readable_db(
        db_storage_root / "contact" / "contact.db",
        cache_root / "contact",
        frida_log_path=frida_log,
    )
    session_copy = prepare_readable_db(
        db_storage_root / "session" / "session.db",
        cache_root / "session",
        frida_log_path=frida_log,
    )

    contacts = load_contacts(contact_copy)
    sessions = load_sessions(session_copy, contacts)

    exact_hits = []
    fuzzy_hits = []
    lower_needle = needle.strip().lower()
    for username, session in sessions.items():
        contact = contacts.get(username, {})
        fields = [
            username,
            session.get("display_name", ""),
            contact.get("remark", ""),
            contact.get("nick_name", ""),
            contact.get("alias", ""),
        ]
        lowered = [str(v or "").lower() for v in fields]
        if lower_needle in {v for v in lowered if v}:
            exact_hits.append({"session": session, "contact": contact})
        elif any(lower_needle in v for v in lowered if v):
            fuzzy_hits.append({"session": session, "contact": contact})

    hits = exact_hits or fuzzy_hits
    if not hits:
        raise ValueError(f"no conversation matched: {needle}")
    if len(hits) > 1:
        preview = [
            {
                "conversation_username": item["session"]["conversation_username"],
                "display_name": item["session"]["display_name"],
                "alias": item["contact"].get("alias", ""),
                "remark": item["contact"].get("remark", ""),
            }
            for item in hits[:10]
        ]
        raise ValueError(f"multiple conversations matched {needle}: {json.dumps(preview, ensure_ascii=False)}")
    hit = hits[0]
    conversation_type = str(hit["session"].get("conversation_type") or "")
    return {
        "conversation_username": hit["session"]["conversation_username"],
        "display_name": hit["session"]["display_name"],
        "conversation_type": conversation_type,
        "chat_type": normalize_private_type(conversation_type),
        "alias": hit["contact"].get("alias", ""),
        "remark": hit["contact"].get("remark", ""),
        "nick_name": hit["contact"].get("nick_name", ""),
        "last_active_at": hit["session"].get("last_active_at", ""),
        "chat_room_notify": hit["contact"].get("chat_room_notify"),
        "notification_muted": hit["contact"].get("notification_muted"),
        "notification_state": hit["contact"].get("notification_state") or "unknown",
        "notification_policy_source": "contact.chat_room_notify",
    }


def _message_key(message: dict[str, Any]) -> tuple[Any, ...]:
    return (
        message.get("timestamp") or "",
        int(message.get("message_id") or 0),
        message.get("message_svr_id") or "",
        message.get("source_db") or "",
    )


def _refresh_prepared_messages(
    db_storage_root: Path,
    cache_root: Path,
    frida_log: Path,
    state: dict[str, dict[str, Any]],
) -> list[Path]:
    prepared_paths = []
    for source in _iter_message_db_paths(db_storage_root / "message"):
        current_mtime = source.stat().st_mtime_ns
        entry = state.get(str(source))
        if not entry or entry.get("mtime_ns") != current_mtime or not Path(entry["prepared_path"]).exists():
            prepared = prepare_readable_db(
                source,
                cache_root / "message",
                frida_log_path=frida_log,
            )
            state[str(source)] = {
                "mtime_ns": current_mtime,
                "prepared_path": str(prepared),
            }
        prepared_paths.append(Path(state[str(source)]["prepared_path"]))
    return prepared_paths


def _read_messages(prepared_paths: list[Path], conversation_username: str) -> list[dict[str, Any]]:
    messages = []
    for prepared_path in prepared_paths:
        messages.extend(_iter_messages_for_conversation(prepared_path, conversation_username))
    messages.sort(key=_message_key)
    return messages


def _print_message(message: dict[str, Any]) -> None:
    timestamp = message.get("timestamp") or ""
    direction = message.get("direction") or ""
    render_type = message.get("render_type") or message.get("message_type") or ""
    sender = message.get("sender_id") or "-"
    text = str(message.get("text") or "").replace("\n", "\\n")
    print(
        json.dumps(
            {
                "timestamp": timestamp,
                "direction": direction,
                "render_type": render_type,
                "sender_id": sender,
                "text": text[:500],
                "source_db": message.get("source_db") or "",
                "source_provider": message.get("source_provider") or "db",
                "message_id": message.get("message_id") or 0,
            },
            ensure_ascii=False,
        )
    )


def _run_db_watch(args: argparse.Namespace) -> int:
    db_storage_root = _resolve_db_storage_root(Path(args.wechat_root))
    frida_log = Path(args.frida_log).expanduser().resolve()
    if not frida_log.exists():
        raise FileNotFoundError(f"frida log not found: {frida_log}")

    cache_root = _ensure_dir(Path(".cache-watch").resolve())
    target = enrich_target_with_contact_policy(
        _load_target_session(db_storage_root, cache_root, frida_log, args.conversation),
        args.contact_db,
    )
    print(json.dumps({"watch_target": target}, ensure_ascii=False, indent=2))

    policy_block = contact_policy_block(
        target,
        allow_non_private=args.allow_non_private,
        include_muted=args.include_muted,
        allow_unknown_notification_state=args.allow_unknown_notification_state,
    )
    if policy_block:
        print(
            json.dumps(
                {
                    "error": "target_skipped_by_contact_policy",
                    "message": "watch only handles private, non-muted chats by default",
                    "chat_type": target.get("chat_type") or target.get("conversation_type"),
                    "notification_state": target.get("notification_state"),
                    "policy_block": policy_block,
                },
                ensure_ascii=False,
            )
        )
        return 6

    prepared_state: dict[str, dict[str, Any]] = {}
    prepared_paths = _refresh_prepared_messages(db_storage_root, cache_root, frida_log, prepared_state)
    baseline_messages = _read_messages(prepared_paths, target["conversation_username"])
    if baseline_messages:
        print(json.dumps({"baseline_count": len(baseline_messages)}, ensure_ascii=False))
        for message in baseline_messages[-max(args.history, 0) :]:
            _print_message(message)
        seen_keys = {_message_key(message) for message in baseline_messages}
    else:
        print(json.dumps({"baseline_count": 0}, ensure_ascii=False))
        seen_keys = set()

    deadline = time.time() + max(args.duration, 0)
    while time.time() < deadline:
        time.sleep(max(args.interval, 0.5))
        try:
            prepared_paths = _refresh_prepared_messages(db_storage_root, cache_root, frida_log, prepared_state)
            messages = _read_messages(prepared_paths, target["conversation_username"])
        except ChatCryptoError as exc:
            print(json.dumps({"warning": str(exc)}, ensure_ascii=False))
            continue

        new_messages = [message for message in messages if _message_key(message) not in seen_keys]
        for message in new_messages:
            _print_message(message)
            seen_keys.add(_message_key(message))

    print(json.dumps({"status": "done", "watched_seconds": args.duration}, ensure_ascii=False))
    return 0


def _wx_message_key(message: dict[str, Any]) -> tuple[str, str, str]:
    raw = message.get("raw_payload") or {}
    try:
        raw_key = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        raw_key = str(raw)
    return (
        str(message.get("message_id") or ""),
        str(message.get("timestamp") or ""),
        raw_key,
    )


def _wx_message_matches_target(message: dict[str, Any], target: dict[str, Any]) -> bool:
    target_values = {
        str(value).strip().lower()
        for value in [
            target.get("conversation_username"),
            target.get("display_name"),
            target.get("remark"),
            target.get("alias"),
            *(target.get("search_candidates") or []),
        ]
        if str(value or "").strip()
    }
    if not target_values:
        return False

    raw = message.get("raw_payload") if isinstance(message.get("raw_payload"), dict) else {}
    message_values = {
        str(value).strip().lower()
        for value in [
            message.get("conversation_username"),
            message.get("display_name"),
            message.get("sender_id"),
            raw.get("chat"),
            raw.get("chat_name"),
            raw.get("conversation"),
            raw.get("conversation_username"),
            raw.get("talker"),
            raw.get("room_id"),
        ]
        if str(value or "").strip()
    }
    return bool(target_values & message_values)


def _run_wx_cli_watch(args: argparse.Namespace) -> int:
    try:
        target = enrich_target_with_contact_policy(resolve_conversation(args.conversation), args.contact_db)
        print(json.dumps({"watch_target": target}, ensure_ascii=False, indent=2))

        policy_block = contact_policy_block(
            target,
            allow_non_private=args.allow_non_private,
            include_muted=args.include_muted,
            allow_unknown_notification_state=args.allow_unknown_notification_state,
        )
        if policy_block:
            print(
                json.dumps(
                    {
                        "error": "target_skipped_by_contact_policy",
                        "message": "watch only handles private, non-muted chats by default",
                        "chat_type": target.get("chat_type"),
                        "notification_state": target.get("notification_state"),
                        "policy_block": policy_block,
                    },
                    ensure_ascii=False,
                )
            )
            return 6

        history_limit = max(args.history, 0)
        history_chat = target.get("conversation_username") or target.get("display_name") or args.conversation
        baseline_messages = get_history(history_chat, history_limit) if history_limit else []
        print(json.dumps({"baseline_count": len(baseline_messages), "source_provider": "wx-cli"}, ensure_ascii=False))
        for message in baseline_messages[-history_limit:]:
            _print_message(message)
        seen_keys = {_wx_message_key(message) for message in baseline_messages}

        deadline = time.time() + max(args.duration, 0)
        while time.time() < deadline:
            time.sleep(max(args.interval, 0.5))
            messages = [message for message in get_new_messages() if _wx_message_matches_target(message, target)]
            new_messages = [message for message in messages if _wx_message_key(message) not in seen_keys]
            for message in new_messages:
                _print_message(message)
                seen_keys.add(_wx_message_key(message))

        print(json.dumps({"status": "done", "source_provider": "wx-cli", "watched_seconds": args.duration}, ensure_ascii=False))
        return 0
    except WxCliError as exc:
        print(json.dumps({"error": exc.to_dict(), "source_provider": "wx-cli"}, ensure_ascii=False))
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch one WeChat conversation for newly synced messages")
    parser.add_argument("--source", choices=["db", "wx-cli"], default="db", help="message source provider")
    parser.add_argument("--wechat-root", help="wxid account directory or db_storage directory")
    parser.add_argument("--frida-log", help="Frida PBKDF2 log path")
    parser.add_argument("--conversation", required=True, help="conversation username / display name / alias / remark")
    parser.add_argument("--contact-db", help="prepared contact.db path used for private/non-muted policy checks")
    parser.add_argument("--include-muted", action="store_true", help="allow watching muted chats")
    parser.add_argument("--allow-non-private", action="store_true", help="allow watching group or official-account chats")
    parser.add_argument(
        "--allow-unknown-notification-state",
        action="store_true",
        help="allow watching when muted/non-muted state cannot be determined",
    )
    parser.add_argument("--interval", type=float, default=5.0, help="polling interval in seconds")
    parser.add_argument("--duration", type=float, default=180.0, help="watch duration in seconds")
    parser.add_argument("--history", type=int, default=3, help="how many latest existing messages to print as baseline")
    args = parser.parse_args()

    if args.source == "wx-cli":
        return _run_wx_cli_watch(args)
    if not args.wechat_root:
        parser.error("--wechat-root is required when --source db")
    if not args.frida_log:
        parser.error("--frida-log is required when --source db")
    return _run_db_watch(args)


if __name__ == "__main__":
    raise SystemExit(main())
