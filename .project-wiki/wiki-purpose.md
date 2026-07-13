---
type: operation
status: active
confidence: 0.9
privacy: public
sources:
  - sources/2026-04-21-llm-wiki.md
  - sources/2026-04-21-llm-wiki-v2.md
supersedes: []
last_verified: 2026-04-21
---

# Wiki Purpose

The project wiki is the durable knowledge layer for `wechat-local-service-kit`.

It helps maintainers and operators avoid rediscovering project rules, architecture, safety boundaries, and operating procedures.

## What Belongs Here

- Architecture decisions.
- Stable workflows.
- Operating rules.
- Safety policies.
- Review outcomes.
- Public or sanitized references.
- Design notes for future implementation.

## What Does Not Belong Here

- Raw WeChat databases.
- Decrypted data.
- Frida logs or key material.
- Customer memory JSON.
- Customer wiki pages generated under `out/customer-memory/pages/`.
- Real customer names, identifiers, or private message excerpts.

## Relationship To Auto-Reply

The project wiki improves auto-reply by defining stable rules and playbooks. It does not replace private customer memory.

Reply-time knowledge should be layered:

1. Latest live message.
2. Recent live conversation context.
3. Private customer memory when the memory gate allows it.
4. Public project playbooks from this wiki when a relevant policy or operating rule is needed.
