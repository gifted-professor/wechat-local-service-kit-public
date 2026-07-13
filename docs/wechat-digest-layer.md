# WeChat Digest Layer

This repository keeps digest generation as a read-only output layer. It can
summarize captured WeChat events into local Markdown/JSON files without calling
an LLM, touching the WeChat UI, or sending messages.

The design borrows the stable shape from `cliffyan28/wechat-digest`: keep exact
message counts deterministic, save the source records, then optionally give the
Markdown to a model outside the data-extraction script.

## Recommended Path

Use the synced live inbox when possible:

```bash
python3 scripts/build_wechat_digest.py \
  --source live-inbox \
  --live-inbox-root "$HOME/Sync/wechat-live-inbox" \
  --date 2026-06-29
```

Outputs are written under:

```text
out/wechat-digest/<date>/<chat-or-all>/
  digest.json
  digest.md
  messages.jsonl
```

The generated payload includes:

- `model_api_touched: false`
- `ui_touched: false`
- `sent: false`
- exact `message_count`, `conversation_count`, link count, and follow-up count

## Chat Filters

For one chat:

```bash
python3 scripts/build_wechat_digest.py \
  --source live-inbox \
  --chat "44111650274@chatroom" \
  --date 2026-06-29
```

For multiple chats, repeat `--chat` or pass comma-separated values:

```bash
python3 scripts/build_wechat_digest.py \
  --chat "客户A" \
  --chat "客户B,44111650274@chatroom"
```

## Historical Fallback

Use `wx-history` only when the local `wx-cli` profile and database decryption are
healthy:

```bash
python3 scripts/build_wechat_digest.py \
  --source wx-history \
  --chat "联系人或群名" \
  --date 2026-06-29 \
  --wx-cli-profile /Users/<user>/.wx-cli
```

If this fails with a profile or decryption error, switch back to `live-inbox`.
The digest layer should not trigger profile repair, key extraction, daemon
stops, UI sends, or any irreversible action.

## Why This Is Separate

`wechat-digest` also includes direct database extraction, voice transcription,
article fetching, and PDF generation. Those are useful ideas, but this project
adds them gradually:

- direct database parsing stays inside `export_chat_history.py` and
  `parse_chat_history.py`
- voice transcription should be a sidecar artifact before it is allowed into
  reply drafting
- article body fetching should extend `export_appmsg_archive.py` as an optional
  network step
- PDF generation is optional presentation, not part of the core local agent path

This keeps the operational boundary clear: capture/read first, summarize second,
and only send after a separate supervised confirmation path.
