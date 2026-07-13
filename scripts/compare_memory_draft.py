#!/usr/bin/env python3

import argparse
import json
import os
import sys
from pathlib import Path

from customer_memory import build_runtime_context_for_query, should_use_memory_for_message
from reply_api import generate_reply_openai_compatible


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare API drafts with and without customer memory")
    parser.add_argument("--memory-root", required=True, help="Customer memory root, e.g. out/customer-memory")
    parser.add_argument("--conversation", required=True, help="Profile id, username, display name, or fuzzy text")
    parser.add_argument("--message", required=True, help="Latest incoming message text")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"))
    parser.add_argument("--api-base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument(
        "--memory-use-policy",
        choices=("auto", "always", "never"),
        default="auto",
        help="policy for deciding whether the memory-backed draft receives customer memory",
    )
    args = parser.parse_args()

    latest_message = {
        "timestamp": "",
        "direction": "received",
        "message_type": "text",
        "render_type": "text",
        "text": args.message,
    }
    recent_messages = [latest_message]

    try:
        memory_context = build_runtime_context_for_query(Path(args.memory_root), args.conversation)
        memory_gate = should_use_memory_for_message(args.message, policy=args.memory_use_policy)
        api_memory_context = memory_context if memory_gate["decision"] else None
        without_memory = generate_reply_openai_compatible(
            latest_message=latest_message,
            recent_messages=recent_messages,
            conversation_name=args.conversation,
            model=args.model,
            base_url=args.api_base_url,
            api_key=args.api_key,
            context_limit=4,
            memory_context=None,
        )
        with_memory = generate_reply_openai_compatible(
            latest_message=latest_message,
            recent_messages=recent_messages,
            conversation_name=args.conversation,
            model=args.model,
            base_url=args.api_base_url,
            api_key=args.api_key,
            context_limit=4,
            memory_context=api_memory_context,
        )
        print(
            json.dumps(
                {
                    "message": args.message,
                    "model": args.model,
                    "conversation": args.conversation,
                    "memory_profile_id": memory_context.get("profile_id"),
                    "memory_use_policy": args.memory_use_policy,
                    "memory_gate": memory_gate,
                    "without_memory": without_memory["reply_text"],
                    "with_memory": with_memory["reply_text"],
                    "memory_context_used": with_memory.get("memory_context_used", False),
                    "risk": memory_context.get("risk"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
