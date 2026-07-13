---
type: operation
status: active
confidence: 0.84
privacy: public
sources:
  - scripts/build_contact_wiki.py
  - scripts/build_contact_activity_report.py
  - scripts/customer_memory.py
supersedes: []
last_verified: 2026-04-21
---

# Contact Wiki

The contact wiki is a private local review layer for common or high-value WeChat contacts.

It is built from:

- `out/contact-activity/contact_activity_report.json`
- `out/customer-memory/indexes/profile_index.json`
- existing customer-memory profile JSON files

It does not rebuild memory and does not send messages.

## Command

```bash
python3 scripts/build_contact_wiki.py \
  --activity-report out/contact-activity/contact_activity_report.json \
  --memory-root out/customer-memory \
  --out-root out/contact-wiki \
  --max-pages 200 \
  --clean
```

## Outputs

- `out/contact-wiki/summary.json`: aggregate counts, thresholds, exclusions, and quality flags.
- `out/contact-wiki/manifest.json`: selected contacts with selection reasons and page paths.
- `out/contact-wiki/pages/*.md`: private local Markdown pages with frontmatter plus deterministic customer-memory content.

These outputs stay under `out/` and must not be committed.

## Default Selection

Version 1 only treats direct friend conversations with `profile_state == eligible` and `notification_muted == false` as reply-ready.

Excluded by default:

- group conversations
- official accounts
- muted contacts
- conversations with unknown notification state
- ambiguous profiles
- no-message profiles
- conversations missing from the activity report

Selected contacts are capped by `--max-pages` and sorted by a deterministic score:

- message-count percentile: 50%
- active-days percentile: 30%
- recency: 20%

Tiers:

- `high_value`: top message count, top active days, or recent contact with sufficient total messages.
- `common`: enough messages, enough active days, and recent enough contact.

## Reply Boundary

Contact wiki page existence does not mean auto-reply is allowed.

Each manifest record includes:

- `reply_ready`: profile is eligible, not auto-reply blocked, and explicitly non-muted.
- `review_only`: profile exists for human review but should not be injected into reply drafts automatically.
- `quality_flags`: advisory flags such as PII, staleness, low readable ratio, auto-reply block, or activity-index/message-timestamp mismatch. PII is local-only metadata and does not block reply readiness.
- `suggested_next_action`: conservative next-use guidance.

## Live Candidate Monitoring

The durable live monitor should be managed by the local reply service:

```bash
python3 scripts/wechat_reply_service.py start --check-wx-cli
```

The underlying monitor command is:

```bash
mkdir -p out/dry-run-replies/monitor
nohup python3 scripts/monitor_reply_candidates.py \
  --duration 86400 \
  --interval 5 \
  --history-limit 12 \
  --new-message-limit 100 \
  --message-fresh-within-seconds 1800 \
  --out-root out/dry-run-replies/monitor \
  > out/dry-run-replies/monitor/monitor.log 2>&1 &
echo $! > out/dry-run-replies/monitor/monitor.pid
```

External supervision should not own the service lifecycle. Sandboxed checks may be unable to access `~/.wx-cli`, which causes false daemon-permission failures. A supervisor should only read:

- `out/dry-run-replies/monitor/state.json`
- `out/dry-run-replies/monitor/events.jsonl`
- `out/dry-run-replies/monitor/monitor.pid`
- `out/dry-run-replies/monitor/monitor.log`

If the Terminal monitor is not running, ask the operator to restart it. Do not fall back to starting wx-cli from a sandboxed checker.

Only fresh events may drive draft generation. The current gate is `--message-fresh-within-seconds 1800`; old wx-cli backlog events may be inspected, but must not be drafted or pasted into WeChat.

## Automatic Draft-Only Worker

For this owner-operated local account, the user has approved automatic draft generation and draft-only UI paste once the polling task is running. This approval covers:

- fresh `reply_ready` private, non-muted candidates;
- sending relevant recent chat context to the currently configured OpenAI-compatible draft API;
- opening WeChat and pasting the generated draft into the input box;
- never pressing Enter or sending the message.

Actual WeChat sending still requires immediate user confirmation.

The durable draft worker should also run from the user's normal system Terminal or launchd:

```bash
mkdir -p out/dry-run-replies/drafts
nohup python3 scripts/draft_reply_worker.py \
  --duration 86400 \
  --interval 8 \
  --fresh-within-seconds 1800 \
  --context-messages 16 \
  --memory-mode draft-only \
  --memory-use-policy auto \
  --service-knowledge-mode draft-only \
  --service-knowledge-policy auto \
  > out/dry-run-replies/drafts/worker.log 2>&1 &
echo $! > out/dry-run-replies/drafts/worker.pid
```

The draft worker calls `scripts/draft_reply_candidate_once.py`, which applies a draft-worthiness gate before model/API or UI work. Likely broadcast promotions, app-card spam, placeholders, and non-text messages are skipped and recorded under `out/dry-run-replies/drafts/`.

A supervisor may check the worker by reading:

- `out/dry-run-replies/drafts/worker-state.json`
- `out/dry-run-replies/drafts/worker-events.jsonl`
- `out/dry-run-replies/drafts/drafts.jsonl`
- `out/dry-run-replies/drafts/worker.pid`
- `out/dry-run-replies/drafts/worker.log`

## Interpretation Notes

- Selection is deterministic and does not use LLM ranking.
- Pages reuse the existing customer-memory Markdown renderer.
- Extracted facts are candidates, not confirmed truth.
- A conversation index can be newer than the last timestamped exported message; those pages should be checked against live context before use.
- A later iteration may add a lower-leakage facts-only page mode.
