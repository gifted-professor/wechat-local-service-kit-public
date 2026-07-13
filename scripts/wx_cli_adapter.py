#!/usr/bin/env python3

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

from wechat_contact_policy import notification_muted_from_chat_room_notify, notification_state_from_chat_room_notify
from wechat_privacy import redact_obj, redact_text, redacted_error_details
from wx_cli_profile import load_profile_config, profile_db_dir, resolve_profile_dir


WX_NOT_INSTALLED = "wx_not_installed"
COMMAND_TIMEOUT = "command_timeout"
JSON_PARSE_FAILED = "json_parse_failed"
COMMAND_FAILED = "command_failed"
CONVERSATION_NOT_FOUND = "conversation_not_found"
CONVERSATION_AMBIGUOUS = "conversation_ambiguous"
WX_PROFILE_INVALID = "wx_profile_invalid"
WX_DAEMON_MISMATCH = "wx_daemon_mismatch"
COMMAND_BLOCKED = "command_blocked"
UNKNOWN_COMMAND = "unknown_command"

ERROR_CODES = {
    WX_NOT_INSTALLED,
    COMMAND_TIMEOUT,
    JSON_PARSE_FAILED,
    COMMAND_FAILED,
    CONVERSATION_NOT_FOUND,
    CONVERSATION_AMBIGUOUS,
    WX_PROFILE_INVALID,
    WX_DAEMON_MISMATCH,
    COMMAND_BLOCKED,
    UNKNOWN_COMMAND,
}

RISK_READ_ONLY = "read_only"
RISK_CURSOR_SENSITIVE = "cursor_sensitive"
RISK_LOCAL_WRITE = "local_write"
RISK_BLOCKED = "blocked_by_default"
RISK_UNKNOWN = "unknown"

READ_ONLY_COMMANDS = {
    "sessions",
    "history",
    "search",
    "contacts",
    "unread",
    "members",
    "stats",
    "favorites",
    "sns-notifications",
    "sns-feed",
    "sns-search",
}
CURSOR_SENSITIVE_COMMANDS = {"new-messages"}
LOCAL_WRITE_COMMANDS = {"export"}
BLOCKED_COMMANDS = {"init"}
READ_ONLY_DAEMON_SUBCOMMANDS = {"status"}
LOCAL_WRITE_DAEMON_SUBCOMMANDS = {"logs"}
BLOCKED_DAEMON_SUBCOMMANDS = {"stop"}

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_WX_BIN = Path(__file__).resolve().parents[1] / ".wx-cli-tools" / "node_modules" / ".bin" / "wx"
WX_DAEMON_DIR = Path.home() / ".wx-cli"
WX_DAEMON_PID = WX_DAEMON_DIR / "daemon.pid"
WX_DAEMON_LOG = WX_DAEMON_DIR / "daemon.log"


class WxCliError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self, *, redacted: bool = True) -> dict[str, Any]:
        payload = {"code": self.code, "message": redact_text(self.message) if redacted else self.message}
        if self.details:
            payload["details"] = redacted_error_details(self.details) if redacted else self.details
        return payload


def _command_name(args: list[str]) -> str:
    return str(args[0]).strip() if args else ""


def classify_wx_command(args: list[str]) -> str:
    command = _command_name(args)
    if not command:
        return RISK_UNKNOWN
    if command in BLOCKED_COMMANDS:
        return RISK_BLOCKED
    if command in LOCAL_WRITE_COMMANDS:
        return RISK_LOCAL_WRITE
    if command in CURSOR_SENSITIVE_COMMANDS:
        return RISK_CURSOR_SENSITIVE
    if command in READ_ONLY_COMMANDS:
        return RISK_READ_ONLY
    if command == "daemon":
        subcommand = str(args[1]).strip() if len(args) > 1 else ""
        if subcommand in READ_ONLY_DAEMON_SUBCOMMANDS:
            return RISK_READ_ONLY
        if subcommand in LOCAL_WRITE_DAEMON_SUBCOMMANDS:
            return RISK_LOCAL_WRITE
        if subcommand in BLOCKED_DAEMON_SUBCOMMANDS:
            return RISK_BLOCKED
    return RISK_UNKNOWN


def describe_command_policy() -> dict[str, Any]:
    return {
        "default_mode": "read_only",
        "read_only": sorted(READ_ONLY_COMMANDS) + ["daemon status"],
        "cursor_sensitive": sorted(CURSOR_SENSITIVE_COMMANDS),
        "local_write": sorted(LOCAL_WRITE_COMMANDS) + ["daemon logs"],
        "blocked_by_default": sorted(BLOCKED_COMMANDS) + ["daemon stop"],
        "unknown_commands": "blocked_by_default",
    }


def _assert_command_allowed(
    args: list[str],
    *,
    allow_cursor_sensitive: bool = True,
    allow_local_write: bool = False,
    allow_blocked: bool = False,
) -> str:
    risk = classify_wx_command(args)
    if risk == RISK_READ_ONLY:
        return risk
    if risk == RISK_CURSOR_SENSITIVE and allow_cursor_sensitive:
        return risk
    if risk == RISK_LOCAL_WRITE and allow_local_write:
        return risk
    if risk == RISK_BLOCKED and allow_blocked:
        return risk
    raise WxCliError(
        COMMAND_BLOCKED if risk != RISK_UNKNOWN else UNKNOWN_COMMAND,
        "wx command is not allowed by the default read-only policy",
        {"args": args, "risk": risk, "policy": describe_command_policy()},
    )


def _run_wx_json(
    args: list[str],
    timeout: float = 10.0,
    *,
    allow_cursor_sensitive: bool = True,
    allow_local_write: bool = False,
    allow_blocked: bool = False,
    allow_daemon_switch: bool = False,
) -> Any:
    _assert_command_allowed(
        args,
        allow_cursor_sensitive=allow_cursor_sensitive,
        allow_local_write=allow_local_write,
        allow_blocked=allow_blocked,
    )
    wx_path = _find_wx()
    if not wx_path:
        raise WxCliError(WX_NOT_INSTALLED, "wx-cli is not installed or not on PATH")

    profile_dir = _resolve_profile_dir_or_error()
    _ensure_profile_daemon(wx_path, profile_dir, allow_stop=allow_daemon_switch)

    command = [wx_path, *args]
    if "--json" not in command:
        command.append("--json")

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(profile_dir) if profile_dir else None,
            env=_wx_env(profile_dir),
        )
    except subprocess.TimeoutExpired as exc:
        raise WxCliError(
            COMMAND_TIMEOUT,
            f"wx command timed out after {timeout:g}s",
            {"args": args, "timeout": timeout},
        ) from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise WxCliError(
            COMMAND_FAILED,
            "wx command failed",
            {"args": args, "returncode": completed.returncode, "stdout": stdout, "stderr": stderr},
        )
    if not stdout:
        return None

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise WxCliError(
            JSON_PARSE_FAILED,
            "wx command did not return valid JSON",
            {"args": args, "stdout": stdout[:2000], "stderr": stderr},
        ) from exc


def _run_wx_text(
    args: list[str],
    timeout: float = 10.0,
    *,
    allow_cursor_sensitive: bool = True,
    allow_local_write: bool = False,
    allow_blocked: bool = False,
    allow_daemon_switch: bool = False,
) -> str:
    _assert_command_allowed(
        args,
        allow_cursor_sensitive=allow_cursor_sensitive,
        allow_local_write=allow_local_write,
        allow_blocked=allow_blocked,
    )
    wx_path = _find_wx()
    if not wx_path:
        raise WxCliError(WX_NOT_INSTALLED, "wx-cli is not installed or not on PATH")

    profile_dir = _resolve_profile_dir_or_error()
    _ensure_profile_daemon(wx_path, profile_dir, allow_stop=allow_daemon_switch)

    try:
        completed = subprocess.run(
            [wx_path, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(profile_dir) if profile_dir else None,
            env=_wx_env(profile_dir),
        )
    except subprocess.TimeoutExpired as exc:
        raise WxCliError(
            COMMAND_TIMEOUT,
            f"wx command timed out after {timeout:g}s",
            {"args": args, "timeout": timeout},
        ) from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise WxCliError(
            COMMAND_FAILED,
            "wx command failed",
            {"args": args, "returncode": completed.returncode, "stdout": stdout, "stderr": stderr},
        )
    return stdout


def _find_wx() -> Optional[str]:
    if PROJECT_WX_BIN.exists():
        return str(PROJECT_WX_BIN)
    return shutil.which("wx")


def _wx_env(profile_dir: Optional[Path]) -> dict[str, str]:
    env = os.environ.copy()
    if profile_dir:
        env["WX_CLI_CONFIG_DIR"] = str(profile_dir.resolve())
    return env


def _resolve_profile_dir_or_error() -> Optional[Path]:
    profile_dir = resolve_profile_dir()
    if not profile_dir:
        return None
    try:
        load_profile_config(profile_dir)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise WxCliError(
            WX_PROFILE_INVALID,
            "wx-cli profile is invalid",
            {"profile_dir": str(profile_dir), "error": str(exc)},
        ) from exc
    return profile_dir


def _read_daemon_pid() -> Optional[int]:
    try:
        text = WX_DAEMON_PID.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def _process_exists(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _active_daemon_db_dir() -> str:
    if not WX_DAEMON_LOG.exists():
        return ""
    try:
        text = WX_DAEMON_LOG.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in reversed(text.splitlines()):
        marker = "DB_DIR:"
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return ""


def _ensure_profile_daemon(wx_path: str, profile_dir: Optional[Path], *, allow_stop: bool = False) -> None:
    if not profile_dir:
        return

    try:
        desired_db_dir = profile_db_dir(profile_dir)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise WxCliError(
            WX_PROFILE_INVALID,
            "wx-cli profile is missing a usable db_dir",
            {"profile_dir": str(profile_dir), "error": str(exc)},
        ) from exc

    pid = _read_daemon_pid()
    if not _process_exists(pid):
        return

    active_db_dir = _active_daemon_db_dir()
    if not active_db_dir:
        if allow_stop:
            _stop_daemon_direct(wx_path, profile_dir)
            return
        raise WxCliError(
            WX_DAEMON_MISMATCH,
            "wx daemon is running but its active db_dir could not be verified",
            {"profile_dir": str(profile_dir), "daemon_pid": pid},
        )

    try:
        active_path = Path(active_db_dir).expanduser().resolve()
    except OSError as exc:
        if allow_stop:
            _stop_daemon_direct(wx_path, profile_dir)
            return
        raise WxCliError(
            WX_DAEMON_MISMATCH,
            "wx daemon active db_dir is not readable",
            {"profile_dir": str(profile_dir), "active_db_dir": active_db_dir, "error": str(exc)},
        ) from exc

    if active_path != desired_db_dir:
        if allow_stop:
            _stop_daemon_direct(wx_path, profile_dir)
            return
        raise WxCliError(
            WX_DAEMON_MISMATCH,
            "wx daemon is attached to a different db_dir; refusing to auto-stop in read-only mode",
            {"profile_dir": str(profile_dir), "desired_db_dir": str(desired_db_dir), "active_db_dir": str(active_path)},
        )


def _stop_daemon_direct(wx_path: str, profile_dir: Optional[Path]) -> None:
    try:
        subprocess.run(
            [wx_path, "daemon", "stop"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
            cwd=str(profile_dir) if profile_dir else None,
            env=_wx_env(profile_dir),
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def _daemon_is_running(status: Any) -> bool:
    status_text = _string(status.get("text") if isinstance(status, dict) else status).lower()
    return "未运行" not in status_text and "not running" not in status_text


def _first(raw: dict[str, Any], names: Iterable[str], default: Any = "") -> Any:
    for name in names:
        value = raw.get(name)
        if value is not None and value != "":
            return value
    return default


def _as_list(payload: Any, keys: Iterable[str]) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _as_list(value, keys)
                if nested:
                    return nested
        data = payload.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            nested = _as_list(data, keys)
            if nested:
                return nested
        return [payload]
    return []


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _infer_chat_type(raw: dict[str, Any], username: str) -> str:
    explicit = _string(_first(raw, ["chat_type", "type", "conversation_type", "room_type"]))
    if explicit:
        return explicit
    if username.endswith("@chatroom"):
        return "group"
    if username.startswith("gh_") or username in {"mphelper", "qqsafe"}:
        return "official_account"
    if username.startswith("wxid_") or username.endswith("@openim"):
        return "private"
    return ""


def normalize_session(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"value": raw}

    conversation_username = _string(
        _first(
            raw,
            [
                "conversation_username",
                "username",
                "user_name",
                "chat",
                "chat_id",
                "chat_name",
                "conversation_id",
                "talker",
                "wxid",
                "id",
            ],
        )
    )
    display_name = _string(
        _first(raw, ["display_name", "display", "chat", "name", "nick_name", "nickname", "remark_name", "title", "chat_title"])
    )
    remark = _string(_first(raw, ["remark", "remark_name", "con_remark"]))
    alias = _string(_first(raw, ["alias", "py_initial", "quan_pin"]))
    chat_type = _infer_chat_type(raw, conversation_username)
    chat_room_notify = _first(raw, ["chat_room_notify", "chatRoomNotify", "notify", "notification_muted"], None)

    candidates = []
    for value in [conversation_username, display_name, remark, alias]:
        if value and value not in candidates:
            candidates.append(value)

    return {
        "conversation_username": conversation_username,
        "display_name": display_name,
        "remark": remark,
        "alias": alias,
        "chat_type": chat_type,
        "chat_room_notify": chat_room_notify,
        "notification_muted": notification_muted_from_chat_room_notify(chat_room_notify),
        "notification_state": notification_state_from_chat_room_notify(chat_room_notify),
        "search_candidates": candidates,
        "raw_payload": raw,
    }


def normalize_message(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"value": raw}

    conversation_username = _string(
        _first(
            raw,
            [
                "conversation_username",
                "username",
                "user_name",
                "chat",
                "chat_id",
                "chat_name",
                "conversation_id",
                "talker",
                "room_id",
            ],
        )
    )
    display_name = _string(_first(raw, ["display_name", "chat_display_name", "chat_title", "name"]))
    text = _string(_first(raw, ["text", "content", "message", "msg", "body", "summary", "plain_text"]))
    sender_id = _string(_first(raw, ["sender_id", "sender", "from_user", "from", "from_username", "talker"]))
    direction = _string(_first(raw, ["direction", "is_sender", "from_me", "is_self"]))
    if direction in {"1", "true", "True"}:
        direction = "sent"
    elif direction in {"0", "false", "False"}:
        direction = "received"

    return {
        "message_id": _first(raw, ["message_id", "msg_id", "msgid", "local_id", "id", "server_id"], ""),
        "timestamp": _first(raw, ["timestamp", "create_time", "created_at", "time", "msg_time"], ""),
        "direction": direction,
        "message_type": _first(raw, ["message_type", "type", "msg_type", "render_type"], ""),
        "text": text,
        "sender_id": sender_id,
        "source_provider": "wx-cli",
        "conversation_username": conversation_username,
        "display_name": display_name,
        "chat_type": _infer_chat_type(raw, conversation_username),
        "raw_payload": raw,
    }


def normalize_contact(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"value": raw}

    username = _string(
        _first(raw, ["username", "user_name", "wxid", "contact_id", "id", "conversation_username", "alias"])
    )
    display_name = _string(_first(raw, ["display_name", "display", "name", "nick_name", "nickname", "remark_name", "title"]))
    remark = _string(_first(raw, ["remark", "remark_name", "con_remark"]))
    alias = _string(_first(raw, ["alias", "wechat_id", "py_initial", "quan_pin"]))
    chat_type = _infer_chat_type(raw, username)
    candidates = []
    for value in [username, display_name, remark, alias]:
        if value and value not in candidates:
            candidates.append(value)
    return {
        "username": username,
        "conversation_username": username,
        "display_name": display_name or remark or username,
        "remark": remark,
        "alias": alias,
        "chat_type": chat_type,
        "search_candidates": candidates,
        "source_provider": "wx-cli",
        "raw_payload": raw,
    }


def normalize_member(raw: Any, room_username: str = "") -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"value": raw}
    member_username = _string(_first(raw, ["member_username", "username", "user_name", "wxid", "id", "contact_id"]))
    display_name = _string(_first(raw, ["display_name", "display", "name", "nick_name", "nickname", "remark_name"]))
    room_nickname = _string(_first(raw, ["room_nickname", "room_nick_name", "chatroom_nickname", "member_display_name"]))
    return {
        "room_username": room_username or _string(_first(raw, ["room_username", "room_id", "chat", "chat_id"])),
        "member_username": member_username,
        "display_name": display_name or room_nickname or member_username,
        "room_nickname": room_nickname,
        "contact_remark": _string(_first(raw, ["remark", "remark_name", "con_remark"])),
        "alias": _string(_first(raw, ["alias", "wechat_id"])),
        "source_provider": "wx-cli",
        "raw_payload": raw,
    }


def normalize_favorite(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"value": raw}
    tags = _first(raw, ["tags", "tag", "labels"], [])
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]
    elif not isinstance(tags, list):
        tags = []
    return {
        "favorite_id": _first(raw, ["favorite_id", "fav_id", "local_id", "id"], ""),
        "type": _string(_first(raw, ["type", "fav_type", "item_type", "category"])),
        "title": _string(_first(raw, ["title", "data_title", "datatitle", "name"])),
        "desc": _string(_first(raw, ["desc", "description", "data_desc", "datadesc", "summary", "text"])),
        "link": _string(_first(raw, ["link", "url", "web_url", "source_url"])),
        "timestamp": _first(raw, ["timestamp", "time", "update_time", "created_at", "create_time"], ""),
        "source": _string(_first(raw, ["source", "source_name", "from", "sender", "author"])),
        "tags": tags,
        "source_provider": "wx-cli",
        "raw_payload": raw,
    }


def normalize_sns_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"value": raw}
    media = _first(raw, ["media", "media_items", "images", "attachments"], [])
    if not isinstance(media, list):
        media = [media] if media else []
    return {
        "sns_id": _first(raw, ["sns_id", "id", "feed_id", "moment_id"], ""),
        "author_username": _string(_first(raw, ["author_username", "user_name", "username", "wxid", "author_id"])),
        "author_name": _string(_first(raw, ["author_name", "author", "nickname", "display_name", "name"])),
        "timestamp": _first(raw, ["timestamp", "time", "created_at", "create_time"], ""),
        "content": _string(_first(raw, ["content", "text", "desc", "description"])),
        "media_refs": media,
        "like_count": _first(raw, ["like_count", "likes"], 0),
        "comment_count": _first(raw, ["comment_count", "comments_count"], 0),
        "comments": _first(raw, ["comments", "comment_list"], []),
        "source_provider": "wx-cli",
        "raw_payload": raw,
    }


def normalize_sns_notification(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"value": raw}
    return {
        "notification_id": _first(raw, ["notification_id", "id", "sns_id", "comment_id"], ""),
        "sns_id": _first(raw, ["sns_id", "feed_id", "moment_id"], ""),
        "actor_username": _string(_first(raw, ["actor_username", "username", "user_name", "wxid"])),
        "actor_name": _string(_first(raw, ["actor_name", "nickname", "display_name", "name"])),
        "type": _string(_first(raw, ["type", "notification_type", "action"])),
        "text": _string(_first(raw, ["text", "content", "comment", "summary"])),
        "timestamp": _first(raw, ["timestamp", "time", "created_at", "create_time"], ""),
        "source_provider": "wx-cli",
        "raw_payload": raw,
    }


def normalize_stats(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {"value": raw}
    return {
        "total_messages": _first(raw, ["total_messages", "total", "message_count", "count"], 0),
        "type_counts": _first(raw, ["type_counts", "types", "message_types"], {}),
        "top_senders": _first(raw, ["top_senders", "senders"], []),
        "hourly_activity": _first(raw, ["hourly_activity", "hours"], []),
        "source_provider": "wx-cli",
        "raw_payload": raw,
    }


def make_envelope(command: list[str], data: Any, *, warnings: Optional[list[str]] = None, meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": "wx_cli_adapter_envelope_v1",
        "source_provider": "wx-cli",
        "command": command,
        "risk": classify_wx_command(command),
        "data": data,
        "warnings": warnings or [],
        "meta": meta or {},
    }


def check_wx_cli_ready(*, autostart: bool = True) -> dict[str, Any]:
    try:
        status = get_daemon_status()
        if _daemon_is_running(status):
            return {"ready": True, "status": status}
        if not autostart:
            return {"ready": False, "status": status}

        try:
            # wx-cli has no explicit `daemon start`; read-only commands lazily
            # start the daemon. Avoid `new-messages` here because it advances
            # wx-cli's last-check cursor before the monitor can record events.
            _run_wx_json(["sessions", "--limit", "1"], timeout=30.0)
        except WxCliError as probe_error:
            return {"ready": False, "status": status, "autostart_probe": probe_error.to_dict()}

        refreshed_status = get_daemon_status()
        return {
            "ready": _daemon_is_running(refreshed_status),
            "status": refreshed_status,
            "previous_status": status,
            "autostart_probe": {"command": "sessions --limit 1", "ok": True},
        }
    except WxCliError as exc:
        return {"ready": False, "error": exc.to_dict()}


def get_daemon_status() -> Any:
    return {"text": _run_wx_text(["daemon", "status"])}


def stop_daemon(*, allow_unsafe: bool = False) -> Any:
    if not allow_unsafe:
        raise WxCliError(
            COMMAND_BLOCKED,
            "daemon stop is blocked by default; pass allow_unsafe=True for explicit manual control",
            {"args": ["daemon", "stop"], "risk": RISK_BLOCKED},
        )
    return {"text": _run_wx_text(["daemon", "stop"], allow_blocked=True, allow_daemon_switch=True)}


def list_sessions(limit: Optional[int] = None) -> list[dict[str, Any]]:
    args = ["sessions"]
    if limit is not None:
        args.extend(["--limit", str(max(limit, 1))])
    payload = _run_wx_json(args)
    return [normalize_session(item) for item in _as_list(payload, ["sessions", "conversations", "items", "results"])]


def list_unread(chat_type_filter: Optional[str] = None) -> list[dict[str, Any]]:
    args = ["unread"]
    if chat_type_filter:
        args.extend(["--filter", chat_type_filter])
    payload = _run_wx_json(args)
    sessions = [normalize_session(item) for item in _as_list(payload, ["unread", "sessions", "conversations", "items"])]
    if chat_type_filter:
        allowed = {part.strip() for part in chat_type_filter.split(",") if part.strip()}
        sessions = [session for session in sessions if not session["chat_type"] or session["chat_type"] in allowed]
    return sessions


def list_contacts(query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    args = ["contacts", "--limit", str(max(limit, 1))]
    if query:
        args.extend(["--query", query])
    payload = _run_wx_json(args)
    return [normalize_contact(item) for item in _as_list(payload, ["contacts", "items", "results"])]


def query_contacts(query: str, limit: int = 20) -> list[dict[str, Any]]:
    return list_contacts(query=query, limit=limit)


def search_messages(
    query: str,
    *,
    chat: str = "",
    limit: int = 50,
    since: str = "",
    until: str = "",
    message_type: str = "",
) -> list[dict[str, Any]]:
    args = ["search", query, "--limit", str(max(limit, 1))]
    if chat:
        args.extend(["--in", chat])
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if message_type:
        args.extend(["--type", message_type])
    payload = _run_wx_json(args)
    return [normalize_message(item) for item in _as_list(payload, ["messages", "results", "items", "history"])]


def list_group_members(chat: str, limit: int = 500) -> list[dict[str, Any]]:
    payload = _run_wx_json(["members", chat], timeout=30.0)
    members = [normalize_member(item, room_username=chat) for item in _as_list(payload, ["members", "items", "results"])]
    return members[: max(limit, 1)]


def get_stats(chat: str, *, since: str = "", until: str = "") -> dict[str, Any]:
    args = ["stats", chat]
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    return normalize_stats(_run_wx_json(args, timeout=30.0))


def list_favorites(limit: int = 100, *, type_filter: str = "", query: str = "") -> list[dict[str, Any]]:
    args = ["favorites", "--limit", str(max(limit, 1))]
    if type_filter:
        args.extend(["--type", type_filter])
    if query:
        args.extend(["--query", query])
    payload = _run_wx_json(args, timeout=30.0)
    return [normalize_favorite(item) for item in _as_list(payload, ["favorites", "items", "results"])]


def get_sns_notifications(limit: int = 100, *, since: str = "", until: str = "", include_read: bool = False) -> list[dict[str, Any]]:
    args = ["sns-notifications", "--limit", str(max(limit, 1))]
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if include_read:
        args.append("--include-read")
    payload = _run_wx_json(args, timeout=30.0)
    return [normalize_sns_notification(item) for item in _as_list(payload, ["notifications", "items", "results"])]


def get_sns_feed(limit: int = 100, *, since: str = "", until: str = "", user: str = "") -> list[dict[str, Any]]:
    args = ["sns-feed", "--limit", str(max(limit, 1))]
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if user:
        args.extend(["--user", user])
    payload = _run_wx_json(args, timeout=30.0)
    return [normalize_sns_item(item) for item in _as_list(payload, ["feed", "sns", "items", "results"])]


def search_sns(query: str, limit: int = 50, *, since: str = "", until: str = "", user: str = "") -> list[dict[str, Any]]:
    args = ["sns-search", query, "--limit", str(max(limit, 1))]
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if user:
        args.extend(["--user", user])
    payload = _run_wx_json(args, timeout=30.0)
    return [normalize_sns_item(item) for item in _as_list(payload, ["feed", "sns", "items", "results"])]


def get_new_messages(limit: Optional[int] = None) -> list[dict[str, Any]]:
    args = ["new-messages"]
    if limit is not None:
        args.extend(["--limit", str(limit)])
    payload = _run_wx_json(args, timeout=30.0)
    return [normalize_message(item) for item in _as_list(payload, ["messages", "new_messages", "items", "results"])]


def get_history(
    chat: str,
    limit: int,
    *,
    offset: int = 0,
    since: str = "",
    until: str = "",
    message_type: str = "",
) -> list[dict[str, Any]]:
    args = ["history", chat, "--limit", str(max(limit, 0))]
    if offset > 0:
        args.extend(["--offset", str(offset)])
    if since:
        args.extend(["--since", since])
    if until:
        args.extend(["--until", until])
    if message_type:
        args.extend(["--type", message_type])
    payload = _run_wx_json(args)
    return [normalize_message(item) for item in _as_list(payload, ["messages", "history", "items", "results"])]


def list_members(chat: str) -> list[dict[str, Any]]:
    payload = _run_wx_json(["members", chat, "--with-meta"], timeout=30.0)
    return [item if isinstance(item, dict) else {"value": item} for item in _as_list(payload, ["members", "items", "results"])]


def _dedupe_sessions(sessions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen = set()
    for session in sessions:
        key = session.get("conversation_username") or "|".join(session.get("search_candidates") or [])
        if not key:
            key = json.dumps(session.get("raw_payload"), ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(session)
    return deduped


def resolve_conversation(needle: str, chat_type_filter: Optional[str] = None) -> dict[str, Any]:
    query = needle.strip().lower()
    if not query:
        raise WxCliError(CONVERSATION_NOT_FOUND, "empty conversation query")

    sessions = _dedupe_sessions([*list_sessions(), *list_unread(chat_type_filter=chat_type_filter)])
    if chat_type_filter:
        allowed = {part.strip() for part in chat_type_filter.split(",") if part.strip()}
        sessions = [session for session in sessions if not session["chat_type"] or session["chat_type"] in allowed]

    exact_hits = []
    fuzzy_hits = []
    for session in sessions:
        candidates = [_string(value).lower() for value in session.get("search_candidates", []) if _string(value)]
        if query in candidates:
            exact_hits.append(session)
        elif any(query in candidate for candidate in candidates):
            fuzzy_hits.append(session)

    hits = exact_hits or fuzzy_hits
    if not hits:
        contact_hits = query_contacts(needle)
        exact_hits = []
        fuzzy_hits = []
        for contact in contact_hits:
            candidates = [_string(value).lower() for value in contact.get("search_candidates", []) if _string(value)]
            if query in candidates:
                exact_hits.append(contact)
            elif any(query in candidate for candidate in candidates):
                fuzzy_hits.append(contact)
        hits = exact_hits or fuzzy_hits
    if not hits:
        raise WxCliError(CONVERSATION_NOT_FOUND, f"no conversation matched: {needle}", {"needle": needle})
    if len(hits) > 1:
        preview = [
            {
                "conversation_username": item.get("conversation_username", ""),
                "display_name": item.get("display_name", ""),
                "remark": item.get("remark", ""),
                "alias": item.get("alias", ""),
                "chat_type": item.get("chat_type", ""),
            }
            for item in hits[:10]
        ]
        raise WxCliError(
            CONVERSATION_AMBIGUOUS,
            f"multiple conversations matched: {needle}",
            {"needle": needle, "matches": preview, "match_count": len(hits)},
        )
    return hits[0]
