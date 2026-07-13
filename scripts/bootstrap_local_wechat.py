#!/usr/bin/env python3

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XWECHAT_FILES = (
    Path.home()
    / "Library"
    / "Containers"
    / "com.tencent.xinWeChat"
    / "Data"
    / "Documents"
    / "xwechat_files"
)


def debug_app_executable(debug_app: Optional[str] = None) -> Path:
    app = Path(debug_app).expanduser() if debug_app else Path.home() / ".wx-debug" / "WeChat-debug.app"
    return app / "Contents" / "MacOS" / "WeChat"


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def db_storage_ok(path: Path) -> bool:
    return (
        (path / "contact" / "contact.db").exists()
        and (path / "session" / "session.db").exists()
        and (path / "message").exists()
    )


def discover_db_storage_roots(base: Path = DEFAULT_XWECHAT_FILES) -> list[Path]:
    roots: list[Path] = []
    if not base.exists():
        return roots
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        db_storage = child / "db_storage"
        if db_storage_ok(db_storage):
            roots.append(db_storage)
    return roots


def resolve_wechat_root(value: str, *, yes: bool = False) -> Path:
    if value != "auto":
        root = Path(value).expanduser().resolve()
        if db_storage_ok(root):
            return root
        nested = root / "db_storage"
        if db_storage_ok(nested):
            return nested
        raise FileNotFoundError(f"could not locate db_storage under: {root}")

    roots = discover_db_storage_roots()
    if not roots:
        raise FileNotFoundError(f"no local WeChat db_storage roots found under: {DEFAULT_XWECHAT_FILES}")
    if len(roots) == 1:
        return roots[0].resolve()
    if yes or not sys.stdin.isatty():
        preview = "\n".join(f"- {root}" for root in roots)
        raise RuntimeError(f"multiple WeChat accounts found; pass --wechat-root explicitly:\n{preview}")

    print("Multiple WeChat accounts found:")
    for index, root in enumerate(roots, start=1):
        print(f"{index}. {root}")
    choice = input("Select account number: ").strip()
    try:
        selected = roots[int(choice) - 1]
    except Exception as exc:
        raise RuntimeError(f"invalid account selection: {choice}") from exc
    return selected.resolve()


def run_command(args: list[str], *, cwd: Path = REPO_ROOT) -> None:
    print("+ " + " ".join(args), flush=True)
    completed = subprocess.run(args, cwd=str(cwd), check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(args)}")


def confirm(prompt: str, *, yes: bool = False) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        return False
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def print_section(title: str) -> None:
    print("")
    print(f"== {title} ==")


def run_doctor(*, skip_preflight: bool = False) -> dict[str, object]:
    print_section("Doctor")
    checks: dict[str, object] = {
        "python": sys.version.split()[0],
        "repo_root": str(REPO_ROOT),
        "wechat_app": Path("/Applications/WeChat.app").exists(),
        "debug_app_executable": str(debug_app_executable()),
        "debug_app_ready": debug_app_executable().exists(),
        "frida_module": module_available("frida"),
        "crypto_module": module_available("Crypto.Cipher.AES"),
        "local_accounts": [str(path) for path in discover_db_storage_roots()],
    }
    print(json.dumps(checks, ensure_ascii=False, indent=2))

    if not checks["frida_module"]:
        raise RuntimeError("missing Python module: frida. Install with: pip3 install frida frida-tools")
    if not checks["crypto_module"]:
        raise RuntimeError("missing Python module: pycryptodome. Install with: pip3 install pycryptodome")
    if not checks["wechat_app"]:
        raise RuntimeError("WeChat.app not found under /Applications/WeChat.app")

    if not skip_preflight:
        from frida_support import format_preflight_report, run_frida_preflight

        preflight = run_frida_preflight()
        print(format_preflight_report(preflight))
        tests = preflight.get("tests", {})
        if not tests.get("spawn_attach", {}).get("ok"):
            raise RuntimeError("Frida spawn preflight failed. Follow the next-step hints above, then rerun bootstrap.")

    return checks


def ensure_debug_app(args: argparse.Namespace) -> None:
    print_section("Debug WeChat App")
    exe = debug_app_executable(args.debug_app)
    if exe.exists() and not args.recreate_debug_app:
        print(f"Debug app ready: {exe}")
        return
    if not confirm("Create/recreate the local debug WeChat.app copy now?", yes=args.yes):
        raise RuntimeError("debug app is missing; rerun with --yes or run scripts/setup_wechat_debug_app.py")
    setup_script = REPO_ROOT / "scripts" / "setup_wechat_debug_app.py"
    command = [sys.executable, str(setup_script)]
    if args.debug_app:
        command.extend(["--dest", str(Path(args.debug_app).expanduser())])
    if args.recreate_debug_app:
        command.append("--recreate")
    run_command(command)


def capture_keys(args: argparse.Namespace) -> None:
    print_section("Key Capture")
    log_path = Path(args.frida_log).expanduser()
    if log_path.exists() and not args.refresh_keys:
        print(f"Reusing existing Frida log: {log_path}")
        print("Pass --refresh-keys to recapture.")
        return
    exe = debug_app_executable(args.debug_app)
    if not exe.exists():
        raise FileNotFoundError(f"debug app executable not found: {exe}")
    print("The debug WeChat app will launch. Log in, then open one or two chats so WeChat loads message databases.")
    if not confirm("Start Frida key capture now?", yes=args.yes):
        raise RuntimeError("key capture skipped by operator")
    run_command(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "grab_wechat_key.py"),
            "--app",
            str(exe),
            "--log",
            str(log_path),
            "--wait",
            str(args.wait),
        ]
    )
    if not log_path.exists() or log_path.stat().st_size == 0:
        raise RuntimeError(f"Frida log is missing or empty after capture: {log_path}")


def export_chat(args: argparse.Namespace, db_storage_root: Path) -> None:
    print_section("Chat Export")
    run_command(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "export_chat_history.py"),
            "--wechat-root",
            str(db_storage_root),
            "--output",
            str(Path(args.output).expanduser()),
            "--frida-log",
            str(Path(args.frida_log).expanduser()),
        ]
    )


def build_memory(args: argparse.Namespace) -> None:
    print_section("Customer Memory")
    run_command(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "build_customer_memory.py"),
            "--export-root",
            str(Path(args.output).expanduser() / "export"),
            "--out-root",
            str(Path(args.memory_root).expanduser()),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Guided bootstrap for local macOS WeChat export and customer memory")
    parser.add_argument("--run", action="store_true", help="run setup, key capture, export, and optional memory build")
    parser.add_argument("--doctor-only", action="store_true", help="only run environment checks")
    parser.add_argument("--with-memory", action="store_true", help="build customer memory after chat export")
    parser.add_argument("--wechat-root", default="auto", help="db_storage path, account root, or 'auto'")
    parser.add_argument("--output", default="out/chat-export", help="chat export output root")
    parser.add_argument("--memory-root", default="out/customer-memory", help="customer memory output root")
    parser.add_argument("--frida-log", default="/tmp/wechat_frida_keys.log", help="Frida key log path")
    parser.add_argument("--debug-app", help="debug WeChat.app path; defaults to ~/.wx-debug/WeChat-debug.app")
    parser.add_argument("--wait", type=int, default=240, help="seconds to wait during Frida key capture")
    parser.add_argument("--yes", action="store_true", help="answer yes to bootstrap prompts")
    parser.add_argument("--skip-preflight", action="store_true", help="skip Frida preflight checks")
    parser.add_argument("--skip-key-capture", action="store_true", help="reuse existing --frida-log")
    parser.add_argument("--refresh-keys", action="store_true", help="recapture keys even if --frida-log already exists")
    parser.add_argument("--skip-export", action="store_true", help="skip chat export step")
    parser.add_argument("--recreate-debug-app", action="store_true", help="recreate debug WeChat.app even if it exists")
    args = parser.parse_args()

    try:
        run_doctor(skip_preflight=args.skip_preflight)
        if args.doctor_only or not args.run:
            print("")
            print("Doctor finished. To run the full local bootstrap:")
            print("python3 scripts/bootstrap_local_wechat.py --run --with-memory")
            return 0

        ensure_debug_app(args)
        if not args.skip_key_capture:
            capture_keys(args)
        else:
            print_section("Key Capture")
            print(f"Skipping key capture and reusing: {Path(args.frida_log).expanduser()}")

        db_storage_root = resolve_wechat_root(args.wechat_root, yes=args.yes)
        print_section("Selected Account")
        print(str(db_storage_root))

        if not args.skip_export:
            export_chat(args, db_storage_root)
        else:
            print_section("Chat Export")
            print("Skipping chat export.")

        if args.with_memory:
            build_memory(args)

        print_section("Done")
        print(f"Chat export: {Path(args.output).expanduser() / 'export'}")
        if args.with_memory:
            print(f"Customer memory: {Path(args.memory_root).expanduser()}")
        print("Next useful commands:")
        print(f"python3 scripts/query_customer_memory.py --memory-root {args.memory_root} --query '<contact>' --limit 3")
        print(f"python3 scripts/render_customer_pages.py --memory-root {args.memory_root} --conversation '<contact>' --limit 1")
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
