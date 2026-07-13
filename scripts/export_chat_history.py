#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

from chat_crypto import ChatCryptoError, KEY_MODE_CHOICES, KEY_MODE_DERIVED, prepare_readable_db, read_sqlite_tables
from parse_chat_history import export_chat_history
from wechat_common import _ensure_dir, _write_json
from wx_cli_profile import load_profile_keys, profile_db_dir, resolve_profile_dir


REQUIRED_DB_RELATIVE_PATHS = {
    "contact": Path("contact/contact.db"),
    "session": Path("session/session.db"),
}


MESSAGE_DIR_RELATIVE = Path("message")



def _resolve_db_storage_root(root: Path) -> Path:
    root = root.expanduser().resolve()
    direct = all((root / rel).exists() for rel in REQUIRED_DB_RELATIVE_PATHS.values())
    if direct and (root / MESSAGE_DIR_RELATIVE).exists():
        return root
    db_storage = root / "db_storage"
    nested = all((db_storage / rel).exists() for rel in REQUIRED_DB_RELATIVE_PATHS.values())
    if nested and (db_storage / MESSAGE_DIR_RELATIVE).exists():
        return db_storage
    raise FileNotFoundError(f"could not locate db_storage under: {root}")



def _prepare_account_workspace(
    db_storage_root: Path,
    workspace_dir: Path,
    enc_key: str = None,
    key_mode: str = "auto",
    frida_log: Path = None,
    per_db_keys: dict[str, str] = None,
) -> Path:
    readable_root = workspace_dir / "db_storage"
    _ensure_dir(readable_root / "contact")
    _ensure_dir(readable_root / "session")
    _ensure_dir(readable_root / "message")

    per_db_keys = per_db_keys or {}
    prepared = {}
    for name, relative in REQUIRED_DB_RELATIVE_PATHS.items():
        source = db_storage_root / relative
        target_dir = readable_root / relative.parent
        matched_key = per_db_keys.get(relative.as_posix())
        prepared[name] = str(
            prepare_readable_db(
                source,
                target_dir,
                matched_key or enc_key,
                KEY_MODE_DERIVED if matched_key else key_mode,
                None if matched_key else frida_log,
            )
        )

    message_sources = sorted((db_storage_root / MESSAGE_DIR_RELATIVE).glob("message*.db"))
    prepared_messages = []
    for message_source in message_sources:
        rel_path = message_source.relative_to(db_storage_root).as_posix()
        matched_key = per_db_keys.get(rel_path)
        prepared_messages.append(
            str(
                prepare_readable_db(
                    message_source,
                    readable_root / "message",
                    matched_key or enc_key,
                    KEY_MODE_DERIVED if matched_key else key_mode,
                    None if matched_key else frida_log,
                )
            )
        )

    _write_json(
        workspace_dir / "prepared_dbs.json",
        {
            "source_root": str(db_storage_root),
            "prepared": prepared,
            "prepared_messages": prepared_messages,
        },
    )
    return readable_root



def main() -> None:
    parser = argparse.ArgumentParser(description="Export macOS WeChat chat history into structured files")
    parser.add_argument("--wechat-root", help="wxid account directory or db_storage directory")
    parser.add_argument("--wx-cli-profile", help="wx-cli profile directory or config.json path; defaults to repo/env active profile when available")
    parser.add_argument("--output", required=True, help="output directory for exported files")
    parser.add_argument("--enc-key", help="database encryption key in hex form")
    parser.add_argument("--key-mode", choices=KEY_MODE_CHOICES, default="auto", help="how to interpret --enc-key")
    parser.add_argument("--frida-log", help="Frida PBKDF2 log path; auto-match each DB key by salt when provided")
    parser.add_argument("--conversation", help="optional conversation username/display name filter")
    args = parser.parse_args()

    try:
        output_dir = Path(args.output).expanduser().resolve()
        export_dir = output_dir / "export"
        workspace_dir = output_dir / "workspace"
        _ensure_dir(export_dir)
        _ensure_dir(workspace_dir)
        frida_log = Path(args.frida_log).expanduser().resolve() if args.frida_log else None
        profile_dir = resolve_profile_dir(args.wx_cli_profile)
        per_db_keys: dict[str, str] = {}

        if args.wechat_root:
            wechat_root = Path(args.wechat_root)
        elif profile_dir:
            wechat_root = profile_db_dir(profile_dir)
        else:
            raise FileNotFoundError("one of --wechat-root or --wx-cli-profile is required, or configure a repo-local .wx-cli-profile")

        if profile_dir:
            per_db_keys = load_profile_keys(profile_dir)

        db_storage_root = _resolve_db_storage_root(wechat_root)
        readable_root = _prepare_account_workspace(
            db_storage_root,
            workspace_dir,
            args.enc_key,
            args.key_mode,
            frida_log,
            per_db_keys=per_db_keys,
        )
        manifest = export_chat_history(readable_root, export_dir, args.conversation)

        prepared_info_path = workspace_dir / "prepared_dbs.json"
        prepared_info = json.loads(prepared_info_path.read_text(encoding="utf-8")) if prepared_info_path.exists() else {}
        summary = {
            "db_storage_root": str(db_storage_root),
            "wx_cli_profile": str(profile_dir) if profile_dir else "",
            "per_db_key_count": len(per_db_keys),
            "workspace": str(workspace_dir),
            "export": str(export_dir),
            "key_mode": args.key_mode,
            "frida_log": str(frida_log) if frida_log else "",
            "prepared_tables": {
                "contact": read_sqlite_tables(Path(prepared_info.get("prepared", {}).get("contact", readable_root / "contact" / "contact.db"))),
                "session": read_sqlite_tables(Path(prepared_info.get("prepared", {}).get("session", readable_root / "session" / "session.db"))),
            },
            "manifest": manifest,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (FileNotFoundError, ChatCryptoError, OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
