#!/usr/bin/env python3

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from wechat_common import _timestamp_to_iso
from wx_cli_adapter import WxCliError, get_history, list_members, resolve_conversation
from wx_cli_profile import profile_db_dir, resolve_profile_dir


SCHEMA_VERSION = "recent_chat_query_v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read recent WeChat history for one contact or group. This is read-only.")
    parser.add_argument("--chat", required=True, help="contact/group name, username, remark, or alias")
    parser.add_argument(
        "--source",
        choices=("wx-history", "live-inbox"),
        default="wx-history",
        help="read source: wx-history calls wx-cli history; live-inbox reads synced plaintext events.jsonl",
    )
    parser.add_argument(
        "--live-inbox-root",
        default="~/Sync/wechat-live-inbox",
        help="folder containing live-inbox events.jsonl when --source live-inbox",
    )
    parser.add_argument("--limit", type=int, default=30, help="number of recent messages to read")
    parser.add_argument("--offset", type=int, default=0, help="history offset passed to wx-cli")
    parser.add_argument("--since", default="", help="optional start date passed to wx history, YYYY-MM-DD")
    parser.add_argument("--until", default="", help="optional end date passed to wx history, YYYY-MM-DD")
    parser.add_argument("--wx-cli-profile", help="optional wx-cli profile directory or config.json path")
    parser.add_argument("--self-username", default="", help="optional current-account username/wxid for group speaker checks")
    parser.add_argument("--self-display", default="", help="optional current-account display name fallback")
    parser.add_argument("--format", choices=("text", "json", "jsonl"), default="text", help="output format")
    parser.add_argument("--summary-only", action="store_true", help="omit the message list in text/json output")
    parser.add_argument("--recent-first", action="store_true", help="keep newest messages first")
    parser.add_argument("--include-raw", action="store_true", help="include raw wx-cli payloads in json/jsonl output")
    parser.add_argument("--max-text-chars", type=int, default=300, help="truncate each message text to this length")
    return parser.parse_args()


def set_profile_env(profile: str) -> tuple[str, bool]:
    previous = os.environ.get("WX_CLI_CONFIG_DIR", "")
    if profile:
        os.environ["WX_CLI_CONFIG_DIR"] = profile
        return previous, True
    return previous, False


def restore_profile_env(previous: str, changed: bool) -> None:
    if not changed:
        return
    if previous:
        os.environ["WX_CLI_CONFIG_DIR"] = previous
    else:
        os.environ.pop("WX_CLI_CONFIG_DIR", None)


def conversation_type(target: dict[str, Any]) -> str:
    chat_type = str(target.get("chat_type") or target.get("conversation_type") or "").strip()
    if chat_type in {"private", "friend"}:
        return "friend"
    if chat_type in {"group", "chatroom"}:
        return "group"
    if chat_type in {"official", "official_account"}:
        return "official"
    username = str(target.get("conversation_username") or "")
    if username.endswith("@chatroom"):
        return "group"
    if username.startswith("gh_"):
        return "official"
    return chat_type or "unknown"


def target_display_name(target: dict[str, Any]) -> str:
    for key in ("display_name", "remark", "nick_name", "alias", "conversation_username"):
        value = str(target.get(key) or "").strip()
        if value:
            return value
    return ""


def message_raw(message: dict[str, Any]) -> dict[str, Any]:
    raw = message.get("raw_payload")
    return raw if isinstance(raw, dict) else {}


def first_text(source: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = str(source.get(name) or "").strip()
        if value:
            return value
    return ""


def raw_sender(message: dict[str, Any]) -> str:
    raw = message_raw(message)
    return (
        str(message.get("sender_id") or "").strip()
        or first_text(raw, ("sender", "from_user", "from", "from_username", "talker", "username"))
    )


def raw_message_text(message: dict[str, Any]) -> str:
    raw = message_raw(message)
    text = str(message.get("text") or "").strip()
    if not text:
        text = first_text(raw, ("content", "text", "message", "msg", "body", "summary", "plain_text"))
    if text:
        return text
    message_type = str(message.get("message_type") or raw.get("type") or "").strip()
    return f"[{message_type or '消息'}]"


def compact_text(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max(max_chars - 1, 0)].rstrip() + "…"


def timestamp_candidates(message: dict[str, Any]) -> list[Any]:
    raw = message_raw(message)
    return [
        message.get("timestamp"),
        raw.get("timestamp"),
        raw.get("create_time"),
        raw.get("created_at"),
        raw.get("time"),
        raw.get("msg_time"),
    ]


def timestamp_iso(message: dict[str, Any]) -> str:
    for value in timestamp_candidates(message):
        iso = _timestamp_to_iso(value)
        if iso:
            return iso
    for value in timestamp_candidates(message):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def parse_timestamp(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        number = float(text)
        return number / 1000 if number > 10_000_000_000 else number
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).timestamp()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[: len(datetime.now().strftime(fmt))], fmt).timestamp()
        except ValueError:
            pass
    return None


def message_sort_value(message: dict[str, Any]) -> Optional[float]:
    for value in timestamp_candidates(message):
        parsed = parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def ordered_messages(messages: list[dict[str, Any]], recent_first: bool) -> list[dict[str, Any]]:
    if recent_first:
        return messages
    sortable = [(index, message_sort_value(message), message) for index, message in enumerate(messages)]
    if all(item[1] is not None for item in sortable):
        return [message for _, _, message in sorted(sortable, key=lambda item: (item[1], item[0]))]
    return list(reversed(messages))


def member_username(member: dict[str, Any]) -> str:
    return first_text(member, ("username", "user_name", "wxid", "id"))


def member_display(member: dict[str, Any]) -> str:
    return first_text(
        member,
        (
            "display",
            "display_name",
            "group_nickname",
            "contact_display",
            "remark",
            "remark_name",
            "nick_name",
            "nickname",
            "name",
            "username",
        ),
    )


def member_names(member: dict[str, Any]) -> set[str]:
    names = set()
    for key in (
        "display",
        "display_name",
        "group_nickname",
        "contact_display",
        "remark",
        "remark_name",
        "nick_name",
        "nickname",
        "name",
        "username",
        "user_name",
        "wxid",
    ):
        value = str(member.get(key) or "").strip()
        if value:
            names.add(value)
    return names


def build_member_index(members: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        for name in member_names(member):
            index.setdefault(name, []).append(member)
    return index


def account_candidates_from_db_dir(db_dir: Optional[Path]) -> list[str]:
    if not db_dir:
        return []
    candidates = []
    for part in (db_dir.name, db_dir.parent.name):
        if not part.startswith("wxid_"):
            continue
        candidates.append(part)
        pieces = part.split("_")
        if len(pieces) >= 3:
            candidates.append("_".join(pieces[:2]))
    out = []
    seen = set()
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def active_profile_db_dir() -> Optional[Path]:
    profile_dir = resolve_profile_dir()
    if not profile_dir:
        return None
    try:
        return profile_db_dir(profile_dir)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return None


def resolve_self_member(
    members: list[dict[str, Any]],
    *,
    explicit_username: str,
    explicit_display: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence: dict[str, Any] = {
        "method": "not_resolved",
        "confidence": "missing",
        "candidates": [],
        "warnings": [],
    }
    if explicit_username:
        for member in members:
            if member_username(member) == explicit_username:
                evidence.update({"method": "explicit_username", "confidence": "high"})
                return member, evidence
        evidence.update({"method": "explicit_username", "confidence": "missing"})
        evidence["warnings"].append("explicit self username was not found in group members")

    db_dir = active_profile_db_dir()
    candidates = account_candidates_from_db_dir(db_dir)
    evidence["candidates"] = candidates
    for candidate in candidates:
        for member in members:
            if member_username(member) == candidate:
                evidence.update({"method": "profile_db_dir_account_username", "confidence": "high"})
                return member, evidence

    if explicit_display:
        matches = [member for member in members if explicit_display in member_names(member)]
        evidence["method"] = "explicit_display"
        if len(matches) == 1:
            evidence["confidence"] = "medium"
            return matches[0], evidence
        if len(matches) > 1:
            evidence["confidence"] = "ambiguous"
            evidence["warnings"].append("explicit self display matched more than one group member")
        else:
            evidence["warnings"].append("explicit self display was not found in group members")

    return {}, evidence


def group_context(target: dict[str, Any], args: argparse.Namespace, warnings: list[str]) -> dict[str, Any]:
    if conversation_type(target) != "group":
        return {}
    chat = str(target.get("conversation_username") or target.get("display_name") or args.chat).strip()
    try:
        members = list_members(chat)
    except WxCliError as exc:
        warnings.append(f"group member lookup failed: {exc.code}: {exc.message}")
        return {
            "members": [],
            "member_index": {},
            "self_member": {},
            "self_evidence": {"method": "members_unavailable", "confidence": "missing", "warnings": [exc.message]},
        }

    self_member, self_evidence = resolve_self_member(
        members,
        explicit_username=args.self_username.strip(),
        explicit_display=args.self_display.strip(),
    )
    for warning in self_evidence.get("warnings", []):
        warnings.append(str(warning))
    if not self_member:
        warnings.append("current account was not resolved in this group; self/other labels are conservative")
    return {
        "members": members,
        "member_index": build_member_index(members),
        "self_member": self_member,
        "self_evidence": self_evidence,
    }


def infer_private_direction(message: dict[str, Any]) -> str:
    direction = str(message.get("direction") or "").strip().lower()
    if direction in {"sent", "received"}:
        return direction
    return ""


def annotate_private_message(message: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    direction = infer_private_direction(message)
    if direction == "sent":
        return {
            "speaker": "我",
            "speaker_role": "self",
            "speaker_confidence": "high",
            "speaker_reason": "private chat direction from wx-cli",
        }
    if direction == "received":
        return {
            "speaker": target_display_name(target) or raw_sender(message) or "对方",
            "speaker_role": "other",
            "speaker_confidence": "high",
            "speaker_reason": "private chat direction from wx-cli",
        }
    sender = raw_sender(message)
    if sender:
        return {
            "speaker": sender,
            "speaker_role": "sender_field",
            "speaker_confidence": "medium",
            "speaker_reason": "private chat direction was missing; using sender field without assuming self/other",
        }
    return {
        "speaker": target_display_name(target) or "对方",
        "speaker_role": "other",
        "speaker_confidence": "low",
        "speaker_reason": "private chat direction and sender field were missing; using the conversation name as a fallback",
    }


def annotate_group_message(message: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    sender = raw_sender(message)
    if not sender:
        return {
            "speaker": "系统/未知",
            "speaker_role": "unknown_or_system",
            "speaker_confidence": "low",
            "speaker_reason": "wx history did not provide a sender field",
            "matched_usernames": [],
        }

    member_index = ctx.get("member_index") if isinstance(ctx.get("member_index"), dict) else {}
    matches = member_index.get(sender, [])
    self_member = ctx.get("self_member") if isinstance(ctx.get("self_member"), dict) else {}
    self_username = member_username(self_member) if self_member else ""
    matched_usernames = [member_username(member) for member in matches if member_username(member)]

    if len(matches) == 1:
        member = matches[0]
        username = member_username(member)
        if not self_username:
            return {
                "speaker": member_display(member) or sender,
                "speaker_role": "known_member",
                "speaker_confidence": "medium",
                "speaker_reason": "sender matched exactly one group member, but the current account was not resolved",
                "matched_usernames": matched_usernames,
            }
        is_self = username == self_username
        return {
            "speaker": "我" if is_self else member_display(member) or sender,
            "speaker_role": "self" if is_self else "other_member",
            "speaker_confidence": "high",
            "speaker_reason": "sender matched exactly one group member",
            "matched_usernames": matched_usernames,
        }
    if len(matches) > 1:
        return {
            "speaker": f"{sender}（多人同名）",
            "speaker_role": "ambiguous_member",
            "speaker_confidence": "low",
            "speaker_reason": "sender matched multiple group members",
            "matched_usernames": matched_usernames,
        }
    return {
        "speaker": sender,
        "speaker_role": "unknown_member",
        "speaker_confidence": "low",
        "speaker_reason": "sender did not match the group member list",
        "matched_usernames": [],
    }


def annotate_message(
    message: dict[str, Any],
    target: dict[str, Any],
    ctx: dict[str, Any],
    *,
    include_raw: bool,
    max_text_chars: int,
) -> dict[str, Any]:
    ctype = conversation_type(target)
    speaker = annotate_group_message(message, ctx) if ctype == "group" else annotate_private_message(message, target)
    raw = message_raw(message)
    row = {
        "time": timestamp_iso(message),
        "message_id": message.get("message_id") or raw.get("local_id") or raw.get("msg_id") or raw.get("id") or "",
        "conversation_type": ctype,
        "direction": str(message.get("direction") or ""),
        "message_type": str(message.get("message_type") or raw.get("type") or ""),
        "sender_id": raw_sender(message),
        "text": compact_text(raw_message_text(message), max_text_chars),
        **speaker,
    }
    if include_raw:
        row["raw_payload"] = raw
    return row


def is_question_or_followup(text: str) -> bool:
    probes = ("?", "？", "吗", "嘛", "能不能", "可不可以", "需要", "麻烦", "确认", "什么时候", "哪里", "怎么", "是否", "为啥", "为什么")
    return any(token in text for token in probes)


def build_summary(target: dict[str, Any], messages: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
    speakers = Counter(message.get("speaker") or "未知" for message in messages)
    roles = Counter(message.get("speaker_role") or "unknown" for message in messages)
    meaningful = [
        message
        for message in messages
        if message.get("text") and not re.fullmatch(r"\[[^\]]+\]", str(message.get("text") or "").strip())
    ]
    followups = [
        {
            "time": message.get("time") or "",
            "speaker": message.get("speaker") or "",
            "text": message.get("text") or "",
        }
        for message in meaningful
        if is_question_or_followup(str(message.get("text") or ""))
    ]
    return {
        "conversation_name": target_display_name(target),
        "conversation_username": target.get("conversation_username") or "",
        "conversation_type": conversation_type(target),
        "message_count": len(messages),
        "time_start": messages[0].get("time") if messages else "",
        "time_end": messages[-1].get("time") if messages else "",
        "speaker_counts": dict(speakers.most_common()),
        "speaker_roles": dict(roles.most_common()),
        "latest_messages": [
            {
                "time": message.get("time") or "",
                "speaker": message.get("speaker") or "",
                "text": message.get("text") or "",
            }
            for message in meaningful[-5:]
        ],
        "possible_followups": followups[-5:],
        "warnings": warnings,
    }


def output_payload(target: dict[str, Any], messages: list[dict[str, Any]], summary: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_provider": "live-inbox" if args.source == "live-inbox" else "wx-cli",
        "model_api_touched": False,
        "ui_touched": False,
        "sent": False,
        "query": {
            "chat": args.chat,
            "source": args.source,
            "live_inbox_root": str(Path(args.live_inbox_root).expanduser()) if args.source == "live-inbox" else "",
            "limit": max(args.limit, 0),
            "offset": max(args.offset, 0),
            "since": args.since,
            "until": args.until,
            "recent_first": bool(args.recent_first),
        },
        "target": {
            "conversation_username": target.get("conversation_username") or "",
            "display_name": target.get("display_name") or "",
            "remark": target.get("remark") or "",
            "alias": target.get("alias") or "",
            "chat_type": target.get("chat_type") or "",
            "conversation_type": conversation_type(target),
            "notification_state": target.get("notification_state") or "unknown",
        },
        "summary": summary,
    }
    if not args.summary_only:
        payload["messages"] = messages
    return payload


def render_text(payload: dict[str, Any]) -> str:
    target = payload["target"]
    summary = payload["summary"]
    source = payload.get("source_provider") or "unknown"
    lines = [
        f"会话：{target.get('display_name') or target.get('conversation_username')}",
        f"类型：{target.get('conversation_type')}  来源：{source}",
        f"消息数：{summary.get('message_count')}  时间：{summary.get('time_start') or '-'} -> {summary.get('time_end') or '-'}",
    ]
    warnings = summary.get("warnings") or []
    if warnings:
        lines.append("提示：" + "；".join(str(warning) for warning in warnings))

    speaker_counts = summary.get("speaker_counts") or {}
    if speaker_counts:
        top = "，".join(f"{name} {count}" for name, count in list(speaker_counts.items())[:6])
        lines.append(f"发言统计：{top}")

    latest = summary.get("latest_messages") or []
    if latest:
        lines.append("")
        lines.append("最新内容：")
        for item in latest:
            lines.append(f"- [{item.get('time') or '-'}] {item.get('speaker') or '未知'}：{item.get('text') or ''}")

    followups = summary.get("possible_followups") or []
    if followups:
        lines.append("")
        lines.append("可能需要关注：")
        for item in followups:
            lines.append(f"- [{item.get('time') or '-'}] {item.get('speaker') or '未知'}：{item.get('text') or ''}")

    messages = payload.get("messages") or []
    if messages:
        lines.append("")
        lines.append("消息明细：")
        for message in messages:
            suffix = ""
            if message.get("speaker_confidence") == "low":
                suffix = f" ({message.get('speaker_role')})"
            lines.append(f"[{message.get('time') or '-'}] {message.get('speaker') or '未知'}{suffix}：{message.get('text') or ''}")
    return "\n".join(lines)


def load_live_inbox_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"live-inbox events file not found: {path}")
    events = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def live_inbox_event_matches(event: dict[str, Any], needle: str) -> bool:
    query = needle.strip().lower()
    if not query:
        return True
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    values = [
        event.get("conversation_username"),
        event.get("display_name"),
        event.get("chat_type"),
        message.get("sender_id"),
    ]
    return any(query in str(value or "").lower() for value in values if str(value or "").strip())


def live_inbox_message_sort_value(event: dict[str, Any]) -> Optional[float]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    for value in (
        message.get("timestamp_iso"),
        message.get("timestamp"),
        event.get("detected_at"),
    ):
        parsed = parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def live_inbox_event_in_window(event: dict[str, Any], since: str, until: str) -> bool:
    value = live_inbox_message_sort_value(event)
    if value is None:
        return True
    since_value = parse_timestamp(since) if since else None
    until_value = parse_timestamp(until) if until else None
    if since_value is not None and value < since_value:
        return False
    if until_value is not None and value > until_value:
        return False
    return True


def live_inbox_target(events: list[dict[str, Any]], chat: str) -> dict[str, Any]:
    latest = events[-1] if events else {}
    username = str(latest.get("conversation_username") or chat).strip()
    display_name = str(latest.get("display_name") or username or chat).strip()
    chat_type = str(latest.get("chat_type") or "").strip()
    return {
        "conversation_username": username,
        "display_name": display_name,
        "remark": "",
        "alias": "",
        "chat_type": chat_type,
        "conversation_type": conversation_type({"conversation_username": username, "chat_type": chat_type}),
        "notification_state": "unknown",
    }


def live_inbox_message_to_normalized(event: dict[str, Any]) -> dict[str, Any]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    return {
        "message_id": message.get("message_id") or event.get("event_id") or "",
        "timestamp": message.get("timestamp_iso") or message.get("timestamp") or event.get("detected_at") or "",
        "direction": message.get("direction") or "",
        "message_type": message.get("message_type") or "",
        "text": message.get("text") or "",
        "sender_id": message.get("sender_id") or "",
        "source_provider": message.get("source_provider") or "live-inbox",
        "conversation_username": event.get("conversation_username") or "",
        "display_name": event.get("display_name") or "",
        "chat_type": event.get("chat_type") or "",
        "raw_payload": {
            "event_id": event.get("event_id") or "",
            "detected_at": event.get("detected_at") or "",
            "source": event.get("source") or "",
        },
    }


def annotate_live_inbox_message(
    message: dict[str, Any],
    target: dict[str, Any],
    *,
    include_raw: bool,
    max_text_chars: int,
) -> dict[str, Any]:
    ctype = conversation_type(target)
    if ctype == "group":
        sender = raw_sender(message)
        speaker = {
            "speaker": sender or "系统/未知",
            "speaker_role": "sender_field" if sender else "unknown_or_system",
            "speaker_confidence": "medium" if sender else "low",
            "speaker_reason": "live-inbox has plaintext sender field but no group member resolution",
            "matched_usernames": [],
        }
    else:
        speaker = annotate_private_message(message, target)
    raw = message_raw(message)
    row = {
        "time": timestamp_iso(message),
        "message_id": message.get("message_id") or "",
        "conversation_type": ctype,
        "direction": str(message.get("direction") or ""),
        "message_type": str(message.get("message_type") or ""),
        "sender_id": raw_sender(message),
        "text": compact_text(raw_message_text(message), max_text_chars),
        **speaker,
    }
    if include_raw:
        row["raw_payload"] = raw
    return row


def run_live_inbox(args: argparse.Namespace) -> dict[str, Any]:
    inbox_root = Path(args.live_inbox_root).expanduser()
    events_path = inbox_root / "events.jsonl"
    events = load_live_inbox_events(events_path)
    matched = [
        event
        for event in events
        if live_inbox_event_matches(event, args.chat) and live_inbox_event_in_window(event, args.since, args.until)
    ]
    matched = sorted(
        matched,
        key=lambda event: (
            live_inbox_message_sort_value(event) is None,
            live_inbox_message_sort_value(event) or 0,
        ),
    )
    if args.recent_first:
        selected = list(reversed(matched))[: max(args.limit, 0)]
    else:
        selected = matched[-max(args.limit, 0) :] if args.limit > 0 else []
    target = live_inbox_target(selected or matched, args.chat)
    normalized = [live_inbox_message_to_normalized(event) for event in selected]
    messages = [
        annotate_live_inbox_message(
            message,
            target,
            include_raw=args.include_raw,
            max_text_chars=max(args.max_text_chars, 0),
        )
        for message in normalized
    ]
    warnings = [
        "live-inbox source only includes messages captured after the inbox worker started",
        "group speaker names come from the live event sender field and are not resolved against group members",
    ]
    if not matched:
        warnings.append("no matching live-inbox events were found for this chat query")
    summary = build_summary(target, messages, warnings)
    payload = output_payload(target, messages, summary, args)
    payload["query"]["events_path"] = str(events_path)
    payload["query"]["matched_event_count"] = len(matched)
    return payload


def run_wx_history(args: argparse.Namespace) -> dict[str, Any]:
    previous_profile, profile_changed = set_profile_env(args.wx_cli_profile or "")
    try:
        target = resolve_conversation(args.chat)
        chat = str(target.get("conversation_username") or target.get("display_name") or args.chat).strip()
        raw_messages = get_history(
            chat,
            max(args.limit, 0),
            offset=max(args.offset, 0),
            since=args.since,
            until=args.until,
        )
        raw_messages = ordered_messages(raw_messages, args.recent_first)
        warnings: list[str] = []
        ctx = group_context(target, args, warnings)
        messages = [
            annotate_message(
                message,
                target,
                ctx,
                include_raw=args.include_raw,
                max_text_chars=max(args.max_text_chars, 0),
            )
            for message in raw_messages
        ]
        summary = build_summary(target, messages, warnings)
        return output_payload(target, messages, summary, args)
    finally:
        restore_profile_env(previous_profile, profile_changed)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.source == "live-inbox":
        return run_live_inbox(args)
    return run_wx_history(args)


def main() -> int:
    args = parse_args()
    try:
        payload = run(args)
    except WxCliError as exc:
        error = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "status": "wx_cli_error",
            "error": exc.to_dict(),
            "sent": False,
        }
        if args.format == "json":
            print(json.dumps(error, ensure_ascii=False, indent=2))
        else:
            print(f"wx-cli error: {exc.code}: {exc.message}")
            if exc.details:
                print(json.dumps(exc.details, ensure_ascii=False, indent=2))
        return 2
    except OSError as exc:
        error = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "status": "local_file_error",
            "error": {"message": str(exc)},
            "sent": False,
        }
        if args.format == "json":
            print(json.dumps(error, ensure_ascii=False, indent=2))
        else:
            print(f"local file error: {exc}")
        return 2

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.format == "jsonl":
        for message in payload.get("messages") or []:
            print(json.dumps(message, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
