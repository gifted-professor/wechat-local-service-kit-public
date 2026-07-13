#!/usr/bin/env python3

import hashlib
import hmac
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from Crypto.Cipher import AES

SQLITE_HEADER = b"SQLite format 3\x00"
PAGE_SIZE = 4096
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
KDF_ITER = 256000
RESERVE_SIZE = 80
MAC_SALT_XOR = 0x3A
KEY_MODE_AUTO = "auto"
KEY_MODE_RAW = "raw"
KEY_MODE_DERIVED = "derived"
KEY_MODE_CHOICES = (KEY_MODE_AUTO, KEY_MODE_RAW, KEY_MODE_DERIVED)


@dataclass
class DecryptValidation:
    ok: bool
    reason: str
    page: int = 0
    key_mode: Optional[str] = None


class ChatCryptoError(RuntimeError):
    pass



def parse_frida_key_log(log_path: Path) -> list[dict[str, Any]]:
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    entries: list[dict[str, Any]] = []
    for block in blocks:
        entry: dict[str, Any] = {}
        for line in block.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            entry[key.strip()] = value.strip()
        if entry:
            entries.append(entry)
    return entries



def read_sqlite_tables(db_path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()



def is_plain_sqlite(db_path: Path) -> bool:
    try:
        with db_path.open("rb") as f:
            return f.read(16) == SQLITE_HEADER
    except Exception:
        return False



def read_db_salt(db_path: Path) -> bytes:
    with db_path.open("rb") as f:
        salt = f.read(SALT_SIZE)
    if len(salt) != SALT_SIZE:
        raise ChatCryptoError(f"database is smaller than expected: {db_path}")
    if salt == SQLITE_HEADER:
        raise ChatCryptoError(f"database is already plain sqlite and does not contain an SQLCipher salt: {db_path}")
    return salt



def _normalize_key_hex(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        raise ValueError("empty key")
    if len(text) % 2 != 0:
        raise ValueError("hex key must contain an even number of characters")
    return text



def _derive_keys(enc_key_hex: str, salt: bytes, key_mode: str = KEY_MODE_RAW) -> tuple[bytes, bytes]:
    if key_mode not in KEY_MODE_CHOICES:
        raise ValueError(f"unsupported key mode: {key_mode}")
    key_material = bytes.fromhex(_normalize_key_hex(enc_key_hex))
    if key_mode == KEY_MODE_DERIVED:
        if len(key_material) != 32:
            raise ValueError("derived key must be exactly 32 bytes / 64 hex characters")
        derived_key = key_material
    elif key_mode == KEY_MODE_RAW:
        derived_key = hashlib.pbkdf2_hmac("sha512", key_material, salt, KDF_ITER, dklen=32)
    else:
        raise ValueError("auto mode must be resolved before deriving keys")
    mac_salt = bytes(b ^ MAC_SALT_XOR for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", derived_key, mac_salt, 2, dklen=32)
    return derived_key, mac_key



def _validate_sqlcipher_key_once(db_path: Path, enc_key_hex: str, key_mode: str) -> DecryptValidation:
    with db_path.open("rb") as f:
        first_page = f.read(PAGE_SIZE)

    if len(first_page) < PAGE_SIZE:
        return DecryptValidation(False, "database page too small")

    salt = first_page[:SALT_SIZE]
    try:
        derived_key, mac_key = _derive_keys(enc_key_hex, salt, key_mode)
    except ValueError as exc:
        return DecryptValidation(False, f"invalid hex key: {exc}")

    hmac_start = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
    hmac_end = hmac_start + HMAC_SIZE
    stored_hmac = first_page[hmac_start:hmac_end]
    data_end = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
    hmac_data = first_page[SALT_SIZE:data_end]

    mac = hmac.new(mac_key, digestmod=hashlib.sha512)
    mac.update(hmac_data)
    mac.update((1).to_bytes(4, "little"))
    expected_hmac = mac.digest()
    if stored_hmac != expected_hmac:
        return DecryptValidation(False, "page 1 hmac mismatch", page=1)

    iv = first_page[PAGE_SIZE - RESERVE_SIZE:PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    encrypted_page = first_page[SALT_SIZE:PAGE_SIZE - RESERVE_SIZE]
    try:
        cipher = AES.new(derived_key, AES.MODE_CBC, iv)
        cipher.decrypt(encrypted_page)
    except Exception as exc:
        return DecryptValidation(False, f"page 1 aes failure: {exc}", page=1)

    return DecryptValidation(True, "ok", page=1, key_mode=key_mode)



def validate_sqlcipher_key(db_path: Path, enc_key_hex: str, key_mode: str = KEY_MODE_AUTO) -> DecryptValidation:
    if key_mode == KEY_MODE_AUTO:
        last_failure = DecryptValidation(False, "no key modes succeeded")
        for candidate_mode in (KEY_MODE_RAW, KEY_MODE_DERIVED):
            result = _validate_sqlcipher_key_once(db_path, enc_key_hex, candidate_mode)
            if result.ok:
                return result
            last_failure = result
        return last_failure
    return _validate_sqlcipher_key_once(db_path, enc_key_hex, key_mode)


def resolve_key_from_frida_log(db_path: Path, log_path: Path) -> dict[str, Any]:
    salt_hex = read_db_salt(db_path).hex()
    matches = []
    for index, entry in enumerate(parse_frida_key_log(log_path), start=1):
        try:
            rounds = int(entry.get("rounds") or 0)
        except ValueError:
            continue
        if rounds != KDF_ITER:
            continue
        if (entry.get("salt") or "").lower() != salt_hex:
            continue
        matches.append({"index": index, **entry})

    if not matches:
        raise ChatCryptoError(f"no Frida key-log entries matched db salt {salt_hex} for {db_path}")

    for entry in matches:
        for field in ("dk", "pw"):
            candidate = entry.get(field)
            if not candidate:
                continue
            validation = validate_sqlcipher_key(db_path, candidate, KEY_MODE_AUTO)
            if validation.ok:
                return {
                    "db_path": str(db_path),
                    "salt": salt_hex,
                    "matched_entry_index": entry["index"],
                    "matched_field": field,
                    "key_hex": candidate,
                    "key_mode": validation.key_mode,
                }

    raise ChatCryptoError(f"Frida key-log matched salt {salt_hex} but no candidate key validated for {db_path}")



def decrypt_sqlcipher_db(db_path: Path, output_path: Path, enc_key_hex: str, key_mode: str = KEY_MODE_AUTO) -> dict[str, Any]:
    validation = validate_sqlcipher_key(db_path, enc_key_hex, key_mode)
    if not validation.ok:
        raise ChatCryptoError(validation.reason)

    encrypted_data = db_path.read_bytes()
    salt = encrypted_data[:SALT_SIZE]
    resolved_key_mode = validation.key_mode or key_mode
    derived_key, mac_key = _derive_keys(enc_key_hex, salt, resolved_key_mode)

    total_pages = len(encrypted_data) // PAGE_SIZE
    decrypted = bytearray()
    decrypted.extend(SQLITE_HEADER)

    successful_pages = 0
    failed_pages = []

    for cur_page in range(total_pages):
        start = cur_page * PAGE_SIZE
        end = start + PAGE_SIZE
        page = encrypted_data[start:end]
        page_num = cur_page + 1
        if len(page) < PAGE_SIZE:
            break

        offset = SALT_SIZE if cur_page == 0 else 0
        hmac_start = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
        hmac_end = hmac_start + HMAC_SIZE
        stored_hmac = page[hmac_start:hmac_end]
        data_end = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
        hmac_data = page[offset:data_end]

        mac = hmac.new(mac_key, digestmod=hashlib.sha512)
        mac.update(hmac_data)
        mac.update(page_num.to_bytes(4, "little"))
        expected_hmac = mac.digest()
        if stored_hmac != expected_hmac:
            failed_pages.append({"page": page_num, "reason": "hmac"})
            continue

        iv = page[PAGE_SIZE - RESERVE_SIZE:PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
        encrypted_page = page[offset:PAGE_SIZE - RESERVE_SIZE]
        cipher = AES.new(derived_key, AES.MODE_CBC, iv)
        decrypted_page = cipher.decrypt(encrypted_page)
        decrypted.extend(decrypted_page)
        decrypted.extend(page[PAGE_SIZE - RESERVE_SIZE:])
        successful_pages += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(decrypted))

    return {
        "db_path": str(db_path),
        "output_path": str(output_path),
        "total_pages": total_pages,
        "successful_pages": successful_pages,
        "failed_pages": failed_pages,
        "ok": is_plain_sqlite(output_path),
        "key_mode": resolved_key_mode,
    }



def prepare_readable_db(
    db_path: Path,
    work_dir: Path,
    enc_key_hex: Optional[str] = None,
    key_mode: str = KEY_MODE_AUTO,
    frida_log_path: Optional[Path] = None,
) -> Path:
    if is_plain_sqlite(db_path):
        target = work_dir / db_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_path, target)
        return target

    resolved_key_hex = enc_key_hex
    resolved_key_mode = key_mode
    if not resolved_key_hex and frida_log_path:
        matched = resolve_key_from_frida_log(db_path, frida_log_path)
        resolved_key_hex = str(matched["key_hex"])
        resolved_key_mode = str(matched["key_mode"])

    if not resolved_key_hex:
        raise ChatCryptoError(f"database is encrypted and no key was provided: {db_path}")

    target = work_dir / db_path.name
    decrypt_sqlcipher_db(db_path, target, resolved_key_hex, resolved_key_mode)
    if not is_plain_sqlite(target):
        raise ChatCryptoError(f"decryption did not produce a readable sqlite database: {db_path}")
    return target
