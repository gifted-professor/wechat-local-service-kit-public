#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from customer_memory import build_runtime_context_for_query


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact runtime context from customer memory")
    parser.add_argument("--memory-root", required=True, help="Customer memory root, e.g. out/customer-memory")
    parser.add_argument("--conversation", required=True, help="Profile id, username, display name, or fuzzy text")
    parser.add_argument("--fact-limit", type=int, default=3, help="Max candidate facts per category")
    parser.add_argument("--recent-limit", type=int, default=5, help="Max recent messages")
    args = parser.parse_args()

    try:
        context = build_runtime_context_for_query(
            Path(args.memory_root),
            args.conversation,
            fact_limit=args.fact_limit,
            recent_limit=args.recent_limit,
        )
        print(json.dumps(context, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
