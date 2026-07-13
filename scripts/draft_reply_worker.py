#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from wechat_common import _ensure_dir, _write_json


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        f.write("\n")


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        sys.executable,
        "scripts/draft_reply_candidate_once.py",
        "--events",
        args.events,
        "--manifest",
        args.manifest,
        "--contacts-json",
        args.contacts_json,
        "--out-root",
        args.out_root,
        "--memory-root",
        args.memory_root,
        "--memory-mode",
        args.memory_mode,
        "--memory-use-policy",
        args.memory_use_policy,
        "--service-knowledge-root",
        args.service_knowledge_root,
        "--service-knowledge-mode",
        args.service_knowledge_mode,
        "--service-knowledge-policy",
        args.service_knowledge_policy,
        "--personal-style-root",
        args.personal_style_root,
        "--personal-style-mode",
        args.personal_style_mode,
        "--personal-style-max-examples",
        str(args.personal_style_max_examples),
        "--context-messages",
        str(args.context_messages),
        "--fresh-within-seconds",
        str(args.fresh_within_seconds),
        "--draft-to-input",
    ]
    if args.include_non_text:
        command.append("--include-non-text")
    if args.skip_draft_worthiness_gate:
        command.append("--skip-draft-worthiness-gate")

    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    parsed = None
    if stdout:
        try:
            parsed = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            parsed = None
    return {
        "created_at": utc_now_iso(),
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "parsed": parsed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuously generate WeChat reply drafts for fresh monitored candidates without sending.")
    parser.add_argument("--events", default="out/dry-run-replies/monitor/events.jsonl")
    parser.add_argument("--manifest", default="out/contact-wiki/manifest.json")
    parser.add_argument("--contacts-json", default="out/chat-export/export/contacts.json")
    parser.add_argument("--out-root", default="out/dry-run-replies/drafts")
    parser.add_argument("--memory-root", default="out/customer-memory")
    parser.add_argument("--memory-mode", choices=("off", "draft-only", "shadow"), default="draft-only")
    parser.add_argument("--memory-use-policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--service-knowledge-root", default=".project-wiki")
    parser.add_argument("--service-knowledge-mode", choices=("off", "shadow", "draft-only"), default="draft-only")
    parser.add_argument("--service-knowledge-policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--personal-style-root", default="")
    parser.add_argument("--personal-style-mode", choices=("off", "shadow", "draft-only"), default="off")
    parser.add_argument("--personal-style-max-examples", type=int, default=4)
    parser.add_argument("--context-messages", type=int, default=16)
    parser.add_argument("--fresh-within-seconds", type=float, default=1800.0)
    parser.add_argument("--interval", type=float, default=8.0)
    parser.add_argument("--duration", type=float, default=86400.0)
    parser.add_argument("--include-non-text", action="store_true")
    parser.add_argument("--skip-draft-worthiness-gate", action="store_true")
    parser.add_argument("--state", default="out/dry-run-replies/drafts/worker-state.json")
    parser.add_argument("--log-jsonl", default="out/dry-run-replies/drafts/worker-events.jsonl")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_path = Path(args.state).expanduser().resolve()
    log_path = Path(args.log_jsonl).expanduser().resolve()
    started_at = utc_now_iso()
    deadline = time.time() + max(args.duration, 0)
    iteration = 0

    while time.time() < deadline:
        iteration += 1
        result = run_once(args)
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        state = {
            "schema_version": "reply_draft_worker_state_v1",
            "status": parsed.get("status") or ("error" if result["returncode"] not in {0, 2, 3} else "idle"),
            "started_at": started_at,
            "updated_at": utc_now_iso(),
            "iteration": iteration,
            "last_returncode": result["returncode"],
            "last_result": parsed,
            "sent": bool(parsed.get("sent")) if isinstance(parsed, dict) else False,
        }
        _write_json(state_path, state)
        append_jsonl(log_path, result)
        time.sleep(max(args.interval, 1.0))

    final_state = {
        "schema_version": "reply_draft_worker_state_v1",
        "status": "done",
        "started_at": started_at,
        "updated_at": utc_now_iso(),
        "iteration": iteration,
        "sent": False,
    }
    _write_json(state_path, final_state)
    print(json.dumps(final_state, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
