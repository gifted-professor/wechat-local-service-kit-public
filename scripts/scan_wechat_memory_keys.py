#!/usr/bin/env python3

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import frida

from chat_crypto import KEY_MODE_DERIVED, read_db_salt, validate_sqlcipher_key
from frida_support import format_preflight_report, run_frida_preflight

JS_CODE = r"""
const TARGET_SALTS = JSON.parse(TARGET_SALTS_JSON);

function asciiFrom(buffer) {
    const bytes = new Uint8Array(buffer);
    let out = "";
    for (let i = 0; i < bytes.length; i++) {
        out += String.fromCharCode(bytes[i]);
    }
    return out;
}

function shouldReportRange(index) {
    return index === 0 || (index + 1) % 50 === 0;
}

rpc.exports = {
    scan: function () {
        const totalLen = 99; // x' + 96 hex + '
        const hexRe = /^x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'$/;
        const seen = {};
        const hits = [];
        const rangeKeys = {};
        const ranges = [];
        const specs = ["rw-", "r--", "r-x"];
        for (let s = 0; s < specs.length; s++) {
            Process.enumerateRanges(specs[s], {
                onMatch: function (range) {
                    const key = range.base.toString() + ":" + range.size;
                    if (!(key in rangeKeys)) {
                        rangeKeys[key] = true;
                        ranges.push(range);
                    }
                    return true;
                },
                onComplete: function () {
                }
            });
        }

        for (let i = 0; i < ranges.length; i++) {
            const range = ranges[i];
            if (shouldReportRange(i)) {
                send({
                    type: "progress",
                    index: i + 1,
                    total: ranges.length,
                    base: range.base.toString(),
                    size: range.size,
                });
            }

            let matches = [];
            try {
                matches = Memory.scanSync(range.base, range.size, "78 27");
            } catch (e) {
                continue;
            }

            for (let j = 0; j < matches.length; j++) {
                try {
                    const buf = Memory.readByteArray(matches[j].address, totalLen);
                    const text = asciiFrom(buf);
                    const m = hexRe.exec(text);
                    if (m === null) {
                        continue;
                    }
                    const keyHex = m[1].toLowerCase();
                    const saltHex = m[2].toLowerCase();
                    if (!(saltHex in TARGET_SALTS)) {
                        continue;
                    }
                    const uniq = keyHex + ":" + saltHex;
                    if (uniq in seen) {
                        continue;
                    }
                    seen[uniq] = true;
                    const entry = {
                        key_hex: keyHex,
                        salt_hex: saltHex,
                        address: matches[j].address.toString(),
                    };
                    hits.push(entry);
                    send({
                        type: "hit",
                        salt_hex: saltHex,
                        key_hex: keyHex,
                        address: matches[j].address.toString(),
                    });
                } catch (e) {
                }
            }
        }

        return hits;
    }
};
"""


def json_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def load_targets(db_dir: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, str]]:
    by_salt: dict[str, list[dict[str, str]]] = {}
    rel_to_salt: dict[str, str] = {}
    for path in sorted(db_dir.rglob("*.db")):
        try:
            salt_hex = read_db_salt(path).hex()
        except Exception:
            continue
        rel_path = path.relative_to(db_dir).as_posix()
        rel_to_salt[rel_path] = salt_hex
        by_salt.setdefault(salt_hex, []).append(
            {
                "db_path": str(path),
                "rel_path": rel_path,
            }
        )
    return by_salt, rel_to_salt


def pick_validation_targets(by_salt: dict[str, list[dict[str, str]]], hits: list[dict[str, str]]) -> dict[str, Any]:
    all_keys: dict[str, str] = {}
    validated: list[dict[str, Any]] = []
    for hit in hits:
        salt_hex = hit["salt_hex"]
        key_hex = hit["key_hex"]
        for item in by_salt.get(salt_hex, []):
            db_path = Path(item["db_path"])
            validation = validate_sqlcipher_key(db_path, key_hex, KEY_MODE_DERIVED)
            if not validation.ok:
                continue
            rel_path = item["rel_path"]
            all_keys[rel_path] = key_hex
            validated.append(
                {
                    "db_path": str(db_path),
                    "rel_path": rel_path,
                    "salt_hex": salt_hex,
                    "key_hex": key_hex,
                    "key_mode": validation.key_mode,
                }
            )
    return {
        "all_keys": all_keys,
        "validated": validated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan WeChat process memory for SQLCipher derived keys and validate them against a db_storage tree")
    parser.add_argument("--pid", type=int, help="existing WeChat PID to attach to; defaults to the single running WeChat process")
    parser.add_argument("--app", help="spawn this WeChat executable with Frida and attach to it")
    parser.add_argument("--wait", type=int, default=45, help="seconds to wait after spawning the app before scanning")
    parser.add_argument("--db-dir", required=True, help="target db_storage directory")
    parser.add_argument("--out", help="optional JSON output path")
    parser.add_argument("--skip-preflight", action="store_true", help="skip local Frida attach checks")
    args = parser.parse_args()

    db_dir = Path(args.db_dir).expanduser().resolve()
    if not db_dir.exists():
        print(f"[ERROR] db dir not found: {db_dir}", file=sys.stderr)
        return 1

    app_path = Path(args.app).expanduser().resolve() if args.app else None
    if app_path and not app_path.exists():
        print(f"[ERROR] app not found: {app_path}", file=sys.stderr)
        return 1

    if not args.skip_preflight:
        preflight = run_frida_preflight()
        tests = preflight.get("tests", {})
        required = "spawn_attach" if app_path else "attach_existing"
        if not tests.get(required, {}).get("ok"):
            print(f"[ERROR] Frida {required} preflight failed.", file=sys.stderr)
            print(format_preflight_report(preflight), file=sys.stderr)
            return 2

    by_salt, rel_to_salt = load_targets(db_dir)
    if not by_salt:
        print(f"[ERROR] no encrypted db files found under {db_dir}", file=sys.stderr)
        return 1

    js_code = JS_CODE.replace("TARGET_SALTS_JSON", json_string(json.dumps(by_salt, ensure_ascii=False)))

    device = frida.get_local_device()
    pid = args.pid
    spawned = False
    if app_path:
        pid = device.spawn([str(app_path)])
        spawned = True
    elif pid is None:
        matches = [proc for proc in device.enumerate_processes() if proc.name == "WeChat"]
        if len(matches) != 1:
            print(f"[ERROR] expected exactly one WeChat process, found {len(matches)}", file=sys.stderr)
            for proc in matches:
                print(f"  pid={proc.pid} name={proc.name}", file=sys.stderr)
            return 1
        pid = matches[0].pid

    try:
        session = device.attach(pid)
        script = session.create_script(js_code)

        def on_message(message: dict[str, Any], _data: Any) -> None:
            payload = message.get("payload")
            if not isinstance(payload, dict):
                return
            if payload.get("type") == "hit":
                salt_hex = payload.get("salt_hex", "")
                paths = [item["rel_path"] for item in by_salt.get(salt_hex, [])]
                print(f"[hit] salt={salt_hex} paths={paths} key={payload.get('key_hex','')[:16]}...")
            elif payload.get("type") == "progress":
                index = payload.get("index")
                total = payload.get("total")
                print(f"[scan] range {index}/{total}")

        script.on("message", on_message)
        script.load()
        if spawned:
            device.resume(pid)
            print(f"[info] spawned {app_path} with pid={pid}")
            print(f"[info] waiting {args.wait}s for WeChat to finish startup and load target databases...")
            time.sleep(args.wait)
        hits = script.exports_sync.scan()
    finally:
        try:
            session.detach()
        except Exception:
            pass

    if not isinstance(hits, list):
        print("[ERROR] unexpected Frida scan result", file=sys.stderr)
        return 2

    validated_info = pick_validation_targets(by_salt, hits)
    result = {
        "pid": pid,
        "db_dir": str(db_dir),
        "db_count": len(rel_to_salt),
        "target_salt_count": len(by_salt),
        "hit_count": len(hits),
        "validated_count": len(validated_info["validated"]),
        "validated": validated_info["validated"],
        "all_keys": validated_info["all_keys"],
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")

    return 0 if result["validated_count"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
