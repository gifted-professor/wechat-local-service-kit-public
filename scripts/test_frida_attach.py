#!/usr/bin/env python3

import argparse
import json

from frida_support import format_preflight_report, open_developer_tools_settings, run_frida_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose local Frida attach permissions on macOS")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--open-settings", action="store_true", help="open macOS Developer Tools privacy settings")
    args = parser.parse_args()

    if args.open_settings:
        open_developer_tools_settings()

    preflight = run_frida_preflight()
    if args.json:
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
    else:
        print(format_preflight_report(preflight))

    tests = preflight.get("tests", {})
    if tests.get("attach_existing", {}).get("ok") or tests.get("spawn_attach", {}).get("ok"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
