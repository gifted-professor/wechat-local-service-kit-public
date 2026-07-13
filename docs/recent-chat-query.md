# Recent Chat Query

This document explains the read-only path for asking a local agent, such as Hermes, to inspect recent WeChat messages.

The important distinction is the data source:

- `live-inbox`: reads plaintext JSONL events already captured by the live inbox worker. This does not call `wx`, does not decrypt historical databases, and is the preferred source for synced cross-device access.
- `wx-history`: calls `wx sessions` / `wx history` through `wx-cli`. This can read older chat history, but it depends on a working local wx-cli profile and database decryption.

## Single-Machine Path

When WeChat, the live inbox worker, and the local agent run on the same Mac, no
Syncthing layer is needed. Point the reader at the local inbox:

```bash
python3 scripts/query_recent_chat.py \
  --source live-inbox \
  --live-inbox-root "$HOME/Sync/wechat-live-inbox" \
  --chat "44111650274@chatroom" \
  --limit 20 \
  --format json
```

The agent still reads only:

```text
~/Sync/wechat-live-inbox/events.jsonl
```

## Synced Hermes Path

When the remote machine already receives the synced live inbox folder, use:

```bash
python3 scripts/query_recent_chat.py \
  --source live-inbox \
  --live-inbox-root "$HOME/Sync/wechat-live-inbox" \
  --chat "44111650274@chatroom" \
  --limit 20 \
  --format json
```

Hermes should treat the JSON output as local tool data, then summarize it for the user.

The remote reader also reads only its local synced copy of:

```text
~/Sync/wechat-live-inbox/events.jsonl
```

It does not:

- call a model
- call `wx sessions`
- call `wx history`
- decrypt `session.db`
- control the WeChat UI
- send messages

The JSON payload includes safety markers:

```json
{
  "model_api_touched": false,
  "ui_touched": false,
  "sent": false
}
```

## Source Selection

Use `live-inbox` when the question is about messages captured after the live inbox worker started:

```bash
python3 scripts/query_recent_chat.py \
  --source live-inbox \
  --chat "group or contact name" \
  --limit 50 \
  --format json
```

Use `wx-history` only when older local chat history is required and wx-cli database decryption is healthy:

```bash
python3 scripts/query_recent_chat.py \
  --source wx-history \
  --chat "group or contact name" \
  --limit 50 \
  --format json
```

If `wx-history` fails with `unable to decrypt session.db`, switch back to `live-inbox` for real-time captured messages.

## Live Inbox Limits

`live-inbox` is an append-only new-message stream. It can only answer questions using events that were captured after the worker started.

It is good for:

- "What did this group talk about recently?"
- "Who spoke in the latest live events?"
- "What did I receive since the inbox worker started?"

It is not enough for:

- old messages before the worker started
- complete conversation history
- reliable group member identity resolution

For group chats, `live-inbox` reports the sender field from the event. It does not resolve that sender against the group member list, because that would require calling wx-cli member/history APIs.

## Health Checks

On the producer Mac:

```bash
cat ~/Sync/wechat-live-inbox/heartbeat.json
wc -l ~/Sync/wechat-live-inbox/events.jsonl
tail -n 5 ~/Sync/wechat-live-inbox/events.jsonl
```

On the remote Mac, check that Syncthing has delivered the same files:

```bash
ls -lah ~/Sync/wechat-live-inbox
cat ~/Sync/wechat-live-inbox/heartbeat.json
wc -l ~/Sync/wechat-live-inbox/events.jsonl
```

Remember that timestamps ending in `Z` are UTC. For China Standard Time, add 8 hours.

## Key Capture Boundary

The `live-inbox` reader does not use keys. Key/profile state belongs to the
producer side, where `wx-cli` reads local WeChat data before appending plaintext
events.

For fresh setup, account rebinding, or fixing `key_count = 0`, the operator must
open WeChat, click into representative private/group chats, and scroll upward so
WeChat loads the local databases. Then rerun `wx init --force`. This is not
needed for every later summary once `events.jsonl` is already being written.

## Troubleshooting

If `--source live-inbox` says the events file is missing:

```bash
ls -lah "$HOME/Sync/wechat-live-inbox"
```

Pass the actual synced folder:

```bash
python3 scripts/query_recent_chat.py \
  --source live-inbox \
  --live-inbox-root "/actual/path/to/wechat-live-inbox" \
  --chat "44111650274@chatroom" \
  --limit 20 \
  --format json
```

If `--source wx-history` fails with `unable to decrypt session.db`, that is a wx-cli profile/key issue, not a live-inbox issue. The live-inbox path can still work as long as `events.jsonl` has synced plaintext events.
