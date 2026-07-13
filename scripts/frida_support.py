#!/usr/bin/env python3

import grp
import os
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any

import frida


def _run_command(args: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return 127, ""
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode, output


def get_group_names() -> list[str]:
    names = []
    for gid in sorted(set(os.getgroups())):
        try:
            names.append(grp.getgrgid(gid).gr_name)
        except KeyError:
            names.append(str(gid))
    return names


def get_process_chain(depth: int = 4) -> list[dict[str, Any]]:
    chain = []
    pid = os.getpid()
    visited = set()
    while pid > 1 and pid not in visited and len(chain) < depth:
        visited.add(pid)
        code, output = _run_command(["ps", "-o", "pid=,ppid=,comm=", "-p", str(pid)])
        if code != 0 or not output:
            break
        parts = output.split(None, 2)
        if len(parts) < 3:
            break
        cur_pid = int(parts[0])
        parent_pid = int(parts[1])
        command = parts[2].strip()
        chain.append({"pid": cur_pid, "ppid": parent_pid, "command": command})
        pid = parent_pid
    return chain


def get_devtools_status() -> str:
    _, output = _run_command(["/usr/sbin/DevToolsSecurity", "-status"])
    return output or "unable to read DevToolsSecurity status"


def get_codesigning_identities() -> list[str]:
    code, output = _run_command(["security", "find-identity", "-v", "-p", "codesigning"])
    if code != 0 and not output:
        return []
    identities = []
    for line in output.splitlines():
        line = line.strip()
        if ")" not in line or '"' not in line:
            continue
        quote_start = line.find('"')
        quote_end = line.rfind('"')
        if quote_start >= 0 and quote_end > quote_start:
            identities.append(line[quote_start + 1 : quote_end])
    return identities


def get_taskport_policy_summary() -> str:
    _, output = _run_command(["security", "authorizationdb", "read", "system.privilege.taskport"])
    if not output:
        return "unable to read taskport policy"
    summary = []
    if "<key>group</key>" in output:
        summary.append("group=_developer")
    if "<key>authenticate-user</key>" in output:
        summary.append("authenticate-user=true")
    if "<key>shared</key>" in output:
        summary.append("shared=true")
    return ", ".join(summary) or "custom policy"


def detect_host_hint(process_chain: list[dict[str, Any]]) -> str:
    preferred = []
    for entry in process_chain:
        command = entry.get("command", "")
        if ".app/" not in command and not command.endswith(".app"):
            continue
        preferred.append(command)
        if "terminal.app" in command.lower() or "iterm.app" in command.lower():
            return command
    if preferred:
        return preferred[-1]
    return process_chain[1]["command"] if len(process_chain) > 1 else process_chain[0]["command"] if process_chain else ""


def build_probe_binary() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="frida-probe-"))
    source_path = temp_dir / "probe_loop.c"
    binary_path = temp_dir / "probe_loop"
    source_path.write_text(
        """#include <signal.h>
#include <stdio.h>
#include <unistd.h>
static volatile sig_atomic_t keep_running = 1;
static void on_signal(int sig) { (void)sig; keep_running = 0; }
int main(void) {
    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);
    printf("pid=%d\\n", getpid());
    fflush(stdout);
    while (keep_running) sleep(1);
    return 0;
}
""",
        encoding="utf-8",
    )
    code, output = _run_command(["clang", str(source_path), "-o", str(binary_path)])
    if code != 0:
        raise RuntimeError(f"failed to build probe binary: {output}")
    _run_command(["codesign", "--force", "--sign", "-", str(binary_path)])
    return binary_path


def _frida_test_attach_existing() -> dict[str, Any]:
    try:
        binary_path = build_probe_binary()
    except Exception as exc:
        return {
            "ok": False,
            "method": "attach_existing",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }

    proc = subprocess.Popen([str(binary_path)])
    time.sleep(0.5)
    try:
        session = frida.attach(proc.pid)
        session.detach()
        return {"ok": True, "method": "attach_existing"}
    except Exception as exc:
        return {
            "ok": False,
            "method": "attach_existing",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except Exception:
            proc.kill()


def _frida_test_spawn_attach() -> dict[str, Any]:
    device = frida.get_local_device()
    try:
        binary_path = build_probe_binary()
    except Exception as exc:
        return {
            "ok": False,
            "method": "spawn_attach",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    pid = None
    try:
        pid = device.spawn([str(binary_path)])
        session = device.attach(pid)
        session.detach()
        return {"ok": True, "method": "spawn_attach"}
    except Exception as exc:
        return {
            "ok": False,
            "method": "spawn_attach",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }
    finally:
        if pid is not None:
            try:
                device.kill(pid)
            except Exception:
                try:
                    device.resume(pid)
                    device.kill(pid)
                except Exception:
                    pass


def run_frida_preflight() -> dict[str, Any]:
    process_chain = get_process_chain()
    group_names = get_group_names()
    identities = get_codesigning_identities()
    tests = {
        "attach_existing": _frida_test_attach_existing(),
        "spawn_attach": _frida_test_spawn_attach(),
    }
    return {
        "python_executable": sys.executable,
        "group_names": group_names,
        "has_developer_group": "_developer" in group_names,
        "devtools_status": get_devtools_status(),
        "codesigning_identities": identities,
        "codesigning_identity_count": len(identities),
        "taskport_policy": get_taskport_policy_summary(),
        "process_chain": process_chain,
        "host_hint": detect_host_hint(process_chain),
        "tests": tests,
    }


def build_permission_hints(preflight: dict[str, Any]) -> list[str]:
    hints = []
    if not preflight.get("has_developer_group"):
        hints.append("当前 shell 还不在 `_developer` 组里。把用户加入 `_developer` 后，需要完整退出当前宿主 App/终端并重新登录会话。")

    status = preflight.get("devtools_status", "")
    if "enabled" not in status.lower():
        hints.append("`Developer mode` 还没开启，先执行 `DevToolsSecurity -enable`，然后重新登录会话。")

    if preflight.get("codesigning_identity_count", 0) == 0:
        hints.append("本机目前没有可用的代码签名身份。Frida 官方在 macOS 上建议使用可信代码签名证书；当前机器若要走这条路，需要先通过 Xcode/Apple Developer 拿到可用签名身份。")

    tests = preflight.get("tests", {})
    attach_existing = tests.get("attach_existing", {})
    spawn_attach = tests.get("spawn_attach", {})
    permission_denied = any(
        "PermissionDeniedError" == test.get("error_type") or "unable to access process" in (test.get("error_message") or "")
        for test in (attach_existing, spawn_attach)
        if test
    )

    if permission_denied:
        host_hint = preflight.get("host_hint") or "当前宿主进程"
        chain_summary = " <- ".join(entry.get("command", "") for entry in preflight.get("process_chain", []))
        hints.append(
            f"Frida 现在被 `task_for_pid` 拒绝了。当前命令是从 `{host_hint}` 发起的，先到“系统设置 > 隐私与安全性 > 开发者工具”里给这个宿主 App 放行，然后把它完全退出后再重开。"
        )
        if chain_summary:
            hints.append(f"当前进程链路是：`{chain_summary}`。如果你改在 Terminal/iTerm 里跑，就给对应终端 App 放行。")
        hints.append("如果你更想避开宿主 App 的权限问题，可以直接在 `Terminal` 或 `iTerm` 里运行同样的 Python 命令，并给对应终端 App 勾选开发者工具权限。")

    return hints


def format_preflight_report(preflight: dict[str, Any]) -> str:
    lines = []
    lines.append("Frida preflight")
    lines.append(f"- python: {preflight.get('python_executable', '')}")
    lines.append(f"- DevToolsSecurity: {preflight.get('devtools_status', '')}")
    lines.append(f"- taskport policy: {preflight.get('taskport_policy', '')}")
    lines.append(f"- groups: {', '.join(preflight.get('group_names', []))}")
    lines.append(f"- codesigning identities: {preflight.get('codesigning_identity_count', 0)}")
    identities = preflight.get("codesigning_identities", [])
    for identity in identities[:3]:
        lines.append(f"  - {identity}")
    chain = preflight.get("process_chain", [])
    if chain:
        lines.append("- process chain:")
        for entry in chain:
            lines.append(f"  - pid={entry['pid']} ppid={entry['ppid']} cmd={entry['command']}")
    tests = preflight.get("tests", {})
    for name in ("attach_existing", "spawn_attach"):
        result = tests.get(name, {})
        if result.get("ok"):
            lines.append(f"- {name}: ok")
        else:
            lines.append(
                f"- {name}: {result.get('error_type', 'Error')} {result.get('error_message', '').strip()}".rstrip()
            )
    hints = build_permission_hints(preflight)
    if hints:
        lines.append("- next steps:")
        for hint in hints:
            lines.append(f"  - {hint}")
    return "\n".join(lines)


def open_developer_tools_settings() -> bool:
    try:
        return webbrowser.open("x-apple.systempreferences:com.apple.preference.security?Privacy_DeveloperTools")
    except Exception:
        return False
