#!/usr/bin/env python3

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from wechat_common import _ensure_dir, _write_json


PROFILE_SCHEMA_VERSION = "customer_profile_v1"

PHONE_NUMBER_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

PII_PATTERNS = [
    PHONE_NUMBER_RE,
    re.compile(r"\b\d{15,18}[0-9Xx]?\b"),
    re.compile(r"\d{11,18}"),
]

SECRET_PATTERNS = [
    re.compile(r"(?i)(token|api[_-]?key|access[_-]?token|authorization|code|pwd|password|secret)=([^\s&]+)"),
    re.compile(r"(?i)(sk-[A-Za-z0-9_-]{16,})"),
    re.compile(r"(?i)(apify_api_[A-Za-z0-9_-]+)"),
    re.compile(r"(https?://[^\s?]+)\?[^\s]+"),
]

BUDGET_RE = re.compile(r"(?:[¥￥]\s*)?\d+(?:\.\d+)?\s*(?:元|块|万|w|W)")
DATE_RE = re.compile(r"\d{1,2}\s*(?:月|/|-)\s*\d{1,2}\s*(?:日|号)?|\d{4}[-/]\d{1,2}[-/]\d{1,2}")
RECIPIENT_FIELD_PATTERNS = [
    re.compile(r"(?:收件人|收货人|联系人|姓名)\s*[:：]\s*([^\n,，]{2,20})"),
]
REGION_FIELD_PATTERNS = [
    re.compile(r"所在地区\s*[:：]\s*([^\n]+)"),
]
ADDRESS_FIELD_PATTERNS = [
    re.compile(r"(?:详细地址|收货地址|收件地址|地址)\s*[:：]\s*([^\n]+)"),
]
ADDRESS_HINT_RE = re.compile(r"(省|市|自治区|自治州|地区|盟|县|区|镇|乡|街道|路|道|栋|幢|单元|室|楼|村|巷|小区|宿舍|驿站|社区)")
GENERIC_ADDRESS_NOTE_RE = re.compile(
    r"^(?:(?:这|那)(?:个|里)?(?:是)?|以上|下面).{0,24}地址$|^(?:退货|发货|换货|售后|寄回).{0,20}地址$"
)
RECIPIENT_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9_·-]{2,16}")
NON_PERSON_NAME_HINTS = [
    "售后",
    "奥莱",
    "物流",
    "快递",
    "仓",
    "地址",
    "电话",
    "手机",
    "商品",
    "货号",
    "视频",
]
ADDRESS_NOTE_MARKERS = [
    "注意：",
    "注意:",
    "请保持",
    "寄出前",
    "检查好",
    "无穿洗痕迹",
    "不影响第二次销售",
    "质量问题",
    "拍商品视频",
    "不用打电话",
    "不要打电话",
    "请勿拨打",
    "电话联系",
]

AFTER_SALES_KEYWORDS = [
    "售后",
    "退货",
    "退款",
    "补发",
    "漏发",
    "少发",
    "没收到",
    "未收到",
    "物流",
    "快递",
    "换货",
    "质量",
    "投诉",
    "坏了",
]

PREFERENCE_KEYWORDS = [
    "希望",
    "想要",
    "只要",
    "最好",
    "优先",
    "喜欢",
    "接受",
]

REJECTION_KEYWORDS = [
    "不要",
    "不用",
    "别",
    "不想",
    "不接受",
    "不喜欢",
    "不能",
]

COMMITMENT_KEYWORDS = [
    "我给你",
    "帮你",
    "给你",
    "马上",
    "稍后",
    "明天",
    "今天",
    "安排",
    "确认",
    "处理",
    "补发",
    "退款",
    "发出",
    "回你",
]

FOLLOWUP_KEYWORDS = [
    "吗",
    "呢",
    "什么时候",
    "怎么",
    "有没有",
    "能不能",
    "可以吗",
    "咋",
    "?",
    "？",
]

MEMORY_USE_KEYWORD_GROUPS = {
    "order": [
        "订单",
        "订单号",
        "下单",
        "拍了",
        "购买",
        "买了",
        "付款",
        "支付",
        "发票",
        "尾款",
        "定金",
    ],
    "after_sales": [
        "售后",
        "退款",
        "退货",
        "换货",
        "补发",
        "漏发",
        "少发",
        "坏了",
        "破了",
        "质量",
        "投诉",
        "赔",
        "赔付",
        "退回",
        "拒收",
    ],
    "logistics": [
        "快递",
        "物流",
        "发货",
        "到货",
        "收到",
        "没收到",
        "未收到",
        "签收",
        "派送",
        "运费",
        "地址",
        "单号",
    ],
    "prior_context": [
        "之前",
        "上次",
        "刚才",
        "昨天",
        "前天",
        "今天",
        "明天",
        "稍后",
        "你说",
        "说好",
        "答应",
        "承诺",
        "什么时候",
        "怎么处理",
        "处理了吗",
        "进度",
        "还没",
        "催",
    ],
    "customer_specific": [
        "我的",
        "我这个",
        "我那个",
        "给我",
        "帮我查",
        "查一下",
        "申请",
        "截图",
        "记录",
        "客服",
        "官方",
    ],
}

STRONG_CUSTOMER_SPECIFIC_MEMORY_KEYWORDS = {
    "帮我查",
    "查一下",
    "申请",
    "截图",
    "记录",
    "客服",
    "官方",
}

BROAD_GENERAL_MEMORY_SKIP_KEYWORDS = [
    "未来",
    "人才",
    "能力",
    "职业",
    "学习",
    "成长",
    "趋势",
    "行业",
    "创业",
    "ai",
    "大模型",
    "建议",
    "怎么看",
    "怎么理解",
]

PLACEHOLDER_TEXTS = {
    "[图片]",
    "[语音]",
    "[视频]",
    "[表情]",
    "[消息]",
    "[应用消息]",
    "[系统消息]",
    "[位置]",
    "[文件]",
}


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def printable_ratio(text: str) -> float:
    sample = (text or "")[:600]
    if not sample:
        return 0.0
    printable = sum(1 for ch in sample if ch.isprintable() or ch in {"\n", "\r", "\t"})
    return printable / len(sample)


def looks_readable_text(text: str) -> bool:
    text = str(text or "").strip()
    if not text:
        return False
    if text in PLACEHOLDER_TEXTS:
        return False
    if len(text) > 1000:
        return False
    if printable_ratio(text) < 0.92:
        return False
    control_count = sum(1 for ch in text if ord(ch) < 32 and ch not in {"\n", "\r", "\t"})
    if control_count:
        return False
    return True


def redact_text(text: str) -> tuple[str, list[str]]:
    redacted = str(text or "")
    flags = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(redacted):
            redacted = pattern.sub(
                lambda m: f"{m.group(1)}=[REDACTED_SECRET]"
                if m.lastindex and m.lastindex >= 2 and "=" not in str(m.group(1))
                else f"{m.group(1)}?[REDACTED_QUERY]"
                if m.lastindex and str(m.group(1)).startswith("http")
                else "[REDACTED_SECRET]",
                redacted,
            )
            flags.append("secret")
    return redacted, flags


def compact_whitespace(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def extract_phone_numbers(text: str) -> list[str]:
    seen: list[str] = []
    for match in PHONE_NUMBER_RE.findall(str(text or "")):
        if match not in seen:
            seen.append(match)
    return seen


def looks_like_address_fragment(text: str) -> bool:
    value = compact_whitespace(text)
    if len(value) < 6:
        return False
    if GENERIC_ADDRESS_NOTE_RE.match(value):
        return False
    if any(keyword in value for keyword in ["收件", "收货", "详细地址", "所在地区", "寄到", "地址：", "地址:"]):
        return True
    return bool(ADDRESS_HINT_RE.search(value))


def extract_labeled_values(text: str, patterns: list[re.Pattern[str]]) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        for match in pattern.findall(str(text or "")):
            value = compact_whitespace(match)
            if value and value not in values:
                values.append(value)
    return values


def infer_inline_recipient(text: str) -> str:
    prefix = compact_whitespace(text).strip(" ,，:：;；")
    if not prefix:
        return ""
    tokens = RECIPIENT_TOKEN_RE.findall(prefix)
    if not tokens:
        return ""
    candidate = tokens[-1]
    if candidate.isdigit():
        return ""
    if ADDRESS_HINT_RE.search(candidate):
        return ""
    if any(hint in candidate for hint in NON_PERSON_NAME_HINTS):
        return ""
    return candidate


def build_shipping_summary(recipient: str, phone: str, address: str) -> str:
    parts = []
    if recipient:
        parts.append(f"收件人={recipient}")
    if phone:
        parts.append(f"手机号={phone}")
    if address:
        parts.append(f"地址={address}")
    return " | ".join(parts)


def trim_address_text(text: str) -> str:
    value = compact_whitespace(text).strip(" ,，:：;；。.!！")
    value = re.sub(r"^(?:所在地区)\s*[:：]\s*", "", value)
    value = re.sub(r"^(?:详细地址|收货地址|收件地址|地址)\s*[:：]?\s*", "", value)
    for marker in ADDRESS_NOTE_MARKERS:
        if marker in value:
            value = value.split(marker, 1)[0].strip(" ,，:：;；。.!！")
    if GENERIC_ADDRESS_NOTE_RE.match(value):
        return ""
    return value


def extract_shipping_entries(text: str) -> list[dict[str, str]]:
    value = str(text or "").strip()
    if not value:
        return []

    entries: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    def add_entry(recipient: str = "", phone: str = "", address: str = "") -> None:
        recipient_value = compact_whitespace(recipient)
        phone_value = compact_whitespace(phone)
        address_value = trim_address_text(address)
        if not any([recipient_value, phone_value, address_value]):
            return
        key = (recipient_value, phone_value, address_value)
        if key in seen_keys:
            return
        seen_keys.add(key)
        entries.append(
            {
                "recipient": recipient_value,
                "phone": phone_value,
                "address": address_value,
            }
        )

    labeled_recipients = extract_labeled_values(value, RECIPIENT_FIELD_PATTERNS)
    labeled_regions = extract_labeled_values(value, REGION_FIELD_PATTERNS)
    labeled_addresses = extract_labeled_values(value, ADDRESS_FIELD_PATTERNS)
    labeled_phones = extract_phone_numbers(value)
    if labeled_recipients or labeled_regions or labeled_addresses:
        address_parts = []
        if labeled_regions:
            address_parts.append(labeled_regions[0])
        if labeled_addresses:
            address_parts.append(labeled_addresses[0])
        add_entry(
            recipient=labeled_recipients[0] if labeled_recipients else "",
            phone=labeled_phones[0] if len(labeled_phones) == 1 else "",
            address=" ".join(part for part in address_parts if part),
        )

    lines = [compact_whitespace(line) for line in value.splitlines()]
    lines = [line for line in lines if line]
    for idx, line in enumerate(lines):
        phones = extract_phone_numbers(line)
        if not phones:
            continue

        phone = phones[0] if len(phones) == 1 else ""
        recipient = ""
        after_phone = line
        if phone:
            before, _, after = line.partition(phone)
            recipient = infer_inline_recipient(before)
            after_phone = compact_whitespace(after.strip(" ,，:："))

        address_parts = []
        if looks_like_address_fragment(after_phone):
            address_parts.append(after_phone)

        for lookahead in lines[idx + 1 : idx + 3]:
            if looks_like_address_fragment(lookahead):
                address_parts.append(lookahead)
            elif address_parts:
                break

        add_entry(
            recipient=recipient,
            phone=phone,
            address=" ".join(address_parts),
        )

    return entries


def looks_url_or_secret_heavy(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    lower = value.lower()
    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
        return True
    if "http://" in lower or "https://" in lower:
        non_url_text = re.sub(r"https?://\S+", "", value).strip()
        return len(non_url_text) < 20
    return False


def should_use_memory_for_message(text: str, *, policy: str = "auto") -> dict[str, Any]:
    normalized = str(text or "").strip().lower()
    policy = policy or "auto"
    if policy not in {"auto", "always", "never"}:
        raise ValueError(f"unsupported memory use policy: {policy}")

    if policy == "always":
        return {
            "policy": policy,
            "decision": True,
            "reason": "policy_always",
            "matched_categories": [],
            "matched_keywords": [],
        }
    if policy == "never":
        return {
            "policy": policy,
            "decision": False,
            "reason": "policy_never",
            "matched_categories": [],
            "matched_keywords": [],
        }

    matched_categories = []
    matched_keywords = []
    for category, keywords in MEMORY_USE_KEYWORD_GROUPS.items():
        category_hits = [keyword for keyword in keywords if keyword.lower() in normalized]
        if not category_hits:
            continue
        matched_categories.append(category)
        matched_keywords.extend(category_hits)

    broad_hits = [keyword for keyword in BROAD_GENERAL_MEMORY_SKIP_KEYWORDS if keyword.lower() in normalized]
    service_categories = [category for category in matched_categories if category != "customer_specific"]
    strong_customer_hits = [
        keyword for keyword in matched_keywords if keyword in STRONG_CUSTOMER_SPECIFIC_MEMORY_KEYWORDS
    ]

    if service_categories or strong_customer_hits:
        return {
            "policy": policy,
            "decision": True,
            "reason": "matched_customer_service_keywords",
            "matched_categories": matched_categories,
            "matched_keywords": matched_keywords,
        }

    if broad_hits:
        return {
            "policy": policy,
            "decision": False,
            "reason": "broad_general_question",
            "matched_categories": [],
            "matched_keywords": broad_hits,
        }

    if matched_categories:
        return {
            "policy": policy,
            "decision": False,
            "reason": "weak_customer_specific_signal",
            "matched_categories": matched_categories,
            "matched_keywords": matched_keywords,
        }

    return {
        "policy": policy,
        "decision": False,
        "reason": "no_customer_specific_signal",
        "matched_categories": [],
        "matched_keywords": [],
    }


def message_key(message: dict[str, Any]) -> str:
    source_db = str(message.get("source_db") or "")
    message_id = str(message.get("message_id") or "")
    timestamp = str(message.get("timestamp") or "")
    return f"{source_db}:{message_id}:{timestamp}"


def evidence_from_message(message: dict[str, Any], max_chars: int = 180) -> dict[str, Any]:
    text, redaction_flags = redact_text(str(message.get("text") or ""))
    text = text.replace("\n", "\\n").strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "..."
    return {
        "message_id": message.get("message_id"),
        "message_key": message_key(message),
        "timestamp": message.get("timestamp") or "",
        "direction": message.get("direction") or "",
        "sender_id": message.get("sender_id") or "",
        "source_db": message.get("source_db") or "",
        "excerpt": text,
        "redaction_flags": redaction_flags,
    }


def stable_id(*parts: Any) -> str:
    joined = "\x1f".join(str(part or "") for part in parts)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()


def make_fact(category: str, value: str, message: dict[str, Any], derived_by: str, confidence: float = 0.9) -> dict[str, Any]:
    timestamp = message.get("timestamp") or ""
    redacted_value, redaction_flags = redact_text(value)
    return {
        "fact_id": stable_id(category, redacted_value, message_key(message)),
        "category": category,
        "value": redacted_value,
        "status": "active",
        "confidence": confidence,
        "first_seen_at": timestamp,
        "last_seen_at": timestamp,
        "evidence": [evidence_from_message(message)],
        "redaction_flags": redaction_flags,
        "derived_by": derived_by,
    }


def merge_fact(target: list[dict[str, Any]], fact: dict[str, Any], max_items: int) -> None:
    value = str(fact.get("value") or "").strip()
    if not value:
        return
    for existing in target:
        if existing.get("value") == value and existing.get("category") == fact.get("category"):
            existing["last_seen_at"] = max(str(existing.get("last_seen_at") or ""), str(fact.get("last_seen_at") or ""))
            evidence = existing.setdefault("evidence", [])
            if len(evidence) < 3:
                evidence.append(fact["evidence"][0])
            return
    if len(target) < max_items:
        target.append(fact)


def iter_jsonl_messages(path: Path) -> tuple[list[dict[str, Any]], str]:
    messages: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for raw_line in f:
            digest.update(raw_line)
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages, digest.hexdigest()


def load_conversation_index(export_root: Path) -> list[dict[str, Any]]:
    path = export_root / "conversation_index.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"conversation_index.json must be a list: {path}")
    return [row for row in data if isinstance(row, dict)]


def load_profile_index(memory_root: Path) -> list[dict[str, Any]]:
    path = memory_root / "indexes" / "profile_index.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"profile_index.json must be a list: {path}")
    return [row for row in data if isinstance(row, dict)]


def load_profile(memory_root: Path, index_row: dict[str, Any]) -> dict[str, Any]:
    profile_path = str(index_row.get("profile_path") or "")
    if not profile_path:
        raise ValueError("profile index row is missing profile_path")
    path = memory_root / profile_path
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"profile must be an object: {path}")
    return data


def find_single_profile(memory_root: Path, query: str) -> tuple[dict[str, Any], dict[str, Any]]:
    matches = query_profiles(memory_root, query=query, limit=None)
    if not matches:
        raise ValueError(f"no customer memory profile matched: {query}")

    query_lower = query.strip().lower()
    exact = [
        row
        for row in matches
        if query_lower
        in {
            str(row.get("profile_id") or "").lower(),
            str(row.get("conversation_username") or "").lower(),
            str(row.get("display_name") or "").lower(),
        }
    ]
    candidates = exact or matches
    if len(candidates) > 1:
        preview = [
            {
                "profile_id": row.get("profile_id"),
                "conversation_username": row.get("conversation_username"),
                "display_name": row.get("display_name"),
                "conversation_type": row.get("conversation_type"),
            }
            for row in candidates[:10]
        ]
        raise ValueError(f"multiple customer memory profiles matched {query}: {json.dumps(preview, ensure_ascii=False)}")

    row = candidates[0]
    return row, load_profile(memory_root, row)


def conversation_matches(row: dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    target = needle.strip().lower()
    fields = [
        row.get("conversation_id"),
        row.get("conversation_username"),
        row.get("display_name"),
        row.get("file_label"),
    ]
    return any(target in str(value or "").lower() for value in fields)


def profile_matches(row: dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    target = needle.strip().lower()
    fields = [
        row.get("profile_id"),
        row.get("conversation_username"),
        row.get("display_name"),
        row.get("conversation_type"),
    ]
    return any(target in str(value or "").lower() for value in fields)


def query_profiles(
    memory_root: Path,
    *,
    query: str = "",
    limit: Optional[int] = None,
    include_blocked: bool = True,
    conversation_type: str = "",
) -> list[dict[str, Any]]:
    rows = load_profile_index(memory_root)
    out = []
    for row in rows:
        if conversation_type and str(row.get("conversation_type") or "") != conversation_type:
            continue
        if not include_blocked and row.get("auto_reply_blocked"):
            continue
        if query and not profile_matches(row, query):
            continue
        out.append(row)
        if limit and len(out) >= limit:
            break
    return out


def infer_stale_level(last_at: Optional[datetime], as_of: datetime) -> str:
    if not last_at:
        return "unknown"
    days = (as_of - last_at).days
    if days <= 30:
        return "fresh"
    if days <= 90:
        return "warm"
    return "stale"


def is_ambiguous_display_name(value: str, username: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if text in {"-", "未知", "微信用户", "用户"}:
        return True
    if text == username:
        return True
    return False


def has_pii(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in PII_PATTERNS)


def add_keyword_fact(
    buckets: dict[str, list[dict[str, Any]]],
    bucket: str,
    category: str,
    message: dict[str, Any],
    keyword: str,
    derived_by: str,
    max_items: int,
) -> None:
    value = str(message.get("text") or "").strip()
    if not value:
        return
    fact = make_fact(category, value, message, derived_by, confidence=0.82)
    fact["matched_keyword"] = keyword
    merge_fact(buckets[bucket], fact, max_items)


def extract_observed_facts(
    readable_messages: Iterable[dict[str, Any]],
    max_items_per_category: int = 20,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], bool]:
    observed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    service_memory: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pii_present = False

    for message in readable_messages:
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        pii_present = pii_present or has_pii(text)

        for phone in extract_phone_numbers(text):
            merge_fact(
                observed["contact_points"],
                make_fact("phone_number", phone, message, "regex_phone_v1", confidence=0.96),
                max_items_per_category,
            )

        for recipient in extract_labeled_values(text, RECIPIENT_FIELD_PATTERNS):
            merge_fact(
                observed["contact_points"],
                make_fact("recipient_name", recipient, message, "label_recipient_v1", confidence=0.8),
                max_items_per_category,
            )

        for shipping in extract_shipping_entries(text):
            recipient = shipping.get("recipient") or ""
            phone = shipping.get("phone") or ""
            address = shipping.get("address") or ""

            if recipient:
                merge_fact(
                    observed["contact_points"],
                    make_fact("recipient_name", recipient, message, "shipping_recipient_v1", confidence=0.82),
                    max_items_per_category,
                )

            if phone:
                merge_fact(
                    observed["contact_points"],
                    make_fact("phone_number", phone, message, "shipping_phone_v1", confidence=0.98),
                    max_items_per_category,
                )

            if address:
                merge_fact(
                    observed["locations"],
                    make_fact("shipping_address", address, message, "shipping_address_v1", confidence=0.74),
                    max_items_per_category,
                )

            summary = build_shipping_summary(recipient, phone, address)
            if summary:
                merge_fact(
                    observed["shipping_requirements"],
                    make_fact("shipping_requirement", summary, message, "shipping_requirement_v1", confidence=0.72),
                    max_items_per_category,
                )

        for match in BUDGET_RE.finditer(text):
            merge_fact(
                observed["budgets"],
                make_fact("budget", match.group(0), message, "regex_budget_v1", confidence=0.9),
                max_items_per_category,
            )

        for match in DATE_RE.finditer(text):
            merge_fact(
                observed["deadlines"],
                make_fact("date_or_deadline", match.group(0), message, "regex_date_v1", confidence=0.78),
                max_items_per_category,
            )

        for keyword in AFTER_SALES_KEYWORDS:
            if keyword in text:
                add_keyword_fact(
                    observed,
                    "after_sales_issues",
                    "after_sales_issue",
                    message,
                    keyword,
                    "keyword_after_sales_v1",
                    max_items_per_category,
                )
                break

        for keyword in PREFERENCE_KEYWORDS:
            if keyword in text:
                add_keyword_fact(
                    observed,
                    "explicit_preferences",
                    "explicit_preference",
                    message,
                    keyword,
                    "keyword_preference_v1",
                    max_items_per_category,
                )
                break

        for keyword in REJECTION_KEYWORDS:
            if keyword in text:
                add_keyword_fact(
                    observed,
                    "explicit_rejections",
                    "explicit_rejection",
                    message,
                    keyword,
                    "keyword_rejection_v1",
                    max_items_per_category,
                )
                break

        if message.get("direction") == "sent":
            for keyword in COMMITMENT_KEYWORDS:
                if keyword in text:
                    add_keyword_fact(
                        service_memory,
                        "commitments_from_us",
                        "merchant_commitment",
                        message,
                        keyword,
                        "keyword_commitment_v1",
                        max_items_per_category,
                    )
                    break
        elif message.get("direction") == "received":
            for keyword in FOLLOWUP_KEYWORDS:
                if keyword in text:
                    add_keyword_fact(
                        service_memory,
                        "pending_followups",
                        "pending_followup_candidate",
                        message,
                        keyword,
                        "keyword_followup_v1",
                        max_items_per_category,
                    )
                    break

    for key in [
        "products",
        "scenes",
        "budgets",
        "deadlines",
        "locations",
        "contact_points",
        "payment_terms",
        "shipping_requirements",
        "after_sales_issues",
        "explicit_preferences",
        "explicit_rejections",
    ]:
        observed.setdefault(key, [])

    for key in ["open_threads", "resolved_threads", "commitments_from_us", "pending_followups"]:
        service_memory.setdefault(key, [])

    return dict(observed), dict(service_memory), pii_present


def compact_recent_messages(messages: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    recent = messages[-max(limit, 1) :]
    out = []
    for message in recent:
        text, redaction_flags = redact_text(str(message.get("text") or "").strip())
        out.append(
            {
            "message_id": message.get("message_id"),
            "message_key": message_key(message),
            "timestamp": message.get("timestamp") or "",
            "direction": message.get("direction") or "",
            "render_type": message.get("render_type") or message.get("message_type") or "",
                "text": text[:240],
                "redaction_flags": redaction_flags,
            }
        )
    return out


def build_profile(
    export_root: Path,
    row: dict[str, Any],
    *,
    generated_at: Optional[str] = None,
    as_of: Optional[datetime] = None,
    recent_limit: int = 20,
    max_items_per_category: int = 20,
) -> dict[str, Any]:
    generated_at = generated_at or utc_now_iso()
    as_of = as_of or datetime.utcnow()
    conversation_id = str(row.get("conversation_id") or stable_id(row.get("conversation_username"), row.get("display_name")))
    conversation_username = str(row.get("conversation_username") or "")
    raw_display_name = str(row.get("display_name") or "")
    display_name, display_name_redaction_flags = redact_text(raw_display_name)
    conversation_type = str(row.get("conversation_type") or "unknown")
    notification_muted = row.get("notification_muted")
    notification_state = str(row.get("notification_state") or "unknown")
    relative_file = str(row.get("file") or f"conversations/{conversation_id}.jsonl")
    conversation_file = export_root / relative_file

    messages, source_hash = iter_jsonl_messages(conversation_file)
    messages.sort(key=lambda m: (str(m.get("timestamp") or ""), int(m.get("message_id") or 0)))
    readable_messages = [message for message in messages if looks_readable_text(str(message.get("text") or ""))]

    timestamps = [parse_timestamp(message.get("timestamp")) for message in messages]
    timestamps = [ts for ts in timestamps if ts is not None]
    readable_timestamps = [parse_timestamp(message.get("timestamp")) for message in readable_messages]
    readable_timestamps = [ts for ts in readable_timestamps if ts is not None]

    first_at = min(timestamps).isoformat() if timestamps else ""
    last_at_dt = max(timestamps) if timestamps else parse_timestamp(row.get("last_active_at"))
    last_at = last_at_dt.isoformat() if last_at_dt else str(row.get("last_active_at") or "")

    inbound_messages = [message for message in messages if message.get("direction") == "received"]
    outbound_messages = [message for message in messages if message.get("direction") == "sent"]
    readable_inbound = [message for message in readable_messages if message.get("direction") == "received"]
    readable_outbound = [message for message in readable_messages if message.get("direction") == "sent"]

    last_inbound = next((message for message in reversed(readable_inbound) if message.get("timestamp")), None)
    last_outbound = next((message for message in reversed(readable_outbound) if message.get("timestamp")), None)

    recent_7d_count = 0
    recent_30d_count = 0
    active_days_30d = set()
    cutoff_7d = as_of - timedelta(days=7)
    cutoff_30d = as_of - timedelta(days=30)
    for ts in readable_timestamps:
        if ts >= cutoff_7d:
            recent_7d_count += 1
        if ts >= cutoff_30d:
            recent_30d_count += 1
            active_days_30d.add(ts.date().isoformat())

    observed_facts, service_memory, pii_present = extract_observed_facts(
        readable_messages,
        max_items_per_category=max_items_per_category,
    )
    pii_message_count = sum(1 for message in readable_messages if has_pii(str(message.get("text") or "")))

    type_counts = Counter(str(message.get("render_type") or message.get("message_type") or "unknown") for message in messages)
    stale_level = infer_stale_level(last_at_dt, as_of)
    block_reasons = []
    if conversation_type != "friend":
        block_reasons.append(f"conversation_type:{conversation_type}")
    if notification_muted is True:
        block_reasons.append("notification_muted")
    elif notification_muted is not False:
        block_reasons.append("notification_state_unknown")
    if stale_level in {"stale", "unknown"}:
        block_reasons.append(f"stale_level:{stale_level}")
    if is_ambiguous_display_name(raw_display_name, conversation_username):
        block_reasons.append("ambiguous_display_name")

    ambiguity_level = "high" if "ambiguous_display_name" in block_reasons else "medium" if conversation_type != "friend" else "low"
    if conversation_type != "friend":
        profile_state = f"ineligible_{conversation_type}"
    elif is_ambiguous_display_name(raw_display_name, conversation_username):
        profile_state = "ineligible_ambiguous_name"
    elif not messages:
        profile_state = "ineligible_no_messages"
    else:
        profile_state = "eligible"

    last_inbound_text, _ = redact_text(str(last_inbound.get("text") or "")) if last_inbound else ("", [])
    last_outbound_text, _ = redact_text(str(last_outbound.get("text") or "")) if last_outbound else ("", [])

    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_id": conversation_id,
        "customer_id": conversation_id,
        "canonical_contact_key": conversation_username,
        "conversation_keys": [conversation_username] if conversation_username else [],
        "generated_at": generated_at,
        "source": {
            "export_root": str(export_root),
            "conversation_files": [relative_file],
            "message_count": len(messages),
            "readable_message_count": len(readable_messages),
            "filtered_unreadable_message_count": len(messages) - len(readable_messages),
            "first_message_at": first_at,
            "last_message_at": last_at,
            "source_hash": source_hash,
        },
        "identity": {
            "display_name": display_name,
            "display_name_hash": stable_id(raw_display_name) if raw_display_name else "",
            "display_name_redaction_flags": display_name_redaction_flags,
            "remark_name": str(row.get("remark") or ""),
            "nick_name": str(row.get("nick_name") or ""),
            "username": conversation_username,
            "alias": str(row.get("alias") or ""),
            "conversation_type": conversation_type,
            "notification_muted": notification_muted,
            "notification_state": notification_state,
        },
        "activity": {
            "inbound_count": len(inbound_messages),
            "outbound_count": len(outbound_messages),
            "readable_inbound_count": len(readable_inbound),
            "readable_outbound_count": len(readable_outbound),
            "recent_7d_count": recent_7d_count,
            "recent_30d_count": recent_30d_count,
            "active_days_30d": len(active_days_30d),
            "last_inbound_at": last_inbound.get("timestamp") if last_inbound else "",
            "last_outbound_at": last_outbound.get("timestamp") if last_outbound else "",
            "type_counts": dict(type_counts),
        },
        "recent_window": {
            "last_customer_message_id": last_inbound.get("message_id") if last_inbound else "",
            "last_customer_message_at": last_inbound.get("timestamp") if last_inbound else "",
            "last_customer_excerpt": last_inbound_text[:240],
            "last_agent_message_id": last_outbound.get("message_id") if last_outbound else "",
            "last_agent_message_at": last_outbound.get("timestamp") if last_outbound else "",
            "last_agent_excerpt": last_outbound_text[:240],
            "recent_messages": compact_recent_messages(readable_messages, limit=recent_limit),
        },
        "observed_facts": observed_facts,
        "service_memory": service_memory,
        "risk": {
            "profile_state": profile_state,
            "auto_reply_blocked": bool(block_reasons),
            "block_reasons": block_reasons,
            "stale_level": stale_level,
            "ambiguity_level": ambiguity_level,
            "pii_present": pii_present,
            "pii_message_count": pii_message_count,
        },
    }


def select_conversations(
    index_rows: list[dict[str, Any]],
    *,
    conversation: str = "",
    limit: Optional[int] = None,
    private_only: bool = False,
) -> list[dict[str, Any]]:
    selected = []
    stable_rows = sorted(index_rows, key=lambda item: str(item.get("conversation_id") or ""))
    for row in stable_rows:
        if private_only and row.get("conversation_type") != "friend":
            continue
        if conversation and not conversation_matches(row, conversation):
            continue
        selected.append(row)
        if limit and len(selected) >= limit:
            break
    return selected


def write_profile(out_root: Path, profile: dict[str, Any]) -> Path:
    profile_id = str(profile.get("profile_id") or stable_id(profile.get("canonical_contact_key")))
    path = out_root / "profiles" / f"{profile_id}.json"
    _write_json(path, profile)
    return path


def build_customer_memory(
    export_root: Path,
    out_root: Path,
    *,
    conversation: str = "",
    limit: Optional[int] = None,
    private_only: bool = False,
    recent_limit: int = 20,
    max_items_per_category: int = 20,
) -> dict[str, Any]:
    export_root = export_root.expanduser().resolve()
    out_root = out_root.expanduser().resolve()
    index_rows = load_conversation_index(export_root)
    selected = select_conversations(index_rows, conversation=conversation, limit=limit, private_only=private_only)
    generated_at = utc_now_iso()
    as_of = datetime.utcnow()

    for relative in ["profiles", "indexes", "manifests"]:
        _ensure_dir(out_root / relative)

    profile_index = []
    skipped = []
    for row in selected:
        try:
            profile = build_profile(
                export_root,
                row,
                generated_at=generated_at,
                as_of=as_of,
                recent_limit=recent_limit,
                max_items_per_category=max_items_per_category,
            )
            profile_path = write_profile(out_root, profile)
            profile_index.append(
                {
                    "profile_id": profile["profile_id"],
                    "conversation_username": profile["identity"]["username"],
                    "display_name": profile["identity"]["display_name"],
                    "display_name_redaction_flags": profile["identity"]["display_name_redaction_flags"],
                    "remark_name": profile["identity"].get("remark_name") or "",
                    "nick_name": profile["identity"].get("nick_name") or "",
                    "alias": profile["identity"].get("alias") or "",
                    "conversation_type": profile["identity"]["conversation_type"],
                    "notification_muted": profile["identity"].get("notification_muted"),
                    "notification_state": profile["identity"].get("notification_state"),
                    "message_count": profile["source"]["message_count"],
                    "readable_message_count": profile["source"]["readable_message_count"],
                    "filtered_unreadable_message_count": profile["source"]["filtered_unreadable_message_count"],
                    "last_message_at": profile["source"]["last_message_at"],
                    "profile_state": profile["risk"]["profile_state"],
                    "stale_level": profile["risk"]["stale_level"],
                    "auto_reply_blocked": profile["risk"]["auto_reply_blocked"],
                    "block_reasons": profile["risk"]["block_reasons"],
                    "pii_present": profile["risk"]["pii_present"],
                    "source_hash": profile["source"]["source_hash"],
                    "profile_path": str(profile_path.relative_to(out_root)),
                }
            )
        except Exception as exc:
            skipped.append(
                {
                    "conversation_id": row.get("conversation_id") or "",
                    "conversation_username": row.get("conversation_username") or "",
                    "display_name": row.get("display_name") or "",
                    "error": str(exc),
                }
            )

    profile_index.sort(key=lambda item: (item.get("display_name") or "", item.get("conversation_username") or ""))
    _write_json(out_root / "indexes" / "profile_index.json", profile_index)

    manifest = {
        "schema_version": "customer_memory_build_v1",
        "generated_at": generated_at,
        "export_root": str(export_root),
        "out_root": str(out_root),
        "filter": {
            "conversation": conversation,
            "limit": limit,
            "private_only": private_only,
        },
        "total_conversations_in_export": len(index_rows),
        "selected_conversations": len(selected),
        "built_profiles": len(profile_index),
        "skipped_profiles": len(skipped),
        "aggregate": {
            "filtered_unreadable_messages": sum(item.get("filtered_unreadable_message_count") or 0 for item in profile_index),
            "auto_reply_blocked_profiles": sum(1 for item in profile_index if item.get("auto_reply_blocked")),
            "pii_present_profiles": sum(1 for item in profile_index if item.get("pii_present")),
            "group_profiles": sum(1 for item in profile_index if item.get("conversation_type") == "group"),
            "official_profiles": sum(1 for item in profile_index if item.get("conversation_type") == "official"),
        },
        "skipped": skipped[:50],
    }
    _write_json(out_root / "manifests" / "build_manifest.json", manifest)
    return manifest


def markdown_escape(text: Any) -> str:
    value = str(text or "").strip()
    value = value.replace("|", "\\|")
    return value


def short_text(text: Any, limit: int = 160) -> str:
    value = str(text or "").replace("\n", " ").strip()
    if len(value) > limit:
        return value[: limit - 1] + "..."
    return value


def fact_context_items(items: list[dict[str, Any]], *, limit: int = 4) -> list[dict[str, Any]]:
    out = []
    sorted_items = sorted(items, key=lambda item: str(item.get("last_seen_at") or item.get("first_seen_at") or ""), reverse=True)
    for item in sorted_items:
        value = short_text(redact_text(str(item.get("value") or ""))[0], 140)
        if not value:
            continue
        if looks_url_or_secret_heavy(value):
            continue
        evidence = item.get("evidence") or []
        first_evidence = evidence[0] if evidence else {}
        evidence_excerpt = short_text(redact_text(str(first_evidence.get("excerpt") or ""))[0], 120)
        out.append(
            {
                "value": value,
                "category": item.get("category") or "",
                "status": item.get("status") or "",
                "confidence": item.get("confidence"),
                "matched_keyword": item.get("matched_keyword") or "",
                "evidence": {
                    "timestamp": first_evidence.get("timestamp") or "",
                    "message_id": first_evidence.get("message_id") or "",
                    "excerpt": evidence_excerpt,
                },
            }
        )
        if len(out) >= limit:
            break
    return out


def build_runtime_context(profile: dict[str, Any], *, fact_limit: int = 3, recent_limit: int = 5) -> dict[str, Any]:
    identity = profile.get("identity") or {}
    source = profile.get("source") or {}
    risk = profile.get("risk") or {}
    recent = profile.get("recent_window") or {}
    observed = profile.get("observed_facts") or {}
    service = profile.get("service_memory") or {}

    recent_messages = []
    for message in (recent.get("recent_messages") or [])[-recent_limit:]:
        text = short_text(message.get("text"), 160)
        if not text:
            continue
        recent_messages.append(
            {
                "timestamp": message.get("timestamp") or "",
                "direction": message.get("direction") or "",
                "render_type": message.get("render_type") or "",
                "text": text,
            }
        )

    block_reasons = risk.get("block_reasons") or []
    do_not_assume = [
        "Deterministic memory entries are candidates, not confirmed facts.",
        "Do not promise refunds, replacement shipments, delivery dates, prices, or responsibility unless the latest chat explicitly supports it.",
        "If information is missing or ambiguous, ask one short clarifying question.",
        "Do not mention internal risk flags or memory extraction to the customer.",
    ]
    if risk.get("stale_level") in {"stale", "unknown"}:
        do_not_assume.append("The profile is stale or has unknown freshness; rely primarily on the latest messages.")
    if identity.get("conversation_type") != "friend":
        do_not_assume.append("This is not a private friend conversation; do not infer a single customer's preferences from group/system context.")
    if "ambiguous_display_name" in block_reasons:
        do_not_assume.append("The display name is ambiguous; do not assume identity beyond the current conversation.")

    return {
        "schema_version": "customer_runtime_context_v1",
        "profile_id": profile.get("profile_id") or "",
        "identity": {
            "display_name": identity.get("display_name") or "",
            "username": identity.get("username") or "",
            "conversation_type": identity.get("conversation_type") or "",
        },
        "source": {
            "last_message_at": source.get("last_message_at") or "",
            "readable_message_count": source.get("readable_message_count") or 0,
            "source_hash": source.get("source_hash") or "",
        },
        "risk": {
            "profile_state": risk.get("profile_state") or "",
            "auto_reply_blocked": bool(risk.get("auto_reply_blocked")),
            "block_reasons": block_reasons,
            "stale_level": risk.get("stale_level") or "",
            "pii_present": bool(risk.get("pii_present")),
            "ambiguity_level": risk.get("ambiguity_level") or "",
        },
        "recent_messages": recent_messages,
        "candidate_facts": {
            "after_sales_issues": fact_context_items(observed.get("after_sales_issues") or [], limit=fact_limit),
            "explicit_preferences": fact_context_items(observed.get("explicit_preferences") or [], limit=fact_limit),
            "explicit_rejections": fact_context_items(observed.get("explicit_rejections") or [], limit=fact_limit),
            "deadlines": fact_context_items(observed.get("deadlines") or [], limit=fact_limit),
            "pending_followups": fact_context_items(service.get("pending_followups") or [], limit=fact_limit),
            "commitments_from_us": fact_context_items(service.get("commitments_from_us") or [], limit=fact_limit),
        },
        "do_not_assume": do_not_assume,
    }


def build_runtime_context_for_query(
    memory_root: Path,
    query: str,
    *,
    fact_limit: int = 3,
    recent_limit: int = 5,
) -> dict[str, Any]:
    _row, profile = find_single_profile(memory_root, query)
    return build_runtime_context(profile, fact_limit=fact_limit, recent_limit=recent_limit)


def format_fact_lines(items: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    lines = []
    for item in items[:limit]:
        value = short_text(item.get("value"), 140)
        if not value:
            continue
        evidence = item.get("evidence") or []
        evidence_hint = ""
        if evidence:
            first = evidence[0]
            timestamp = first.get("timestamp") or ""
            message_id = first.get("message_id") or ""
            evidence_hint = f" ({timestamp}, msg {message_id})"
        keyword = item.get("matched_keyword")
        keyword_hint = f" keyword={keyword}" if keyword else ""
        lines.append(f"- {value}{evidence_hint}{keyword_hint}")
    return lines or ["- None"]


def format_recent_messages(messages: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    lines = []
    for message in messages[-limit:]:
        timestamp = message.get("timestamp") or ""
        direction = message.get("direction") or ""
        render_type = message.get("render_type") or ""
        text = short_text(message.get("text"), 160)
        if not text:
            continue
        lines.append(f"- `{timestamp}` `{direction}` `{render_type}` {text}")
    return lines or ["- None"]


def profile_markdown(profile: dict[str, Any]) -> str:
    identity = profile.get("identity") or {}
    source = profile.get("source") or {}
    activity = profile.get("activity") or {}
    recent = profile.get("recent_window") or {}
    risk = profile.get("risk") or {}
    observed = profile.get("observed_facts") or {}
    service = profile.get("service_memory") or {}

    title = identity.get("display_name") or profile.get("profile_id") or "Unknown Profile"
    lines = [
        f"# {markdown_escape(title)}",
        "",
        "> Local customer Wiki view generated from deterministic profile JSON. Extracted facts are candidates, not final truth.",
        "",
        "## Identity",
        "",
        f"- Profile ID: `{profile.get('profile_id') or ''}`",
        f"- Username: `{identity.get('username') or ''}`",
        f"- Remark name: `{identity.get('remark_name') or ''}`",
        f"- Nick name: `{identity.get('nick_name') or ''}`",
        f"- Alias / WeChat ID: `{identity.get('alias') or ''}`",
        f"- Conversation type: `{identity.get('conversation_type') or ''}`",
        f"- Display redaction flags: `{', '.join(identity.get('display_name_redaction_flags') or []) or 'none'}`",
        "",
        "## Status",
        "",
        f"- Messages: `{source.get('message_count')}` total, `{source.get('readable_message_count')}` readable, `{source.get('filtered_unreadable_message_count')}` filtered",
        f"- First message: `{source.get('first_message_at') or ''}`",
        f"- Last message: `{source.get('last_message_at') or ''}`",
        f"- Recent 30d readable messages: `{activity.get('recent_30d_count')}`",
        f"- Profile state: `{risk.get('profile_state') or ''}`",
        f"- Stale level: `{risk.get('stale_level') or ''}`",
        f"- Auto reply blocked: `{risk.get('auto_reply_blocked')}`",
        f"- Block reasons: `{', '.join(risk.get('block_reasons') or []) or 'none'}`",
        f"- PII present: `{risk.get('pii_present')}` (`{risk.get('pii_message_count')}` messages)",
        "",
        "## Recent Messages",
        "",
        *format_recent_messages(recent.get("recent_messages") or [], limit=8),
        "",
        "## Deterministic Fact Candidates",
        "",
        "### After-sales / Logistics / Quality",
        "",
        *format_fact_lines(observed.get("after_sales_issues") or [], limit=6),
        "",
        "### Explicit Preferences",
        "",
        *format_fact_lines(observed.get("explicit_preferences") or [], limit=6),
        "",
        "### Explicit Rejections",
        "",
        *format_fact_lines(observed.get("explicit_rejections") or [], limit=6),
        "",
        "### Budgets",
        "",
        *format_fact_lines(observed.get("budgets") or [], limit=6),
        "",
        "### Dates / Deadlines",
        "",
        *format_fact_lines(observed.get("deadlines") or [], limit=6),
        "",
        "### Contact Points",
        "",
        *format_fact_lines(observed.get("contact_points") or [], limit=8),
        "",
        "### Locations / Addresses",
        "",
        *format_fact_lines(observed.get("locations") or [], limit=8),
        "",
        "### Shipping Requirements",
        "",
        *format_fact_lines(observed.get("shipping_requirements") or [], limit=8),
        "",
        "## Service Memory Candidates",
        "",
        "### Commitments From Us",
        "",
        *format_fact_lines(service.get("commitments_from_us") or [], limit=6),
        "",
        "### Pending Followups",
        "",
        *format_fact_lines(service.get("pending_followups") or [], limit=6),
        "",
        "## Source",
        "",
        f"- Source hash: `{source.get('source_hash') or ''}`",
        f"- Conversation files: `{', '.join(source.get('conversation_files') or [])}`",
        "",
    ]
    return "\n".join(lines)


def render_profile_page(memory_root: Path, index_row: dict[str, Any], pages_root: Optional[Path] = None) -> Path:
    profile = load_profile(memory_root, index_row)
    pages_root = pages_root or memory_root / "pages"
    _ensure_dir(pages_root)
    profile_id = str(profile.get("profile_id") or index_row.get("profile_id") or stable_id(index_row))
    page_path = pages_root / f"{profile_id}.md"
    page_path.write_text(profile_markdown(profile), encoding="utf-8")
    return page_path
