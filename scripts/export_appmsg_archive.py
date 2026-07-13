#!/usr/bin/env python3

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from wechat_common import _ensure_dir, _extract_xml_field, _md5_hex, _normalize_text, _timestamp_to_iso, _write_json, _write_jsonl
from wechat_privacy import redact_obj
from wechat_schema import stable_id

URL_RE = re.compile(r"https?://[^\s<'\"<>]+", re.IGNORECASE)
APPMSG_HINT_RE = re.compile(r"<appmsg|<msg|<recorditem|<weappinfo|<finderFeed", re.IGNORECASE)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                yield {"_error": "json_decode", "_path": str(path), "_line": line_no}
                continue
            if isinstance(payload, dict):
                yield payload


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_favorite_rows(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    if path.suffix.lower() == ".jsonl":
        yield from _iter_jsonl(path)
        return
    payload = _read_json(path)
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(payload, dict):
        for key in ("items", "favorites", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return
        yield payload


def _candidate_texts(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("text", "content", "message", "desc", "description", "title", "link"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
    for key in ("attachment_meta", "raw_payload", "raw_type"):
        value = row.get(key)
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str) and nested.strip():
                    values.append(nested)
                elif isinstance(nested, dict):
                    for deep in nested.values():
                        if isinstance(deep, str) and deep.strip():
                            values.append(deep)
    return values


def _first_field(text: str, tags: list[str]) -> str:
    for tag in tags:
        value = _extract_xml_field(text, tag)
        if value:
            return value
    return ""


def _extract_urls(text: str) -> list[str]:
    out: list[str] = []
    for match in URL_RE.findall(text or ""):
        url = match.rstrip(").,;:，。；：")
        if url not in out:
            out.append(url)
    return out


def _classify_item(row: dict[str, Any], text: str, url: str, title: str, file_name: str) -> str:
    message_type = _normalize_text(row.get("render_type") or row.get("message_type") or row.get("type")).lower()
    if file_name or message_type == "file":
        return "file"
    if "mini" in message_type or _extract_xml_field(text, "weappinfo"):
        return "mini_program"
    if url:
        return "article" if any(host in url for host in ("mp.weixin.qq.com", "mp.weixinbridge.com")) else "link"
    if title:
        return "appmsg"
    return "unknown"


def extract_appmsg_from_row(row: dict[str, Any], *, source_kind: str, source_file: str = "") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for text in _candidate_texts(row):
        if not text:
            continue
        urls = _extract_urls(text)
        looks_structured = bool(APPMSG_HINT_RE.search(text))
        title = _first_field(text, ["title", "datatitle", "pagetitle", "sourcedisplayname"])
        desc = _first_field(text, ["des", "desc", "datadesc", "pagedesc", "digest"])
        file_name = _first_field(text, ["filename", "fileName", "datatitle"])
        file_size = _first_field(text, ["totallen", "filesize", "fileSize", "fullsize"])
        appid = _first_field(text, ["appid", "weappinfo"])
        thumb = _first_field(text, ["thumburl", "cdnthumburl", "pagethumb_url"])
        if not looks_structured and not urls and not title and not file_name:
            continue
        if not urls:
            urls = [""]
        for url in urls:
            kind = _classify_item(row, text, url, title, file_name)
            record = {
                "schema_version": "appmsg_archive_v1",
                "appmsg_id": stable_id(
                    source_kind,
                    row.get("conversation_id") or row.get("conversation_username") or row.get("favorite_id") or "",
                    row.get("message_id") or row.get("id") or "",
                    title,
                    url,
                    file_name,
                    prefix="appmsg",
                ),
                "kind": kind,
                "title": _normalize_text(title or row.get("title") or file_name),
                "desc": _normalize_text(desc or row.get("desc") or row.get("text")),
                "url": _normalize_text(url),
                "source_name": _normalize_text(row.get("conversation_name") or row.get("source") or row.get("display_name")),
                "source_kind": source_kind,
                "source_file": str(redact_obj(source_file)),
                "conversation_id": _normalize_text(row.get("conversation_id")),
                "conversation_username": _normalize_text(row.get("conversation_username")),
                "message_id": _normalize_text(row.get("message_id") or row.get("id")),
                "favorite_id": _normalize_text(row.get("favorite_id")),
                "timestamp": _timestamp_to_iso(row.get("timestamp")) or _normalize_text(row.get("timestamp")),
                "file_name": _normalize_text(file_name),
                "file_size": _normalize_text(file_size),
                "appid": _normalize_text(appid),
                "thumb": _normalize_text(thumb),
                "source_provider": row.get("source_provider") or source_kind,
            }
            if record["url"] or record["title"] or record["file_name"]:
                records.append(record)
    return _dedupe_records(records)


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for record in records:
        key = record.get("url") or record.get("appmsg_id") or json.dumps(record, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


def _iter_conversation_rows(export_root: Path) -> Iterable[tuple[dict[str, Any], str]]:
    conversations_dir = export_root / "conversations"
    if not conversations_dir.exists():
        return
    for path in sorted(conversations_dir.glob("*.jsonl")):
        for row in _iter_jsonl(path):
            yield row, str(path.relative_to(export_root))


def build_archive(export_root: Path, out_root: Path, *, favorites_path: Optional[Path] = None) -> dict[str, Any]:
    appmsg_rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    scanned = Counter()

    for row, source_file in _iter_conversation_rows(export_root):
        if row.get("_error"):
            warnings.append(f"{source_file}:{row.get('_line')}: {row.get('_error')}")
            continue
        scanned["messages"] += 1
        appmsg_rows.extend(extract_appmsg_from_row(row, source_kind="message", source_file=source_file))

    fav_path = favorites_path or (export_root / "favorites.jsonl")
    if fav_path.exists():
        for row in _iter_favorite_rows(fav_path):
            scanned["favorites"] += 1
            appmsg_rows.extend(extract_appmsg_from_row(row, source_kind="favorite", source_file=str(fav_path.name)))

    appmsg_rows = _dedupe_records(appmsg_rows)
    type_counts = Counter(row.get("kind") or "unknown" for row in appmsg_rows)
    articles = [row for row in appmsg_rows if row.get("kind") in {"article", "link"} and row.get("url")]
    files = [row for row in appmsg_rows if row.get("kind") == "file" or row.get("file_name")]

    _ensure_dir(out_root)
    _write_jsonl(out_root / "appmsg_index.jsonl", appmsg_rows)
    _write_jsonl(out_root / "articles.jsonl", articles)
    _write_jsonl(out_root / "files_index.jsonl", files)
    manifest = {
        "schema_version": "appmsg_archive_manifest_v1",
        "generated_at": utc_now_iso(),
        "source_export_root": str(redact_obj(str(export_root))),
        "out_root": str(redact_obj(str(out_root))),
        "favorites_path": str(redact_obj(str(fav_path))) if fav_path.exists() else "",
        "scanned": dict(scanned),
        "counts": {
            "appmsg": len(appmsg_rows),
            "articles": len(articles),
            "files": len(files),
            "by_kind": dict(sorted(type_counts.items())),
        },
        "privacy": {
            "network": "not_used",
            "downloads": "not_used",
            "raw_payload": "not_written",
        },
        "warnings": warnings,
    }
    _write_json(out_root / "manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract appmsg/link/file metadata from WeChat export files without network access")
    parser.add_argument("--export-root", required=True, help="export root containing conversations/*.jsonl")
    parser.add_argument("--out-root", required=True, help="output directory for appmsg archive")
    parser.add_argument("--favorites", help="optional favorites JSON/JSONL path; defaults to <export-root>/favorites.jsonl")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_archive(
        Path(args.export_root).expanduser().resolve(),
        Path(args.out_root).expanduser().resolve(),
        favorites_path=Path(args.favorites).expanduser().resolve() if args.favorites else None,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
