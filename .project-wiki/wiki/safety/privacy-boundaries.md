---
type: safety
status: active
confidence: 0.92
privacy: public
sources:
  - ../../../docs/security-and-privacy.md
supersedes: []
last_verified: 2026-04-21
---

# Privacy Boundaries

This project separates public operating knowledge from private customer data.

## Public Or Commit-Safe

- Sanitized documentation.
- Source code.
- Project wiki pages that do not include customer data.
- Operating rules.
- Bootstrap instructions.

## Local Only

- Local workspace notes.
- Frida logs.
- Decrypted database workspaces.
- Chat exports.
- Customer memory profiles and pages.
- wx-cli local tooling and config.

## Never Print Or Commit

- `all_keys.json`.
- `config.json`.
- Database encryption keys.
- Decrypted private database contents.
- API keys or access tokens.
- Customer names, addresses, phone numbers, IDs, order numbers, or private message excerpts.

protects:: [[architecture/reply-time-knowledge-layer]]

## Live Reply Gate

Default live monitoring and reply drafting only handle private, explicitly non-muted chats. Group chats, official accounts, muted chats, and chats with unknown notification state are skipped before model drafting or UI sending.
