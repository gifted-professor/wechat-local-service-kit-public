#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "decrypted",
    "node_modules",
    "out",
}

PRIVATE_DIR_NAMES = {
    ".supervision",
}

FORBIDDEN_FILE_NAMES = {
    ".env",
    ".wx-cli-profile",
    "all_keys.json",
}

FORBIDDEN_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".log",
    ".sqlite",
    ".sqlite3",
}

PRIVATE_FILE_PATTERNS = [
    re.compile(r"^HANDOFF-.*\.md$"),
    re.compile(r"^CLAUDE\.md$"),
    re.compile(r"^AGENTS\.md$"),
]

OWNER_PATH_RE = r"(?<![<\w])(?:/" + r"Users/a1234|/" + r"Users/gpfs|/" + r"Volumes/GPFS)\b"

SECRET_PATTERNS = [
    (
        "real_wxid",
        re.compile(r"\bwxid_(?!xxx\b|example\b)[A-Za-z0-9][A-Za-z0-9_\-]{8,}\b"),
        "blocker",
    ),
    (
        "owner_local_path",
        re.compile(OWNER_PATH_RE),
        "blocker",
    ),
    (
        "long_hex_secret",
        re.compile(r"\b(?:0x)?[0-9a-fA-F]{64,}\b"),
        "blocker",
    ),
    (
        "sqlcipher_literal",
        re.compile(r"x'[0-9a-fA-F]{32,}'", re.IGNORECASE),
        "blocker",
    ),
    (
        "openai_secret_value",
        re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
        "blocker",
    ),
    (
        "bearer_token_value",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{24,}\b"),
        "blocker",
    ),
]

REDACTION_PATTERNS = [
    (re.compile(r"\bwxid_(?!xxx\b|example\b)[A-Za-z0-9][A-Za-z0-9_\-]{8,}\b"), "<redacted_wxid>"),
    (re.compile(OWNER_PATH_RE), "<redacted_local_path>"),
    (re.compile(r"\b(?:0x)?[0-9a-fA-F]{64,}\b"), "<redacted_hex>"),
    (re.compile(r"x'[0-9a-fA-F]{32,}'", re.IGNORECASE), "<redacted_sqlcipher_literal>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "<redacted_openai_secret>"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{24,}\b"), "Bearer <redacted_token>"),
]


@dataclass
class Finding:
    severity: str
    code: str
    path: str
    line: int | None
    detail: str


def relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_private_dir(path: Path) -> bool:
    return any(part in PRIVATE_DIR_NAMES for part in path.parts)


def is_wx_cli_profile_path(path: Path) -> bool:
    return any(part.startswith(".wx-cli-") and part != ".wx-cli-tools" for part in path.parts)


def is_skipped_dir(path: Path, *, include_private_local: bool) -> bool:
    if path.name in SKIP_DIR_NAMES:
        return True
    if path.name == ".wx-cli-tools":
        return False
    if path.name.startswith(".wx-cli-") and path.name != ".wx-cli-tools":
        return not include_private_local
    if path.name in PRIVATE_DIR_NAMES:
        return not include_private_local
    return False


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def redact_detail(value: str) -> str:
    out = value
    for pattern, replacement in REDACTION_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def iter_files(root: Path, *, include_private_local: bool) -> Iterable[Path]:
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        kept_dirs = []
        for name in dirnames:
            child = current_path / name
            if not is_skipped_dir(child.relative_to(root), include_private_local=include_private_local):
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in filenames:
            yield current_path / name


def forbidden_path_findings(path: Path, root: Path) -> list[Finding]:
    rel = relpath(path, root)
    out: list[Finding] = []
    name = path.name
    lower_name = name.lower()
    if is_wx_cli_profile_path(path.relative_to(root)):
        out.append(Finding("blocker", "wx_cli_profile_path", rel, None, "wx-cli profiles and caches are private local state"))
    if is_private_dir(path.relative_to(root)):
        out.append(Finding("blocker", "supervision_path", rel, None, "supervision/run state is private operator context"))
    if lower_name in FORBIDDEN_FILE_NAMES:
        out.append(Finding("blocker", "forbidden_file_name", rel, None, f"forbidden public file name: {name}"))
    if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        out.append(Finding("blocker", "forbidden_file_suffix", rel, None, f"forbidden public file suffix: {path.suffix or name}"))
    if any(pattern.match(name) for pattern in PRIVATE_FILE_PATTERNS):
        out.append(Finding("blocker", "private_operator_file", rel, None, f"private operator file should not be public: {name}"))
    return out


def scan_text_file(path: Path, root: Path) -> list[Finding]:
    rel = relpath(path, root)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [Finding("warning", "non_utf8_text", rel, None, "text-like file is not UTF-8")]
    except OSError as exc:
        return [Finding("warning", "read_failed", rel, None, str(exc))]

    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for code, pattern, severity in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                preview = line.strip()
                if len(preview) > 180:
                    preview = preview[:177] + "..."
                preview = redact_detail(preview)
                findings.append(Finding(severity, code, rel, lineno, preview))
    return findings


def run_check(root: Path, *, include_private_local: bool) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in iter_files(root, include_private_local=include_private_local):
        findings.extend(forbidden_path_findings(path, root))
        if is_text_file(path):
            findings.extend(scan_text_file(path, root))
    return findings


def summarize(findings: list[Finding]) -> dict[str, int]:
    return {
        "blockers": sum(1 for item in findings if item.severity == "blocker"),
        "warnings": sum(1 for item in findings if item.severity == "warning"),
        "findings": len(findings),
    }


def print_text(findings: list[Finding]) -> None:
    summary = summarize(findings)
    print(f"public release check: {summary['blockers']} blockers, {summary['warnings']} warnings")
    for item in findings:
        location = item.path if item.line is None else f"{item.path}:{item.line}"
        print(f"[{item.severity}] {item.code} {location} - {item.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a tree for public-release blockers.")
    parser.add_argument("--root", default=".", help="Repository or snapshot root to scan")
    parser.add_argument("--include-private-local", action="store_true", help="also scan private local dirs such as .wx-cli-* and .supervision")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    if not root.exists():
        print(f"root not found: {root}", file=sys.stderr)
        return 2

    findings = run_check(root, include_private_local=args.include_private_local)
    payload = {
        "root": "<scan_root>",
        "summary": summarize(findings),
        "findings": [asdict(item) for item in findings],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_text(findings)
    return 1 if payload["summary"]["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
