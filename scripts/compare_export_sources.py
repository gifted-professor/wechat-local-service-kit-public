#!/usr/bin/env python3

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from wechat_common import _timestamp_to_iso
from wechat_privacy import redact_obj


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                yield {"_error": "json_decode", "_line": line_no}
                continue
            yield row if isinstance(row, dict) else {"value": row}


def _load_index(export_root: Path) -> list[dict[str, Any]]:
    index_path = export_root / "conversation_index.json"
    if not index_path.exists():
        return []
    payload = _read_json(index_path)
    return payload if isinstance(payload, list) else []


def _conversation_key(item: dict[str, Any]) -> str:
    return str(
        item.get("conversation_username")
        or item.get("conversation_id")
        or item.get("display_name")
        or item.get("file")
        or ""
    )


def _message_stats(path: Path) -> dict[str, Any]:
    count = 0
    errors = 0
    type_counts: Counter[str] = Counter()
    direction_counts: Counter[str] = Counter()
    min_ts: Optional[str] = None
    max_ts: Optional[str] = None
    first_fields: set[str] = set()
    missing_required: Counter[str] = Counter()
    required = ["timestamp", "message_type", "text", "direction"]

    if not path.exists():
        return {
            "exists": False,
            "messages": 0,
            "errors": 0,
            "type_counts": {},
            "direction_counts": {},
            "min_timestamp": None,
            "max_timestamp": None,
            "fields": [],
            "missing_required": {},
        }

    for row in _iter_jsonl(path):
        if row.get("_error"):
            errors += 1
            continue
        count += 1
        first_fields.update(row.keys())
        for key in required:
            if row.get(key) in (None, ""):
                missing_required[key] += 1
        message_type = str(row.get("render_type") or row.get("message_type") or "unknown")
        direction = str(row.get("direction") or "unknown")
        type_counts[message_type] += 1
        direction_counts[direction] += 1
        iso = _timestamp_to_iso(row.get("timestamp")) or str(row.get("timestamp") or "")
        if iso:
            min_ts = iso if min_ts is None or iso < min_ts else min_ts
            max_ts = iso if max_ts is None or iso > max_ts else max_ts

    return {
        "exists": True,
        "messages": count,
        "errors": errors,
        "type_counts": dict(sorted(type_counts.items())),
        "direction_counts": dict(sorted(direction_counts.items())),
        "min_timestamp": min_ts,
        "max_timestamp": max_ts,
        "fields": sorted(first_fields),
        "missing_required": dict(sorted(missing_required.items())),
    }


def _resolve_conversation_file(export_root: Path, item: dict[str, Any]) -> Path:
    rel = str(item.get("file") or "").strip()
    if rel:
        return export_root / rel
    conversation_id = str(item.get("conversation_id") or "").strip()
    return export_root / "conversations" / f"{conversation_id}.jsonl"


def _summarize_export(export_root: Path) -> dict[str, Any]:
    index = _load_index(export_root)
    conversations = {}
    total_messages = 0
    total_errors = 0
    type_counts: Counter[str] = Counter()
    missing_files = 0

    for item in index:
        key = _conversation_key(item)
        path = _resolve_conversation_file(export_root, item)
        stats = _message_stats(path)
        if not stats["exists"]:
            missing_files += 1
        total_messages += int(stats.get("messages") or 0)
        total_errors += int(stats.get("errors") or 0)
        type_counts.update(stats.get("type_counts") or {})
        conversations[key] = {
            "conversation_id": item.get("conversation_id") or "",
            "conversation_username": item.get("conversation_username") or "",
            "display_name": item.get("display_name") or "",
            "file": str(path.relative_to(export_root)) if path.is_relative_to(export_root) else str(path),
            "index_message_count": item.get("message_count"),
            "message_stats": stats,
        }

    return {
        "export_root": str(redact_obj(str(export_root))),
        "conversation_count": len(index),
        "message_count": total_messages,
        "jsonl_errors": total_errors,
        "missing_files": missing_files,
        "type_counts": dict(sorted(type_counts.items())),
        "conversations": conversations,
    }


def _diff_counts(left: int, right: int) -> dict[str, int]:
    return {"left": left, "right": right, "delta": left - right}


def compare_exports(left_root: Path, right_root: Path, *, limit_conversations: int = 20) -> dict[str, Any]:
    left = _summarize_export(left_root)
    right = _summarize_export(right_root)
    left_keys = set(left["conversations"].keys())
    right_keys = set(right["conversations"].keys())
    shared = sorted(left_keys & right_keys)
    left_only = sorted(left_keys - right_keys)
    right_only = sorted(right_keys - left_keys)

    conversation_diffs = []
    for key in shared[: max(limit_conversations, 0)]:
        lconv = left["conversations"][key]
        rconv = right["conversations"][key]
        lstats = lconv["message_stats"]
        rstats = rconv["message_stats"]
        conversation_diffs.append(
            {
                "key": key,
                "display_name": lconv.get("display_name") or rconv.get("display_name") or "",
                "messages": _diff_counts(int(lstats.get("messages") or 0), int(rstats.get("messages") or 0)),
                "time_range": {
                    "left": [lstats.get("min_timestamp"), lstats.get("max_timestamp")],
                    "right": [rstats.get("min_timestamp"), rstats.get("max_timestamp")],
                },
                "type_counts": {
                    "left": lstats.get("type_counts") or {},
                    "right": rstats.get("type_counts") or {},
                },
                "missing_required": {
                    "left": lstats.get("missing_required") or {},
                    "right": rstats.get("missing_required") or {},
                },
            }
        )

    return {
        "schema_version": "wechat_export_compare_v1",
        "generated_at": utc_now_iso(),
        "left": {key: value for key, value in left.items() if key != "conversations"},
        "right": {key: value for key, value in right.items() if key != "conversations"},
        "diff": {
            "conversation_count": _diff_counts(left["conversation_count"], right["conversation_count"]),
            "message_count": _diff_counts(left["message_count"], right["message_count"]),
            "shared_conversations": len(shared),
            "left_only_count": len(left_only),
            "right_only_count": len(right_only),
            "left_only_preview": left_only[:limit_conversations],
            "right_only_preview": right_only[:limit_conversations],
            "conversation_diffs": conversation_diffs,
            "conversation_diffs_truncated": max(len(shared) - len(conversation_diffs), 0),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two WeChat export roots without printing message text")
    parser.add_argument("--left", required=True, help="left export root containing conversation_index.json")
    parser.add_argument("--right", required=True, help="right export root containing conversation_index.json")
    parser.add_argument("--limit-conversations", type=int, default=20, help="maximum shared conversation diffs to include")
    parser.add_argument("--output", help="optional JSON output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = compare_exports(
        Path(args.left).expanduser().resolve(),
        Path(args.right).expanduser().resolve(),
        limit_conversations=args.limit_conversations,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
