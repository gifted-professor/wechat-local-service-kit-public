#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from wx_cli_profile import (
    PROFILE_POINTER,
    load_profile_config,
    profile_db_dir,
    profile_keys_path,
    resolve_profile_dir,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = REPO_ROOT / "out" / "accounts"


@dataclass
class ProfileRecord:
    account_id: str
    profile_dir: Path
    profile_name: str
    db_dir: Path
    keys_path: Path
    key_count: int
    active: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "profile_name": self.profile_name,
            "profile_dir": str(self.profile_dir),
            "db_dir": str(self.db_dir),
            "keys_path": str(self.keys_path),
            "key_count": self.key_count,
            "active": self.active,
        }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def discover_profile_dirs() -> list[Path]:
    profile_dirs: list[Path] = []
    for path in sorted(REPO_ROOT.glob(".wx-cli-*")):
        if not path.is_dir():
            continue
        if path.name == ".wx-cli-tools":
            continue
        if not (path / "config.json").exists():
            continue
        profile_dirs.append(path.resolve())
    return profile_dirs


def _load_key_count(keys_path: Path) -> int:
    if not keys_path.exists():
        return 0
    try:
        payload = json.loads(keys_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(payload) if isinstance(payload, dict) else 0


def _account_id_from_db_dir(db_dir: Path) -> str:
    if db_dir.name == "db_storage":
        return db_dir.parent.name
    return db_dir.name


def load_records() -> list[ProfileRecord]:
    active_dir = resolve_profile_dir()
    records: list[ProfileRecord] = []
    for profile_dir in discover_profile_dirs():
        config = load_profile_config(profile_dir)
        db_dir = profile_db_dir(profile_dir)
        keys_path = profile_keys_path(profile_dir)
        account_id = _account_id_from_db_dir(db_dir)
        profile_name = profile_dir.name.removeprefix(".wx-cli-")
        records.append(
            ProfileRecord(
                account_id=account_id,
                profile_dir=profile_dir,
                profile_name=profile_name,
                db_dir=db_dir,
                keys_path=keys_path,
                key_count=_load_key_count(keys_path),
                active=bool(active_dir and active_dir.resolve() == profile_dir),
            )
        )
    return records


def write_active_profile(profile_dir: Path) -> Path:
    try:
        relative = profile_dir.relative_to(REPO_ROOT)
        text = relative.as_posix()
    except ValueError:
        text = str(profile_dir)
    PROFILE_POINTER.write_text(text + "\n", encoding="utf-8")
    return PROFILE_POINTER


def find_record(identifier: str, records: Iterable[ProfileRecord]) -> ProfileRecord:
    ident = identifier.strip()
    if not ident:
        raise ValueError("empty account/profile identifier")

    matches = []
    for record in records:
        if ident in {
            record.account_id,
            record.profile_name,
            record.profile_dir.name,
            str(record.profile_dir),
        }:
            matches.append(record)
    if not matches:
        raise FileNotFoundError(f"no account/profile matched: {identifier}")
    if len(matches) > 1:
        payload = [item.to_dict() for item in matches]
        raise ValueError(f"ambiguous identifier {identifier}: {json.dumps(payload, ensure_ascii=False)}")
    return matches[0]


def run_json_command(command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            json.dumps(
                {
                    "command": command,
                    "returncode": completed.returncode,
                    "stdout": (completed.stdout or "")[-2000:],
                    "stderr": (completed.stderr or "")[-2000:],
                },
                ensure_ascii=False,
            )
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            json.dumps(
                {
                    "command": command,
                    "error": "stdout is not valid JSON",
                    "stdout": (completed.stdout or "")[-2000:],
                    "stderr": (completed.stderr or "")[-2000:],
                },
                ensure_ascii=False,
            )
        ) from exc


def run_export(record: ProfileRecord, out_root: Path, conversation: str = "") -> dict[str, Any]:
    account_root = out_root / record.account_id
    export_root = account_root / "chat-export"
    command = [
        sys.executable,
        "scripts/export_chat_history.py",
        "--output",
        str(export_root),
        "--wx-cli-profile",
        str(record.profile_dir),
    ]
    if conversation:
        command.extend(["--conversation", conversation])
    summary = run_json_command(command, REPO_ROOT)
    return {
        "account_id": record.account_id,
        "profile_name": record.profile_name,
        "profile_dir": str(record.profile_dir),
        "export_root": str(export_root),
        "export_summary": summary,
    }


def run_build_memory(
    record: ProfileRecord,
    out_root: Path,
    *,
    conversation: str = "",
    private_only: bool = False,
    recent_limit: Optional[int] = None,
    max_items_per_category: Optional[int] = None,
) -> dict[str, Any]:
    account_root = out_root / record.account_id
    export_root = account_root / "chat-export" / "export"
    memory_root = account_root / "customer-memory"
    command = [
        sys.executable,
        "scripts/build_customer_memory.py",
        "--export-root",
        str(export_root),
        "--out-root",
        str(memory_root),
    ]
    if conversation:
        command.extend(["--conversation", conversation])
    if private_only:
        command.append("--private-only")
    if recent_limit is not None:
        command.extend(["--recent-limit", str(recent_limit)])
    if max_items_per_category is not None:
        command.extend(["--max-items-per-category", str(max_items_per_category)])
    summary = run_json_command(command, REPO_ROOT)
    return {
        "account_id": record.account_id,
        "profile_name": record.profile_name,
        "memory_root": str(memory_root),
        "memory_summary": summary,
    }


def run_build_mapping(
    record: ProfileRecord,
    out_root: Path,
    *,
    dashboard_customers: Path,
    dashboard_orders: Optional[Path] = None,
    conversation: str = "",
    top_candidates: int = 5,
) -> dict[str, Any]:
    account_root = out_root / record.account_id
    memory_root = account_root / "customer-memory"
    mapping_root = account_root / "customer-mapping"
    command = [
        sys.executable,
        "scripts/build_customer_order_mapping_report.py",
        "--memory-root",
        str(memory_root),
        "--dashboard-customers",
        str(dashboard_customers),
        "--out-root",
        str(mapping_root),
        "--top-candidates",
        str(top_candidates),
    ]
    if dashboard_orders is not None:
        command.extend(["--dashboard-orders", str(dashboard_orders)])
    if conversation:
        command.extend(["--conversation", conversation])
    summary = run_json_command(command, REPO_ROOT)
    return {
        "account_id": record.account_id,
        "profile_name": record.profile_name,
        "mapping_root": str(mapping_root),
        "mapping_summary": summary,
    }


def selected_records(args: argparse.Namespace, records: list[ProfileRecord]) -> list[ProfileRecord]:
    if args.all:
        return records
    if args.account:
        return [find_record(identifier, records) for identifier in args.account]
    active = [record for record in records if record.active]
    if active:
        return active
    raise ValueError("no account selected; pass --account or --all, or set .wx-cli-profile")


def command_list(_args: argparse.Namespace) -> int:
    records = load_records()
    payload = {
        "schema_version": "wechat_account_profiles_v1",
        "generated_at": utc_now_iso(),
        "active_profile_pointer": str(PROFILE_POINTER),
        "accounts": [record.to_dict() for record in records],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_activate(args: argparse.Namespace) -> int:
    records = load_records()
    record = find_record(args.account, records)
    pointer_path = write_active_profile(record.profile_dir)
    payload = {
        "schema_version": "wechat_account_activate_v1",
        "activated_at": utc_now_iso(),
        "pointer_path": str(pointer_path),
        "active_profile": record.to_dict(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_sync(args: argparse.Namespace) -> int:
    records = load_records()
    chosen = selected_records(args, records)
    out_root = Path(args.out_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    dashboard_customers = Path(args.dashboard_customers).expanduser().resolve() if args.dashboard_customers else None
    dashboard_orders = Path(args.dashboard_orders).expanduser().resolve() if args.dashboard_orders else None

    account_entries = []
    for record in chosen:
        export_entry = run_export(record, out_root, conversation=args.conversation)
        memory_entry = None
        if not args.skip_memory:
            memory_entry = run_build_memory(
                record,
                out_root,
                conversation=args.conversation,
                private_only=args.private_only,
                recent_limit=args.recent_limit,
                max_items_per_category=args.max_items_per_category,
            )
        mapping_entry = None
        if dashboard_customers:
            mapping_entry = run_build_mapping(
                record,
                out_root,
                dashboard_customers=dashboard_customers,
                dashboard_orders=dashboard_orders,
                conversation=args.conversation,
                top_candidates=args.mapping_top_candidates,
            )
        account_entries.append(
            {
                "account": record.to_dict(),
                "export": export_entry,
                "memory": memory_entry,
                "mapping": mapping_entry,
            }
        )

    manifest = {
        "schema_version": "wechat_accounts_sync_v1",
        "generated_at": utc_now_iso(),
        "out_root": str(out_root),
        "selected_count": len(account_entries),
        "skip_memory": bool(args.skip_memory),
        "dashboard_customers": str(dashboard_customers) if dashboard_customers else "",
        "dashboard_orders": str(dashboard_orders) if dashboard_orders else "",
        "accounts": account_entries,
    }
    manifest_path = out_root / "accounts_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest_path": str(manifest_path), **manifest}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage repo-local multi-account WeChat wx-cli profiles and account-specific exports")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List discovered repo-local wx-cli account profiles")
    list_parser.set_defaults(func=command_list)

    activate_parser = subparsers.add_parser("activate", help="Set the repo-local active wx-cli profile")
    activate_parser.add_argument("account", help="account_id, profile_name, profile dir name, or absolute profile path")
    activate_parser.set_defaults(func=command_activate)

    sync_parser = subparsers.add_parser("sync", help="Export and optionally build customer memory for one or more accounts")
    sync_parser.add_argument("--account", action="append", help="account_id or profile_name; repeat for multiple accounts")
    sync_parser.add_argument("--all", action="store_true", help="sync all discovered repo-local account profiles")
    sync_parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="root output directory for account-specific exports")
    sync_parser.add_argument("--conversation", default="", help="optional conversation filter passed through to export/memory steps")
    sync_parser.add_argument("--skip-memory", action="store_true", help="only export chats, skip customer-memory build")
    sync_parser.add_argument("--private-only", action="store_true", help="only build memory for private conversations")
    sync_parser.add_argument("--recent-limit", type=int, help="override build_customer_memory recent message limit")
    sync_parser.add_argument("--max-items-per-category", type=int, help="override build_customer_memory extracted fact cap")
    sync_parser.add_argument("--dashboard-customers", help="optional dashboard customer_action_data.json path to build customer mapping")
    sync_parser.add_argument("--dashboard-orders", help="optional orders_realtime.json path for address reinforcement in customer mapping")
    sync_parser.add_argument("--mapping-top-candidates", type=int, default=5, help="number of candidate dashboard matches to keep per conversation")
    sync_parser.set_defaults(func=command_sync)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "command": args.command}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
