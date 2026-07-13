#!/usr/bin/env python3
"""Capture a Windows wx-cli key profile without running live monitors.

This wrapper is intentionally one-shot:
- it scans a fixed target db_storage tree,
- validates keys through win_wx_multi_key_scan.py,
- writes the keys into a repo-local .wx-cli-<account> profile,
- optionally activates that profile for later offline reads.

It does not watch filesystem changes, list sessions, read history, or start the
wx-cli daemon.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER = REPO_ROOT / "scripts" / "win_wx_multi_key_scan.py"
PROFILE_POINTER = REPO_ROOT / ".wx-cli-profile"
PROFILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def profile_dir_for(account: str, explicit: str = "") -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    return (REPO_ROOT / f".wx-cli-{account}").resolve()


def resolve_account(raw: str) -> str:
    account = raw.strip()
    if PROFILE_NAME_RE.fullmatch(account):
        return account
    raise ValueError(
        "account must be a safe local profile name using letters, digits, '.', '_' or '-'"
    )


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def configured_db_dir(profile_dir: Path) -> Path | None:
    config_path = profile_dir / "config.json"
    if not config_path.exists():
        return None
    config = read_json(config_path)
    raw = str(config.get("db_dir") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = profile_dir / path
    return path.resolve()


def discover_profiles() -> list[dict[str, str]]:
    profiles: list[dict[str, str]] = []
    for profile_dir in sorted(REPO_ROOT.glob(".wx-cli-*")):
        if not profile_dir.is_dir() or profile_dir.name == ".wx-cli-tools":
            continue
        try:
            db_dir = configured_db_dir(profile_dir)
        except (OSError, ValueError, json.JSONDecodeError):
            db_dir = None
        profiles.append(
            {
                "account": profile_dir.name.removeprefix(".wx-cli-"),
                "db_dir": str(db_dir) if db_dir else "",
                "profile_dir": str(profile_dir.resolve()),
            }
        )
    return profiles


def stop_daemon_if_running() -> None:
    wx_exe = (
        REPO_ROOT
        / ".wx-cli-tools"
        / "node_modules"
        / "@jackwener"
        / "wx-cli-win32-x64"
        / "bin"
        / "wx.exe"
    )
    if not wx_exe.exists():
        return
    try:
        subprocess.run(
            [str(wx_exe), "daemon", "stop"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot Windows WeChat key capture into a repo-local wx-cli profile"
    )
    parser.add_argument(
        "account",
        nargs="?",
        help="safe local profile name, used for .wx-cli-<account>",
    )
    parser.add_argument("--list", action="store_true", help="list existing repo-local profiles and exit")
    parser.add_argument(
        "--db-dir",
        help="target db_storage directory; optional only when the profile config already defines db_dir",
    )
    parser.add_argument(
        "--profile-dir",
        help="output profile dir; defaults to .wx-cli-<account>",
    )
    parser.add_argument(
        "--out",
        help="sanitized scanner status path; defaults under out/",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="write .wx-cli-profile to this saved profile after capture",
    )
    parser.add_argument(
        "--stop-daemon",
        action="store_true",
        help="stop any existing wx-cli daemon before and after capture",
    )
    parser.add_argument(
        "--include-readonly",
        action="store_true",
        help="also scan read-only memory pages; slower",
    )
    parser.add_argument(
        "--include-bare-hex",
        action="store_true",
        help="also scan standalone 64-hex strings; noisy but HMAC-validated",
    )
    parser.add_argument(
        "--pid",
        type=int,
        action="append",
        help="scan a specific Weixin.exe PID; repeat to limit scanning to known main processes",
    )
    return parser.parse_args()


def main() -> int:
    configure_utf8_stdio()
    args = parse_args()

    if os.name != "nt":
        print(json.dumps({"ok": False, "error": "Windows only"}, ensure_ascii=False))
        return 2

    if args.list:
        print(
            json.dumps(
                {"profiles": discover_profiles()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not args.account:
        print(json.dumps({"ok": False, "error": "account is required unless --list is used"}, ensure_ascii=False))
        return 2

    try:
        account = resolve_account(args.account)
    except ValueError as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2

    profile_dir = profile_dir_for(account, args.profile_dir or "")
    db_dir = Path(args.db_dir).expanduser().resolve() if args.db_dir else configured_db_dir(profile_dir)
    if db_dir is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "account": account,
                    "error": "--db-dir is required when the profile has no configured db_dir",
                },
                ensure_ascii=False,
            )
        )
        return 2
    out_path = (
        Path(args.out).expanduser()
        if args.out
        else REPO_ROOT / "out" / f"capture-wx-profile-{account}.json"
    )
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_dir.is_dir():
        print(
            json.dumps(
                {"ok": False, "account": args.account, "error": "db_dir not found", "db_dir": str(db_dir)},
                ensure_ascii=False,
            )
        )
        return 1

    if args.stop_daemon:
        stop_daemon_if_running()

    cmd = [
        sys.executable,
        str(SCANNER),
        "--db-dir",
        str(db_dir),
        "--out",
        str(out_path),
        "--wx-cli-home",
        str(profile_dir),
        "--write-wx-cli",
    ]
    if args.include_readonly:
        cmd.append("--include-readonly")
    if args.include_bare_hex:
        cmd.append("--include-bare-hex")
    for pid in args.pid or []:
        cmd.extend(["--pid", str(pid)])

    cp = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    if args.stop_daemon:
        stop_daemon_if_running()

    if cp.returncode != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "account": account,
                    "returncode": cp.returncode,
                    "status_path": str(out_path),
                    "stderr_tail": cp.stderr[-1000:],
                },
                ensure_ascii=False,
            )
        )
        return cp.returncode

    status = read_json(out_path)
    config_path = profile_dir / "config.json"
    keys_path = profile_dir / "all_keys.json"
    key_count = 0
    if keys_path.exists():
        keys = json.loads(keys_path.read_text(encoding="utf-8"))
        key_count = len(keys) if isinstance(keys, dict) else 0

    if args.activate:
        try:
            pointer_text = str(profile_dir.relative_to(REPO_ROOT))
        except ValueError:
            pointer_text = str(profile_dir)
        PROFILE_POINTER.write_text(pointer_text + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "account": account,
                "db_dir": str(db_dir),
                "profile_dir": str(profile_dir),
                "config_path": str(config_path),
                "keys_path": str(keys_path),
                "status_path": str(out_path),
                "validated_hit_count": status.get("validated_hit_count"),
                "written_key_count": status.get("written_key_count"),
                "profile_key_count": key_count,
                "activated": bool(args.activate),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
