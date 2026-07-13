#!/usr/bin/env python3
"""Scan Windows Weixin.exe processes for wx-cli SQLCipher keys.

This is a Windows-only fallback for multi-login launchers.  Upstream wx-cli
currently scans the first Weixin.exe process it finds; multi-account launchers
often keep many Weixin.exe processes alive, so the first process can belong to
a different account than the selected db_storage tree.

Raw keys are never printed or written to the status report. Validated keys are
only written to a local wx-cli profile when --write-wx-cli is explicitly used.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any


if os.name != "nt":
    raise SystemExit("win_wx_multi_key_scan.py only runs on Windows")


MAX_PATH = 260
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100
PAGE_NOCACHE = 0x200
PAGE_WRITECOMBINE = 0x400

PAGE_SIZE = 4096
SALT_SIZE = 16
RESERVE_SIZE = 80
IV_SIZE = 16
HMAC_SIZE = 64
MAC_SALT_XOR = 0x3A
SQLITE_HEADER_PREFIX = b"SQLite format 3"

CHUNK_SIZE = 2 * 1024 * 1024
OVERLAP_SIZE = 256
ASCII_KEY_RE = re.compile(rb"x'([0-9a-fA-F]{64,192})'")
BARE_HEX_KEY_RE = re.compile(rb"(?<![0-9a-fA-F])([0-9a-fA-F]{64})(?![0-9a-fA-F])")
UTF16LE_KEY_RE = re.compile(
    b"x\x00'\x00"
    + rb"((?:[0-9a-fA-F]\x00){64})"
    + rb"((?:[0-9a-fA-F]\x00){32})"
    + b"'\x00"
)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * MAX_PATH),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
kernel32.Process32First.restype = wintypes.BOOL
kernel32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
kernel32.Process32Next.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
shell32.IsUserAnAdmin.argtypes = []
shell32.IsUserAnAdmin.restype = wintypes.BOOL


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def last_error() -> int:
    return ctypes.get_last_error()


def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def close_handle(handle: int | None) -> None:
    if handle and handle != INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(handle)


def process_name_from_entry(entry: PROCESSENTRY32) -> str:
    raw = bytes(entry.szExeFile)
    return raw.split(b"\x00", 1)[0].decode("mbcs", errors="replace")


def enumerate_processes(process_name: str) -> list[int]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        raise OSError(last_error(), "CreateToolhelp32Snapshot failed")

    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(entry)):
            return []

        pids: list[int] = []
        target = process_name.lower()
        while True:
            name = process_name_from_entry(entry)
            if name.lower() == target:
                pids.append(int(entry.th32ProcessID))
            if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                break
        return pids
    finally:
        close_handle(snap)


def read_db_salt(db_path: Path) -> bytes | None:
    try:
        with db_path.open("rb") as f:
            header = f.read(SALT_SIZE)
    except OSError:
        return None
    if len(header) != SALT_SIZE:
        return None
    if header.startswith(SQLITE_HEADER_PREFIX):
        return None
    return header


def collect_db_salts(db_dir: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, str]]:
    by_salt: dict[str, list[dict[str, str]]] = {}
    rel_to_salt: dict[str, str] = {}
    for db_path in sorted(db_dir.rglob("*.db")):
        salt = read_db_salt(db_path)
        if not salt:
            continue
        salt_hex = salt.hex()
        rel_path = db_path.relative_to(db_dir).as_posix()
        rel_to_salt[rel_path] = salt_hex
        by_salt.setdefault(salt_hex, []).append(
            {
                "db_path": str(db_path),
                "rel_path": rel_path,
            }
        )
    return by_salt, rel_to_salt


def is_scannable_page(protect: int, include_readonly: bool) -> bool:
    if protect & PAGE_GUARD or protect & PAGE_NOACCESS:
        return False
    base = protect & ~(PAGE_GUARD | PAGE_NOCACHE | PAGE_WRITECOMBINE)
    writable = {
        PAGE_READWRITE,
        PAGE_WRITECOPY,
        PAGE_EXECUTE_READWRITE,
        PAGE_EXECUTE_WRITECOPY,
    }
    readable = writable | {PAGE_READONLY, PAGE_EXECUTE_READ}
    return base in (readable if include_readonly else writable)


def decode_utf16le_hex(blob: bytes) -> str:
    return blob.replace(b"\x00", b"").decode("ascii").lower()


def find_key_patterns(
    data: bytes,
    absolute_base: int,
    target_salts: set[str],
    scan_utf16: bool,
    scan_bare_hex: bool,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for match in ASCII_KEY_RE.finditer(data):
        hex_str = match.group(1).decode("ascii").lower()
        if len(hex_str) == 64:
            hits.append(
                {
                    "key_hex": hex_str,
                    "salt_hex": "",
                    "address": absolute_base + match.start(),
                    "encoding": "ascii",
                    "shape": "key_only",
                }
            )
            continue

        key_hex = hex_str[:64]
        salt_hex = hex_str[64:] if len(hex_str) == 96 else hex_str[-32:]
        if len(salt_hex) == 32 and salt_hex in target_salts:
            hits.append(
                {
                    "key_hex": key_hex,
                    "salt_hex": salt_hex,
                    "address": absolute_base + match.start(),
                    "encoding": "ascii",
                    "shape": f"hex_{len(hex_str)}",
                }
            )

    if scan_bare_hex:
        for match in BARE_HEX_KEY_RE.finditer(data):
            key_hex = match.group(1).decode("ascii").lower()
            hits.append(
                {
                    "key_hex": key_hex,
                    "salt_hex": "",
                    "address": absolute_base + match.start(),
                    "encoding": "ascii",
                    "shape": "bare_hex_64",
                }
            )

    if scan_utf16:
        for match in UTF16LE_KEY_RE.finditer(data):
            key_hex = decode_utf16le_hex(match.group(1))
            salt_hex = decode_utf16le_hex(match.group(2))
            if salt_hex in target_salts:
                hits.append(
                    {
                        "key_hex": key_hex,
                        "salt_hex": salt_hex,
                        "address": absolute_base + match.start(),
                        "encoding": "utf16le",
                        "shape": "hex_96",
                    }
                )
    return hits


def scan_region(
    process: int,
    base: int,
    size: int,
    target_salts: set[str],
    scan_utf16: bool,
    scan_bare_hex: bool,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    offset = 0
    while offset < size:
        chunk_size = min(CHUNK_SIZE, size - offset)
        buf = ctypes.create_string_buffer(chunk_size)
        bytes_read = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            process,
            ctypes.c_void_p(base + offset),
            buf,
            chunk_size,
            ctypes.byref(bytes_read),
        )
        if ok and bytes_read.value:
            data = ctypes.string_at(buf, bytes_read.value)
            hits.extend(find_key_patterns(data, base + offset, target_salts, scan_utf16, scan_bare_hex))

        if chunk_size > OVERLAP_SIZE:
            offset += chunk_size - OVERLAP_SIZE
        else:
            offset += chunk_size
    return hits


def scan_process(
    pid: int,
    target_salts: set[str],
    include_readonly: bool,
    scan_utf16: bool,
    scan_bare_hex: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    process = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not process:
        return [], {
            "pid": pid,
            "opened": False,
            "error": last_error(),
            "regions": 0,
            "scanned_mb": 0,
        }

    started = time.time()
    hits: list[dict[str, Any]] = []
    regions = 0
    scanned_bytes = 0
    last_progress = started
    try:
        addr = 0
        while True:
            mbi = MEMORY_BASIC_INFORMATION()
            ret = kernel32.VirtualQueryEx(
                process,
                ctypes.c_void_p(addr),
                ctypes.byref(mbi),
                ctypes.sizeof(MEMORY_BASIC_INFORMATION),
            )
            if ret == 0:
                break

            base = int(mbi.BaseAddress or 0)
            region_size = int(mbi.RegionSize or 0)
            if (
                region_size > 0
                and mbi.State == MEM_COMMIT
                and is_scannable_page(int(mbi.Protect), include_readonly)
            ):
                regions += 1
                scanned_bytes += region_size
                hits.extend(scan_region(process, base, region_size, target_salts, scan_utf16, scan_bare_hex))
                now = time.time()
                if now - last_progress >= 8:
                    eprint(
                        f"[pid {pid}] scanned {scanned_bytes // (1024 * 1024)} MB, "
                        f"hits={len(hits)}"
                    )
                    last_progress = now

            next_addr = base + region_size
            if region_size <= 0 or next_addr <= addr:
                break
            addr = next_addr
    finally:
        close_handle(process)

    return hits, {
        "pid": pid,
        "opened": True,
        "error": 0,
        "regions": regions,
        "scanned_mb": scanned_bytes // (1024 * 1024),
        "elapsed_sec": round(time.time() - started, 3),
    }


def validate_derived_key_hmac(db_path: Path, key_hex: str) -> bool:
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        return False
    if len(key) != 32:
        return False
    try:
        with db_path.open("rb") as f:
            page = f.read(PAGE_SIZE)
    except OSError:
        return False
    if len(page) < PAGE_SIZE:
        return False

    salt = page[:SALT_SIZE]
    mac_salt = bytes(b ^ MAC_SALT_XOR for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, dklen=32)
    hmac_start = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
    hmac_end = hmac_start + HMAC_SIZE
    stored_hmac = page[hmac_start:hmac_end]
    data_end = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
    hmac_data = page[SALT_SIZE:data_end]

    mac = hmac.new(mac_key, digestmod=hashlib.sha512)
    mac.update(hmac_data)
    mac.update((1).to_bytes(4, "little"))
    return hmac.compare_digest(stored_hmac, mac.digest())


def unique_hits(raw_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, str, str]] = set()
    out: list[dict[str, Any]] = []
    for hit in raw_hits:
        marker = (int(hit["pid"]), str(hit["key_hex"]), str(hit["salt_hex"]))
        if marker in seen:
            continue
        seen.add(marker)
        out.append(hit)
    return out


def validate_hits(
    hits: list[dict[str, Any]],
    by_salt: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for hit in hits:
        salt_hex = hit["salt_hex"]
        key_hex = hit["key_hex"]
        rel_paths: list[str] = []
        target_items = []
        if salt_hex:
            target_items = [(salt_hex, item) for item in by_salt.get(salt_hex, [])]
        else:
            target_items = [
                (candidate_salt, item)
                for candidate_salt, items in by_salt.items()
                for item in items
            ]

        validated_salt = salt_hex
        for candidate_salt, item in target_items:
            db_path = Path(item["db_path"])
            if validate_derived_key_hmac(db_path, key_hex):
                validated_salt = candidate_salt
                rel_paths.append(item["rel_path"])
        if rel_paths:
            validated.append(
                {
                    "pid": hit["pid"],
                    "salt_hex": validated_salt,
                    "key_hex": key_hex,
                    "rel_paths": sorted(rel_paths),
                    "address": hit["address"],
                    "encoding": hit["encoding"],
                    "shape": hit.get("shape", ""),
                }
            )
    return validated


def key_fingerprint(key_hex: str) -> str:
    return hashlib.sha256(key_hex.encode("ascii")).hexdigest()[:16]


def build_all_keys(
    validated: list[dict[str, Any]],
    rel_to_salt: dict[str, str],
    fill_all_dbs: bool,
) -> tuple[dict[str, dict[str, str]], bool]:
    unique_keys = {item["key_hex"] for item in validated}
    if fill_all_dbs and len(unique_keys) == 1:
        account_key = next(iter(unique_keys))
        return {rel: {"enc_key": account_key} for rel in sorted(rel_to_salt)}, True

    all_keys: dict[str, dict[str, str]] = {}
    for item in validated:
        key_hex = item["key_hex"]
        for rel_path in item["rel_paths"]:
            all_keys[rel_path] = {"enc_key": key_hex}
    return dict(sorted(all_keys.items())), False


def write_wx_cli_files(
    wx_cli_home: Path,
    db_dir: Path,
    all_keys: dict[str, dict[str, str]],
) -> dict[str, str]:
    wx_cli_home.mkdir(parents=True, exist_ok=True)
    keys_path = wx_cli_home / "all_keys.json"
    config_path = wx_cli_home / "config.json"

    keys_path.write_text(
        json.dumps(all_keys, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    cfg: dict[str, Any] = {}
    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
        except Exception:
            cfg = {}
    cfg["db_dir"] = str(db_dir)
    cfg["keys_file"] = "all_keys.json"
    cfg.setdefault("decrypted_dir", "decrypted")
    config_path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"keys_file": str(keys_path), "config_file": str(config_path)}


def sanitized_validated(validated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in validated:
        out.append(
            {
                "pid": item["pid"],
                "salt_hex": item["salt_hex"],
                "rel_paths": item["rel_paths"],
                "address": hex(int(item["address"])),
                "encoding": item["encoding"],
                "shape": item.get("shape", ""),
                "key_fingerprint": key_fingerprint(item["key_hex"]),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan all Windows Weixin.exe processes for wx-cli SQLCipher keys "
            "matching a target db_storage tree."
        )
    )
    parser.add_argument("--db-dir", required=True, help="target xwechat db_storage directory")
    parser.add_argument("--pid", type=int, action="append", help="specific Weixin.exe PID to scan")
    parser.add_argument("--process-name", default="Weixin.exe", help="process name to enumerate")
    parser.add_argument("--out", help="write sanitized JSON status here")
    parser.add_argument("--wx-cli-home", default=str(Path.home() / ".wx-cli"))
    parser.add_argument("--write-wx-cli", action="store_true", help="write all_keys.json/config.json")
    parser.add_argument(
        "--fill-all-dbs",
        action="store_true",
        help="if one account key validates, write that key for every DB under db_storage",
    )
    parser.add_argument(
        "--include-readonly",
        action="store_true",
        help="also scan read-only memory pages; slower but can catch uncommon layouts",
    )
    parser.add_argument(
        "--no-utf16",
        action="store_true",
        help="skip UTF-16LE x'<key><salt>' patterns",
    )
    parser.add_argument(
        "--include-bare-hex",
        action="store_true",
        help="also scan standalone 64-hex strings; noisy but still HMAC-validated",
    )
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    db_dir = Path(args.db_dir).expanduser().resolve()
    if not db_dir.is_dir():
        eprint(f"[error] db_storage directory not found: {db_dir}")
        return 1

    by_salt, rel_to_salt = collect_db_salts(db_dir)
    if not by_salt:
        eprint(f"[error] no encrypted .db files found under: {db_dir}")
        return 1

    pids = sorted(set(args.pid or enumerate_processes(args.process_name)))
    if not pids:
        eprint(f"[error] no {args.process_name} processes found")
        return 2

    eprint(f"[info] admin={is_admin()} pids={pids}")
    eprint(f"[info] target dbs={len(rel_to_salt)} unique_salts={len(by_salt)} db_dir={db_dir}")

    raw_hits: list[dict[str, Any]] = []
    process_stats: list[dict[str, Any]] = []
    for pid in pids:
        eprint(f"[scan] pid={pid}")
        hits, stats = scan_process(
            pid=pid,
            target_salts=set(by_salt),
            include_readonly=args.include_readonly,
            scan_utf16=not args.no_utf16,
            scan_bare_hex=args.include_bare_hex,
        )
        for hit in hits:
            hit["pid"] = pid
        raw_hits.extend(hits)
        process_stats.append(stats)
        if stats.get("opened"):
            eprint(
                f"[done] pid={pid} scanned={stats['scanned_mb']} MB "
                f"regions={stats['regions']} hits={len(hits)} elapsed={stats['elapsed_sec']}s"
            )
        else:
            eprint(f"[skip] pid={pid} OpenProcess failed error={stats.get('error')}")

    hits = unique_hits(raw_hits)
    validated = validate_hits(hits, by_salt)
    all_keys, fill_all_dbs_applied = build_all_keys(
        validated,
        rel_to_salt,
        fill_all_dbs=args.fill_all_dbs,
    )

    written: dict[str, str] = {}
    if args.write_wx_cli and all_keys:
        written = write_wx_cli_files(Path(args.wx_cli_home).expanduser(), db_dir, all_keys)

    result: dict[str, Any] = {
        "db_dir": str(db_dir),
        "admin": is_admin(),
        "pid_count": len(pids),
        "db_count": len(rel_to_salt),
        "unique_salt_count": len(by_salt),
        "raw_hit_count": len(hits),
        "validated_hit_count": len(validated),
        "written_key_count": len(all_keys),
        "fill_all_dbs": bool(args.fill_all_dbs),
        "fill_all_dbs_applied": fill_all_dbs_applied,
        "process_stats": process_stats,
        "validated": sanitized_validated(validated),
        "written": written,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")

    return 0 if validated else 3


if __name__ == "__main__":
    raise SystemExit(main())
