#!/usr/bin/env python3

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from wechat_common import _ensure_dir, _write_json
from wx_cli_adapter import check_wx_cli_ready


REPO_ROOT = Path(__file__).resolve().parents[1]


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_pid(path: Path) -> Optional[int]:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def process_exists(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_command(pid: Optional[int]) -> str:
    if not pid:
        return ""
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
    )
    return (completed.stdout or "").strip()


def managed_process(pid_path: Path, expected_script: str) -> dict[str, Any]:
    pid = read_pid(pid_path)
    command = process_command(pid)
    alive = process_exists(pid)
    managed = bool(alive and expected_script in command)
    return {
        "pid": pid,
        "alive": alive,
        "managed": managed,
        "command": command,
        "pid_path": str(pid_path),
    }


def effective_status(info: dict[str, Any], state: Optional[dict[str, Any]]) -> str:
    state_status = str((state or {}).get("status") or "")
    if info["managed"]:
        return "running"
    if info["alive"]:
        return "pid_alive_unmanaged"
    if state_status in {"running", "starting"}:
        return "stale_state_process_not_running"
    return "stopped"


def terminate_pid(pid: int, *, timeout: float = 5.0) -> str:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already_stopped"
    deadline = time.time() + max(timeout, 0.5)
    while time.time() < deadline:
        if not process_exists(pid):
            return "stopped"
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "stopped"
    return "killed"


def tail_text(path: Path, max_chars: int = 4000) -> str:
    if not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-max_chars:].decode("utf-8", errors="replace")


def paths(args: argparse.Namespace) -> dict[str, Path]:
    out_root = Path(args.out_root).expanduser()
    return {
        "monitor_root": out_root / "monitor",
        "draft_root": out_root / "drafts",
        "monitor_pid": out_root / "monitor" / "monitor.pid",
        "worker_pid": out_root / "drafts" / "worker.pid",
        "monitor_log": out_root / "monitor" / "monitor.log",
        "worker_log": out_root / "drafts" / "worker.log",
        "monitor_state": out_root / "monitor" / "state.json",
        "worker_state": out_root / "drafts" / "worker-state.json",
        "events": out_root / "monitor" / "events.jsonl",
        "manifest": Path(args.manifest).expanduser(),
        "contacts_json": Path(args.contacts_json).expanduser(),
        "memory_root": Path(args.memory_root).expanduser(),
        "service_knowledge_root": Path(args.service_knowledge_root).expanduser(),
        "personal_style_root": Path(args.personal_style_root).expanduser() if args.personal_style_root else None,
    }


def service_status(args: argparse.Namespace) -> dict[str, Any]:
    p = paths(args)
    monitor = managed_process(p["monitor_pid"], "monitor_reply_candidates.py")
    worker = managed_process(p["worker_pid"], "draft_reply_worker.py")
    monitor_state = read_json(p["monitor_state"])
    worker_state = read_json(p["worker_state"])
    return {
        "schema_version": "wechat_reply_service_status_v1",
        "updated_at": utc_now_iso(),
        "monitor": {
            **monitor,
            "effective_status": effective_status(monitor, monitor_state),
            "state": monitor_state,
            "log_tail": tail_text(p["monitor_log"]) if args.verbose else "",
        },
        "worker": {
            **worker,
            "effective_status": effective_status(worker, worker_state),
            "state": worker_state,
            "log_tail": tail_text(p["worker_log"]) if args.verbose else "",
        },
        "paths": {key: str(value) if value else "" for key, value in p.items()},
    }


def prerequisite_report(args: argparse.Namespace, *, check_wx: bool) -> dict[str, Any]:
    p = paths(args)
    report = {
        "schema_version": "wechat_reply_service_doctor_v1",
        "updated_at": utc_now_iso(),
        "repo_root": str(REPO_ROOT),
        "manifest_exists": p["manifest"].exists(),
        "contacts_json_exists": p["contacts_json"].exists(),
        "memory_root_exists": p["memory_root"].exists(),
        "service_knowledge_root_exists": p["service_knowledge_root"].exists(),
        "personal_style_root_exists": bool(args.personal_style_root and p["personal_style_root"].exists()),
        "openai_base_url_present": bool(os.environ.get("OPENAI_BASE_URL")),
        "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        "manual_permissions": [
            "WeChat must be installed and logged in on this Mac.",
            "The command host, usually Terminal or iTerm, must have required macOS permissions.",
            "Accessibility permission is required for draft-only paste into WeChat.",
            "Actual sending remains a human action and is not performed by this service.",
        ],
    }
    if check_wx:
        report["wx_cli"] = check_wx_cli_ready()
    return report


def require_start_prerequisites(args: argparse.Namespace) -> None:
    report = prerequisite_report(args, check_wx=args.check_wx_cli)
    missing = []
    if not report["manifest_exists"] and not args.allow_all_private:
        missing.append(f"missing contact wiki manifest: {args.manifest}")
    if not args.no_worker and not report["openai_api_key_present"]:
        missing.append("missing OPENAI_API_KEY for draft worker")
    if args.personal_style_mode != "off" and not report["personal_style_root_exists"]:
        missing.append(f"missing personal style root: {args.personal_style_root}")
    if args.check_wx_cli and not report.get("wx_cli", {}).get("ready"):
        missing.append(f"wx-cli is not ready: {json.dumps(report.get('wx_cli'), ensure_ascii=False)}")
    if missing:
        raise RuntimeError("; ".join(missing))


def start_process(command: list[str], *, pid_path: Path, log_path: Path, cwd: Path = REPO_ROOT) -> int:
    _ensure_dir(pid_path.parent)
    _ensure_dir(log_path.parent)
    log_file = log_path.open("ab")
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return process.pid


def monitor_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        "scripts/monitor_reply_candidates.py",
        "--manifest",
        args.manifest,
        "--duration",
        str(args.duration),
        "--interval",
        str(args.monitor_interval),
        "--history-limit",
        str(args.history_limit),
        "--new-message-limit",
        str(args.new_message_limit),
        "--message-fresh-within-seconds",
        str(args.fresh_within_seconds),
        "--out-root",
        str(paths(args)["monitor_root"]),
    ]
    if args.allow_all_private:
        command.append("--allow-all-private")
    return command


def worker_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        "scripts/draft_reply_worker.py",
        "--events",
        str(paths(args)["events"]),
        "--manifest",
        args.manifest,
        "--contacts-json",
        args.contacts_json,
        "--out-root",
        str(paths(args)["draft_root"]),
        "--memory-root",
        args.memory_root,
        "--memory-mode",
        args.memory_mode,
        "--memory-use-policy",
        args.memory_use_policy,
        "--service-knowledge-root",
        args.service_knowledge_root,
        "--service-knowledge-mode",
        args.service_knowledge_mode,
        "--service-knowledge-policy",
        args.service_knowledge_policy,
        "--personal-style-root",
        args.personal_style_root,
        "--personal-style-mode",
        args.personal_style_mode,
        "--personal-style-max-examples",
        str(args.personal_style_max_examples),
        "--context-messages",
        str(args.context_messages),
        "--fresh-within-seconds",
        str(args.fresh_within_seconds),
        "--interval",
        str(args.worker_interval),
        "--duration",
        str(args.duration),
    ]


def start_service(args: argparse.Namespace) -> dict[str, Any]:
    require_start_prerequisites(args)
    p = paths(args)
    result: dict[str, Any] = {"updated_at": utc_now_iso(), "started": {}, "already_running": {}}

    monitor = managed_process(p["monitor_pid"], "monitor_reply_candidates.py")
    if monitor["managed"] and not args.force:
        result["already_running"]["monitor"] = monitor
    else:
        if monitor["alive"] and not monitor["managed"] and not args.force:
            raise RuntimeError(f"monitor pid exists but is not managed by this service: {monitor}")
        if monitor["alive"] and args.force and monitor["pid"]:
            terminate_pid(int(monitor["pid"]))
        result["started"]["monitor_pid"] = start_process(
            monitor_command(args),
            pid_path=p["monitor_pid"],
            log_path=p["monitor_log"],
        )

    if not args.no_worker:
        worker = managed_process(p["worker_pid"], "draft_reply_worker.py")
        if worker["managed"] and not args.force:
            result["already_running"]["worker"] = worker
        else:
            if worker["alive"] and not worker["managed"] and not args.force:
                raise RuntimeError(f"worker pid exists but is not managed by this service: {worker}")
            if worker["alive"] and args.force and worker["pid"]:
                terminate_pid(int(worker["pid"]))
            result["started"]["worker_pid"] = start_process(
                worker_command(args),
                pid_path=p["worker_pid"],
                log_path=p["worker_log"],
            )

    _write_json(Path(args.service_state).expanduser(), {"schema_version": "wechat_reply_service_state_v1", **result})
    return result


def stop_service(args: argparse.Namespace) -> dict[str, Any]:
    p = paths(args)
    result: dict[str, Any] = {"updated_at": utc_now_iso(), "stopped": {}, "skipped": {}}
    for name, pid_key, state_key, expected in [
        ("worker", "worker_pid", "worker_state", "draft_reply_worker.py"),
        ("monitor", "monitor_pid", "monitor_state", "monitor_reply_candidates.py"),
    ]:
        info = managed_process(p[pid_key], expected)
        if not info["alive"]:
            result["skipped"][name] = "not_running"
            p[pid_key].unlink(missing_ok=True)
            mark_process_stopped(p[state_key])
            continue
        if not info["managed"] and not args.force:
            result["skipped"][name] = "pid_not_managed"
            continue
        result["stopped"][name] = terminate_pid(int(info["pid"]))
        p[pid_key].unlink(missing_ok=True)
        mark_process_stopped(p[state_key])
    return result


def mark_process_stopped(state_path: Path) -> None:
    state = read_json(state_path) or {}
    state.update(
        {
            "status": "stopped",
            "updated_at": utc_now_iso(),
            "stopped_at": utc_now_iso(),
            "process_alive": False,
            "sent": bool(state.get("sent")),
        }
    )
    _write_json(state_path, state)


def restart_service(args: argparse.Namespace) -> dict[str, Any]:
    stopped = stop_service(args)
    time.sleep(max(args.restart_delay, 0.5))
    started = start_service(args)
    return {"updated_at": utc_now_iso(), "stopped": stopped, "started": started}


def print_payload(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the local WeChat reply monitor and draft-only worker.")
    parser.add_argument("command", choices=("doctor", "start", "stop", "restart", "status"))
    parser.add_argument("--manifest", default="out/contact-wiki/manifest.json")
    parser.add_argument("--contacts-json", default="out/chat-export/export/contacts.json")
    parser.add_argument("--out-root", default="out/dry-run-replies")
    parser.add_argument("--memory-root", default="out/customer-memory")
    parser.add_argument("--service-knowledge-root", default=".project-wiki")
    parser.add_argument("--personal-style-root", default="")
    parser.add_argument("--service-state", default="out/dry-run-replies/service-state.json")
    parser.add_argument("--duration", type=float, default=86400.0)
    parser.add_argument("--monitor-interval", type=float, default=5.0)
    parser.add_argument("--worker-interval", type=float, default=8.0)
    parser.add_argument("--history-limit", type=int, default=12)
    parser.add_argument("--new-message-limit", type=int, default=100)
    parser.add_argument("--fresh-within-seconds", type=float, default=1800.0)
    parser.add_argument("--context-messages", type=int, default=16)
    parser.add_argument("--memory-mode", choices=("off", "draft-only", "shadow"), default="draft-only")
    parser.add_argument("--memory-use-policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--service-knowledge-mode", choices=("off", "shadow", "draft-only"), default="draft-only")
    parser.add_argument("--service-knowledge-policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--personal-style-mode", choices=("off", "shadow", "draft-only"), default="off")
    parser.add_argument("--personal-style-max-examples", type=int, default=4)
    parser.add_argument("--allow-all-private", action="store_true", help="Monitor all private chats even without a contact wiki manifest.")
    parser.add_argument("--no-worker", action="store_true", help="Only start the candidate monitor.")
    parser.add_argument("--check-wx-cli", action="store_true", help="Run wx-cli readiness check before start.")
    parser.add_argument("--force", action="store_true", help="Replace already-running managed processes.")
    parser.add_argument("--restart-delay", type=float, default=1.0)
    parser.add_argument("--verbose", action="store_true", help="Include log tails in status output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "doctor":
            print_payload(prerequisite_report(args, check_wx=True))
        elif args.command == "status":
            print_payload(service_status(args))
        elif args.command == "start":
            print_payload(start_service(args))
        elif args.command == "stop":
            print_payload(stop_service(args))
        elif args.command == "restart":
            print_payload(restart_service(args))
        return 0
    except Exception as exc:
        error = {"status": "error", "updated_at": utc_now_iso(), "error": str(exc)}
        print_payload(error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
