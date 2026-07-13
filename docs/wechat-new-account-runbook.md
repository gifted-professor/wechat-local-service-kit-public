# WeChat New Account Runbook

This runbook is the repeatable checklist for adding another local macOS WeChat account to this repository.

It intentionally avoids real account IDs. Use `wxid_xxx` as the placeholder for the account currently being processed.

## Goal

For each newly logged-in WeChat account, we want all of these to be true:

1. The local WeChat database folder exists.
2. The repo-local `wx-cli` profile exists.
3. `all_keys.json` contains non-zero database keys.
4. Chat export finishes with a real `manifest.json`.
5. Customer memory build finishes with the same number of profiles as exported conversations.

If any step is missing, the account is not considered fully captured.

## Directory Map

Raw WeChat data stays in the macOS WeChat container:

```text
/Users/<user>/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage
```

Repo-local account profiles live beside the code:

```text
.wx-cli-<profile-name>/
  config.json
  all_keys.json
```

Trusted per-account outputs should live under:

```text
out/accounts/<wxid>/chat-export/
out/accounts/<wxid>/customer-memory/
```

If a special recovery/export path was used, name it clearly, for example:

```text
out/accounts/<wxid>/chat-export-keyed/
out/accounts/<wxid>/customer-memory-keyed/
```

Do not treat old exploratory folders as trusted unless their manifests were verified.

## Step 1. Confirm Which Account Is Active

After logging into a new WeChat account, identify the most recently updated `wxid` folder:

```bash
python3 - <<'PY'
from pathlib import Path

root = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
for path in sorted(
    [p for p in root.iterdir() if p.is_dir() and p.name.startswith("wxid_")],
    key=lambda p: p.stat().st_mtime,
    reverse=True,
):
    db = path / "db_storage"
    latest = 0
    latest_file = ""
    if db.exists():
        for file in db.rglob("*.db*"):
            try:
                mtime = file.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime > latest:
                latest = mtime
                latest_file = str(file.relative_to(db))
    print(path.name, "db_storage=", db.exists(), "latest=", int(latest) if latest else "", latest_file)
PY
```

The account at the top is usually the current account. Confirm it has `db_storage=True` and recent write activity.

## Step 2. Warm Up WeChat Before Capturing Keys

Before key capture, keep WeChat open and logged into the target account.

Open several chats:

- Open at least 2 private chats.
- Open at least 1 group chat.
- Prefer chats with more history.
- Scroll upward a few screens in each chat.

This makes WeChat load more local databases and increases the chance that key extraction succeeds.

## Step 3. Capture Keys

Run:

```bash
./.wx-cli-tools/node_modules/.bin/wx init --force
```

If root/admin permission is needed on macOS, run it through the existing admin flow:

```bash
osascript -e 'do shell script "cd /path/to/repo && /path/to/repo/.wx-cli-tools/node_modules/.bin/wx init --force" with administrator privileges'
```

Success looks like:

```text
找到数据目录: .../<wxid>/db_storage
成功提取 N 个数据库密钥
```

`N` must be greater than zero.

## Step 4. Copy Keys Into The Repo Profile

Create or update a repo-local profile:

```text
.wx-cli-<wxid>/
  config.json
  all_keys.json
```

`config.json` should look like:

```json
{
  "decrypted_dir": "decrypted",
  "db_dir": "/Users/<user>/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage",
  "keys_file": "all_keys.json"
}
```

Copy the freshly captured keys:

```bash
cp "$HOME/.wx-cli/all_keys.json" ".wx-cli-<wxid>/all_keys.json"
chmod 600 ".wx-cli-<wxid>/all_keys.json"
```

Check the account list:

```bash
python3 scripts/manage_wechat_accounts.py list
```

The new account should show `key_count > 0`.

## Step 5. Export Chats

Preferred command:

```bash
python3 scripts/export_chat_history.py \
  --output "out/accounts/<wxid>/chat-export" \
  --wx-cli-profile ".wx-cli-<wxid>"
```

Success output should include:

```json
{
  "per_db_key_count": 20,
  "manifest": {
    "total_conversations": 1234,
    "total_messages": 56789,
    "warnings": []
  }
}
```

The exact counts will differ by account. `warnings` should normally be empty.

## Step 6. Build Customer Memory

Run:

```bash
python3 scripts/build_customer_memory.py \
  --export-root "out/accounts/<wxid>/chat-export/export" \
  --out-root "out/accounts/<wxid>/customer-memory"
```

Success output should include:

```json
{
  "total_conversations_in_export": 1234,
  "built_profiles": 1234,
  "skipped_profiles": 0
}
```

The important check is that `built_profiles` matches the exported conversation count.

## Final Verification

Use:

```bash
python3 - <<'PY'
from pathlib import Path
import json

wxid = "<wxid>"
account_root = Path("out/accounts") / wxid
manifest_path = account_root / "chat-export" / "export" / "manifest.json"
profile_index_path = account_root / "customer-memory" / "indexes" / "profile_index.json"

manifest = json.loads(manifest_path.read_text())
profiles = json.loads(profile_index_path.read_text())

print("conversations:", manifest.get("total_conversations"))
print("messages:", manifest.get("total_messages"))
print("memory profiles:", len(profiles))
print("warnings:", manifest.get("warnings"))
PY
```

The account is complete only if:

- `total_conversations` is greater than zero.
- `total_messages` is greater than zero.
- `memory profiles` equals `total_conversations`.
- `warnings` is empty or understood.

## Pitfalls We Hit

### Pitfall: `wx init --force` Finds The Account But Extracts 0 Keys

Observed symptom:

```text
找到数据目录: .../<wxid>/db_storage
成功提取 0 个数据库密钥
```

What it means:

The account folder exists, but WeChat did not expose usable database keys at that moment.

What to do:

1. Keep WeChat open.
2. Open several private/group chats.
3. Scroll up in message history.
4. If it still returns 0, quit WeChat, reopen it, log into the same account, and repeat.
5. Run `wx init --force` again.

Do not mark the account as complete while `key_count = 0`.

### Pitfall: `scan_wechat_memory_keys.py` Returns 0 Hits

Observed symptom:

```json
{
  "hit_count": 0,
  "validated_count": 0,
  "all_keys": {}
}
```

What it means:

The memory scanner did not find key strings matching the target database salts. This can happen even when the account is logged in.

What to do:

Use `wx init --force` after warming up chats. In the successful run, `wx init --force` extracted keys even after direct memory scanning returned 0.

### Pitfall: `wx-daemon` Can Point At The Wrong Account

Observed symptom:

```text
错误: 无法解密 session.db
```

or session/history output seems to belong to another account.

What it means:

`wx-cli` has a global daemon/cache under `~/.wx-cli`. It may keep state from a previous account.

What to do:

Stop the daemon before switching accounts:

```bash
./.wx-cli-tools/node_modules/.bin/wx daemon stop
```

Then rerun the intended command from the correct profile context.

### Pitfall: A 0-Key Repo Profile Can Break Reads

Observed symptom:

`scripts/manage_wechat_accounts.py list` shows the account, but `key_count = 0`, and `wx sessions` fails with:

```text
错误: 无法解密 session.db
```

What it means:

The profile exists but cannot decrypt the account.

What to do:

Do not set `.wx-cli-profile` to a 0-key profile. Capture keys first, then activate/use the profile.

### Pitfall: Cache-Based Test Output Can Be Misleading

Observed symptom:

A new account output folder exists, but the conversation/message counts are identical to an older account.

What it means:

The output may have been produced from stale `wx-cli` cache or a mismatched daemon, not from the new account.

What to do:

Verify:

- The account profile has `key_count > 0`.
- The export summary has `per_db_key_count > 0`.
- The export `db_storage_root` points to the intended `<wxid>/db_storage`.
- The counts make sense for the target account.

If any of these fail, mark the export as exploratory, not trusted.

### Pitfall: Full Export Can Take A Long Time

Observed symptom:

The export command appears quiet for many minutes.

What it means:

The parser walks many sessions and message tables. Large accounts can take 10 to 20+ minutes.

What to do:

Check that files are increasing:

```bash
find "out/accounts/<wxid>/chat-export/export/conversations" -type f | wc -l
du -sh "out/accounts/<wxid>/chat-export"
```

If file counts and size are growing and CPU is active, let it finish.

## Push Safety

Do not push private account state to GitHub:

- `.wx-cli-*/`
- `all_keys.json`
- `out/`
- exact customer chats
- decrypted databases
- docs containing real private account IDs, if the repo should stay clean

Generic runbooks like this file are safe to push. Account-specific status documents should stay local unless deliberately sanitized.
