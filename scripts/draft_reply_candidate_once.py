#!/usr/bin/env python3

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from customer_memory import build_runtime_context_for_query, should_use_memory_for_message
from personal_reply_style import build_personal_style_context, personal_style_context_for_api
from reply_api import generate_reply_openai_compatible
from service_knowledge import build_service_knowledge_context, service_knowledge_context_for_api
from wechat_common import _ensure_dir, _write_json
from wechat_ui_send import draft_text
from wx_cli_adapter import get_history


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def event_key(event: dict[str, Any]) -> str:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    return "|".join(
        [
            str(event.get("conversation_username") or ""),
            str(message.get("message_id") or ""),
            str(message.get("timestamp") or ""),
            str(message.get("text") or ""),
        ]
    )


def event_sort_key(index: int, event: dict[str, Any]) -> tuple[float, str, int]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    try:
        timestamp = float(message.get("timestamp") or 0)
    except (TypeError, ValueError):
        timestamp = 0
    return (timestamp, str(event.get("detected_at") or ""), index)


def message_timestamp_seconds(event: dict[str, Any]) -> Optional[float]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    try:
        return float(message.get("timestamp") or 0)
    except (TypeError, ValueError):
        return None


def message_is_fresh(event: dict[str, Any], fresh_within_seconds: float) -> bool:
    if fresh_within_seconds <= 0:
        return True
    timestamp = message_timestamp_seconds(event)
    if not timestamp:
        return False
    age = time.time() - timestamp
    return -300 <= age <= fresh_within_seconds


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    if not path.exists():
        return events
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def message_text(event: dict[str, Any]) -> str:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    return str(message.get("text") or "").strip()


def is_draftable_event(event: dict[str, Any], *, include_non_text: bool) -> bool:
    if event.get("draft_status") not in {"", None, "not_generated"}:
        return False
    if event.get("ui_status") not in {"", None, "not_touched"}:
        return False
    if event.get("send_status") not in {"", None, "not_sent"}:
        return False
    text = message_text(event)
    if not text:
        return False
    if include_non_text:
        return True
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    message_type = str(message.get("message_type") or "").strip()
    return message_type in {"", "文本", "text", "Text"} and text not in {"[表情]", "[图片]"}


def draft_worthiness_gate(event: dict[str, Any]) -> dict[str, Any]:
    text = message_text(event)
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    message_type = str(message.get("message_type") or "").strip()
    signals = {
        "message_type": message_type,
        "text_length": len(text),
        "line_count": text.count("\n") + 1 if text else 0,
    }

    if message_type not in {"", "文本", "text", "Text"}:
        return {"decision": False, "reason": "non_text_message", "signals": signals}
    if text in {"[表情]", "[图片]"}:
        return {"decision": False, "reason": "placeholder_non_text", "signals": signals}

    promo_markers = [
        "秒杀",
        "清仓",
        "特价",
        "限时",
        "补货",
        "小程序",
        "CDATA",
        "优惠",
        "满减",
        "团购",
        "爆品",
        "元起",
        "贵妇面霜",
        "💰",
        "✅",
        "⭕",
        "-----------",
    ]
    intent_markers = [
        "?",
        "？",
        "吗",
        "么",
        "在吗",
        "怎么",
        "多少",
        "有没有",
        "能不能",
        "可以",
        "帮我",
        "发一下",
        "什么时候",
        "物流",
        "订单",
        "售后",
        "退款",
        "换货",
        "地址",
        "价格",
        "码数",
        "尺码",
        "干嘛",
        "在干嘛",
    ]
    promo_hits = [marker for marker in promo_markers if marker in text]
    intent_hits = [marker for marker in intent_markers if marker in text]
    signals["promo_hits"] = promo_hits
    signals["intent_hits"] = intent_hits

    if len(promo_hits) >= 4 and (signals["line_count"] >= 3 or "-----------" in text):
        return {"decision": False, "reason": "likely_broadcast_promotion", "signals": signals}
    if len(text) > 220 and len(promo_hits) >= 2 and not intent_hits:
        return {"decision": False, "reason": "long_promotion_without_request", "signals": signals}
    return {"decision": True, "reason": "draft_worthy", "signals": signals}


def select_candidate(
    events: list[dict[str, Any]],
    processed: set[str],
    *,
    include_non_text: bool,
    fresh_within_seconds: float,
) -> Optional[dict[str, Any]]:
    candidates = []
    for index, event in enumerate(events):
        key = event_key(event)
        if key in processed:
            continue
        if not is_draftable_event(event, include_non_text=include_non_text):
            continue
        if not message_is_fresh(event, fresh_within_seconds):
            continue
        candidates.append((event_sort_key(index, event), event))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def load_manifest_contact(manifest_path: Path, event: dict[str, Any]) -> dict[str, Any]:
    data = load_json(manifest_path, {})
    contacts = data.get("contacts") if isinstance(data, dict) else []
    username = str(event.get("conversation_username") or "")
    contact_id = str(event.get("contact_id") or "")
    display_name = str(event.get("display_name") or "")
    for contact in contacts or []:
        if not isinstance(contact, dict):
            continue
        if username and contact.get("conversation_username") == username:
            return contact
        if contact_id and contact.get("contact_id") == contact_id:
            return contact
        if display_name and contact.get("display_name") == display_name:
            return contact
    return {}


def load_export_contact(contacts_path: Path, username: str) -> dict[str, Any]:
    if not username or not contacts_path.exists():
        return {}
    data = load_json(contacts_path, [])
    if not isinstance(data, list):
        return {}
    for contact in data:
        if not isinstance(contact, dict):
            continue
        if str(contact.get("username") or "") == username:
            return contact
    return {}


def memory_query_candidates(event: dict[str, Any], contact: dict[str, Any]) -> list[str]:
    candidates = []
    for value in [
        event.get("conversation_username"),
        event.get("display_name"),
        event.get("contact_id"),
        contact.get("conversation_username"),
        contact.get("display_name"),
        contact.get("contact_id"),
    ]:
        text = str(value or "").strip()
        if text and text not in candidates:
            candidates.append(text)
    return candidates


def load_memory_context(
    memory_root: Optional[str],
    event: dict[str, Any],
    contact: dict[str, Any],
    *,
    fact_limit: int,
    recent_limit: int,
) -> tuple[Optional[dict[str, Any]], list[dict[str, str]]]:
    if not memory_root:
        return None, []
    root = Path(memory_root).expanduser().resolve()
    errors = []
    for query in memory_query_candidates(event, contact):
        try:
            context = build_runtime_context_for_query(root, query, fact_limit=fact_limit, recent_limit=recent_limit)
            context["lookup_query"] = query
            return context, errors
        except (OSError, ValueError) as exc:
            errors.append({"query": query, "error": str(exc)})
    return None, errors[:5]


def load_personal_style_context(
    style_root: Optional[str],
    *,
    latest_message: dict[str, Any],
    conversation_username: str,
    conversation_name: str,
    max_examples: int,
) -> tuple[Optional[dict[str, Any]], list[dict[str, str]]]:
    if not style_root:
        return None, []
    root = Path(style_root).expanduser().resolve()
    try:
        return (
            build_personal_style_context(
                root,
                latest_message=latest_message,
                conversation_username=conversation_username,
                conversation_name=conversation_name,
                max_examples=max_examples,
            ),
            [],
        )
    except (OSError, ValueError) as exc:
        return None, [{"root": str(root), "error": str(exc)}]


def prepare_latest_message(event: dict[str, Any]) -> dict[str, Any]:
    message = dict(event.get("message") if isinstance(event.get("message"), dict) else {})
    message.setdefault("conversation_username", event.get("conversation_username") or "")
    message.setdefault("display_name", event.get("display_name") or "")
    return message


def history_key(message: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(message.get("message_id") or ""),
        str(message.get("timestamp") or ""),
        str(message.get("text") or ""),
    )


def send_search_candidates(
    event: dict[str, Any],
    contact: dict[str, Any],
    export_contact: dict[str, Any],
    overrides: list[str],
) -> list[str]:
    candidates = []
    for value in [
        *overrides,
        event.get("wechat_id"),
        event.get("alias"),
        contact.get("wechat_id"),
        contact.get("alias"),
        export_contact.get("alias"),
        event.get("conversation_username"),
        contact.get("conversation_username"),
        export_contact.get("username"),
        event.get("display_name"),
        contact.get("display_name"),
        export_contact.get("remark"),
        export_contact.get("nick_name"),
        contact.get("remark"),
    ]:
        text = str(value or "").strip()
        if text and text not in candidates:
            candidates.append(text)
    return candidates


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        f.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a reply draft for one monitored candidate and optionally paste it into WeChat without sending.")
    parser.add_argument("--events", default="out/dry-run-replies/monitor/events.jsonl", help="Monitor events JSONL path.")
    parser.add_argument("--manifest", default="out/contact-wiki/manifest.json", help="Contact wiki manifest path.")
    parser.add_argument("--contacts-json", default="out/chat-export/export/contacts.json", help="Exported contacts JSON used to prefer public WeChat IDs for UI search.")
    parser.add_argument("--out-root", default="out/dry-run-replies/drafts", help="Draft output root.")
    parser.add_argument("--memory-root", default="out/customer-memory", help="Customer memory root.")
    parser.add_argument("--memory-mode", choices=("off", "draft-only", "shadow"), default="draft-only")
    parser.add_argument("--memory-use-policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--memory-fact-limit", type=int, default=3)
    parser.add_argument("--memory-recent-limit", type=int, default=5)
    parser.add_argument("--service-knowledge-root", default=".project-wiki")
    parser.add_argument("--service-knowledge-mode", choices=("off", "shadow", "draft-only"), default="draft-only")
    parser.add_argument("--service-knowledge-policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--service-knowledge-max-playbooks", type=int, default=2)
    parser.add_argument("--personal-style-root", default="", help="Optional root built by build_personal_reply_style.py")
    parser.add_argument("--personal-style-mode", choices=("off", "shadow", "draft-only"), default="off")
    parser.add_argument("--personal-style-max-examples", type=int, default=4)
    parser.add_argument("--api-model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"))
    parser.add_argument("--api-base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--context-messages", type=int, default=12)
    parser.add_argument("--draft-to-input", action="store_true", help="Paste the generated draft into WeChat input without pressing Enter.")
    parser.add_argument("--send-search", action="append", default=[], help="Override WeChat search text; can be repeated.")
    parser.add_argument("--include-non-text", action="store_true", help="Allow image/sticker/link candidates to be drafted.")
    parser.add_argument("--allow-repeat", action="store_true", help="Allow reprocessing an event already drafted by this script.")
    parser.add_argument("--skip-draft-worthiness-gate", action="store_true", help="Allow drafting likely broadcast/promotional text.")
    parser.add_argument(
        "--fresh-within-seconds",
        type=float,
        default=1800.0,
        help="Only draft messages whose message timestamp is this recent; 0 disables the freshness gate.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    events_path = Path(args.events).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    contacts_path = Path(args.contacts_json).expanduser().resolve()
    out_root = _ensure_dir(Path(args.out_root).expanduser().resolve())
    state_path = out_root / "state.json"
    drafts_path = out_root / "drafts.jsonl"

    state = load_json(state_path, {})
    processed_event_keys = set() if args.allow_repeat else set(state.get("processed_event_keys") or [])
    skipped_event_keys = set() if args.allow_repeat else set(state.get("skipped_event_keys") or [])
    ignored_event_keys = processed_event_keys | skipped_event_keys
    events = load_events(events_path)
    candidate = select_candidate(
        events,
        ignored_event_keys,
        include_non_text=args.include_non_text,
        fresh_within_seconds=max(args.fresh_within_seconds, 0),
    )
    if not candidate:
        payload = {
            "status": "no_candidate",
            "updated_at": utc_now_iso(),
            "event_count": len(events),
            "processed_count": len(processed_event_keys),
            "skipped_count": len(skipped_event_keys),
            "fresh_within_seconds": max(args.fresh_within_seconds, 0),
            "sent": False,
        }
        _write_json(state_path, {**state, **payload})
        print(json.dumps(payload, ensure_ascii=False))
        return 3

    key = event_key(candidate)
    contact = load_manifest_contact(manifest_path, candidate)
    latest_message = prepare_latest_message(candidate)
    conversation_username = str(candidate.get("conversation_username") or contact.get("conversation_username") or "")
    export_contact = load_export_contact(contacts_path, conversation_username)
    conversation_name = str(candidate.get("display_name") or contact.get("display_name") or conversation_username)

    worthiness_gate = (
        {"decision": True, "reason": "disabled", "signals": {}}
        if args.skip_draft_worthiness_gate
        else draft_worthiness_gate(candidate)
    )
    if not worthiness_gate.get("decision"):
        row = {
            "schema_version": "reply_candidate_skip_v1",
            "created_at": utc_now_iso(),
            "event_key": key,
            "candidate": {
                "contact_id": candidate.get("contact_id") or "",
                "conversation_username": conversation_username,
                "display_name": conversation_name,
                "tier": candidate.get("tier") or "",
                "score": candidate.get("score") or 0,
                "message": latest_message,
            },
            "skip": {
                "reason": worthiness_gate.get("reason") or "not_draft_worthy",
                "draft_worthiness_gate": worthiness_gate,
            },
            "sent": False,
            "send_status": "not_sent",
        }
        append_jsonl(drafts_path, row)
        skipped_event_keys = set(skipped_event_keys)
        skipped_event_keys.add(key)
        new_state = {
            **state,
            "status": "skipped_not_draft_worthy",
            "updated_at": utc_now_iso(),
            "last_event_key": key,
            "last_skip": row,
            "processed_event_keys": list(processed_event_keys),
            "skipped_event_keys": list(skipped_event_keys),
            "processed_count": len(processed_event_keys),
            "skipped_count": len(skipped_event_keys),
            "sent": False,
        }
        _write_json(state_path, new_state)
        print(
            json.dumps(
                {
                    "status": "skipped_not_draft_worthy",
                    "display_name": conversation_name,
                    "conversation_username": conversation_username,
                    "trigger_text": message_text(candidate),
                    "reason": worthiness_gate.get("reason"),
                    "sent": False,
                    "out_state": str(state_path),
                },
                ensure_ascii=False,
            )
        )
        return 2

    recent_messages = get_history(conversation_username or conversation_name, max(args.context_messages, 1))
    if history_key(latest_message) not in {history_key(message) for message in recent_messages}:
        recent_messages.append(latest_message)

    memory_context, memory_errors = load_memory_context(
        None if args.memory_mode == "off" else args.memory_root,
        candidate,
        contact,
        fact_limit=args.memory_fact_limit,
        recent_limit=args.memory_recent_limit,
    )
    memory_gate = should_use_memory_for_message(message_text(candidate), policy=args.memory_use_policy)
    memory_gate["memory_mode"] = args.memory_mode
    memory_gate["memory_context_available"] = bool(memory_context)
    api_memory_context = memory_context if args.memory_mode == "draft-only" and memory_gate.get("decision") else None

    service_context = (
        None
        if args.service_knowledge_mode == "off"
        else build_service_knowledge_context(
            message_text(candidate),
            wiki_root=Path(args.service_knowledge_root),
            policy=args.service_knowledge_policy,
            max_playbooks=args.service_knowledge_max_playbooks,
        )
    )
    api_service_context, service_gate = service_knowledge_context_for_api(args.service_knowledge_mode, service_context)
    personal_style_context, personal_style_errors = load_personal_style_context(
        None if args.personal_style_mode == "off" else args.personal_style_root,
        latest_message=latest_message,
        conversation_username=conversation_username,
        conversation_name=conversation_name,
        max_examples=max(args.personal_style_max_examples, 1),
    )
    api_personal_style_context, personal_style_gate = personal_style_context_for_api(
        args.personal_style_mode,
        personal_style_context,
    )

    api_result = generate_reply_openai_compatible(
        latest_message=latest_message,
        recent_messages=recent_messages[-max(args.context_messages, 1) :],
        conversation_name=conversation_name,
        model=args.api_model,
        base_url=args.api_base_url,
        api_key=args.api_key,
        context_limit=args.context_messages,
        memory_context=api_memory_context,
        service_knowledge_context=api_service_context,
        personal_style_context=api_personal_style_context,
    )
    reply_text = api_result["reply_text"]

    before_history_keys = {history_key(message) for message in get_history(conversation_username or conversation_name, 5)}
    attempts = []
    drafted_to_input = False
    if args.draft_to_input:
        for search_text in send_search_candidates(candidate, contact, export_contact, args.send_search):
            result = draft_text(search_text, reply_text)
            attempts.append(
                {
                    "search_text": search_text,
                    "returncode": result.get("returncode"),
                    "stdout": result.get("stdout"),
                    "stderr": bool(result.get("stderr")),
                }
            )
            if result.get("returncode") == 0:
                drafted_to_input = True
                break
    after_history = get_history(conversation_username or conversation_name, 5)
    after_history_keys = {history_key(message) for message in after_history}
    sent = any(
        history_key(message) not in before_history_keys and str(message.get("text") or "").strip() == reply_text.strip()
        for message in after_history
    )

    row = {
        "schema_version": "reply_candidate_draft_v1",
        "created_at": utc_now_iso(),
        "event_key": key,
        "candidate": {
            "contact_id": candidate.get("contact_id") or "",
            "conversation_username": conversation_username,
            "display_name": conversation_name,
            "tier": candidate.get("tier") or "",
            "score": candidate.get("score") or 0,
            "message": latest_message,
        },
        "draft": {
            "reply_text": reply_text,
            "api_model": api_result.get("model") or args.api_model,
            "memory_context_used": api_result.get("memory_context_used", False),
            "service_knowledge_used": api_result.get("service_knowledge_used", False),
            "personal_style_used": api_result.get("personal_style_used", False),
            "memory_gate": memory_gate,
            "memory_errors": memory_errors,
            "service_knowledge_gate": service_gate,
            "personal_style_gate": personal_style_gate,
            "personal_style_errors": personal_style_errors,
        },
        "ui": {
            "draft_to_input_requested": bool(args.draft_to_input),
            "drafted_to_input": drafted_to_input,
            "attempts": attempts,
        },
        "sent": sent,
        "send_status": "sent_unexpectedly" if sent else "not_sent",
        "history_new_keys_after_draft": list(sorted(after_history_keys - before_history_keys)),
    }
    append_jsonl(drafts_path, row)

    processed_event_key_list = list(processed_event_keys)
    if key not in processed_event_key_list:
        processed_event_key_list.append(key)
    new_state = {
        **state,
        "status": "drafted_to_input" if drafted_to_input else "draft_generated",
        "updated_at": utc_now_iso(),
        "last_event_key": key,
        "last_draft": row,
        "processed_event_keys": processed_event_key_list,
        "skipped_event_keys": list(skipped_event_keys),
        "processed_count": len(processed_event_key_list),
        "skipped_count": len(skipped_event_keys),
        "sent": sent,
    }
    _write_json(state_path, new_state)

    print(
        json.dumps(
            {
                "status": new_state["status"],
                "display_name": conversation_name,
                "conversation_username": conversation_username,
                "trigger_text": message_text(candidate),
                "draft_reply": reply_text,
                "memory_context_used": row["draft"]["memory_context_used"],
                "service_knowledge_used": row["draft"]["service_knowledge_used"],
                "personal_style_used": row["draft"]["personal_style_used"],
                "drafted_to_input": drafted_to_input,
                "sent": sent,
                "out_state": str(state_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if drafted_to_input or not args.draft_to_input else 4


if __name__ == "__main__":
    raise SystemExit(main())
