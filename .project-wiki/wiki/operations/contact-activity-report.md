---
type: operation
status: active
confidence: 0.86
privacy: public
sources:
  - README.md
  - scripts/build_contact_activity_report.py
supersedes: []
last_verified: 2026-04-21
---

# Contact Activity Report

The contact activity report is a local-only relationship analytics layer built from exported WeChat conversation metadata and per-conversation JSONL files.

It is intended for account-level understanding before building heavier customer memory or auto-reply workflows.

## Command

```bash
python3 scripts/build_contact_activity_report.py \
  --export-root out/chat-export/export \
  --out-root out/contact-activity
```

Optional:

```bash
python3 scripts/build_contact_activity_report.py \
  --export-root out/chat-export/export \
  --out-root out/contact-activity-anonymized \
  --anonymize
```

## Outputs

- `out/contact-activity/contact_activity_report.json`: machine-readable report and per-conversation metrics.
- `out/contact-activity/contact_activity.csv`: spreadsheet-friendly sortable table.
- `out/contact-activity/contact_activity_report.md`: human-readable top lists and aggregate summary.

The outputs stay under `out/` and must not be committed.

## Metrics

Per conversation:

- Total message count.
- First and last message timestamp.
- History span in days.
- Distinct active days.
- Messages per active day.
- Messages per week over the full span.
- Recent 7/30/90 day message counts.
- Recent 7/30/90 day active-day counts.
- Recency bucket.
- Notification state from local WeChat contact metadata.
- Direction counts.
- Message type and render type counts.
- Readable-text count and placeholder/empty counts.
- Activity score and activity rank.

Aggregate:

- Conversation and message totals.
- Breakdown by conversation type.
- Non-muted, muted, and unknown-notification-state counts.
- Recency buckets.
- Top lists by overall activity, total messages, active days, recent activity, history span, and dormant high-volume relationships.
- Separate top friend and group sections.

## Privacy Boundary

The report never stores raw message text. It may include display names and usernames because it is a local personal output under `out/`.

For shareable diagnostics, run with `--anonymize`.

## Interpretation Notes

- `recent_*` metrics are relative to the newest message timestamp in the export, not necessarily the wall-clock day when the command is run.
- `last_active_*` uses the conversation index timestamp.
- `with_messages_*` uses actual timestamped JSONL message rows.
- Direction counts are advisory because not every export path reliably distinguishes sent and received messages.
- `notification_muted` is derived from local contact metadata and should be calibrated against the current WeChat UI before broad automation.
- The activity score is only a ranking helper; it is not a definitive relationship quality metric.
