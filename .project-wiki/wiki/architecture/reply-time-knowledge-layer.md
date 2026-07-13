---
type: architecture
status: active
confidence: 0.82
privacy: public
sources:
  - ../../sources/2026-04-21-llm-wiki.md
  - ../../sources/2026-04-21-llm-wiki-v2.md
supersedes: []
last_verified: 2026-04-21
---

# Reply-Time Knowledge Layer

This page describes how wiki-style knowledge should improve supervised WeChat reply drafts.

## Problem

Customer memory alone is not enough. It can answer "what happened with this customer," but it should not define global service policy, escalation rules, reply tone, refund boundaries, or agent operating constraints.

The auto-reply stack needs two memory classes:

- Private customer memory: generated locally from chat history.
- Public project/service knowledge: stable policies, playbooks, and operating rules.

## Intended Context Order

Reply drafting should prioritize context in this order:

1. Latest incoming message.
2. Recent live conversation context.
3. Customer memory only when `memory_use_policy` allows it.
4. Project wiki playbooks only when relevant and privacy-safe.
5. Default model behavior last.

The latest message always wins over older memory.

## What The Project Wiki Can Improve

The wiki can improve auto-reply by storing:

- Tone guidelines.
- Escalation rules.
- "Never promise" constraints.
- Common after-sales playbooks.
- Logistics/refund/address clarification templates.
- Human takeover rules.
- Memory gate policy rationale.
- Known failure modes and verification steps.

## What It Must Not Store

- Customer names.
- Customer identifiers.
- Private message excerpts.
- Phone numbers, addresses, order numbers, or ID-like values.
- Frida logs, database keys, or decrypted database paths.

## Near-Term Implementation Direction

Do not inject the full project wiki into every reply.

Instead, add a future `service_knowledge_context` layer:

```text
latest message
  -> classify intent/risk
  -> memory gate decides customer memory
  -> service policy gate selects small wiki playbook snippets
  -> draft generation
  -> dry-run or human approval
```

The first implementation should be rule-based and small:

- order status -> order status playbook
- after-sales issue -> after-sales playbook
- logistics issue -> logistics playbook
- refund/replacement promise -> safety boundary
- broad/general question -> no customer memory and no service playbook unless needed

## First Implementation

The first implementation uses:

- `.project-wiki/wiki/reply-playbooks/*.md` as public playbook sources.
- `scripts/service_knowledge.py` to select a small set of playbooks from the latest message.
- `--service-knowledge-mode off|shadow|draft-only` in `scripts/auto_reply_once.py`.
- `scripts/compare_reply_contexts.py` to compare baseline, customer memory, service knowledge, and combined drafts.

Default behavior remains `off`; service knowledge must be explicitly enabled.

## Acceptance Criteria For Future Code

- Prompt context names customer memory and service knowledge separately.
- Service knowledge snippets include page/source ids.
- Private customer memory never flows into `.project-wiki/`.
- Draft output logs whether wiki knowledge was used.
- Real sends remain gated by human confirmation.

depends_on:: [[safety/privacy-boundaries]]
related_to:: [[operations/milestone-review]]
