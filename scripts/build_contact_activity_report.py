#!/usr/bin/env python3

import argparse
import csv
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from wechat_common import _ensure_dir, _write_json


REPORT_SCHEMA_VERSION = "contact_activity_report_v1"

PLACEHOLDER_TEXTS = {
    "[图片]",
    "[语音]",
    "[视频]",
    "[表情]",
    "[消息]",
    "[应用消息]",
    "[系统消息]",
    "[位置]",
    "[文件]",
}

CJK_OR_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a local contact activity report from exported WeChat JSONL conversations."
    )
    parser.add_argument(
        "--export-root",
        default="out/chat-export/export",
        help="Path to the chat export root containing conversation_index.json.",
    )
    parser.add_argument(
        "--out-root",
        default="out/contact-activity",
        help="Directory for generated JSON, CSV, and Markdown reports.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=50,
        help="Number of conversations to keep in each Markdown top list.",
    )
    parser.add_argument(
        "--anonymize",
        action="store_true",
        help="Replace display names and usernames with stable local labels in outputs.",
    )
    return parser.parse_args()


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return timestamp_number_to_datetime(value)

    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        try:
            return timestamp_number_to_datetime(float(text))
        except Exception:
            return None

    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return normalize_datetime(dt)


def timestamp_number_to_datetime(value: float) -> Optional[datetime]:
    raw = float(value)
    abs_raw = abs(raw)
    if abs_raw >= 10**15:
        raw = raw / 1_000_000
    elif abs_raw >= 10**12:
        raw = raw / 1_000
    try:
        return normalize_datetime(datetime.fromtimestamp(raw))
    except Exception:
        try:
            return normalize_datetime(datetime.fromtimestamp(raw, tz=timezone.utc))
        except Exception:
            return None


def normalize_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(microsecond=0)
    return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def isoformat(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat()


def safe_float(value: float, digits: int = 2) -> float:
    if math.isfinite(value):
        return round(value, digits)
    return 0.0


def looks_readable_text(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text or text in PLACEHOLDER_TEXTS:
        return False
    if CONTROL_RE.search(text):
        return False
    if len(text) <= 2:
        return bool(CJK_OR_WORD_RE.search(text))
    signal = len(CJK_OR_WORD_RE.findall(text))
    return signal / max(len(text), 1) >= 0.25


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError:
                yield {"_json_error": True, "_line_no": line_no}


def conversation_label(row: dict[str, Any], rank: int, anonymize: bool) -> tuple[str, str]:
    if anonymize:
        return f"conversation_{rank:04d}", f"user_{rank:04d}"

    display_name = str(row.get("display_name") or row.get("conversation_username") or row.get("conversation_id") or "")
    username = str(row.get("conversation_username") or "")
    return display_name, username


def empty_stats(row: dict[str, Any], rank: int, anonymize: bool) -> dict[str, Any]:
    display_name, username = conversation_label(row, rank, anonymize)
    return {
        "conversation_id": row.get("conversation_id"),
        "conversation_username": username,
        "display_name": display_name,
        "remark": row.get("remark") or "",
        "nick_name": row.get("nick_name") or "",
        "alias": row.get("alias") or "",
        "conversation_type": row.get("conversation_type") or "unknown",
        "chat_room_notify": row.get("chat_room_notify"),
        "notification_muted": row.get("notification_muted"),
        "notification_state": row.get("notification_state") or "unknown",
        "file": row.get("file"),
        "index_message_count": int(row.get("message_count") or 0),
        "message_count": 0,
        "first_message_at": None,
        "last_message_at": parse_timestamp(row.get("last_active_at")),
        "active_days": set(),
        "day_counts": Counter(),
        "direction_counts": Counter(),
        "message_type_counts": Counter(),
        "render_type_counts": Counter(),
        "readable_text_count": 0,
        "placeholder_text_count": 0,
        "empty_text_count": 0,
        "json_error_count": 0,
        "missing_file": False,
    }


def analyze_conversation(export_root: Path, row: dict[str, Any], rank: int, anonymize: bool) -> dict[str, Any]:
    stats = empty_stats(row, rank, anonymize)
    rel_file = row.get("file")
    if not rel_file:
        stats["missing_file"] = True
        return stats

    path = export_root / str(rel_file)
    if not path.exists():
        stats["missing_file"] = True
        return stats

    for message in iter_jsonl(path):
        if message.get("_json_error"):
            stats["json_error_count"] += 1
            continue

        stats["message_count"] += 1
        timestamp = parse_timestamp(message.get("timestamp"))
        if timestamp:
            if stats["first_message_at"] is None or timestamp < stats["first_message_at"]:
                stats["first_message_at"] = timestamp
            if stats["last_message_at"] is None or timestamp > stats["last_message_at"]:
                stats["last_message_at"] = timestamp
            day = timestamp.date().isoformat()
            stats["active_days"].add(day)
            stats["day_counts"][day] += 1

        direction = str(message.get("direction") or "unknown")
        stats["direction_counts"][direction] += 1

        message_type = str(message.get("message_type") or "unknown")
        render_type = str(message.get("render_type") or "unknown")
        stats["message_type_counts"][message_type] += 1
        stats["render_type_counts"][render_type] += 1

        text = message.get("text")
        if text in (None, ""):
            stats["empty_text_count"] += 1
        elif str(text).strip() in PLACEHOLDER_TEXTS:
            stats["placeholder_text_count"] += 1
        elif looks_readable_text(text):
            stats["readable_text_count"] += 1

    if stats["message_count"] == 0:
        stats["message_count"] = stats["index_message_count"]

    return stats


def recency_bucket(last_message_at: Optional[datetime], as_of: Optional[datetime]) -> str:
    if last_message_at is None or as_of is None:
        return "unknown"
    age_days = (as_of.date() - last_message_at.date()).days
    if age_days <= 7:
        return "active_7d"
    if age_days <= 30:
        return "active_30d"
    if age_days <= 90:
        return "active_90d"
    if age_days <= 365:
        return "inactive_365d"
    return "stale_365d_plus"


def count_since(day_counts: Counter, as_of: Optional[datetime], days: int) -> int:
    if as_of is None:
        return 0
    start = as_of.date() - timedelta(days=days)
    total = 0
    for day_text, count in day_counts.items():
        try:
            day = datetime.fromisoformat(day_text).date()
        except ValueError:
            continue
        if day >= start:
            total += count
    return total


def active_days_since(day_counts: Counter, as_of: Optional[datetime], days: int) -> int:
    if as_of is None:
        return 0
    start = as_of.date() - timedelta(days=days)
    total = 0
    for day_text in day_counts:
        try:
            day = datetime.fromisoformat(day_text).date()
        except ValueError:
            continue
        if day >= start:
            total += 1
    return total


def history_days(first_message_at: Optional[datetime], last_message_at: Optional[datetime]) -> int:
    if first_message_at is None or last_message_at is None:
        return 0
    return max((last_message_at.date() - first_message_at.date()).days + 1, 1)


def activity_score(row: dict[str, Any]) -> float:
    total = row["message_count"]
    active_days_count = row["active_days"]
    recent_30d = row["recent_30d_count"]
    recent_90d = row["recent_90d_count"]
    last_age_days = row.get("last_age_days")

    recency_boost = 0.0
    if isinstance(last_age_days, int):
        recency_boost = max(0.0, 1.0 - min(last_age_days, 365) / 365)

    score = (
        math.log1p(total) * 0.42
        + math.log1p(active_days_count) * 0.25
        + math.log1p(recent_30d) * 0.23
        + math.log1p(recent_90d) * 0.06
        + recency_boost * 1.5
    )
    return safe_float(score, 4)


def finalize_conversation(raw: dict[str, Any], as_of: Optional[datetime]) -> dict[str, Any]:
    first = raw["first_message_at"]
    last = raw["last_message_at"]
    span_days = history_days(first, last)
    active_days_count = len(raw["active_days"])
    message_count = int(raw["message_count"])
    last_age_days = None
    if last is not None and as_of is not None:
        last_age_days = max((as_of.date() - last.date()).days, 0)

    row = {
        "conversation_id": raw["conversation_id"],
        "conversation_username": raw["conversation_username"],
        "display_name": raw["display_name"],
        "remark": raw.get("remark") or "",
        "nick_name": raw.get("nick_name") or "",
        "alias": raw.get("alias") or "",
        "conversation_type": raw["conversation_type"],
        "chat_room_notify": raw["chat_room_notify"],
        "notification_muted": raw["notification_muted"],
        "notification_state": raw["notification_state"],
        "file": raw["file"],
        "message_count": message_count,
        "index_message_count": int(raw["index_message_count"]),
        "message_count_delta": message_count - int(raw["index_message_count"]),
        "first_message_at": isoformat(first),
        "last_message_at": isoformat(last),
        "last_age_days": last_age_days,
        "history_days": span_days,
        "active_days": active_days_count,
        "messages_per_active_day": safe_float(message_count / active_days_count if active_days_count else 0.0),
        "messages_per_week_over_span": safe_float(message_count / (span_days / 7) if span_days else 0.0),
        "recent_7d_count": count_since(raw["day_counts"], as_of, 7),
        "recent_30d_count": count_since(raw["day_counts"], as_of, 30),
        "recent_90d_count": count_since(raw["day_counts"], as_of, 90),
        "recent_7d_active_days": active_days_since(raw["day_counts"], as_of, 7),
        "recent_30d_active_days": active_days_since(raw["day_counts"], as_of, 30),
        "recent_90d_active_days": active_days_since(raw["day_counts"], as_of, 90),
        "recency_bucket": recency_bucket(last, as_of),
        "direction_counts": dict(raw["direction_counts"].most_common()),
        "message_type_counts": dict(raw["message_type_counts"].most_common()),
        "render_type_counts": dict(raw["render_type_counts"].most_common()),
        "readable_text_count": int(raw["readable_text_count"]),
        "placeholder_text_count": int(raw["placeholder_text_count"]),
        "empty_text_count": int(raw["empty_text_count"]),
        "json_error_count": int(raw["json_error_count"]),
        "missing_file": bool(raw["missing_file"]),
    }
    row["activity_score"] = activity_score(row)
    return row


def top_rows(rows: list[dict[str, Any]], key: str, top_n: int) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda item: (item.get(key) or 0, item.get("message_count") or 0), reverse=True)[:top_n]


def dormant_high_volume(rows: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in rows
        if (row.get("message_count") or 0) >= 100 and (row.get("last_age_days") is None or row["last_age_days"] > 90)
    ]
    return sorted(candidates, key=lambda item: (item.get("message_count") or 0, item.get("history_days") or 0), reverse=True)[
        :top_n
    ]


def summarize(rows: list[dict[str, Any]], manifest: dict[str, Any], as_of: Optional[datetime], top_n: int) -> dict[str, Any]:
    by_type: dict[str, dict[str, Any]] = {}
    recency = Counter(row["recency_bucket"] for row in rows)
    for row in rows:
        ctype = row.get("conversation_type") or "unknown"
        bucket = by_type.setdefault(
            ctype,
            {
                "conversation_count": 0,
                "message_count": 0,
                "recent_30d_count": 0,
                "active_30d_conversations": 0,
                "muted_conversations": 0,
                "unmuted_conversations": 0,
                "unknown_notification_state_conversations": 0,
            },
        )
        bucket["conversation_count"] += 1
        bucket["message_count"] += row.get("message_count") or 0
        bucket["recent_30d_count"] += row.get("recent_30d_count") or 0
        if (row.get("recent_30d_count") or 0) > 0:
            bucket["active_30d_conversations"] += 1
        if row.get("notification_muted") is True:
            bucket["muted_conversations"] += 1
        elif row.get("notification_muted") is False:
            bucket["unmuted_conversations"] += 1
        else:
            bucket["unknown_notification_state_conversations"] += 1

    rows_by_score = sorted(
        rows,
        key=lambda item: (item.get("activity_score") or 0, item.get("message_count") or 0),
        reverse=True,
    )
    for rank, row in enumerate(rows_by_score, start=1):
        row["activity_rank"] = rank

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "export_root": str(Path(manifest.get("_export_root", "")).as_posix()),
        "as_of": isoformat(as_of),
        "totals": {
            "conversation_count": len(rows),
            "message_count": sum(row.get("message_count") or 0 for row in rows),
            "with_messages_7d_conversations": sum(1 for row in rows if (row.get("recent_7d_count") or 0) > 0),
            "with_messages_30d_conversations": sum(1 for row in rows if (row.get("recent_30d_count") or 0) > 0),
            "with_messages_90d_conversations": sum(1 for row in rows if (row.get("recent_90d_count") or 0) > 0),
            "last_active_7d_conversations": sum(
                1 for row in rows if isinstance(row.get("last_age_days"), int) and row["last_age_days"] <= 7
            ),
            "last_active_30d_conversations": sum(
                1 for row in rows if isinstance(row.get("last_age_days"), int) and row["last_age_days"] <= 30
            ),
            "last_active_90d_conversations": sum(
                1 for row in rows if isinstance(row.get("last_age_days"), int) and row["last_age_days"] <= 90
            ),
            "missing_files": sum(1 for row in rows if row.get("missing_file")),
            "json_errors": sum(row.get("json_error_count") or 0 for row in rows),
            "private_unmuted_conversations": sum(
                1
                for row in rows
                if row.get("conversation_type") == "friend" and row.get("notification_muted") is False
            ),
            "muted_conversations": sum(1 for row in rows if row.get("notification_muted") is True),
            "unknown_notification_state_conversations": sum(
                1 for row in rows if row.get("notification_muted") is None
            ),
        },
        "by_conversation_type": dict(sorted(by_type.items())),
        "recency_buckets": dict(recency.most_common()),
        "top": {
            "activity_score": [row["conversation_id"] for row in rows_by_score[:top_n]],
            "message_count": [row["conversation_id"] for row in top_rows(rows, "message_count", top_n)],
            "active_days": [row["conversation_id"] for row in top_rows(rows, "active_days", top_n)],
            "recent_30d_count": [row["conversation_id"] for row in top_rows(rows, "recent_30d_count", top_n)],
            "recent_90d_count": [row["conversation_id"] for row in top_rows(rows, "recent_90d_count", top_n)],
            "history_days": [row["conversation_id"] for row in top_rows(rows, "history_days", top_n)],
            "dormant_high_volume": [row["conversation_id"] for row in dormant_high_volume(rows, top_n)],
        },
    }


def csv_columns() -> list[str]:
    return [
        "activity_rank",
        "activity_score",
        "display_name",
        "remark",
        "nick_name",
        "alias",
        "conversation_type",
        "notification_muted",
        "notification_state",
        "chat_room_notify",
        "message_count",
        "first_message_at",
        "last_message_at",
        "last_age_days",
        "history_days",
        "active_days",
        "messages_per_active_day",
        "messages_per_week_over_span",
        "recent_7d_count",
        "recent_30d_count",
        "recent_90d_count",
        "recent_7d_active_days",
        "recent_30d_active_days",
        "recent_90d_active_days",
        "recency_bucket",
        "readable_text_count",
        "placeholder_text_count",
        "empty_text_count",
        "direction_counts",
        "message_type_counts",
        "render_type_counts",
        "conversation_username",
        "conversation_id",
        "file",
        "index_message_count",
        "message_count_delta",
        "json_error_count",
        "missing_file",
    ]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    _ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns(), extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item.get("activity_rank") or 999999):
            output = row.copy()
            for key in ["direction_counts", "message_type_counts", "render_type_counts"]:
                output[key] = json.dumps(output.get(key) or {}, ensure_ascii=False, sort_keys=True)
            writer.writerow(output)


def row_by_id(rows: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    return {row.get("conversation_id"): row for row in rows}


def md_escape(value: Any) -> str:
    text = str(value or "")
    return text.replace("|", "\\|").replace("\n", " ")


def md_table(rows: list[dict[str, Any]], metric: str, top_n: int) -> list[str]:
    lines = [
        "| Rank | Conversation | Type | Messages | Active days | History days | Recent 30d | Last active | Score |",
        "|---:|---|---|---:|---:|---:|---:|---|---:|",
    ]
    for index, row in enumerate(rows[:top_n], start=1):
        lines.append(
            "| {rank} | {name} | {ctype} | {messages} | {active_days} | {history_days} | {recent_30d} | {last} | {score} |".format(
                rank=index,
                name=md_escape(row.get("display_name")),
                ctype=md_escape(row.get("conversation_type")),
                messages=row.get("message_count") or 0,
                active_days=row.get("active_days") or 0,
                history_days=row.get("history_days") or 0,
                recent_30d=row.get("recent_30d_count") or 0,
                last=md_escape(row.get("last_message_at") or ""),
                score=row.get("activity_score") or 0,
            )
        )
    if len(lines) == 2:
        lines.append("| - | No matching conversations | - | 0 | 0 | 0 | 0 | - | 0 |")
    return lines


def write_markdown(path: Path, report: dict[str, Any], rows: list[dict[str, Any]], top_n: int) -> None:
    ids = row_by_id(rows)

    def rows_for(section: str) -> list[dict[str, Any]]:
        return [ids[row_id] for row_id in report["top"][section] if row_id in ids]

    totals = report["totals"]
    lines = [
        "# WeChat Contact Activity Report",
        "",
        "This report is generated from local exported WeChat metadata and message JSONL files. It does not include raw message text.",
        "",
        "## Snapshot",
        "",
        f"- As of: `{report.get('as_of') or 'unknown'}`",
        f"- Conversations: `{totals['conversation_count']}`",
        f"- Messages: `{totals['message_count']}`",
        f"- Conversations with messages in last 7 days: `{totals['with_messages_7d_conversations']}`",
        f"- Conversations with messages in last 30 days: `{totals['with_messages_30d_conversations']}`",
        f"- Conversations with messages in last 90 days: `{totals['with_messages_90d_conversations']}`",
        f"- Conversations last-active in last 7 days: `{totals['last_active_7d_conversations']}`",
        f"- Conversations last-active in last 30 days: `{totals['last_active_30d_conversations']}`",
        f"- Conversations last-active in last 90 days: `{totals['last_active_90d_conversations']}`",
        f"- Missing conversation files: `{totals['missing_files']}`",
        f"- JSON parse errors: `{totals['json_errors']}`",
        f"- Private non-muted conversations: `{totals.get('private_unmuted_conversations', 0)}`",
        f"- Muted conversations: `{totals.get('muted_conversations', 0)}`",
        f"- Unknown notification state: `{totals.get('unknown_notification_state_conversations', 0)}`",
        "",
        "## Conversation Types",
        "",
        "| Type | Conversations | Messages | Active 30d | Recent 30d messages | Non-muted | Muted | Unknown notify |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for ctype, bucket in report["by_conversation_type"].items():
        lines.append(
            f"| {md_escape(ctype)} | {bucket['conversation_count']} | {bucket['message_count']} | {bucket['active_30d_conversations']} | {bucket['recent_30d_count']} | {bucket.get('unmuted_conversations', 0)} | {bucket.get('muted_conversations', 0)} | {bucket.get('unknown_notification_state_conversations', 0)} |"
        )

    lines.extend(
        [
            "",
            "## Recency Buckets",
            "",
            "| Bucket | Conversations |",
            "|---|---:|",
        ]
    )
    for bucket, count in report["recency_buckets"].items():
        lines.append(f"| {md_escape(bucket)} | {count} |")

    sections = [
        ("Top Overall Activity", "activity_score"),
        ("Top By Message Count", "message_count"),
        ("Top By Active Days", "active_days"),
        ("Top Recent 30 Days", "recent_30d_count"),
        ("Top Recent 90 Days", "recent_90d_count"),
        ("Longest Relationship Spans", "history_days"),
        ("Dormant But Historically High Volume", "dormant_high_volume"),
    ]

    for title, section_key in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(md_table(rows_for(section_key), section_key, top_n))

    for ctype in ["friend", "group"]:
        typed_rows = sorted(
            [row for row in rows if row.get("conversation_type") == ctype],
            key=lambda item: (item.get("activity_score") or 0, item.get("message_count") or 0),
            reverse=True,
        )
        lines.extend(["", f"## Top {ctype.title()} Conversations", ""])
        lines.extend(md_table(typed_rows, "activity_score", top_n))

    lines.extend(
        [
            "",
            "## Metric Notes",
            "",
            "- `history_days` is the inclusive span between the first and last timestamped message in a conversation.",
            "- `active_days` counts distinct calendar days with at least one timestamped message.",
            "- `recent_7d_count`, `recent_30d_count`, and `recent_90d_count` are relative to the newest message timestamp in the export.",
            "- `last_active_*` uses the conversation index timestamp; `with_messages_*` uses timestamped rows in the JSONL files.",
            "- `activity_score` is a ranking helper that favors total volume, active days, recent 30-day activity, recent 90-day activity, and recency.",
            "- Direction counts are advisory because some exports may not reliably distinguish sent and received messages.",
        ]
    )

    _ensure_dir(path.parent)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(export_root: Path, out_root: Path, top_n: int, anonymize: bool) -> dict[str, Any]:
    index_path = export_root / "conversation_index.json"
    manifest_path = export_root / "manifest.json"
    if not index_path.exists():
        raise FileNotFoundError(f"Missing conversation index: {index_path}")

    conversations = load_json(index_path)
    if not isinstance(conversations, list):
        raise ValueError(f"Expected a list in {index_path}")

    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    manifest["_export_root"] = str(export_root)

    raw_rows = [
        analyze_conversation(export_root, row, rank=index + 1, anonymize=anonymize)
        for index, row in enumerate(conversations)
    ]
    as_of = max((row["last_message_at"] for row in raw_rows if row["last_message_at"] is not None), default=None)
    rows = [finalize_conversation(row, as_of) for row in raw_rows]
    report = summarize(rows, manifest, as_of, top_n)

    _ensure_dir(out_root)
    json_path = out_root / "contact_activity_report.json"
    csv_path = out_root / "contact_activity.csv"
    md_path = out_root / "contact_activity_report.md"

    _write_json(json_path, {"report": report, "conversations": rows})
    write_csv(csv_path, rows)
    write_markdown(md_path, report, rows, top_n)

    return {
        "json": str(json_path),
        "csv": str(csv_path),
        "markdown": str(md_path),
        "report": report,
    }


def main() -> int:
    args = parse_args()
    result = build_report(
        export_root=Path(args.export_root),
        out_root=Path(args.out_root),
        top_n=args.top,
        anonymize=args.anonymize,
    )
    report = result["report"]
    print(
        json.dumps(
            {
                "json": result["json"],
                "csv": result["csv"],
                "markdown": result["markdown"],
                "as_of": report.get("as_of"),
                "totals": report.get("totals"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
