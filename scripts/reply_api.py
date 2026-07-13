#!/usr/bin/env python3

import json
import os
import urllib.request
from typing import Any, Optional


def _normalize_reply_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        nested = value.get("text")
        if nested is None:
            nested = value.get("content")
        return _normalize_reply_text(nested)
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _normalize_reply_text(item)
            if text:
                parts.append(text)
        return "\n".join(part.strip() for part in parts if str(part).strip()).strip()
    return str(value).strip()


def _collect_stream_delta_text(event: dict[str, Any]) -> str:
    choices = event.get("choices") or []
    parts: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        text = _normalize_reply_text(delta.get("content"))
        if text:
            parts.append(text)
            continue
        text = _normalize_reply_text(choice.get("text"))
        if text:
            parts.append(text)
    return "".join(parts).strip()


def _request_chat_completion(
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if payload.get("stream"):
            text_parts: list[str] = []
            last_event: dict[str, Any] = {}
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    continue
                try:
                    event = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    last_event = event
                    delta_text = _collect_stream_delta_text(event)
                    if delta_text:
                        text_parts.append(delta_text)
            return {
                "response_data": last_event,
                "reply_text": "".join(text_parts).strip(),
                "stream": True,
            }
        response_data = json.load(resp)
    return {
        "response_data": response_data,
        "reply_text": "",
        "stream": False,
    }


def _printable_ratio(text: str) -> float:
    sample = (text or "")[:600]
    if not sample:
        return 0.0
    printable = sum(1 for ch in sample if ch.isprintable() or ch in {"\n", "\r", "\t"})
    return printable / len(sample)


def _looks_usable_for_context(message: dict[str, Any]) -> bool:
    text = str(message.get("text") or "").strip()
    if not text:
        return False
    if len(text) > 800:
        return False
    if _printable_ratio(text) < 0.8:
        return False
    return True


def build_context_excerpt(messages: list[dict[str, Any]], limit: int = 12) -> str:
    usable = [message for message in messages if _looks_usable_for_context(message)]
    excerpt = usable[-max(limit, 1) :]
    lines = []
    for message in excerpt:
        timestamp = message.get("timestamp") or ""
        direction = message.get("direction") or ""
        text = str(message.get("text") or "").replace("\n", "\\n").strip()
        render_type = message.get("render_type") or message.get("message_type") or ""
        lines.append(f"[{timestamp}] direction={direction} type={render_type} text={text}")
    return "\n".join(lines)


def build_memory_context_excerpt(memory_context: Optional[dict[str, Any]]) -> str:
    if not memory_context:
        return ""

    identity = memory_context.get("identity") or {}
    risk = memory_context.get("risk") or {}
    candidate_facts = memory_context.get("candidate_facts") or {}
    recent_messages = memory_context.get("recent_messages") or []
    do_not_assume = memory_context.get("do_not_assume") or []

    lines = [
        "客户记忆上下文（由本地历史记录确定性抽取；以下都是候选信息，不是确认事实）：",
        f"- profile_id={memory_context.get('profile_id') or ''}",
        f"- display_name={identity.get('display_name') or ''}",
        f"- conversation_type={identity.get('conversation_type') or ''}",
        f"- risk.profile_state={risk.get('profile_state') or ''}",
        f"- risk.auto_reply_blocked={risk.get('auto_reply_blocked')}",
        f"- risk.block_reasons={', '.join(risk.get('block_reasons') or []) or 'none'}",
        f"- risk.stale_level={risk.get('stale_level') or ''}",
        f"- risk.pii_present={risk.get('pii_present')}",
        "",
        "最近历史片段（仅作背景，当前消息优先）：",
    ]
    for message in recent_messages[-6:]:
        timestamp = message.get("timestamp") or ""
        direction = message.get("direction") or ""
        text = str(message.get("text") or "").replace("\n", "\\n").strip()
        if text:
            lines.append(f"- [{timestamp}] direction={direction} text={text}")

    def add_fact_group(title: str, key: str) -> None:
        items = candidate_facts.get(key) or []
        if not items:
            return
        lines.extend(["", title])
        for item in items[:4]:
            value = str(item.get("value") or "").replace("\n", " ").strip()
            keyword = item.get("matched_keyword") or ""
            evidence = item.get("evidence") or {}
            evidence_time = evidence.get("timestamp") or ""
            if value:
                lines.append(f"- {value} ({evidence_time}, keyword={keyword or 'n/a'})")

    add_fact_group("售后/物流/质量候选：", "after_sales_issues")
    add_fact_group("明确偏好候选：", "explicit_preferences")
    add_fact_group("明确拒绝候选：", "explicit_rejections")
    add_fact_group("待跟进候选：", "pending_followups")
    add_fact_group("我方承诺候选：", "commitments_from_us")

    if do_not_assume:
        lines.extend(["", "回复边界："])
        for item in do_not_assume:
            lines.append(f"- {item}")

    return "\n".join(lines)


def build_service_knowledge_excerpt(service_knowledge_context: Optional[dict[str, Any]]) -> str:
    if not service_knowledge_context:
        return ""

    playbooks = service_knowledge_context.get("playbooks") or []
    if not playbooks:
        return ""

    lines = [
        "服务知识上下文（公共客服 playbook；不是客户事实）：",
    ]
    for playbook in playbooks[:2]:
        lines.append("")
        lines.append(f"- playbook={playbook.get('id') or ''} path={playbook.get('path') or ''}")
        guidance = playbook.get("draft_guidance") or []
        if guidance:
            lines.append("  回复原则：")
            for item in guidance[:4]:
                lines.append(f"  - {item}")
        safety_rules = playbook.get("safety_rules") or []
        if safety_rules:
            lines.append("  安全边界：")
            for item in safety_rules[:4]:
                lines.append(f"  - {item}")
        questions = playbook.get("clarifying_questions") or []
        if questions:
            lines.append("  可用追问方向：")
            for item in questions[:3]:
                lines.append(f"  - {item}")

    do_not_assume = service_knowledge_context.get("do_not_assume") or []
    if do_not_assume:
        lines.extend(["", "服务知识使用边界："])
        for item in do_not_assume[:5]:
            lines.append(f"- {item}")
    return "\n".join(lines)


def build_personal_style_excerpt(personal_style_context: Optional[dict[str, Any]]) -> str:
    if not personal_style_context:
        return ""

    global_style = personal_style_context.get("global_style") or {}
    contact_style = personal_style_context.get("contact_style") or {}
    examples = personal_style_context.get("similar_examples") or []
    do_not_assume = personal_style_context.get("do_not_assume") or []

    lines = [
        "个人回复风格上下文（目标是更像账号本人，只学习语气和节奏，不复制旧事实）：",
        f"- global.sample_count={global_style.get('sample_count') or 0}",
        f"- global.length_preference={global_style.get('length_preference') or 'unknown'}",
        f"- global.avg_length={global_style.get('avg_length') or 0}",
        f"- global.question_ratio={global_style.get('question_ratio') or 0}",
        f"- global.common_suffixes={', '.join(global_style.get('common_suffixes') or []) or 'none'}",
        f"- global.common_replies={', '.join(global_style.get('common_replies') or []) or 'none'}",
    ]

    if contact_style:
        lines.extend(
            [
                "",
                "当前联系人下的局部风格：",
                f"- contact.display_name={contact_style.get('display_name') or ''}",
                f"- contact.sample_count={contact_style.get('sample_count') or 0}",
                f"- contact.length_preference={contact_style.get('length_preference') or 'unknown'}",
                f"- contact.avg_length={contact_style.get('avg_length') or 0}",
                f"- contact.question_ratio={contact_style.get('question_ratio') or 0}",
                f"- contact.common_suffixes={', '.join(contact_style.get('common_suffixes') or []) or 'none'}",
                f"- contact.common_replies={', '.join(contact_style.get('common_replies') or []) or 'none'}",
            ]
        )

    if examples:
        lines.extend(["", "历史相似回复样本（只学表达方式，不要照抄事实）："])
        for example in examples[:4]:
            incoming_text = str(example.get("trigger_text") or "").replace("\n", "\\n").strip()
            reply_text = str(example.get("reply_text") or "").replace("\n", "\\n").strip()
            timestamp = example.get("timestamp") or ""
            source = example.get("source") or ""
            if incoming_text and reply_text:
                lines.append(f"- [{timestamp}] source={source} incoming={incoming_text} -> reply={reply_text}")

    if do_not_assume:
        lines.extend(["", "个人风格使用边界："])
        for item in do_not_assume[:5]:
            lines.append(f"- {item}")
    return "\n".join(lines)


def generate_reply_openai_compatible(
    *,
    latest_message: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    conversation_name: str,
    model: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    system_prompt: Optional[str] = None,
    timeout: float = 30.0,
    context_limit: int = 12,
    memory_context: Optional[dict[str, Any]] = None,
    service_knowledge_context: Optional[dict[str, Any]] = None,
    personal_style_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or "").rstrip("/")
    api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
    if not base_url:
        raise ValueError("missing OPENAI-compatible base URL")
    if not api_key:
        raise ValueError("missing OPENAI-compatible API key")
    if not model:
        raise ValueError("missing API model")

    latest_text = str(latest_message.get("text") or "").strip()
    if not latest_text:
        raise ValueError("latest message text is empty")

    default_system_prompt = (
        "你是一个微信私聊自动回复助手。"
        "请基于最近聊天上下文，生成一条自然、简短、像真人发出的中文回复。"
        "要求：1) 不要使用 Markdown；2) 不要解释你的思路；3) 一般控制在 1 到 3 句话；"
        "4) 如果对方问题信息不足，就先礼貌追问一个关键点；5) 避免过度热情和机器人口吻。"
        "注意：当前数据库里的 direction 字段可能不准确，所以请把历史消息当作混合上下文参考，不要死依赖说话方标签。"
        "客户记忆、服务知识、个人风格样本都只是辅助上下文，最新消息和最近聊天上下文优先。"
        "如果提供了个人风格上下文，只模仿语气、长度、节奏和常见表达，不要抄旧对话里的具体事实、承诺、价格、日期、人名。"
    )
    system_prompt = system_prompt or default_system_prompt
    context_excerpt = build_context_excerpt(recent_messages, limit=context_limit)
    memory_excerpt = build_memory_context_excerpt(memory_context)
    service_knowledge_excerpt = build_service_knowledge_excerpt(service_knowledge_context)
    personal_style_excerpt = build_personal_style_excerpt(personal_style_context)
    user_prompt = (
        f"会话名称：{conversation_name}\n"
        f"最近收到的新消息：{latest_text}\n\n"
        f"{memory_excerpt + chr(10) + chr(10) if memory_excerpt else ''}"
        f"{service_knowledge_excerpt + chr(10) + chr(10) if service_knowledge_excerpt else ''}"
        f"{personal_style_excerpt + chr(10) + chr(10) if personal_style_excerpt else ''}"
        f"最近聊天上下文（按时间顺序，direction 仅供参考）：\n{context_excerpt or '[无可用上下文]'}\n\n"
        "现在请直接输出一条适合发回微信的回复内容，只输出回复正文。"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    request_result = _request_chat_completion(
        base_url=base_url,
        api_key=api_key,
        payload=payload,
        timeout=timeout,
    )
    response_data = request_result["response_data"]

    choices = response_data.get("choices") or []
    if not choices:
        raise ValueError(f"API returned no choices: {json.dumps(response_data, ensure_ascii=False)}")
    message = choices[0].get("message") or {}
    reply_text = _normalize_reply_text(message.get("content"))
    streaming_fallback_used = False
    if not reply_text:
        stream_result = _request_chat_completion(
            base_url=base_url,
            api_key=api_key,
            payload={**payload, "stream": True},
            timeout=timeout,
        )
        stream_reply_text = _normalize_reply_text(stream_result.get("reply_text"))
        if stream_reply_text:
            response_data = stream_result["response_data"] or response_data
            reply_text = stream_reply_text
            streaming_fallback_used = True
    if not reply_text:
        raise ValueError(
            "API returned empty reply content. "
            "This usually means the proxy exposed the model but did not map its output fields correctly. "
            f"raw={json.dumps(response_data, ensure_ascii=False)}"
        )

    return {
        "reply_text": reply_text,
        "model": model,
        "base_url": base_url,
        "memory_context_used": bool(memory_context),
        "service_knowledge_used": bool(service_knowledge_context),
        "personal_style_used": bool(personal_style_context),
        "streaming_fallback_used": streaming_fallback_used,
        "request_messages": payload["messages"],
        "raw_response": response_data,
    }
