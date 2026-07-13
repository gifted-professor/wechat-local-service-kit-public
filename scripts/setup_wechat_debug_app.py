#!/usr/bin/env python3

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def default_source() -> Path:
    return Path("/Applications/WeChat.app")


def default_dest() -> Path:
    return Path.home() / ".wx-debug" / "WeChat-debug.app"


def run_command(args: list[str], *, print_only: bool = False) -> None:
    print("+ " + " ".join(args), flush=True)
    if print_only:
        return
    completed = subprocess.run(args, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(args)}")


def executable_path(app_path: Path) -> Path:
    return app_path / "Contents" / "MacOS" / "WeChat"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an ad-hoc signed WeChat debug copy for Frida key capture")
    parser.add_argument("--source", default=str(default_source()), help="source WeChat.app path")
    parser.add_argument("--dest", default=str(default_dest()), help="destination debug WeChat.app path")
    parser.add_argument("--recreate", action="store_true", help="remove and recreate the destination app")
    parser.add_argument("--print-only", action="store_true", help="print commands without changing files")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()

    if not source.exists():
        print(f"[ERROR] source app not found: {source}", file=sys.stderr)
        return 1
    if not executable_path(source).exists():
        print(f"[ERROR] source executable not found: {executable_path(source)}", file=sys.stderr)
        return 1

    if dest.exists() and not args.recreate:
        if executable_path(dest).exists():
            print(f"Debug app already exists: {dest}")
            print(f"Executable: {executable_path(dest)}")
            print("Pass --recreate to rebuild it.")
            return 0
        print(f"[ERROR] destination exists but does not look like WeChat.app: {dest}", file=sys.stderr)
        return 1

    if dest.exists():
        print(f"Removing existing debug app: {dest}", flush=True)
        if not args.print_only:
            shutil.rmtree(dest)

    if not args.print_only:
        dest.parent.mkdir(parents=True, exist_ok=True)

    run_command(["ditto", str(source), str(dest)], print_only=args.print_only)
    run_command(["xattr", "-cr", str(dest)], print_only=args.print_only)
    run_command(["codesign", "--force", "--deep", "--sign", "-", str(dest)], print_only=args.print_only)

    print(f"Debug app ready: {dest}")
    print(f"Executable: {executable_path(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
