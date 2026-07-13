---
type: operation
status: active
confidence: 0.86
privacy: public
sources:
  - sources/2026-04-21-llm-wiki-v2.md
supersedes: []
last_verified: 2026-04-21
---

# Wiki Schema

Every durable wiki page should start with YAML frontmatter:

```yaml
---
type: architecture | operation | decision | safety | customer_memory_design | reply_policy
status: draft | active | superseded | deprecated
confidence: 0.0-1.0
privacy: public | local_only | sensitive
sources:
  - path-or-url
supersedes:
  - page-id
last_verified: YYYY-MM-DD
---
```

## Page Rules

- `privacy: public` pages must not include private customer data.
- `local_only` pages may exist locally but should not be committed unless sanitized.
- `sensitive` pages should not be committed.
- Use wikilinks such as `[[architecture/reply-time-knowledge-layer]]` for durable relationships.
- Use explicit relation lines when helpful:

```text
depends_on:: [[safety/privacy-boundaries]]
protects:: [[architecture/reply-time-knowledge-layer]]
related_to:: [[operations/contact-wiki]]
```

## Lifecycle Rules

- Prefer updating `status` and `supersedes` over deleting old decisions.
- Add `last_verified` when a page is checked against current code.
- Lower `confidence` when a rule is speculative or only tested once.
- Raise `confidence` after repeated successful use.

## Reply-Time Knowledge Rules

Reply policies and playbooks may be used in prompts only after they pass privacy checks and are relevant to the latest message. Customer-specific facts must come from `out/customer-memory/`, not this wiki.
