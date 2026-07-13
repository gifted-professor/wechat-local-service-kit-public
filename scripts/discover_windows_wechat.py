#!/usr/bin/env python3

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SQLITE_HEADER = b"SQLite format 3\x00"
DEFAULT_DB_NAMES = (
    "MicroMsg.db",
    "ChatMsg.db",
    "Media.db",
    "Favorite.db",
    "BizChat.db",
    "BizChatMsg.db",
    "ChatRoomUser.db",
    "CustomerService.db",
    "OpenIMMsg.db",
    "PublicMsg.db",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_wechat_files_root() -> Path:
    return Path.home() / "Documents" / "WeChat Files"


def default_xwechat_root() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Tencent" / "xwechat" if appdata else Path.home() / "AppData" / "Roaming" / "Tencent" / "xwechat"


def default_xwechat_files_roots() -> list[Path]:
    candidates = [
        Path("D:/wechat/xwechat_files"),
        Path.home() / "xwechat_files",
    ]
    return candidates


def default_duoliao_roots() -> list[Path]:
    roots = [Path("D:/\u591a\u804a")]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "FYWechatMulti")
    return roots


def db_kind(path: Path) -> str:
    try:
        with path.open("rb") as f:
            header = f.read(16)
    except OSError:
        return "unreadable"
    if header == SQLITE_HEADER:
        return "plain_sqlite"
    if len(header) == 16:
        return "encrypted_or_binary"
    return "too_small"


def sqlite_table_summary(path: Path, limit: int = 12) -> dict[str, Any]:
    if db_kind(path) != "plain_sqlite":
        return {"readable": False, "tables": []}
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        conn.close()
    except sqlite3.Error:
        return {"readable": False, "tables": []}
    tables = [str(row[0]) for row in rows if row and row[0]]
    return {"readable": True, "table_count": len(tables), "tables": tables[:limit]}


def file_info(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False}
    payload = {
        "exists": True,
        "path": str(path),
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }
    if path.suffix.lower() == ".db":
        payload["kind"] = db_kind(path)
        if path.name.lower() == "xinfo.db":
            payload["sqlite"] = sqlite_table_summary(path)
    return payload


def discover_wechat_accounts(root: Path) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    if not root.exists():
        return accounts
    for account_dir in sorted(root.glob("wxid_*")):
        if not account_dir.is_dir():
            continue
        msg_dir = account_dir / "Msg"
        config_dir = account_dir / "config"
        dbs = []
        for name in DEFAULT_DB_NAMES:
            db_path = msg_dir / name
            if db_path.exists():
                dbs.append(file_info(db_path))
        xinfo_path = msg_dir / "xInfo.db"
        if xinfo_path.exists():
            dbs.append(file_info(xinfo_path))
        accounts.append(
            {
                "account_id": account_dir.name,
                "account_dir": str(account_dir),
                "msg_dir_exists": msg_dir.exists(),
                "config_dir_exists": config_dir.exists(),
                "acc_info_exists": (config_dir / "AccInfo.dat").exists(),
                "db_count": len(list(msg_dir.glob("*.db"))) if msg_dir.exists() else 0,
                "sample_dbs": dbs,
            }
        )
    return accounts


def discover_xwechat_logins(root: Path) -> list[dict[str, Any]]:
    login_root = root / "login"
    if not login_root.exists():
        return []
    out = []
    for account_dir in sorted(login_root.glob("wxid_*")):
        if not account_dir.is_dir():
            continue
        key_info = account_dir / "key_info.dat"
        out.append(
            {
                "account_id": account_dir.name,
                "login_dir": str(account_dir),
                "key_info_exists": key_info.exists(),
                "key_info_size": key_info.stat().st_size if key_info.exists() else 0,
            }
        )
    return out


def discover_modern_xwechat_accounts(root: Path) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    if not root.exists():
        return accounts
    for account_dir in sorted(root.glob("wxid_*_*")):
        if not account_dir.is_dir():
            continue
        db_storage = account_dir / "db_storage"
        dbs = []
        for relative in (
            "contact/contact.db",
            "session/session.db",
            "message/message_0.db",
            "message/biz_message_0.db",
            "favorite/favorite.db",
        ):
            db_path = db_storage / relative
            if db_path.exists():
                dbs.append(file_info(db_path))
        accounts.append(
            {
                "account_id": account_dir.name,
                "account_dir": str(account_dir),
                "db_storage_exists": db_storage.exists(),
                "db_count": len(list(db_storage.rglob("*.db"))) if db_storage.exists() else 0,
                "sample_dbs": dbs,
            }
        )
    return accounts


def discover_duoliao(roots: list[Path]) -> list[dict[str, Any]]:
    out = []
    for root in roots:
        if not root.exists():
            continue
        files = []
        for name in ("DK.exe", "DL.dll", "Frida.dll", "main.ini", "config.data", "log.ini"):
            path = root / name
            if path.exists():
                files.append(file_info(path))
        out.append({"root": str(root), "exists": True, "files": files})
    return out


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    wechat_files_root = Path(args.wechat_files_root).expanduser()
    xwechat_root = Path(args.xwechat_root).expanduser()
    xwechat_files_roots = [Path(item).expanduser() for item in args.xwechat_files_root]
    duoliao_roots = [Path(item).expanduser() for item in args.duoliao_root]
    accounts = discover_wechat_accounts(wechat_files_root)
    xwechat_logins = discover_xwechat_logins(xwechat_root)
    modern_roots = [
        {
            "root": str(root),
            "exists": root.exists(),
            "accounts": discover_modern_xwechat_accounts(root),
        }
        for root in xwechat_files_roots
    ]
    return {
        "schema_version": "windows_wechat_discovery_v1",
        "generated_at": utc_now_iso(),
        "wechat_files_root": str(wechat_files_root),
        "xwechat_root": str(xwechat_root),
        "account_count": len(accounts),
        "accounts": accounts,
        "xwechat_login_count": len(xwechat_logins),
        "xwechat_logins": xwechat_logins,
        "xwechat_files_roots": modern_roots,
        "duoliao": discover_duoliao(duoliao_roots),
        "privacy": {
            "message_content_read": False,
            "secret_files_read": False,
            "secret_files": ["AccInfo.dat", "key_info.dat", "encrypted *.db"],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover Windows WeChat/FYWechatMulti local account layout without reading chat content")
    parser.add_argument("--wechat-files-root", default=str(default_wechat_files_root()))
    parser.add_argument("--xwechat-root", default=str(default_xwechat_root()))
    parser.add_argument("--xwechat-files-root", action="append", default=[str(path) for path in default_xwechat_files_roots()])
    parser.add_argument("--duoliao-root", action="append", default=[str(path) for path in default_duoliao_roots()])
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    report = build_report(args)
    print(json.dumps(report, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
