#!/usr/bin/env python3

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from customer_memory import build_runtime_context_for_query, should_use_memory_for_message
from reply_api import generate_reply_openai_compatible
from service_knowledge import build_service_knowledge_context, service_knowledge_context_for_api


def generate_variant(
    *,
    latest_message: dict,
    recent_messages: list[dict],
    conversation: str,
    model: str,
    api_base_url: str,
    api_key: str,
    memory_context: Optional[dict],
    service_knowledge_context: Optional[dict],
) -> dict:
    result = generate_reply_openai_compatible(
        latest_message=latest_message,
        recent_messages=recent_messages,
        conversation_name=conversation,
        model=model,
        base_url=api_base_url,
        api_key=api_key,
        context_limit=4,
        memory_context=memory_context,
        service_knowledge_context=service_knowledge_context,
    )
    return {
        "reply_text": result["reply_text"],
        "memory_context_used": result.get("memory_context_used", False),
        "service_knowledge_used": result.get("service_knowledge_used", False),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare reply drafts across memory and service-knowledge contexts")
    parser.add_argument("--memory-root", required=True, help="Customer memory root, e.g. out/customer-memory")
    parser.add_argument("--wiki-root", default=".project-wiki", help="Project wiki root")
    parser.add_argument("--conversation", required=True, help="Profile id, username, display name, or fuzzy text")
    parser.add_argument("--message", required=True, help="Latest incoming message text")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.4"))
    parser.add_argument("--api-base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    parser.add_argument("--memory-use-policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--service-knowledge-policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--service-knowledge-max-playbooks", type=int, default=2)
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

        service_context = build_service_knowledge_context(
            args.message,
            wiki_root=Path(args.wiki_root),
            policy=args.service_knowledge_policy,
            max_playbooks=args.service_knowledge_max_playbooks,
        )
        api_service_context, service_gate = service_knowledge_context_for_api("draft-only", service_context)

        variants = {
            "baseline": generate_variant(
                latest_message=latest_message,
                recent_messages=recent_messages,
                conversation=args.conversation,
                model=args.model,
                api_base_url=args.api_base_url,
                api_key=args.api_key,
                memory_context=None,
                service_knowledge_context=None,
            ),
            "memory_only": generate_variant(
                latest_message=latest_message,
                recent_messages=recent_messages,
                conversation=args.conversation,
                model=args.model,
                api_base_url=args.api_base_url,
                api_key=args.api_key,
                memory_context=api_memory_context,
                service_knowledge_context=None,
            ),
            "service_knowledge_only": generate_variant(
                latest_message=latest_message,
                recent_messages=recent_messages,
                conversation=args.conversation,
                model=args.model,
                api_base_url=args.api_base_url,
                api_key=args.api_key,
                memory_context=None,
                service_knowledge_context=api_service_context,
            ),
            "memory_and_service_knowledge": generate_variant(
                latest_message=latest_message,
                recent_messages=recent_messages,
                conversation=args.conversation,
                model=args.model,
                api_base_url=args.api_base_url,
                api_key=args.api_key,
                memory_context=api_memory_context,
                service_knowledge_context=api_service_context,
            ),
        }

        print(
            json.dumps(
                {
                    "message": args.message,
                    "model": args.model,
                    "conversation": args.conversation,
                    "memory_gate": memory_gate,
                    "service_knowledge_gate": service_gate,
                    "service_playbooks": [
                        {"id": item.get("id"), "path": item.get("path")}
                        for item in (api_service_context or {}).get("playbooks", [])
                    ],
                    "variants": variants,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
