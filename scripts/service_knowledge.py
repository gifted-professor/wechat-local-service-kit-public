#!/usr/bin/env python3

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional


DEFAULT_WIKI_ROOT = Path(".project-wiki")

PLAYBOOKS = [
    {
        "id": "human-handoff",
        "title": "Human Handoff Playbook",
        "path": "wiki/reply-playbooks/human-handoff.md",
        "priority": 100,
        "keywords": ["投诉", "生气", "不满意", "人工", "客服", "主管", "赔偿", "退一赔", "法律", "举报", "平台", "12315", "黑猫"],
    },
    {
        "id": "refund-and-replacement",
        "title": "Refund And Replacement Playbook",
        "path": "wiki/reply-playbooks/refund-and-replacement.md",
        "priority": 90,
        "keywords": ["退款", "退货", "换货", "补发", "赔偿", "赔付", "拒收", "退回", "重新发"],
    },
    {
        "id": "order-status",
        "title": "Order Status Playbook",
        "path": "wiki/reply-playbooks/order-status.md",
        "priority": 85,
        "keywords": ["订单", "订单号", "下单", "购买", "买了", "付款", "支付", "发票", "尾款", "定金", "拍了"],
    },
    {
        "id": "after-sales",
        "title": "After-Sales Playbook",
        "path": "wiki/reply-playbooks/after-sales.md",
        "priority": 80,
        "keywords": ["售后", "质量", "坏了", "破了", "少发", "漏发", "没收到", "未收到", "投诉", "破损"],
    },
    {
        "id": "logistics",
        "title": "Logistics Playbook",
        "path": "wiki/reply-playbooks/logistics.md",
        "priority": 70,
        "keywords": ["物流", "快递", "发货", "到货", "派送", "签收", "单号", "运费", "收到", "没收到", "未收到"],
    },
    {
        "id": "address-change",
        "title": "Address Change Playbook",
        "path": "wiki/reply-playbooks/address-change.md",
        "priority": 65,
        "keywords": ["地址", "改地址", "收货人", "电话", "手机号", "联系方式", "门牌"],
    },
    {
        "id": "general-question",
        "title": "General Question Playbook",
        "path": "wiki/reply-playbooks/general-question.md",
        "priority": 20,
        "keywords": ["未来", "能力", "职业", "学习", "成长", "趋势", "行业", "创业", "ai", "大模型", "建议", "怎么看", "怎么理解"],
    },
]


def _section(markdown: str, heading: str) -> list[str]:
    pattern = rf"^## {re.escape(heading)}\s*$"
    match = re.search(pattern, markdown, flags=re.MULTILINE)
    if not match:
        return []
    start = match.end()
    next_match = re.search(r"^##\s+", markdown[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(markdown)
    body = markdown[start:end].strip()
    lines = []
    for raw in body.splitlines():
        text = raw.strip()
        if not text:
            continue
        if text.startswith("- "):
            text = text[2:].strip()
        lines.append(text)
    return lines


def _load_playbook(wiki_root: Path, definition: dict[str, Any]) -> dict[str, Any]:
    relative = Path(definition["path"])
    path = wiki_root / relative
    markdown = path.read_text(encoding="utf-8")
    return {
        "id": definition["id"],
        "title": definition["title"],
        "path": str(relative),
        "draft_guidance": _section(markdown, "Draft Guidance")[:6],
        "safety_rules": _section(markdown, "Safety Rules")[:6],
        "clarifying_questions": _section(markdown, "Clarifying Questions")[:4],
    }


def select_service_playbooks(text: str, *, policy: str = "auto", max_playbooks: int = 2) -> dict[str, Any]:
    normalized = str(text or "").strip().lower()
    if policy not in {"auto", "always", "never"}:
        raise ValueError(f"unsupported service knowledge policy: {policy}")
    if policy == "never":
        return {
            "policy": policy,
            "decision": False,
            "reason": "policy_never",
            "matched_playbooks": [],
            "matched_keywords": [],
        }

    matches = []
    matched_keywords = []
    for definition in PLAYBOOKS:
        hits = [keyword for keyword in definition["keywords"] if keyword.lower() in normalized]
        if hits or policy == "always":
            matches.append({**definition, "matched_keywords": hits})
            matched_keywords.extend(hits)

    matches.sort(key=lambda item: (item["priority"], len(item.get("matched_keywords") or [])), reverse=True)
    selected = matches[: max(max_playbooks, 1)]
    if selected:
        return {
            "policy": policy,
            "decision": True,
            "reason": "matched_service_playbooks" if policy == "auto" else "policy_always",
            "matched_playbooks": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "path": item["path"],
                    "matched_keywords": item.get("matched_keywords") or [],
                }
                for item in selected
            ],
            "matched_keywords": matched_keywords,
        }

    return {
        "policy": policy,
        "decision": False,
        "reason": "no_service_playbook_match",
        "matched_playbooks": [],
        "matched_keywords": [],
    }


def build_service_knowledge_context(
    latest_message_text: str,
    *,
    wiki_root: Path = DEFAULT_WIKI_ROOT,
    policy: str = "auto",
    max_playbooks: int = 2,
) -> dict[str, Any]:
    wiki_root = wiki_root.expanduser().resolve()
    gate = select_service_playbooks(latest_message_text, policy=policy, max_playbooks=max_playbooks)
    playbooks = []
    errors = []
    if gate["decision"]:
        for selected in gate["matched_playbooks"]:
            definition = next((item for item in PLAYBOOKS if item["id"] == selected["id"]), None)
            if not definition:
                continue
            try:
                playbook = _load_playbook(wiki_root, definition)
                playbook["matched_keywords"] = selected.get("matched_keywords") or []
                playbooks.append(playbook)
            except OSError as exc:
                errors.append({"id": selected["id"], "error": str(exc)})

    if gate["decision"] and not playbooks:
        gate["decision"] = False
        gate["reason"] = "service_playbook_unavailable"

    return {
        "schema_version": "service_knowledge_context_v1",
        "wiki_root": str(wiki_root),
        "gate": gate,
        "playbooks": playbooks,
        "errors": errors,
        "do_not_assume": [
            "Service playbooks are general guidance, not customer-specific facts.",
            "Do not mention internal playbooks, wiki, gates, or automation to the customer.",
            "Latest customer message and recent live context take priority.",
            "Do not promise refunds, replacements, delivery dates, prices, or fault unless verified in the latest context.",
        ],
    }


def service_knowledge_context_for_api(mode: str, context: Optional[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    if mode not in {"off", "shadow", "draft-only"}:
        raise ValueError(f"unsupported service knowledge mode: {mode}")
    gate = dict((context or {}).get("gate") or {})
    gate["service_knowledge_mode"] = mode
    gate["service_knowledge_available"] = bool((context or {}).get("playbooks"))
    if mode != "draft-only":
        gate["decision"] = False
        gate["reason"] = f"service_knowledge_mode_{mode}"
        return None, gate
    if not context or not context.get("playbooks"):
        gate["decision"] = False
        gate["reason"] = "service_knowledge_unavailable"
        return None, gate
    if not gate.get("decision"):
        return None, gate
    return context, gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Build service knowledge context from project wiki reply playbooks")
    parser.add_argument("--message", required=True, help="latest incoming message text")
    parser.add_argument("--wiki-root", default=str(DEFAULT_WIKI_ROOT), help="project wiki root")
    parser.add_argument("--policy", choices=("auto", "always", "never"), default="auto")
    parser.add_argument("--max-playbooks", type=int, default=2)
    args = parser.parse_args()

    context = build_service_knowledge_context(
        args.message,
        wiki_root=Path(args.wiki_root),
        policy=args.policy,
        max_playbooks=args.max_playbooks,
    )
    print(json.dumps(context, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
