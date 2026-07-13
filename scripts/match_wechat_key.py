#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from chat_crypto import KEY_MODE_AUTO, KEY_MODE_DERIVED, KEY_MODE_RAW, read_db_salt, validate_sqlcipher_key


def parse_frida_log(log_path: Path) -> list[dict[str, Any]]:
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    entries = []
    for block in blocks:
        entry: dict[str, Any] = {}
        for line in block.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            entry[key.strip()] = value.strip()
        if entry:
            entries.append(entry)
    return entries


def build_candidates(entry: dict[str, Any], field: str) -> list[tuple[str, str]]:
    candidates = []
    if field in ("auto", "dk") and entry.get("dk"):
        candidates.append(("dk", entry["dk"]))
    if field in ("auto", "pw") and entry.get("pw"):
        candidates.append(("pw", entry["pw"]))
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Match an SQLCipher key from Frida PBKDF2 logs")
    parser.add_argument("--log", required=True, help="Frida key log path")
    parser.add_argument("--db", help="Encrypted database path for validation")
    parser.add_argument("--salt", help="Database salt in hex, used when --db is not available")
    parser.add_argument("--rounds", type=int, default=256000, help="PBKDF2 round count filter")
    parser.add_argument("--field", choices=("auto", "dk", "pw"), default="auto", help="which Frida field(s) to try")
    args = parser.parse_args()

    if not args.db and not args.salt:
        print("[ERROR] one of --db or --salt is required", file=sys.stderr)
        return 1

    db_path = Path(args.db).expanduser().resolve() if args.db else None
    if db_path and not db_path.exists():
        print(f"[ERROR] db not found: {db_path}", file=sys.stderr)
        return 1

    log_path = Path(args.log).expanduser().resolve()
    if not log_path.exists():
        print(f"[ERROR] log not found: {log_path}", file=sys.stderr)
        return 1

    try:
        salt_hex = read_db_salt(db_path).hex() if db_path else args.salt.strip().lower()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    matches = []
    for index, entry in enumerate(parse_frida_log(log_path), start=1):
        rounds = int(entry.get("rounds") or 0)
        if args.rounds and rounds != args.rounds:
            continue
        if (entry.get("salt") or "").lower() != salt_hex:
            continue
        matches.append({"index": index, **entry})

    result: dict[str, Any] = {
        "db": str(db_path) if db_path else "",
        "log": str(log_path),
        "salt": salt_hex,
        "rounds": args.rounds,
        "match_count": len(matches),
        "validated": False,
        "matches": matches,
    }

    if db_path:
        for entry in matches:
            for candidate_field, candidate_value in build_candidates(entry, args.field):
                validation = validate_sqlcipher_key(db_path, candidate_value, KEY_MODE_AUTO)
                if validation.ok:
                    result["validated"] = True
                    result["matched_entry_index"] = entry["index"]
                    result["matched_field"] = candidate_field
                    result["key_hex"] = candidate_value
                    result["key_mode"] = validation.key_mode
                    break
            if result["validated"]:
                break
    else:
        inferred_mode = KEY_MODE_DERIVED if args.field == "dk" else KEY_MODE_RAW if args.field == "pw" else ""
        if len(matches) == 1:
            entry = matches[0]
            candidates = build_candidates(entry, args.field)
            if len(candidates) == 1:
                result["matched_entry_index"] = entry["index"]
                result["matched_field"] = candidates[0][0]
                result["key_hex"] = candidates[0][1]
                result["key_mode"] = inferred_mode

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["validated"] or result["match_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
