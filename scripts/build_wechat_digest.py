#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from wechat_common import _ensure_dir, _normalize_text, _safe_slug, _write_json, _write_jsonl
from wechat_schema import stable_id, utc_now_iso


SCHEMA_VERSION = "wechat_digest_v1"
URL_RE = re.compile(r"https?://[^\s<'\"<>]+", re.IGNORECASE)
QUESTION_TOKENS = (
    "?",
    "？",
    "吗",
    "嘛",
    "能不能",
    "可不可以",
    "需要",
    "麻烦",
    "确认",
    "什么时候",
    "哪里",
    "怎么",
    "是否",
    "为什么",
)

TAG_RULES = [
    ("order", ("下单", "付款", "支付", "订单", "收件人", "地址", "安排", "要了")),
    ("shipping", ("发货", "物流", "快递", "单号", "到货", "收到")),
    ("inventory", ("有货", "缺货", "没货", "补货", "断码", "尺码", "库存")),
    ("price", ("价格", "报价", "便宜", "优惠", "多少钱", "预算", "贵")),
    ("after_sale", ("售后", "退", "换", "退款", "质量", "坏了", "不合适")),
    ("link_share", ("http://", "https://", "mp.weixin.qq.com")),
]


def parse_args() -> argparse.Namespace:
    today = date.today().isoformat()
    parser = argparse.ArgumentParser(description="Build a read-only WeChat daily digest from live-inbox or wx-history data.")
    parser.add_argument("--source", choices=("live-inbox", "wx-history"), default="live-inbox")
    parser.add_argument("--date", default=today, help="local date to summarize, YYYY-MM-DD; defaults to today")
    parser.add_argument("--hour-offset", type=int, default=0, help="day window starts at this local hour")
    parser.add_argument("--chat", action="append", default=[], help="chat name/username filter; repeat for multiple chats")
    parser.add_argument("--limit", type=int, default=500, help="max messages to keep after filtering")
    parser.add_argument("--recent-limit", type=int, default=20, help="recent message rows to include in the digest")
    parser.add_argument("--followup-limit", type=int, default=20, help="possible follow-up rows to include")
    parser.add_argument("--link-limit", type=int, default=30, help="links to include")
    parser.add_argument("--live-inbox-root", default="~/Sync/wechat-live-inbox", help="folder containing events.jsonl")
    parser.add_argument("--events", help="explicit live-inbox events.jsonl path")
    parser.add_argument("--wx-cli-profile", help="optional wx-cli profile for wx-history source")
    parser.add_argument("--self-username", default="", help="optional current-account username/wxid for group speaker checks")
    parser.add_argument("--self-display", default="", help="optional current-account display name fallback")
    parser.add_argument("--out-root", default="out/wechat-digest", help="output directory")
    parser.add_argument("--stdout", choices=("json", "md", "none"), default="none", help="also print digest to stdout")
    return parser.parse_args()


def split_chat_filters(values: Iterable[str]) -> list[str]:
    filters: list[str] = []
    for value in values:
        for part in str(value or "").split(","):
            item = part.strip()
            if item and item not in filters:
                filters.append(item)
    return filters


def day_window(day: str, hour_offset: int) -> tuple[datetime, datetime]:
    base = datetime.strptime(day, "%Y-%m-%d")
    start = base + timedelta(hours=hour_offset)
    return start, start + timedelta(days=1)


def parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        number = float(value)
        dt = datetime.fromtimestamp(number / 1000 if number > 10_000_000_000 else number)
    else:
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", text):
            number = float(text)
            dt = datetime.fromtimestamp(number / 1000 if number > 10_000_000_000 else number)
        else:
            normalized = text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(text[: len(datetime.now().strftime(fmt))], fmt)
                        break
                    except ValueError:
                        dt = None
                if dt is None:
                    return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def compact_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "..."


def first_text(source: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = _normalize_text(source.get(key))
        if value:
            return value
    return ""


def extract_urls(*texts: str) -> list[str]:
    urls: list[str] = []
    for text in texts:
        for match in URL_RE.findall(text or ""):
            url = match.rstrip(").,;:，。；：")
            if url not in urls:
                urls.append(url)
    return urls


def tags_for_text(text: str) -> list[str]:
    tags = []
    probe = str(text or "").lower()
    for tag, tokens in TAG_RULES:
        if any(token.lower() in probe for token in tokens):
            tags.append(tag)
    return tags


def looks_like_followup(text: str) -> bool:
    return any(token in str(text or "") for token in QUESTION_TOKENS)


def event_timestamp(event: dict[str, Any]) -> Optional[datetime]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    for value in (
        message.get("timestamp_iso"),
        message.get("timestamp"),
        event.get("detected_at"),
        event.get("timestamp"),
        event.get("created_at"),
    ):
        dt = parse_datetime(value)
        if dt:
            return dt
    return None


def normalize_live_event(event: dict[str, Any]) -> dict[str, Any]:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    raw = message.get("raw_payload") if isinstance(message.get("raw_payload"), dict) else {}
    dt = event_timestamp(event)
    conversation_username = first_text(event, ["conversation_username", "conversation_id", "chat"]) or first_text(
        message, ["conversation_username", "chat", "talker"]
    )
    display_name = first_text(event, ["display_name", "conversation_name"]) or conversation_username
    text = first_text(message, ["text", "content", "message", "summary"]) or first_text(raw, ["text", "content", "message", "summary"])
    sender_id = first_text(message, ["sender_id", "sender", "from_user", "from_username"]) or first_text(
        raw, ["sender", "from_user", "from_username", "talker"]
    )
    speaker = first_text(message, ["speaker", "sender_name", "display_name"]) or sender_id or first_text(message, ["direction"]) or "unknown"
    urls = extract_urls(text, first_text(message, ["url", "link"]), first_text(raw, ["url", "link"]))
    title = first_text(message, ["title", "link_title"]) or first_text(raw, ["title", "link_title"])
    return {
        "message_id": first_text(message, ["message_id", "id"]) or first_text(event, ["event_id", "id"]),
        "timestamp": dt.isoformat() if dt else "",
        "conversation_username": conversation_username,
        "conversation_name": display_name,
        "conversation_type": first_text(event, ["chat_type", "conversation_type"]) or first_text(message, ["chat_type"]) or "unknown",
        "speaker": speaker,
        "speaker_id": sender_id,
        "direction": first_text(message, ["direction"]),
        "message_type": first_text(message, ["message_type", "type"]),
        "text": compact_text(text),
        "urls": urls,
        "title": title,
        "tags": tags_for_text(" ".join([text, title, " ".join(urls)])),
        "source_provider": first_text(message, ["source_provider"]) or first_text(event, ["source"]) or "live-inbox",
    }


def load_live_inbox_messages(args: argparse.Namespace, start: datetime, end: datetime, chat_filters: list[str]) -> list[dict[str, Any]]:
    events_path = Path(args.events).expanduser() if args.events else Path(args.live_inbox_root).expanduser() / "events.jsonl"
    if not events_path.exists():
        raise FileNotFoundError(f"live-inbox events file not found: {events_path}")
    messages: list[dict[str, Any]] = []
    with events_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            dt = event_timestamp(event)
            if not dt or dt < start or dt >= end:
                continue
            row = normalize_live_event(event)
            if chat_filters and not message_matches_chat(row, chat_filters):
                continue
            messages.append(row)
    return messages


def message_matches_chat(message: dict[str, Any], chat_filters: list[str]) -> bool:
    haystack = " ".join(
        [
            str(message.get("conversation_username") or ""),
            str(message.get("conversation_name") or ""),
            str(message.get("speaker") or ""),
            str(message.get("speaker_id") or ""),
        ]
    ).lower()
    return any(item.lower() in haystack for item in chat_filters)


def query_recent_chat(chat: str, args: argparse.Namespace, start: datetime, end: datetime) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("query_recent_chat.py")),
        "--source",
        "wx-history",
        "--chat",
        chat,
        "--limit",
        str(max(args.limit, 1)),
        "--since",
        start.strftime("%Y-%m-%d %H:%M:%S"),
        "--until",
        end.strftime("%Y-%m-%d %H:%M:%S"),
        "--format",
        "json",
    ]
    if args.wx_cli_profile:
        command.extend(["--wx-cli-profile", args.wx_cli_profile])
    if args.self_username:
        command.extend(["--self-username", args.self_username])
    if args.self_display:
        command.extend(["--self-display", args.self_display])
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=60)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "query_recent_chat failed").strip())
    return json.loads(completed.stdout)


def normalize_history_message(payload: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    dt = parse_datetime(message.get("time"))
    text = _normalize_text(message.get("text"))
    urls = extract_urls(text)
    return {
        "message_id": _normalize_text(message.get("message_id")),
        "timestamp": dt.isoformat() if dt else _normalize_text(message.get("time")),
        "conversation_username": _normalize_text(target.get("conversation_username")),
        "conversation_name": _normalize_text(target.get("display_name") or target.get("conversation_username")),
        "conversation_type": _normalize_text(target.get("conversation_type") or target.get("chat_type") or "unknown"),
        "speaker": _normalize_text(message.get("speaker")) or _normalize_text(message.get("sender_id")) or "unknown",
        "speaker_id": _normalize_text(message.get("sender_id")),
        "direction": _normalize_text(message.get("direction")),
        "message_type": _normalize_text(message.get("message_type")),
        "text": compact_text(text),
        "urls": urls,
        "title": "",
        "tags": tags_for_text(" ".join([text, " ".join(urls)])),
        "source_provider": "wx-cli",
    }


def load_wx_history_messages(args: argparse.Namespace, start: datetime, end: datetime, chat_filters: list[str]) -> list[dict[str, Any]]:
    if not chat_filters:
        raise ValueError("--source wx-history requires at least one --chat")
    messages: list[dict[str, Any]] = []
    for chat in chat_filters:
        payload = query_recent_chat(chat, args, start, end)
        for item in payload.get("messages") or []:
            if isinstance(item, dict):
                messages.append(normalize_history_message(payload, item))
    return messages


def with_stable_ids(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in messages:
        item = dict(row)
        item["digest_message_id"] = stable_id(
            item.get("source_provider"),
            item.get("conversation_username"),
            item.get("message_id"),
            item.get("timestamp"),
            item.get("speaker_id"),
            item.get("text"),
            prefix="digest_msg",
        )
        out.append(item)
    return out


def build_digest(messages: list[dict[str, Any]], args: argparse.Namespace, start: datetime, end: datetime, chat_filters: list[str]) -> dict[str, Any]:
    messages = sorted(messages, key=lambda row: row.get("timestamp") or "")
    if args.limit > 0:
        messages = messages[-args.limit :]
    conversations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    speakers: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    hours: Counter[str] = Counter()
    links: list[dict[str, Any]] = []
    followups: list[dict[str, Any]] = []

    for row in messages:
        key = row.get("conversation_username") or row.get("conversation_name") or "unknown"
        conversations[str(key)].append(row)
        speakers[str(row.get("speaker") or "unknown")] += 1
        for tag in row.get("tags") or []:
            tags[str(tag)] += 1
        if row.get("timestamp"):
            hours[str(row.get("timestamp"))[:13]] += 1
        for url in row.get("urls") or []:
            links.append(
                {
                    "time": row.get("timestamp") or "",
                    "conversation": row.get("conversation_name") or row.get("conversation_username") or "",
                    "speaker": row.get("speaker") or "",
                    "title": row.get("title") or row.get("text") or "",
                    "url": url,
                }
            )
        if looks_like_followup(str(row.get("text") or "")):
            followups.append(
                {
                    "time": row.get("timestamp") or "",
                    "conversation": row.get("conversation_name") or row.get("conversation_username") or "",
                    "speaker": row.get("speaker") or "",
                    "text": row.get("text") or "",
                }
            )

    conversation_rows = []
    for key, rows in conversations.items():
        conversation_rows.append(
            {
                "conversation_username": rows[0].get("conversation_username") or key,
                "conversation_name": rows[0].get("conversation_name") or key,
                "conversation_type": rows[0].get("conversation_type") or "unknown",
                "message_count": len(rows),
                "time_start": rows[0].get("timestamp") or "",
                "time_end": rows[-1].get("timestamp") or "",
                "top_speakers": dict(Counter(str(row.get("speaker") or "unknown") for row in rows).most_common(5)),
                "top_tags": dict(Counter(tag for row in rows for tag in (row.get("tags") or [])).most_common(6)),
            }
        )
    conversation_rows.sort(key=lambda row: row["message_count"], reverse=True)

    digest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_provider": args.source,
        "model_api_touched": False,
        "ui_touched": False,
        "sent": False,
        "query": {
            "date": args.date,
            "hour_offset": args.hour_offset,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "chat_filters": chat_filters,
            "limit": args.limit,
        },
        "summary": {
            "message_count": len(messages),
            "conversation_count": len(conversation_rows),
            "link_count": len(links),
            "followup_count": len(followups),
            "time_start": messages[0].get("timestamp") if messages else "",
            "time_end": messages[-1].get("timestamp") if messages else "",
            "top_speakers": dict(speakers.most_common(10)),
            "top_tags": dict(tags.most_common(10)),
            "hour_counts": dict(sorted(hours.items())),
        },
        "conversations": conversation_rows,
        "links": links[-max(args.link_limit, 0) :] if args.link_limit else [],
        "possible_followups": followups[-max(args.followup_limit, 0) :] if args.followup_limit else [],
        "recent_messages": messages[-max(args.recent_limit, 0) :] if args.recent_limit else [],
        "messages": messages,
        "llm_guardrails": [
            "message_count is deterministic; do not let a model rewrite it",
            "model_api_touched=false means this script did not call an LLM",
            "ui_touched=false and sent=false mean no WeChat UI or sending action happened",
        ],
    }
    return digest


def render_markdown(digest: dict[str, Any]) -> str:
    query = digest.get("query") or {}
    summary = digest.get("summary") or {}
    title = query.get("date") or "wechat-digest"
    lines = [
        f"# WeChat Digest - {title}",
        "",
        f"- Source: {digest.get('source_provider')}",
        f"- Window: {query.get('window_start')} -> {query.get('window_end')}",
        f"- Messages: {summary.get('message_count')} (exact count, do not rewrite in LLM summary)",
        f"- Conversations: {summary.get('conversation_count')}",
        f"- Links: {summary.get('link_count')}",
        f"- Possible follow-ups: {summary.get('followup_count')}",
        f"- Safety: model_api_touched={str(digest.get('model_api_touched')).lower()}, ui_touched={str(digest.get('ui_touched')).lower()}, sent={str(digest.get('sent')).lower()}",
        "",
    ]
    tags = summary.get("top_tags") or {}
    if tags:
        lines.extend(["## Tags", ""])
        lines.append(", ".join(f"{tag}({count})" for tag, count in tags.items()))
        lines.append("")

    conversations = digest.get("conversations") or []
    if conversations:
        lines.extend(["## Conversations", ""])
        for row in conversations:
            tags_text = ", ".join(f"{tag}({count})" for tag, count in (row.get("top_tags") or {}).items())
            suffix = f" - {tags_text}" if tags_text else ""
            lines.append(f"- {row.get('conversation_name') or row.get('conversation_username')}: {row.get('message_count')} messages{suffix}")
        lines.append("")

    followups = digest.get("possible_followups") or []
    if followups:
        lines.extend(["## Possible Follow-ups", ""])
        for item in followups:
            lines.append(
                f"- [{item.get('time') or '-'}] {item.get('conversation') or '-'} / {item.get('speaker') or 'unknown'}: {item.get('text') or ''}"
            )
        lines.append("")

    links = digest.get("links") or []
    if links:
        lines.extend(["## Links", ""])
        for item in links:
            label = compact_text(item.get("title") or item.get("url"), 80)
            lines.append(f"- [{label}]({item.get('url')}) - {item.get('conversation') or '-'}")
        lines.append("")

    recent = digest.get("recent_messages") or []
    if recent:
        lines.extend(["## Recent Messages", ""])
        for item in recent:
            lines.append(
                f"- [{item.get('timestamp') or '-'}] {item.get('conversation_name') or item.get('conversation_username') or '-'} / {item.get('speaker') or 'unknown'}: {item.get('text') or ''}"
            )
        lines.append("")

    lines.extend(
        [
            "## LLM Summary Prompt",
            "",
            "Use the digest above as source data. Keep exact counts unchanged, stay faithful to the messages, and separate facts from suggested follow-up actions.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(digest: dict[str, Any], out_root: Path) -> dict[str, str]:
    query = digest.get("query") or {}
    date_label = _safe_slug(str(query.get("date") or "unknown-date"))
    chat_filters = query.get("chat_filters") or []
    chat_label = _safe_slug("-".join(chat_filters) if chat_filters else "all")
    out_dir = _ensure_dir(out_root / date_label / chat_label)
    messages = digest.pop("messages", [])
    digest_json = out_dir / "digest.json"
    digest_md = out_dir / "digest.md"
    messages_jsonl = out_dir / "messages.jsonl"
    _write_json(digest_json, digest)
    digest_md.write_text(render_markdown(digest), encoding="utf-8")
    _write_jsonl(messages_jsonl, messages)
    return {
        "digest_json": str(digest_json),
        "digest_md": str(digest_md),
        "messages_jsonl": str(messages_jsonl),
    }


def main() -> int:
    args = parse_args()
    chat_filters = split_chat_filters(args.chat)
    start, end = day_window(args.date, args.hour_offset)
    try:
        if args.source == "live-inbox":
            messages = load_live_inbox_messages(args, start, end, chat_filters)
        else:
            messages = load_wx_history_messages(args, start, end, chat_filters)
        digest = build_digest(with_stable_ids(messages), args, start, end, chat_filters)
        paths = write_outputs(digest, Path(args.out_root).expanduser())
        manifest = {
            "ok": True,
            "schema_version": "wechat_digest_run_v1",
            "generated_at": utc_now_iso(),
            "paths": paths,
            "summary": digest.get("summary") or {},
        }
        if args.stdout == "json":
            print(json.dumps(digest, ensure_ascii=False, indent=2))
        elif args.stdout == "md":
            print(Path(paths["digest_md"]).read_text(encoding="utf-8"))
        else:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except (FileNotFoundError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
