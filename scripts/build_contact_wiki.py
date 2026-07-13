#!/usr/bin/env python3

import argparse
import json
import math
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from customer_memory import load_profile, load_profile_index, profile_markdown
from wechat_common import _ensure_dir, _write_json


CONTACT_WIKI_SCHEMA_VERSION = "contact_wiki_v1"
CONTACT_WIKI_PAGE_SCHEMA_VERSION = "contact_wiki_page_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build private local wiki pages for common/high-value WeChat contacts."
    )
    parser.add_argument(
        "--activity-report",
        default="out/contact-activity/contact_activity_report.json",
        help="Path to contact_activity_report.json.",
    )
    parser.add_argument(
        "--memory-root",
        default="out/customer-memory",
        help="Customer memory root containing indexes/profile_index.json.",
    )
    parser.add_argument(
        "--out-root",
        default="out/contact-wiki",
        help="Output root for private contact wiki pages and manifests.",
    )
    parser.add_argument("--max-pages", type=int, default=200, help="Maximum pages to render.")
    parser.add_argument("--min-common-messages", type=int, default=20, help="Minimum messages for common tier.")
    parser.add_argument("--min-common-active-days", type=int, default=3, help="Minimum active days for common tier.")
    parser.add_argument("--common-recency-days", type=int, default=180, help="Maximum last-age days for common tier.")
    parser.add_argument(
        "--recent-days",
        type=int,
        default=30,
        help="Recent activity window used for high-value recent-contact rule.",
    )
    parser.add_argument(
        "--min-recent-total-messages",
        type=int,
        default=10,
        help="Minimum total messages for the high-value recent-contact rule.",
    )
    parser.add_argument(
        "--top-percent",
        type=float,
        default=0.10,
        help="Top fraction by message count or active days treated as high-value.",
    )
    parser.add_argument(
        "--reply-ready-only",
        action="store_true",
        help="Only render contacts that are not auto-reply blocked.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing generated pages in the output pages directory before rendering.",
    )
    parser.add_argument(
        "--generated-at",
        default="",
        help="Optional fixed generated_at timestamp for deterministic test runs.",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def parse_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def activity_profile_delta_days(profile_row: dict[str, Any], activity_row: dict[str, Any]) -> Optional[float]:
    profile_last = parse_datetime(profile_row.get("last_message_at"))
    activity_last = parse_datetime(activity_row.get("last_message_at"))
    if profile_last is None or activity_last is None:
        return None
    return round((activity_last - profile_last).total_seconds() / 86400, 4)


def percentile_score(value: int, values: list[int]) -> float:
    if not values:
        return 0.0
    leq_count = sum(1 for item in values if item <= value)
    return leq_count / len(values)


def top_threshold(values: list[int], top_percent: float) -> int:
    if not values:
        return 0
    top_percent = max(0.0, min(top_percent, 1.0))
    sorted_values = sorted(values)
    index = max(0, math.ceil((1.0 - top_percent) * len(sorted_values)) - 1)
    return sorted_values[index]


def recency_score(last_age_days: Optional[int]) -> float:
    if last_age_days is None:
        return 0.0
    return max(0.0, 1.0 - min(max(last_age_days, 0), 365) / 365)


def yaml_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def frontmatter(record: dict[str, Any], generated_at: str) -> str:
    fields = {
        "schema_version": CONTACT_WIKI_PAGE_SCHEMA_VERSION,
        "contact_id": record["contact_id"],
        "display_name": record["display_name"],
        "tier": record["tier"],
        "score": record["score"],
        "selection_reasons": record["selection_reasons"],
        "memory_state": record["memory_state"],
        "reply_ready": record["reply_ready"],
        "review_only": record["review_only"],
        "notification_muted": record["activity"].get("notification_muted"),
        "notification_state": record["activity"].get("notification_state"),
        "message_count": record["activity"]["message_count"],
        "active_days": record["activity"]["active_days"],
        "last_contact_at": record["activity"]["last_contact_at"],
        "generated_at": generated_at,
        "privacy": "private_local_only",
    }
    lines = ["---"]
    lines.extend(f"{key}: {yaml_value(value)}" for key, value in fields.items())
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def load_activity_rows(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"activity report must be an object: {path}")
    rows = data.get("conversations")
    if not isinstance(rows, list):
        raise ValueError(f"activity report must contain conversations list: {path}")
    return [row for row in rows if isinstance(row, dict)]


def index_activity_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for row in rows:
        conversation_id = str(row.get("conversation_id") or "")
        if conversation_id:
            out[conversation_id] = row
    return out


def is_reply_ready(
    profile_row: dict[str, Any],
    memory_fresh_with_activity: bool = True,
    activity_row: Optional[dict[str, Any]] = None,
) -> bool:
    activity_row = activity_row or {}
    return memory_fresh_with_activity and (
        str(profile_row.get("conversation_type") or "") == "friend"
        and str(profile_row.get("profile_state") or "") == "eligible"
        and not bool(profile_row.get("auto_reply_blocked"))
        and activity_row.get("notification_muted") is False
    )


def exclusion_reason(profile_row: dict[str, Any], activity_row: Optional[dict[str, Any]]) -> Optional[str]:
    if activity_row is None:
        return "missing_activity"
    if str(profile_row.get("conversation_type") or "") != "friend":
        return f"conversation_type:{profile_row.get('conversation_type') or 'unknown'}"
    state = str(profile_row.get("profile_state") or "")
    if state != "eligible":
        return f"profile_state:{state or 'unknown'}"
    if str(activity_row.get("conversation_type") or "") != "friend":
        return f"activity_conversation_type:{activity_row.get('conversation_type') or 'unknown'}"
    if as_int(activity_row.get("message_count")) <= 0:
        return "no_messages"
    return None


def quality_flags(profile_row: dict[str, Any], activity_row: dict[str, Any]) -> list[str]:
    flags = []
    delta_days = activity_profile_delta_days(profile_row, activity_row)
    if delta_days is not None and delta_days > 1:
        flags.append("activity_index_newer_than_memory_messages")
    if bool(profile_row.get("auto_reply_blocked")):
        flags.append("auto_reply_blocked")
    if activity_row.get("notification_muted") is True:
        flags.append("notification_muted")
    elif activity_row.get("notification_muted") is not False:
        flags.append("notification_state_unknown")
    if bool(profile_row.get("pii_present")):
        flags.append("pii_present")
    stale_level = str(profile_row.get("stale_level") or "")
    if stale_level and stale_level not in {"fresh", "warm"}:
        flags.append(f"stale_level:{stale_level}")
    readable = as_int(profile_row.get("readable_message_count"))
    total = as_int(profile_row.get("message_count"))
    if total and readable / total < 0.2:
        flags.append("low_readable_ratio")
    if as_int(activity_row.get("active_days")) < 3:
        flags.append("low_active_days")
    return flags


def suggested_next_action(record: dict[str, Any]) -> str:
    if "activity_index_newer_than_memory_messages" in record["quality_flags"]:
        return "check_live_context_before_use"
    if record["reply_ready"]:
        return "review_page_then_allow_draft_context_when_relevant"
    if any(flag.startswith("stale_level:") for flag in record["quality_flags"]):
        return "refresh_or_check_recent_context_before_use"
    return "manual_review_only"


def build_candidate_records(
    profile_rows: list[dict[str, Any]],
    activity_by_id: dict[str, dict[str, Any]],
    *,
    top_percent: float,
    min_common_messages: int,
    min_common_active_days: int,
    common_recency_days: int,
    recent_days: int,
    min_recent_total_messages: int,
    reply_ready_only: bool,
) -> tuple[list[dict[str, Any]], Counter]:
    eligible_pairs = []
    excluded = Counter()

    for profile_row in profile_rows:
        profile_id = str(profile_row.get("profile_id") or "")
        activity_row = activity_by_id.get(profile_id)
        reason = exclusion_reason(profile_row, activity_row)
        if reason:
            excluded[reason] += 1
            continue
        memory_fresh_with_activity = not (
            (activity_profile_delta_days(profile_row, activity_row) or 0) > 1
        )
        if reply_ready_only and not is_reply_ready(profile_row, memory_fresh_with_activity, activity_row):
            excluded["not_reply_ready"] += 1
            continue
        eligible_pairs.append((profile_row, activity_row))

    message_values = [as_int(activity.get("message_count")) for _profile, activity in eligible_pairs]
    active_day_values = [as_int(activity.get("active_days")) for _profile, activity in eligible_pairs]
    message_top_threshold = top_threshold(message_values, top_percent)
    active_days_top_threshold = top_threshold(active_day_values, top_percent)

    candidates = []
    for profile_row, activity_row in eligible_pairs:
        message_count = as_int(activity_row.get("message_count"))
        active_days = as_int(activity_row.get("active_days"))
        recent_count_key = f"recent_{recent_days}d_count"
        recent_count = as_int(activity_row.get(recent_count_key))
        if recent_days != 30 and recent_count_key not in activity_row:
            recent_count = as_int(activity_row.get("recent_30d_count"))
        last_age_days = activity_row.get("last_age_days")
        last_age_int = as_int(last_age_days, -1)
        normalized_last_age = last_age_int if last_age_int >= 0 else None

        reasons = []
        if message_count >= message_top_threshold and message_top_threshold > 0:
            reasons.append("top_message_count")
        if active_days >= active_days_top_threshold and active_days_top_threshold > 0:
            reasons.append("top_active_days")
        if recent_count > 0 and message_count >= min_recent_total_messages:
            reasons.append(f"recent_{recent_days}d")

        common_match = (
            message_count >= min_common_messages
            and active_days >= min_common_active_days
            and normalized_last_age is not None
            and normalized_last_age <= common_recency_days
        )
        if common_match:
            reasons.append("common_recent_contact")

        high_value_reasons = [reason for reason in reasons if reason != "common_recent_contact"]
        if high_value_reasons:
            tier = "high_value"
        elif common_match:
            tier = "common"
        else:
            excluded["below_threshold"] += 1
            continue

        message_pct = percentile_score(message_count, message_values)
        active_days_pct = percentile_score(active_days, active_day_values)
        score = round(message_pct * 0.50 + active_days_pct * 0.30 + recency_score(normalized_last_age) * 0.20, 6)
        flags = quality_flags(profile_row, activity_row)
        memory_fresh_with_activity = "activity_index_newer_than_memory_messages" not in flags
        reply_ready = is_reply_ready(profile_row, memory_fresh_with_activity, activity_row)
        delta_days = activity_profile_delta_days(profile_row, activity_row)

        record = {
            "contact_id": str(profile_row.get("profile_id") or ""),
            "conversation_username": str(profile_row.get("conversation_username") or ""),
            "conversation_type": str(profile_row.get("conversation_type") or ""),
            "display_name": str(profile_row.get("display_name") or ""),
            "remark": str(activity_row.get("remark") or profile_row.get("remark") or ""),
            "nick_name": str(activity_row.get("nick_name") or profile_row.get("nick_name") or ""),
            "alias": str(activity_row.get("alias") or profile_row.get("alias") or ""),
            "wechat_id": str(activity_row.get("alias") or profile_row.get("alias") or ""),
            "tier": tier,
            "score": score,
            "selection_reasons": sorted(set(reasons)),
            "activity": {
                "message_count": message_count,
                "active_days": active_days,
                "history_days": as_int(activity_row.get("history_days")),
                "last_age_days": normalized_last_age,
                "last_contact_at": str(activity_row.get("last_message_at") or profile_row.get("last_message_at") or ""),
                "recent_7d_count": as_int(activity_row.get("recent_7d_count")),
                "recent_30d_count": as_int(activity_row.get("recent_30d_count")),
                "recent_90d_count": as_int(activity_row.get("recent_90d_count")),
                "notification_muted": activity_row.get("notification_muted"),
                "notification_state": str(activity_row.get("notification_state") or "unknown"),
                "chat_room_notify": activity_row.get("chat_room_notify"),
            },
            "memory": {
                "profile_path": str(profile_row.get("profile_path") or ""),
                "message_count": as_int(profile_row.get("message_count")),
                "readable_message_count": as_int(profile_row.get("readable_message_count")),
                "filtered_unreadable_message_count": as_int(profile_row.get("filtered_unreadable_message_count")),
                "source_hash": str(profile_row.get("source_hash") or ""),
            },
            "source_consistency": {
                "activity_index_aligned_with_memory_messages": memory_fresh_with_activity,
                "activity_index_minus_memory_message_days": delta_days,
            },
            "memory_state": str(profile_row.get("profile_state") or ""),
            "stale_level": str(profile_row.get("stale_level") or ""),
            "auto_reply_blocked": bool(profile_row.get("auto_reply_blocked")),
            "block_reasons": list(profile_row.get("block_reasons") or []),
            "pii_present": bool(profile_row.get("pii_present")),
            "reply_ready": reply_ready,
            "review_only": not reply_ready,
            "quality_flags": flags,
            "rendered_page": f"pages/{profile_row.get('profile_id')}.md",
        }
        record["suggested_next_action"] = suggested_next_action(record)
        candidates.append(record)

    candidates.sort(
        key=lambda item: (
            -as_float(item.get("score")),
            -as_int(item["activity"].get("message_count")),
            -as_int(item["activity"].get("active_days")),
            item.get("contact_id") or "",
        )
    )

    excluded["eligible_candidates"] = len(eligible_pairs)
    excluded["message_top_threshold"] = message_top_threshold
    excluded["active_days_top_threshold"] = active_days_top_threshold
    return candidates, excluded


def render_pages(
    memory_root: Path,
    pages_root: Path,
    records: list[dict[str, Any]],
    generated_at: str,
) -> None:
    _ensure_dir(pages_root)
    for record in records:
        index_row = {
            "profile_id": record["contact_id"],
            "profile_path": record["memory"]["profile_path"],
        }
        profile = load_profile(memory_root, index_row)
        page_text = frontmatter(record, generated_at) + profile_markdown(profile)
        page_path = pages_root / f"{record['contact_id']}.md"
        page_path.write_text(page_text, encoding="utf-8")


def clean_pages(pages_root: Path) -> None:
    if pages_root.exists():
        shutil.rmtree(pages_root)
    _ensure_dir(pages_root)


def build_contact_wiki(
    activity_report_path: Path,
    memory_root: Path,
    out_root: Path,
    *,
    max_pages: int,
    min_common_messages: int,
    min_common_active_days: int,
    common_recency_days: int,
    recent_days: int,
    min_recent_total_messages: int,
    top_percent: float,
    reply_ready_only: bool,
    clean: bool,
    generated_at: str,
) -> dict[str, Any]:
    activity_report_path = activity_report_path.expanduser().resolve()
    memory_root = memory_root.expanduser().resolve()
    out_root = out_root.expanduser().resolve()
    pages_root = out_root / "pages"
    generated_at = generated_at or utc_now_iso()

    activity_rows = load_activity_rows(activity_report_path)
    profile_rows = load_profile_index(memory_root)
    activity_by_id = index_activity_rows(activity_rows)

    selected, counters = build_candidate_records(
        profile_rows,
        activity_by_id,
        top_percent=top_percent,
        min_common_messages=min_common_messages,
        min_common_active_days=min_common_active_days,
        common_recency_days=common_recency_days,
        recent_days=recent_days,
        min_recent_total_messages=min_recent_total_messages,
        reply_ready_only=reply_ready_only,
    )
    selected = selected[: max(max_pages, 0)]

    if clean:
        clean_pages(pages_root)
    else:
        _ensure_dir(pages_root)

    render_pages(memory_root, pages_root, selected, generated_at)

    by_tier = Counter(record["tier"] for record in selected)
    by_action = Counter(record["suggested_next_action"] for record in selected)
    by_quality_flag = Counter(flag for record in selected for flag in record["quality_flags"])
    summary = {
        "schema_version": CONTACT_WIKI_SCHEMA_VERSION,
        "generated_at": generated_at,
        "activity_report": str(activity_report_path),
        "memory_root": str(memory_root),
        "out_root": str(out_root),
        "pages_root": str(pages_root),
        "inputs": {
            "activity_conversations": len(activity_rows),
            "profile_rows": len(profile_rows),
        },
        "selection": {
            "max_pages": max_pages,
            "selected_contacts": len(selected),
            "rendered_pages": len(selected),
            "reply_ready_contacts": sum(1 for record in selected if record["reply_ready"]),
            "review_only_contacts": sum(1 for record in selected if record["review_only"]),
            "by_tier": dict(sorted(by_tier.items())),
            "by_suggested_next_action": dict(sorted(by_action.items())),
            "by_quality_flag": dict(sorted(by_quality_flag.items())),
            "excluded_counts": dict(sorted((str(key), int(value)) for key, value in counters.items())),
            "thresholds": {
                "top_percent": top_percent,
                "message_top_threshold": int(counters.get("message_top_threshold", 0)),
                "active_days_top_threshold": int(counters.get("active_days_top_threshold", 0)),
                "min_common_messages": min_common_messages,
                "min_common_active_days": min_common_active_days,
                "common_recency_days": common_recency_days,
                "recent_days": recent_days,
                "min_recent_total_messages": min_recent_total_messages,
                "reply_ready_only": reply_ready_only,
            },
        },
    }
    manifest = {
        "schema_version": CONTACT_WIKI_SCHEMA_VERSION,
        "generated_at": generated_at,
        "summary_path": "summary.json",
        "contacts": selected,
    }

    _ensure_dir(out_root)
    _write_json(out_root / "summary.json", summary)
    _write_json(out_root / "manifest.json", manifest)
    return summary


def main() -> int:
    args = parse_args()
    try:
        summary = build_contact_wiki(
            Path(args.activity_report),
            Path(args.memory_root),
            Path(args.out_root),
            max_pages=args.max_pages,
            min_common_messages=args.min_common_messages,
            min_common_active_days=args.min_common_active_days,
            common_recency_days=args.common_recency_days,
            recent_days=args.recent_days,
            min_recent_total_messages=args.min_recent_total_messages,
            top_percent=args.top_percent,
            reply_ready_only=args.reply_ready_only,
            clean=args.clean,
            generated_at=args.generated_at,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
