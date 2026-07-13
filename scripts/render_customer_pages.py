#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from customer_memory import query_profiles, render_profile_page


def main() -> None:
    parser = argparse.ArgumentParser(description="Render customer memory profiles as Markdown pages")
    parser.add_argument("--memory-root", required=True, help="Customer memory root, e.g. out/customer-memory")
    parser.add_argument("--pages-root", help="Output pages root; defaults to <memory-root>/pages")
    parser.add_argument("--conversation", default="", help="Profile id, username, display name, or fuzzy text")
    parser.add_argument("--limit", type=int, help="Maximum pages to render")
    parser.add_argument("--conversation-type", default="", help="Optional type filter, e.g. friend/group/official")
    parser.add_argument("--only-unblocked", action="store_true", help="Only render profiles that are not auto-reply blocked")
    args = parser.parse_args()

    try:
        memory_root = Path(args.memory_root).expanduser().resolve()
        pages_root = Path(args.pages_root).expanduser().resolve() if args.pages_root else None
        rows = query_profiles(
            memory_root,
            query=args.conversation,
            limit=args.limit,
            include_blocked=not args.only_unblocked,
            conversation_type=args.conversation_type,
        )
        pages = [str(render_profile_page(memory_root, row, pages_root)) for row in rows]
        print(
            json.dumps(
                {
                    "memory_root": str(memory_root),
                    "pages_root": str(pages_root or memory_root / "pages"),
                    "rendered": len(pages),
                    "pages": pages,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
