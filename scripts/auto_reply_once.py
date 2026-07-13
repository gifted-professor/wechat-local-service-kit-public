#!/usr/bin/env python3

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from chat_crypto import ChatCryptoError, prepare_readable_db
from customer_memory import build_runtime_context_for_query, should_use_memory_for_message
from parse_chat_history import _iter_message_db_paths, _iter_messages_for_conversation, load_contacts, load_sessions
from reply_api import generate_reply_openai_compatible
from service_knowledge import build_service_knowledge_context, service_knowledge_context_for_api
from wechat_contact_policy import contact_policy_block, enrich_target_with_contact_policy, normalize_private_type
from wechat_common import _ensure_dir
from wechat_ui_send import send_text
from wx_cli_adapter import WxCliError, get_history, get_new_messages, resolve_conversation


def _resolve_db_storage_root(root: Path) -> Path:
    root = root.expanduser().resolve()
    if (root / "message").exists() and (root / "contact" / "contact.db").exists():
        return root
    db_storage = root / "db_storage"
    if (db_storage / "message").exists() and (db_storage / "contact" / "contact.db").exists():
        return db_storage
    raise FileNotFoundError(f"could not locate db_storage under: {root}")


def _message_key(message: dict[str, Any]) -> tuple[Any, ...]:
    return (
        message.get("timestamp") or "",
        int(message.get("message_id") or 0),
        message.get("message_svr_id") or "",
        message.get("source_db") or "",
    )


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
    candidates = []
    for value in (
        hit["contact"].get("remark", ""),
        hit["session"].get("display_name", ""),
        hit["contact"].get("nick_name", ""),
        hit["contact"].get("alias", ""),
    ):
        text = str(value or "").strip()
        if text and text not in candidates:
            candidates.append(text)

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
        "search_candidates": candidates,
    }


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


def _find_verification(messages: list[dict[str, Any]], seen_keys: set[tuple[Any, ...]], reply_text: str) -> Optional[dict[str, Any]]:
    for message in messages:
        key = _message_key(message)
        if key in seen_keys:
            continue
        if (message.get("text") or "").strip() != reply_text.strip():
            continue
        return message
    return None


def _wx_message_key(message: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(message.get("message_id") or ""),
        str(message.get("timestamp") or ""),
        str(message.get("text") or ""),
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
    raw = message.get("raw_payload") if isinstance(message.get("raw_payload"), dict) else {}
    message_values = {
        str(value).strip().lower()
        for value in [
            message.get("conversation_username"),
            message.get("display_name"),
            raw.get("chat"),
            raw.get("username"),
            raw.get("conversation_username"),
        ]
        if str(value or "").strip()
    }
    return bool(target_values & message_values)


def _wx_message_is_received(message: dict[str, Any], target: dict[str, Any], allow_group: bool) -> bool:
    if message.get("direction") == "received":
        return True
    if message.get("direction") == "sent":
        return False
    if target.get("chat_type") != "private" and not allow_group:
        return False
    # wx-cli private history uses an empty sender for the other side and a
    # non-empty sender for local messages. Group direction is ambiguous, so it
    # stays opt-in through --wx-cli-allow-group-auto-reply.
    return not str(message.get("sender_id") or "").strip()


def _find_wx_verification(
    messages: list[dict[str, Any]],
    seen_keys: set[tuple[str, str, str]],
    reply_text: str,
) -> Optional[dict[str, Any]]:
    for message in messages:
        key = _wx_message_key(message)
        if key in seen_keys:
            continue
        if (message.get("text") or "").strip() != reply_text.strip():
            continue
        return message
    return None


def _send_search_candidates(args: argparse.Namespace, target: dict[str, Any]) -> list[str]:
    candidates = []
    for value in [*(args.send_search or []), target.get("display_name"), target.get("remark"), target.get("alias")]:
        text = str(value or "").strip()
        if text and text not in candidates:
            candidates.append(text)
    for value in target.get("search_candidates") or []:
        text = str(value or "").strip()
        if not text or text in candidates:
            continue
        if text.startswith("wxid_"):
            continue
        candidates.append(text)
    if not candidates:
        candidates.append(args.conversation)
    return candidates


def _memory_query_candidates(args: argparse.Namespace, target: dict[str, Any]) -> list[str]:
    candidates = []
    for value in [
        target.get("conversation_username"),
        target.get("display_name"),
        target.get("remark"),
        target.get("alias"),
        args.conversation,
    ]:
        text = str(value or "").strip()
        if text and text not in candidates:
            candidates.append(text)
    return candidates


def _load_memory_context(args: argparse.Namespace, target: dict[str, Any]) -> Optional[dict[str, Any]]:
    if args.memory_mode == "off" or not args.memory_root:
        return None

    memory_root = Path(args.memory_root).expanduser().resolve()
    errors = []
    for query in _memory_query_candidates(args, target):
        try:
            context = build_runtime_context_for_query(
                memory_root,
                query,
                fact_limit=args.memory_fact_limit,
                recent_limit=args.memory_recent_limit,
            )
            context["lookup_query"] = query
            return context
        except (OSError, ValueError) as exc:
            errors.append({"query": query, "error": str(exc)})

    print(
        json.dumps(
            {
                "warning": "customer_memory_context_not_found",
                "memory_root": str(memory_root),
                "errors": errors[:5],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return None


def _maybe_emit_memory_context(args: argparse.Namespace, memory_context: Optional[dict[str, Any]]) -> None:
    if args.emit_context_json:
        print(
            json.dumps(
                {
                    "memory_mode": args.memory_mode,
                    "memory_use_policy": args.memory_use_policy,
                    "memory_context_available": bool(memory_context),
                    "memory_context": memory_context,
                    "memory_context_used_for_api": None,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


def _memory_context_for_api(
    args: argparse.Namespace,
    memory_context: Optional[dict[str, Any]],
    trigger: dict[str, Any],
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    gate = should_use_memory_for_message(
        str(trigger.get("text") or ""),
        policy=args.memory_use_policy,
    )
    gate["memory_mode"] = args.memory_mode
    gate["memory_context_available"] = bool(memory_context)

    if args.memory_mode != "draft-only":
        gate["decision"] = False
        gate["reason"] = f"memory_mode_{args.memory_mode}"
        return None, gate
    if not memory_context:
        gate["decision"] = False
        gate["reason"] = "memory_context_unavailable"
        return None, gate
    if not gate["decision"]:
        return None, gate
    return memory_context, gate


def _load_service_knowledge_context(args: argparse.Namespace, trigger: dict[str, Any]) -> Optional[dict[str, Any]]:
    if args.service_knowledge_mode == "off":
        return None
    return build_service_knowledge_context(
        str(trigger.get("text") or ""),
        wiki_root=Path(args.service_knowledge_root),
        policy=args.service_knowledge_policy,
        max_playbooks=args.service_knowledge_max_playbooks,
    )


def _service_knowledge_context_for_api(
    args: argparse.Namespace,
    service_knowledge_context: Optional[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    return service_knowledge_context_for_api(args.service_knowledge_mode, service_knowledge_context)


def _run_wx_cli_auto_reply(args: argparse.Namespace) -> int:
    if args.reply_source == "fixed" and not args.reply_text:
        raise ValueError("--reply-text is required when --reply-source=fixed")

    try:
        target = enrich_target_with_contact_policy(resolve_conversation(args.conversation), args.contact_db)
        print(json.dumps({"target": target, "source_provider": "wx-cli"}, ensure_ascii=False, indent=2), flush=True)

        allow_non_private = args.allow_non_private_auto_reply or args.wx_cli_allow_group_auto_reply
        policy_block = contact_policy_block(
            target,
            allow_non_private=allow_non_private,
            include_muted=args.include_muted,
            allow_unknown_notification_state=args.allow_unknown_notification_state,
        )
        if policy_block:
            print(
                json.dumps(
                    {
                        "error": "target_skipped_by_contact_policy",
                        "message": "auto-reply only handles private, non-muted chats by default",
                        "chat_type": target.get("chat_type"),
                        "notification_state": target.get("notification_state"),
                        "policy_block": policy_block,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 6

        memory_context = _load_memory_context(args, target)
        _maybe_emit_memory_context(args, memory_context)

        history_chat = target.get("conversation_username") or target.get("display_name") or args.conversation
        baseline_messages = get_history(history_chat, max(args.context_messages, 1))
        seen_keys = {_wx_message_key(message) for message in baseline_messages}
        print(
            json.dumps(
                {"baseline_count": len(baseline_messages), "source_provider": "wx-cli"},
                ensure_ascii=False,
            ),
            flush=True,
        )

        reply_count = 0
        last_reply_at = 0.0
        deadline = time.time() + max(args.duration, 0)
        while time.time() < deadline:
            time.sleep(max(args.interval, 0.5))
            try:
                messages = [message for message in get_new_messages() if _wx_message_matches_target(message, target)]
            except WxCliError as exc:
                print(json.dumps({"warning": exc.to_dict(), "source_provider": "wx-cli"}, ensure_ascii=False), flush=True)
                continue

            new_received = []
            for message in messages:
                key = _wx_message_key(message)
                if key in seen_keys:
                    continue
                if args.reply_text and (message.get("text") or "").strip() == args.reply_text.strip():
                    continue
                if _wx_message_is_received(message, target, allow_non_private):
                    new_received.append(message)

            if not new_received:
                for message in messages:
                    seen_keys.add(_wx_message_key(message))
                continue

            trigger = new_received[-1]
            print(json.dumps({"trigger_message": trigger}, ensure_ascii=False), flush=True)

            now = time.time()
            if last_reply_at and now - last_reply_at < max(args.cooldown, 0):
                print(
                    json.dumps(
                        {
                            "cooldown_skip": {
                                "seconds_since_last_reply": round(now - last_reply_at, 2),
                                "cooldown": args.cooldown,
                                "message_id": trigger.get("message_id"),
                                "text": trigger.get("text") or "",
                            }
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                for message in messages:
                    seen_keys.add(_wx_message_key(message))
                continue

            if args.reply_source == "api":
                recent_context = baseline_messages[-max(args.context_messages, 1) :] + messages
                api_memory_context, memory_gate = _memory_context_for_api(args, memory_context, trigger)
                service_knowledge_context = _load_service_knowledge_context(args, trigger)
                api_service_knowledge_context, service_knowledge_gate = _service_knowledge_context_for_api(
                    args,
                    service_knowledge_context,
                )
                print(json.dumps({"memory_gate": memory_gate}, ensure_ascii=False), flush=True)
                print(json.dumps({"service_knowledge_gate": service_knowledge_gate}, ensure_ascii=False), flush=True)
                try:
                    api_result = generate_reply_openai_compatible(
                        latest_message=trigger,
                        recent_messages=recent_context[-max(args.context_messages, 1) :],
                        conversation_name=target.get("display_name") or args.conversation,
                        model=args.api_model,
                        base_url=args.api_base_url,
                        api_key=args.api_key,
                        context_limit=args.context_messages,
                        memory_context=api_memory_context,
                        service_knowledge_context=api_service_knowledge_context,
                    )
                except Exception as exc:
                    print(
                        json.dumps(
                            {
                                "error": "api_reply_generation_failed",
                                "model": args.api_model,
                                "message": str(exc),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    return 5
                reply_text = api_result["reply_text"]
                print(
                    json.dumps(
                        {
                            "reply_source": "api",
                            "api_model": api_result["model"],
                            "generated_reply": reply_text,
                            "memory_context_used": api_result.get("memory_context_used", False),
                            "service_knowledge_used": api_result.get("service_knowledge_used", False),
                            "memory_gate": memory_gate,
                            "service_knowledge_gate": service_knowledge_gate,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                reply_text = args.reply_text

            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "dry_run": True,
                            "would_reply": reply_text,
                            "trigger_message_id": trigger.get("message_id"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                for message in messages:
                    seen_keys.add(_wx_message_key(message))
                reply_count += 1
                if args.max_replies > 0 and reply_count >= args.max_replies:
                    print(json.dumps({"status": "done", "reply_count": reply_count}, ensure_ascii=False), flush=True)
                    return 0
                continue

            send_result = None
            for search_text in _send_search_candidates(args, target):
                send_result = send_text(search_text, reply_text)
                if send_result["returncode"] == 0:
                    print(json.dumps({"send_search": search_text, "send_result": send_result}, ensure_ascii=False), flush=True)
                    break
                print(json.dumps({"send_search": search_text, "send_result": send_result}, ensure_ascii=False), flush=True)
            else:
                print(json.dumps({"error": "all search candidates failed"}, ensure_ascii=False), flush=True)
                return 2

            verify_deadline = time.time() + max(args.verify_timeout, 1.0)
            while time.time() < verify_deadline:
                time.sleep(max(args.interval, 0.5))
                try:
                    messages = [message for message in get_new_messages() if _wx_message_matches_target(message, target)]
                except WxCliError as exc:
                    print(json.dumps({"warning": exc.to_dict(), "source_provider": "wx-cli"}, ensure_ascii=False), flush=True)
                    continue
                verified = _find_wx_verification(messages, seen_keys, reply_text)
                if verified:
                    print(json.dumps({"verified_sent_message": verified}, ensure_ascii=False), flush=True)
                    baseline_messages.extend(messages)
                    seen_keys.update(_wx_message_key(message) for message in messages)
                    reply_count += 1
                    last_reply_at = time.time()
                    if args.max_replies > 0 and reply_count >= args.max_replies:
                        print(json.dumps({"status": "done", "reply_count": reply_count}, ensure_ascii=False), flush=True)
                        return 0
                    break
            else:
                print(
                    json.dumps({"error": "reply sent via UI but not verified by wx-cli within timeout"}, ensure_ascii=False),
                    flush=True,
                )
                return 3

        print(json.dumps({"status": "timeout", "reply_count": reply_count}, ensure_ascii=False), flush=True)
        return 4
    except WxCliError as exc:
        print(json.dumps({"error": exc.to_dict(), "source_provider": "wx-cli"}, ensure_ascii=False), flush=True)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch one conversation and auto-reply through WeChat UI")
    parser.add_argument("--source", choices=("db", "wx-cli"), default="db", help="message source provider")
    parser.add_argument("--wechat-root", help="wxid account directory or db_storage directory")
    parser.add_argument("--frida-log", help="Frida PBKDF2 log path")
    parser.add_argument("--conversation", required=True, help="conversation username / display name / alias / remark")
    parser.add_argument("--reply-text", help="fixed reply text")
    parser.add_argument("--reply-source", choices=("fixed", "api"), default="fixed", help="reply generation source")
    parser.add_argument("--dry-run", action="store_true", help="print intended reply without sending through WeChat UI")
    parser.add_argument("--send-search", action="append", help="override WeChat UI search text; can be passed multiple times")
    parser.add_argument("--contact-db", help="prepared contact.db path used for private/non-muted policy checks")
    parser.add_argument("--include-muted", action="store_true", help="allow monitoring and replies for muted chats")
    parser.add_argument(
        "--allow-unknown-notification-state",
        action="store_true",
        help="allow monitoring when muted/non-muted state cannot be determined",
    )
    parser.add_argument(
        "--allow-non-private-auto-reply",
        action="store_true",
        help="allow auto replies outside private chats; disabled by default",
    )
    parser.add_argument("--interval", type=float, default=3.0, help="polling interval in seconds")
    parser.add_argument("--duration", type=float, default=180.0, help="watch duration in seconds")
    parser.add_argument("--verify-timeout", type=float, default=25.0, help="seconds to wait for sent-message verification")
    parser.add_argument("--cooldown", type=float, default=15.0, help="minimum seconds between two auto replies")
    parser.add_argument("--max-replies", type=int, default=1, help="max auto replies before exit; 0 means unlimited")
    parser.add_argument(
        "--wx-cli-allow-group-auto-reply",
        action="store_true",
        help="legacy alias: allow wx-cli auto replies in non-private chats",
    )
    parser.add_argument("--api-model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"), help="OpenAI-compatible model name")
    parser.add_argument("--api-base-url", default=os.environ.get("OPENAI_BASE_URL", ""), help="OpenAI-compatible base URL")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""), help="OpenAI-compatible API key")
    parser.add_argument("--context-messages", type=int, default=12, help="recent messages to include when generating API reply")
    parser.add_argument("--memory-root", help="customer memory root, e.g. out/customer-memory")
    parser.add_argument(
        "--memory-mode",
        choices=("off", "draft-only", "shadow"),
        default="off",
        help="use customer memory for API drafts; shadow emits context but does not pass it to the API",
    )
    parser.add_argument("--memory-fact-limit", type=int, default=3, help="max candidate facts per memory category")
    parser.add_argument("--memory-recent-limit", type=int, default=5, help="max recent memory messages")
    parser.add_argument(
        "--memory-use-policy",
        choices=("auto", "always", "never"),
        default="auto",
        help="decide whether a new incoming message should use customer memory",
    )
    parser.add_argument("--service-knowledge-root", default=".project-wiki", help="project wiki root for public reply playbooks")
    parser.add_argument(
        "--service-knowledge-mode",
        choices=("off", "shadow", "draft-only"),
        default="off",
        help="use public service playbooks for API drafts; shadow logs selection but does not pass it to the API",
    )
    parser.add_argument(
        "--service-knowledge-policy",
        choices=("auto", "always", "never"),
        default="auto",
        help="decide whether the latest message should use service knowledge",
    )
    parser.add_argument("--service-knowledge-max-playbooks", type=int, default=2, help="max service playbooks to include")
    parser.add_argument("--emit-context-json", action="store_true", help="print customer memory runtime context JSON")
    args = parser.parse_args()
    if args.reply_source == "fixed" and not args.reply_text:
        raise ValueError("--reply-text is required when --reply-source=fixed")
    if args.source == "wx-cli":
        return _run_wx_cli_auto_reply(args)
    if not args.wechat_root:
        parser.error("--wechat-root is required when --source db")
    if not args.frida_log:
        parser.error("--frida-log is required when --source db")

    db_storage_root = _resolve_db_storage_root(Path(args.wechat_root))
    frida_log = Path(args.frida_log).expanduser().resolve()
    if not frida_log.exists():
        raise FileNotFoundError(f"frida log not found: {frida_log}")

    cache_root = _ensure_dir(Path(".cache-auto-reply").resolve())
    target = enrich_target_with_contact_policy(
        _load_target_session(db_storage_root, cache_root, frida_log, args.conversation),
        args.contact_db,
    )
    print(json.dumps({"target": target}, ensure_ascii=False, indent=2), flush=True)

    policy_block = contact_policy_block(
        target,
        allow_non_private=args.allow_non_private_auto_reply,
        include_muted=args.include_muted,
        allow_unknown_notification_state=args.allow_unknown_notification_state,
    )
    if policy_block:
        print(
            json.dumps(
                {
                    "error": "target_skipped_by_contact_policy",
                    "message": "auto-reply only handles private, non-muted chats by default",
                    "chat_type": target.get("chat_type") or target.get("conversation_type"),
                    "notification_state": target.get("notification_state"),
                    "policy_block": policy_block,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 6

    memory_context = _load_memory_context(args, target)
    _maybe_emit_memory_context(args, memory_context)

    prepared_state: dict[str, dict[str, Any]] = {}
    prepared_paths = _refresh_prepared_messages(db_storage_root, cache_root, frida_log, prepared_state)
    baseline_messages = _read_messages(prepared_paths, target["conversation_username"])
    seen_keys = {_message_key(message) for message in baseline_messages}
    print(json.dumps({"baseline_count": len(baseline_messages)}, ensure_ascii=False), flush=True)

    reply_count = 0
    last_reply_at = 0.0
    deadline = time.time() + max(args.duration, 0)
    while time.time() < deadline:
        time.sleep(max(args.interval, 0.5))
        try:
            prepared_paths = _refresh_prepared_messages(db_storage_root, cache_root, frida_log, prepared_state)
            messages = _read_messages(prepared_paths, target["conversation_username"])
        except ChatCryptoError as exc:
            print(json.dumps({"warning": str(exc)}, ensure_ascii=False), flush=True)
            continue

        new_received = []
        for message in messages:
            key = _message_key(message)
            if key in seen_keys:
                continue
            if args.reply_text and (message.get("text") or "").strip() == args.reply_text.strip():
                continue
            if message.get("direction") == "received":
                new_received.append(message)

        if not new_received:
            for message in messages:
                seen_keys.add(_message_key(message))
            continue

        trigger = new_received[-1]
        print(json.dumps({"trigger_message": trigger}, ensure_ascii=False), flush=True)

        now = time.time()
        if last_reply_at and now - last_reply_at < max(args.cooldown, 0):
            print(
                json.dumps(
                    {
                        "cooldown_skip": {
                            "seconds_since_last_reply": round(now - last_reply_at, 2),
                            "cooldown": args.cooldown,
                            "message_id": trigger.get("message_id"),
                            "text": trigger.get("text") or "",
                        }
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            for message in messages:
                seen_keys.add(_message_key(message))
            continue

        if args.reply_source == "api":
            recent_context = [message for message in messages if _message_key(message) not in seen_keys]
            if len(recent_context) < args.context_messages:
                recent_context = messages[-max(args.context_messages, 1) :]
            api_memory_context, memory_gate = _memory_context_for_api(args, memory_context, trigger)
            service_knowledge_context = _load_service_knowledge_context(args, trigger)
            api_service_knowledge_context, service_knowledge_gate = _service_knowledge_context_for_api(
                args,
                service_knowledge_context,
            )
            print(json.dumps({"memory_gate": memory_gate}, ensure_ascii=False), flush=True)
            print(json.dumps({"service_knowledge_gate": service_knowledge_gate}, ensure_ascii=False), flush=True)
            try:
                api_result = generate_reply_openai_compatible(
                    latest_message=trigger,
                    recent_messages=recent_context,
                    conversation_name=target["display_name"],
                    model=args.api_model,
                    base_url=args.api_base_url,
                    api_key=args.api_key,
                    context_limit=args.context_messages,
                    memory_context=api_memory_context,
                    service_knowledge_context=api_service_knowledge_context,
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "error": "api_reply_generation_failed",
                            "model": args.api_model,
                            "message": str(exc),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return 5
            reply_text = api_result["reply_text"]
            print(
                json.dumps(
                    {
                        "reply_source": "api",
                        "api_model": api_result["model"],
                        "generated_reply": reply_text,
                        "memory_context_used": api_result.get("memory_context_used", False),
                        "service_knowledge_used": api_result.get("service_knowledge_used", False),
                        "memory_gate": memory_gate,
                        "service_knowledge_gate": service_knowledge_gate,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        else:
            reply_text = args.reply_text

        if args.dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "would_reply": reply_text,
                        "trigger_message_id": trigger.get("message_id"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            seen_keys = {_message_key(message) for message in messages}
            reply_count += 1
            if args.max_replies > 0 and reply_count >= args.max_replies:
                print(json.dumps({"status": "done", "reply_count": reply_count}, ensure_ascii=False), flush=True)
                return 0
            continue

        send_result = None
        for search_text in _send_search_candidates(args, target):
            send_result = send_text(search_text, reply_text)
            if send_result["returncode"] == 0:
                print(json.dumps({"send_search": search_text, "send_result": send_result}, ensure_ascii=False), flush=True)
                break
            print(json.dumps({"send_search": search_text, "send_result": send_result}, ensure_ascii=False), flush=True)
        else:
            print(json.dumps({"error": "all search candidates failed"}, ensure_ascii=False), flush=True)
            return 2

        verify_deadline = time.time() + max(args.verify_timeout, 1.0)
        while time.time() < verify_deadline:
            time.sleep(max(args.interval, 0.5))
            prepared_paths = _refresh_prepared_messages(db_storage_root, cache_root, frida_log, prepared_state)
            messages = _read_messages(prepared_paths, target["conversation_username"])
            verified = _find_verification(messages, seen_keys, reply_text)
            if verified:
                print(json.dumps({"verified_sent_message": verified}, ensure_ascii=False), flush=True)
                seen_keys = {_message_key(message) for message in messages}
                reply_count += 1
                last_reply_at = time.time()
                if args.max_replies > 0 and reply_count >= args.max_replies:
                    print(json.dumps({"status": "done", "reply_count": reply_count}, ensure_ascii=False), flush=True)
                    return 0
                break
        else:
            print(json.dumps({"error": "reply sent via UI but not verified in database within timeout"}, ensure_ascii=False), flush=True)
            return 3

    print(json.dumps({"status": "timeout", "reply_count": reply_count}, ensure_ascii=False), flush=True)
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
