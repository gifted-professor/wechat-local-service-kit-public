#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from customer_memory import build_customer_memory


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic WeChat customer memory profiles")
    parser.add_argument("--export-root", required=True, help="Path to chat export root, e.g. out/chat-export/export")
    parser.add_argument("--out-root", required=True, help="Output root for customer memory, e.g. out/customer-memory")
    parser.add_argument("--conversation", default="", help="Optional display name, username, or conversation id filter")
    parser.add_argument("--limit", type=int, help="Optional maximum number of conversations to build")
    parser.add_argument("--private-only", action="store_true", help="Only build private/friend conversations")
    parser.add_argument("--recent-limit", type=int, default=20, help="Readable recent messages to keep per profile")
    parser.add_argument(
        "--max-items-per-category",
        type=int,
        default=20,
        help="Maximum extracted facts per observed fact category",
    )
    args = parser.parse_args()

    try:
        manifest = build_customer_memory(
            Path(args.export_root),
            Path(args.out_root),
            conversation=args.conversation,
            limit=args.limit,
            private_only=args.private_only,
            recent_limit=args.recent_limit,
            max_items_per_category=args.max_items_per_category,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
