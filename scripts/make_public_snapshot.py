#!/usr/bin/env python3

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "out" / "public-release" / "wechat-local-service-kit-public"

EXCLUDE_PREFIXES = (
    ".git/",
    ".supervision/",
    ".venv/",
    "decrypted/",
    "out/",
)

EXCLUDE_NAMES = {
    ".DS_Store",
    ".wx-cli-profile",
    "AGENTS.md",
    "CLAUDE.md",
}

EXCLUDE_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".log",
    ".pyc",
    ".sqlite",
    ".sqlite3",
}

EXCLUDE_GLOBS = (
    "HANDOFF-",
)

PRIVATE_SCRIPT_NAMES = {
    "build_customer_asset_signal_report.py",
    "build_customer_order_mapping_report.py",
    "build_customer_phone_binding_report.py",
}

PUBLIC_EXTRA_FILES = {
    ".wx-cli-tools/package-lock.json",
    ".wx-cli-tools/package.json",
    "LICENSE",
    "MIGRATION.md",
    "PUBLIC_RELEASE.md",
    "docs/wechat-digest-layer.md",
    "requirements.txt",
    "scripts/build_wechat_digest.py",
    "scripts/compare_export_sources.py",
    "scripts/export_appmsg_archive.py",
    "scripts/make_public_snapshot.py",
    "scripts/public_release_check.py",
    "scripts/wechat_privacy.py",
    "scripts/wechat_schema.py",
    "scripts/wechat_tool_status.py",
}


def git_ls_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def should_exclude(rel: str) -> bool:
    path = Path(rel)
    if rel.startswith(EXCLUDE_PREFIXES):
        return True
    if path.name in EXCLUDE_NAMES:
        return True
    if any(part.startswith(".wx-cli-") and part != ".wx-cli-tools" for part in path.parts):
        return True
    if any(path.name.startswith(prefix) for prefix in EXCLUDE_GLOBS):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    if len(path.parts) >= 2 and path.parts[0] == "scripts" and path.name in PRIVATE_SCRIPT_NAMES:
        return True
    if rel == "docs/wechat-multi-account-data-status.md":
        return True
    return False


def collect_files() -> list[str]:
    candidates = set(git_ls_files()) | PUBLIC_EXTRA_FILES
    files = []
    for rel in sorted(candidates):
        source = REPO_ROOT / rel
        if not source.exists() or not source.is_file():
            continue
        if should_exclude(rel):
            continue
        files.append(rel)
    return files


def assert_safe_output_dir(output: Path) -> Path:
    output = output.expanduser().resolve()
    allowed_root = (REPO_ROOT / "out" / "public-release").resolve()
    if output == REPO_ROOT.resolve() or allowed_root not in [output, *output.parents]:
        raise ValueError(f"output must be under {allowed_root}: {output}")
    return output


def copy_files(files: list[str], output: Path, *, clean: bool) -> None:
    if output.exists() and clean:
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    for rel in files:
        source = REPO_ROOT / rel
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def write_manifest(files: list[str], output: Path) -> None:
    payload = {
        "schema_version": "public_snapshot_manifest_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_repo": "<private_working_tree>",
        "file_count": len(files),
        "files": files,
        "excluded_by_policy": [
            ".git/",
            ".supervision/",
            ".venv/",
            ".wx-cli-*",
            ".wx-cli-profile",
            "out/",
            "decrypted/",
            "HANDOFF-*",
            "docs/wechat-multi-account-data-status.md",
            "private customer asset/order/phone report scripts",
        ],
    }
    (output / "PUBLIC_MANIFEST.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_public_check(output: Path) -> int:
    checker = output / "scripts" / "public_release_check.py"
    completed = subprocess.run(
        [sys.executable, str(checker), "--root", str(output), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    report_path = output / "PUBLIC_CHECK_REPORT.json"
    report_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.stderr:
        (output / "PUBLIC_CHECK_STDERR.txt").write_text(completed.stderr, encoding="utf-8")
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a sanitized local public snapshot.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="snapshot output directory under out/public-release")
    parser.add_argument("--clean", action="store_true", help="remove the existing output directory first")
    args = parser.parse_args()

    output = assert_safe_output_dir(Path(args.output))
    files = collect_files()
    copy_files(files, output, clean=args.clean)
    write_manifest(files, output)
    check_code = run_public_check(output)

    print(json.dumps({
        "snapshot": str(output),
        "file_count": len(files),
        "check_report": str(output / "PUBLIC_CHECK_REPORT.json"),
        "check_exit_code": check_code,
    }, ensure_ascii=False, indent=2))
    return check_code


if __name__ == "__main__":
    raise SystemExit(main())
