# Live Monitor and Draft-Only Worker Runbook

This runbook starts the local WeChat reply-candidate monitor and the draft-only worker on a trusted Mac.

The product principle is full local-chain ownership after the user grants permissions. The service manager starts, stops, restarts, and checks the local monitor and draft-only worker. Optional external supervision may still read the project-local output files.

## Prerequisites

- WeChat is installed, logged in, and local data access has already been bootstrapped.
- `out/contact-wiki/manifest.json` exists and includes `reply_ready` contacts.
- `out/customer-memory/` exists if memory-assisted drafts are desired.
- The command host has `OPENAI_BASE_URL` and `OPENAI_API_KEY` available for the draft worker.
- macOS Accessibility permission allows the command host/osascript to control WeChat for draft-only paste.
- The operator understands that draft-only paste is pre-approved for this local workflow, but real sending is not.

## Human Setup Checklist

On a fresh Mac, these are the expected manual steps before starting the live worker:

1. Clone this repository and run commands from the repository root.
2. Install the Python dependencies from `README.md`.
3. Log in to WeChat on the same Mac.
4. Run the local bootstrap/export flow from `README.md`; if multiple WeChat accounts are detected, provide `--wechat-root`.
5. Complete any macOS prompts for the debug WeChat copy, Frida attach/key capture, Terminal local-file access, and Terminal/osascript Accessibility control.
6. Build `out/customer-memory/`, `out/contact-activity/`, and `out/contact-wiki/`.
7. Export `OPENAI_BASE_URL` and `OPENAI_API_KEY` in the command host that will run the service.
8. Confirm `out/contact-wiki/manifest.json` exists before starting the monitor.

After these are done, the monitor and draft worker should run without per-message operator input. The remaining human gate is actual sending.

## Service Manager

From the repository root:

```bash
python3 scripts/wechat_reply_service.py doctor
python3 scripts/wechat_reply_service.py start --check-wx-cli
python3 scripts/wechat_reply_service.py status
```

To restart or stop the chain:

```bash
python3 scripts/wechat_reply_service.py restart --check-wx-cli --force
python3 scripts/wechat_reply_service.py stop
```

## Manual Fallback

The service manager above is preferred. If needed, the underlying commands are:

```bash
mkdir -p out/dry-run-replies/monitor out/dry-run-replies/drafts

nohup python3 scripts/monitor_reply_candidates.py \
  --duration 86400 \
  --interval 5 \
  --history-limit 12 \
  --new-message-limit 100 \
  --message-fresh-within-seconds 1800 \
  --out-root out/dry-run-replies/monitor \
  > out/dry-run-replies/monitor/monitor.log 2>&1 &
echo $! > out/dry-run-replies/monitor/monitor.pid

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

## Verify

```bash
ps -p "$(cat out/dry-run-replies/monitor/monitor.pid)"
ps -p "$(cat out/dry-run-replies/drafts/worker.pid)"

jq '{status, updated_at, ready_contact_count, new_event_count, event_count, sent}' \
  out/dry-run-replies/monitor/state.json

jq '{status, updated_at, iteration, last_returncode, sent, last_result}' \
  out/dry-run-replies/drafts/worker-state.json
```

Expected:

- Monitor status is `running`.
- Draft worker status is usually `no_candidate` while idle.
- `sent` remains `false`.

## Stop Fallback

```bash
kill "$(cat out/dry-run-replies/drafts/worker.pid)" 2>/dev/null || true
kill "$(cat out/dry-run-replies/monitor/monitor.pid)" 2>/dev/null || true
```

## Safety Boundary

- The monitor only records fresh reply-ready candidates.
- The draft worker skips likely broadcast promotions, app-card spam, placeholders, and non-text messages.
- The draft worker may call the configured model API and may paste a generated draft into the WeChat input box.
- Draft-only paste searches WeChat by public WeChat ID from `out/chat-export/export/contacts.json` (`alias`) first, then falls back to internal IDs and names.
- The draft worker must not send. Pressing Enter or otherwise sending a WeChat message still requires immediate operator confirmation.
- `out/` contains private local data and must not be committed.
