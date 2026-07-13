#!/usr/bin/env python3

import argparse
import json
import subprocess
from datetime import datetime, timezone
from typing import Any

from wechat_privacy import redact_obj, redact_text
from wx_cli_adapter import WxCliError, _find_wx, describe_command_policy, get_daemon_status
from wx_cli_profile import active_profile_summary


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_version(wx_path: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [wx_path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": redact_text(str(exc))}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": redact_text((completed.stdout or "").strip()),
        "stderr": redact_text((completed.stderr or "").strip()),
    }


def build_status(*, include_daemon: bool = True) -> dict[str, Any]:
    wx_path = _find_wx()
    daemon_status: dict[str, Any] | None = None
    if include_daemon:
        try:
            daemon_status = {"ok": True, "status": get_daemon_status()}
        except WxCliError as exc:
            daemon_status = {"ok": False, "error": exc.to_dict()}
        except Exception as exc:
            daemon_status = {"ok": False, "error": redact_text(str(exc))}

    payload = {
        "schema_version": "wechat_tool_status_v1",
        "generated_at": utc_now_iso(),
        "wx_cli": {
            "installed": bool(wx_path),
            "path": redact_text(wx_path or ""),
            "version": _run_version(wx_path) if wx_path else None,
            "command_policy": describe_command_policy(),
        },
        "profile": active_profile_summary(redacted=True),
        "daemon": daemon_status,
        "privacy": {
            "redaction": "enabled",
            "key_material": "never printed; only key_count is reported",
            "default_policy": "read-only; write/export/init/UI actions are blocked unless explicit code paths opt in",
        },
    }
    return redact_obj(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show redacted wx-cli/wechat-local-service-kit readiness status")
    parser.add_argument("--skip-daemon", action="store_true", help="do not call wx daemon status")
    parser.add_argument("--compact", action="store_true", help="print compact JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status = build_status(include_daemon=not args.skip_daemon)
    print(json.dumps(status, ensure_ascii=False, indent=None if args.compact else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
