#!/usr/bin/env python3

import argparse
import sys
import time
from pathlib import Path

import frida
from frida_support import format_preflight_report, run_frida_preflight

JS_CODE = r'''
function buf2hex(buffer) {
    var a = new Uint8Array(buffer); var h = '';
    for (var i = 0; i < a.length; i++) h += ('0' + a[i].toString(16)).slice(-2);
    return h;
}
var found = false;
Process.enumerateModules().forEach(function(m) {
    if (found) return;
    m.enumerateExports().forEach(function(exp) {
        if (found) return;
        if (exp.name === "CCKeyDerivationPBKDF") {
            found = true;
            send("[*] Hook installed on " + m.name);
            Interceptor.attach(exp.address, {
                onEnter: function(args) {
                    this.pwLen = args[2].toInt32();
                    this.saltLen = args[4].toInt32();
                    this.rounds = args[6].toInt32();
                    this.pw = args[1];
                    this.salt = args[3];
                    this.dk = args[7];
                    this.dkLen = args[8].toInt32();
                },
                onLeave: function(retval) {
                    if (this.pwLen < 4 || this.pwLen > 256) return;
                    if (this.saltLen < 4 || this.saltLen > 64) return;
                    var saltHex = buf2hex(this.salt.readByteArray(this.saltLen));
                    var dkHex = buf2hex(this.dk.readByteArray(this.dkLen));
                    var pwHex = buf2hex(this.pw.readByteArray(this.pwLen));
                    var f = new File(LOG_PATH, "a");
                    f.write("rounds=" + this.rounds + "\npw=" + pwHex + "\nsalt=" + saltHex + "\ndk=" + dkHex + "\n\n");
                    f.flush();
                    f.close();
                    send("[PBKDF2] rounds=" + this.rounds + " salt=" + saltHex);
                }
            });
        }
    });
});
if (!found) send("[!] CCKeyDerivationPBKDF not found");
'''


def default_wechat_app() -> str:
    home_debug_copy = Path.home() / ".wx-debug" / "WeChat-debug.app" / "Contents" / "MacOS" / "WeChat"
    if home_debug_copy.exists():
        return str(home_debug_copy)
    desktop_copy = Path.home() / "Desktop" / "WeChat.app" / "Contents" / "MacOS" / "WeChat"
    if desktop_copy.exists():
        return str(desktop_copy)
    return "/Applications/WeChat.app/Contents/MacOS/WeChat"


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture WeChat PBKDF2-derived keys with Frida")
    parser.add_argument("--app", default=default_wechat_app(), help="WeChat executable path")
    parser.add_argument("--log", default="/tmp/wechat_frida_keys.log", help="Output log path")
    parser.add_argument("--wait", type=int, default=120, help="Seconds to wait after launch")
    parser.add_argument("--skip-preflight", action="store_true", help="skip local Frida permission checks")
    args = parser.parse_args()

    app_path = Path(args.app)
    if not app_path.exists():
        print(f"[ERROR] app not found: {app_path}", file=sys.stderr)
        return 1

    if not args.skip_preflight:
        preflight = run_frida_preflight()
        tests = preflight.get("tests", {})
        if not tests.get("spawn_attach", {}).get("ok"):
            print("[ERROR] Frida preflight failed before WeChat launch.", file=sys.stderr)
            print(format_preflight_report(preflight), file=sys.stderr)
            return 2

    log_path = Path(args.log)
    if log_path.exists():
        log_path.unlink()

    js_code = JS_CODE.replace("LOG_PATH", json_string(str(log_path)))

    try:
        device = frida.get_local_device()
        pid = device.spawn([str(app_path)])
        session = device.attach(pid)
        script = session.create_script(js_code)
        script.on("message", lambda msg, data: print(msg.get("payload", msg)))
        script.load()
        device.resume(pid)
    except frida.PermissionDeniedError as exc:
        print(f"[ERROR] Frida permission denied: {exc}", file=sys.stderr)
        print(format_preflight_report(run_frida_preflight()), file=sys.stderr)
        return 2

    print(f"WeChat started from {app_path}")
    print(f"Log file: {log_path}")
    print(f"Now login and open one or two customer chats. Waiting {args.wait}s...")

    try:
        time.sleep(args.wait)
    finally:
        try:
            session.detach()
        except Exception:
            pass

    print("Done.")
    return 0


def json_string(value: str) -> str:
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return '"' + escaped + '"'


if __name__ == "__main__":
    raise SystemExit(main())
