#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from customer_memory import query_profiles


def main() -> None:
    parser = argparse.ArgumentParser(description="Query deterministic customer memory profiles")
    parser.add_argument("--memory-root", required=True, help="Customer memory root, e.g. out/customer-memory")
    parser.add_argument("--query", default="", help="Profile id, username, display name, or fuzzy text")
    parser.add_argument("--limit", type=int, default=10, help="Maximum rows to print")
    parser.add_argument("--conversation-type", default="", help="Optional type filter, e.g. friend/group/official")
    parser.add_argument("--only-unblocked", action="store_true", help="Only show profiles that are not auto-reply blocked")
    args = parser.parse_args()

    try:
        rows = query_profiles(
            Path(args.memory_root),
            query=args.query,
            limit=args.limit,
            include_blocked=not args.only_unblocked,
            conversation_type=args.conversation_type,
        )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
