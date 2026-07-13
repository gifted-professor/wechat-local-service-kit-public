# wechat-local-service-kit

**English** | [简体中文](README.zh-CN.md)

Local-first tooling for macOS and Windows WeChat data export, customer memory, and supervised customer-service reply drafts.

This repository started as a WeChat Favorites visualization project. It now also includes a broader local WeChat assistant workflow:

- Export encrypted local WeChat databases on macOS or Windows into structured JSONL.
- Build deterministic, conversation-scoped customer memory profiles.
- Render human-readable customer wiki pages for review.
- Generate OpenAI-compatible reply drafts with optional memory context.
- Keep real WeChat sending behind local UI automation, dry-run checks, and explicit operator confirmation.

The current direction is intentionally local-first. Private WeChat databases, extracted keys, chat exports, and customer memory outputs stay on the user's machine and are ignored by git.

## Single-Machine vs Syncthing Strategy

Use one local live inbox when WeChat and the agent run on the same Mac:

```text
WeChat -> wx-cli/live-inbox worker -> ~/Sync/wechat-live-inbox/events.jsonl -> local agent reads JSONL
```

In this mode, Syncthing is not required. The inbox is still useful because it
keeps the agent on a read-only plaintext log path instead of letting it touch
WeChat databases, key files, or the WeChat UI.

Use Syncthing only when another Mac needs to read the inbox:

```text
producer Mac writes ~/Sync/wechat-live-inbox/events.jsonl
-> Syncthing
-> reader Mac uses the synced events.jsonl
```

Keep `live-inbox` text-first. `events.jsonl` grows over time, but text events
stay relatively small; images, audio, video, or exported databases should not be
mixed into this folder unless there is an explicit retention plan.

## New Mac / Transfer Quick Start

If you are moving this project to another Mac, start here:

- [MIGRATION.md](MIGRATION.md) is the handoff guide for zip packages, private GitHub clones, dependencies, local WeChat data, and repo-local `wx-cli` profiles.
- A code-only clone is not enough to read WeChat history. The target Mac also needs local WeChat data, extracted keys, or a fresh key-capture run.
- A private transfer package may include `out/` and `.wx-cli-*` profiles for the same owner on a trusted Mac, but those paths can contain highly sensitive chat exports and key material.

If you are sending this repository to another operator or to an installation agent, send the GitHub URL plus this instruction:

```text
Clone the repository, follow README.md from "New Mac / Transfer Quick Start",
do not print all_keys.json or decrypted database contents, and stop at the
first permission/decryption blocker with the exact command and redacted error.
```

Fresh Mac assumptions:

- macOS with WeChat for macOS installed at `/Applications/WeChat.app`.
- The target WeChat account is logged in on this Mac.
- Node.js 18+, Python 3.10+, and Git are available.
- The operator can approve local macOS permission prompts when key extraction needs them.

Clone and install:

```bash
git clone https://github.com/gifted-professor/wechat-local-service-kit-public.git wechat-local-service-kit
cd wechat-local-service-kit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm --prefix .wx-cli-tools ci
```

If `.wx-cli-tools/package-lock.json` is not present, install `wx-cli` directly:

```bash
mkdir -p .wx-cli-tools
npm --prefix .wx-cli-tools install @jackwener/wx-cli@0.3.0
```

The `wx-cli` integration target is [`jackwener/wx-cli`](https://github.com/jackwener/wx-cli).

Run the local doctor before touching keys:

```bash
python3 scripts/bootstrap_local_wechat.py --doctor-only
python3 scripts/wechat_tool_status.py --skip-daemon --compact
```

Warm up WeChat before key extraction:

1. Keep WeChat open and logged into the target account.
2. Open at least two private chats and one group chat.
3. Scroll upward a few screens in each chat so WeChat loads local databases.

This is a required user action for fresh key capture, account rebinding, or
repairing `key_count = 0`. Do not expect a background agent to capture usable
keys silently before the operator opens representative chats. Once the live
inbox is already running and writing plaintext events, later digest/query reads
do not need this click-through step.

Initialize `wx-cli` and extract local database keys:

```bash
./.wx-cli-tools/node_modules/.bin/wx init --force
```

Success must show a non-zero key count, for example `成功提取 N 个数据库密钥` with `N > 0`.

If macOS blocks process access or the command needs elevated permission, rerun it through the system prompt:

```bash
osascript -e 'do shell script "cd /path/to/wechat-local-service-kit && ./.wx-cli-tools/node_modules/.bin/wx init --force" with administrator privileges'
```

If the target account folder is unclear, list local WeChat account folders without reading keys:

```bash
python3 - <<'PY'
from pathlib import Path

root = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
for path in sorted([p for p in root.glob("wxid_*") if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
    db = path / "db_storage"
    print(path.name, "db_storage=", db.exists(), "mtime=", int(path.stat().st_mtime))
PY
```

Create a repo-local `wx-cli` profile. Replace `<wxid>` with the target local account folder:

```bash
WXID="<wxid>"
mkdir -p ".wx-cli-$WXID"
cp "$HOME/.wx-cli/all_keys.json" ".wx-cli-$WXID/all_keys.json"
chmod 600 ".wx-cli-$WXID/all_keys.json"
cat > ".wx-cli-$WXID/config.json" <<EOF
{
  "decrypted_dir": "decrypted",
  "db_dir": "/Users/$USER/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/$WXID/db_storage",
  "keys_file": "all_keys.json"
}
EOF
printf '.wx-cli-%s\n' "$WXID" > .wx-cli-profile
```

Never paste or print `all_keys.json`. It is local key material and must stay out of Git, chat logs, and screenshots.

Verify the profile without exposing keys:

```bash
python3 scripts/manage_wechat_accounts.py list
python3 scripts/wechat_tool_status.py --skip-daemon --compact
```

The selected profile should show `key_count > 0` and `has_db_dir: true`. Then run a small read/export check:

```bash
python3 scripts/export_wx_cli_history.py \
  --output "$PWD/out/wx-cli-smoke" \
  --private-only \
  --session-limit 5
```

If the smoke export works, the fuller account flow is:

```bash
python3 scripts/export_chat_history.py \
  --output "out/accounts/<wxid>/chat-export" \
  --wx-cli-profile ".wx-cli-<wxid>"

python3 scripts/build_customer_memory.py \
  --export-root "out/accounts/<wxid>/chat-export/export" \
  --out-root "out/accounts/<wxid>/customer-memory"
```

Common first-install blockers:

- `key_count = 0`: WeChat did not expose usable keys yet. Keep WeChat open, open several chats, scroll history, then rerun `wx init --force`.
- `task_for_pid 失败 (kr=5)` or attach/spawn permission errors: this is a macOS permission/signing boundary, not a repo checkout problem. Approve the system prompt, use the `osascript` command above, or follow `docs/wechat-new-account-runbook.md`.
- `无法解密 session.db` or `file is not a database`: first check active profile, `db_dir`, `key_count`, and `wx-cli` daemon/cache state. Do not delete cache or regenerate outputs until the profile/key boundary is clear.
- Output looks like the wrong account: stop the daemon with `./.wx-cli-tools/node_modules/.bin/wx daemon stop`, verify `.wx-cli-profile`, and rerun from the intended profile.
- GitHub clone does not include `out/`, `.wx-cli-profile`, `.wx-cli-*`, `all_keys.json`, decrypted DBs, or customer memory. That is intentional; those assets are private and machine-local.

For a longer checklist and recovery notes, see [WeChat New Account Runbook](docs/wechat-new-account-runbook.md).

## Windows: Capture Keys and Export Plaintext JSONL

Windows support is intentionally limited to the local read/export path. It does
not include Windows UI sending or unattended auto-reply.

Requirements:

- 64-bit Windows with the target account logged in to `Weixin.exe`.
- Administrator PowerShell, Python 3.10+, Node.js 18+, and Git.
- A known `db_storage` directory for the same account whose process memory will
  be scanned.

Install the repository dependencies from PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
npm --prefix .wx-cli-tools ci
```

Discover likely Windows WeChat account and database locations without reading
message content or secret files:

```powershell
python scripts/discover_windows_wechat.py --compact
```

Keep the target account open, enter a few representative chats, and scroll some
history. Then run the one-shot capture from Administrator PowerShell. Replace
the label and `db_storage` path with local values:

```powershell
python scripts/capture_windows_wx_profile.py windows-main `
  --db-dir 'D:\wechat\xwechat_files\<wxid>\db_storage' `
  --activate `
  --stop-daemon
```

The scanner checks every matching `Weixin.exe` process and accepts a candidate
key only after it validates against an encrypted database page HMAC. Raw keys
are never printed or written to the status JSON. They are stored only in the
gitignored local profile, such as `.wx-cli-windows-main/all_keys.json`.

Verify the active profile, then run a bounded plaintext export:

```powershell
python scripts/wechat_tool_status.py --skip-daemon --compact

python scripts/export_wx_cli_history.py `
  --output '.\out\windows-smoke' `
  --private-only `
  --session-limit 5 `
  --max-messages-per-conversation 50
```

Successful plaintext output appears under:

```text
out\windows-smoke\export\conversations\*.jsonl
out\windows-smoke\export\conversation_index.json
out\windows-smoke\export\sessions.json
out\windows-smoke\export\coverage.json
```

If several accounts are logged in, repeat `--pid <Weixin.exe PID>` to restrict
the scan. If the default writable-page scan finds no validated key, retry once
with `--include-readonly`; `--include-bare-hex` is a noisier final fallback, but
all candidates still require database HMAC validation. Never commit the profile
directory or anything under `out/`.

## Status

The repository currently contains working prototype scripts, not a packaged product.

Verified locally:

- Frida-based PBKDF2 key capture for macOS WeChat 4.x.
- SQLCipher-style local database preparation.
- Contact, session, and message export.
- `wx-cli`-backed session/history/new-message reads.
- Single-conversation dry-run reply generation.
- Customer memory profile generation and Markdown wiki rendering.
- A conservative memory-use gate that avoids injecting customer history into broad/general questions.

Windows read/export scope:

- Account/database discovery and one-shot `Weixin.exe` memory scanning are included.
- Candidate keys are HMAC-validated and written only to a local gitignored profile.
- Plaintext export reuses the same `wx-cli` JSONL pipeline as macOS.
- Runtime capture must be verified on a Windows host; it cannot be exercised from macOS CI.

Still experimental:

- Long-running daemon packaging.
- Robust UI send verification across all WeChat versions.
- Multi-account operations.
- Fully unattended auto-reply.

## Safety Model

This project is designed around a strict safety boundary:

- Do not commit private data, exported chats, decrypted databases, or key files.
- Do not send WeChat messages without explicit immediate operator confirmation.
- Prefer `--dry-run` while developing or testing reply generation.
- Treat extracted customer facts as candidates, not truth.
- Keep group auto-reply disabled unless a human explicitly enables it for a narrow test.

See [Security and Privacy](docs/security-and-privacy.md) for the full policy.

## Repository Layout

```text
scripts/
  grab_wechat_key.py             # Capture PBKDF2 events with Frida.
  discover_windows_wechat.py     # Discover Windows account/database locations without reading messages.
  win_wx_multi_key_scan.py       # HMAC-validate keys found in Windows Weixin.exe memory.
  capture_windows_wx_profile.py  # Save validated keys into a local gitignored wx-cli profile.
  match_wechat_key.py            # Match captured keys against a database salt.
  chat_crypto.py                 # Prepare readable SQLite copies.
  export_chat_history.py         # Export contacts, sessions, and messages.
  export_wx_cli_history.py       # Export sessions/history through wx-cli into the same JSONL shape.
  parse_chat_history.py          # Parse WeChat message databases.
  build_wechat_digest.py         # Build read-only daily digest Markdown/JSON from live-inbox or wx-history.
  wx_cli_adapter.py              # Local wrapper around project wx-cli.
  watch_conversation_messages.py # Watch one conversation for new messages.
  auto_reply_once.py             # Generate or send one supervised reply cycle.
  wechat_reply_service.py        # Manage live monitor and draft-only worker.
  customer_memory.py             # Build/query/render runtime customer memory.
  build_customer_memory.py       # Build deterministic customer profiles.
  query_customer_memory.py       # Search customer memory profiles.
  render_customer_pages.py       # Render human-readable Markdown pages.
  build_runtime_context.py       # Build compact prompt context.
  compare_memory_draft.py        # Compare reply drafts with/without memory.
  compare_reply_contexts.py      # Compare memory/service-knowledge draft variants.
  service_knowledge.py           # Select public reply playbooks from project wiki.
  setup_wechat_debug_app.py      # Create an ad-hoc signed debug WeChat copy.
  bootstrap_local_wechat.py      # Guided setup/export/memory bootstrap.

docs/
  chat-export-runbook.md         # Chat export runbook.
  wechat-new-account-runbook.md  # Repeatable new-account capture flow and pitfalls.
  local-auto-reply-architecture.md
  wechat-digest-layer.md         # Read-only daily digest layer and boundaries.
  security-and-privacy.md
  references.md

.project-wiki/
  index.md                       # Durable project knowledge index.
  wiki-schema.md                 # Lifecycle and privacy schema.
  wiki/architecture/             # Architecture knowledge.
  wiki/reply-playbooks/          # Public service guidance for reply drafts.
  wiki/operations/               # Operating procedures.
  wiki/safety/                   # Safety boundaries.
```

Ignored local outputs include `out/`, `decrypted/`, repo-local `.wx-cli-*` profiles, key/config files, caches, and detailed run logs. The repo keeps `.wx-cli-tools/package.json` and `.wx-cli-tools/package-lock.json` so a new Mac can reinstall the same `wx-cli` toolchain without committing `node_modules`.

## Repo-Local wx-cli Profile

`wx-cli`-backed scripts in this repository can follow a repo-local active profile instead of relying on `~/.wx-cli`.

- Set the active profile by writing a relative or absolute path into `.wx-cli-profile`.
- Override it per shell with `WX_CLI_CONFIG_DIR=/path/to/profile`.
- Profiles are local-only and gitignored. A typical profile directory contains `config.json` and `all_keys.json`.

With an active profile in place, the repository-local scripts automatically:

- run `wx` commands from that profile directory
- restart a mismatched `wx-daemon` when switching between accounts
- let `scripts/export_chat_history.py` reuse the profile's `db_dir` and per-database keys

That means you can export from the active account without manually passing `--wechat-root` or changing directories.

If an account has a usable `wx-cli` view but does not yet have stable per-database keys, you can still export compatible JSONL with:

```bash
python3 scripts/export_wx_cli_history.py \
  --output "$PWD/out/wx-cli-export" \
  --private-only \
  --session-limit 10000
```

The generated `out/wx-cli-export/export/` tree is compatible with `scripts/build_customer_memory.py`.

## Read-Only Daily Digest

For a lightweight daily summary artifact, use the digest layer. It does not call
an LLM, control WeChat, or send messages:

```bash
python3 scripts/build_wechat_digest.py \
  --source live-inbox \
  --live-inbox-root "$HOME/Sync/wechat-live-inbox" \
  --date "$(date +%Y-%m-%d)"
```

On a single Mac, point `--live-inbox-root` at the local live inbox. On a remote
Hermes/Codex Mac, point it at the Syncthing-delivered copy. The reader behavior
is the same in both cases: it reads `events.jsonl`.

It writes `digest.json`, `digest.md`, and `messages.jsonl` under
`out/wechat-digest/`. See [WeChat Digest Layer](docs/wechat-digest-layer.md).

## Multi-Account Workflow

This repository can keep multiple account profiles side by side.

- Each account keeps its own repo-local `wx-cli` profile directory such as `.wx-cli-wxid_.../`.
- `.wx-cli-profile` points at the currently active profile for `wx-cli`-backed scripts.
- `scripts/manage_wechat_accounts.py` can list known profiles, switch the active account, and sync account-specific exports and memory outputs under `out/accounts/<wxid>/`.

Examples:

```bash
python3 scripts/manage_wechat_accounts.py list

python3 scripts/manage_wechat_accounts.py activate wxid_example

python3 scripts/manage_wechat_accounts.py sync --all
```

`sync --all` writes:

- `out/accounts/<wxid>/chat-export/`
- `out/accounts/<wxid>/customer-memory/`
- `out/accounts/accounts_manifest.json`

This keeps each WeChat account isolated while still giving you one place to inspect all exported customer-history assets.

## Quick Start

Install the Python and Node dependencies used by the export path:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm --prefix .wx-cli-tools ci
```

Run the environment doctor:

```bash
python3 scripts/bootstrap_local_wechat.py --doctor-only
```

For a guided local setup, key capture, chat export, and customer-memory build:

```bash
python3 scripts/bootstrap_local_wechat.py --run --with-memory
```

The bootstrap command will:

1. Check dependencies, WeChat.app, local account folders, and Frida permissions.
2. Create `~/.wx-debug/WeChat-debug.app` if needed.
3. Launch the debug WeChat app for key capture.
4. Ask you to log in and open one or two chats while key capture runs.
5. Auto-detect the local `<wxid>/db_storage` folder when possible.
6. Export chats into `out/chat-export/export`.
7. Build customer memory into `out/customer-memory` when `--with-memory` is set.

If multiple local WeChat accounts are found, pass the account explicitly:

```bash
python3 scripts/bootstrap_local_wechat.py \
  --run \
  --with-memory \
  --wechat-root "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/<wxid>/db_storage"
```

## Manual Steps

Create a temporary debug copy of WeChat for key capture:

```bash
mkdir -p "$HOME/.wx-debug"
rm -rf "$HOME/.wx-debug/WeChat-debug.app"
ditto /Applications/WeChat.app "$HOME/.wx-debug/WeChat-debug.app"
xattr -cr "$HOME/.wx-debug/WeChat-debug.app"
codesign --force --deep --sign - "$HOME/.wx-debug/WeChat-debug.app"
```

Capture local database key material:

```bash
python3 scripts/grab_wechat_key.py \
  --app "$HOME/.wx-debug/WeChat-debug.app/Contents/MacOS/WeChat" \
  --wait 240
```

Export chat history:

```bash
python3 scripts/export_chat_history.py \
  --output "$PWD/out/chat-export" \
  --wx-cli-profile .wx-cli-big
```

If `.wx-cli-profile` already points at the account you want, `--wx-cli-profile` can be omitted:

```bash
python3 scripts/export_chat_history.py \
  --output "$PWD/out/chat-export"
```

Build customer memory profiles:

```bash
python3 scripts/build_customer_memory.py \
  --export-root out/chat-export/export \
  --out-root out/customer-memory
```

Build a local contact activity report without raw message text:

```bash
python3 scripts/build_contact_activity_report.py \
  --export-root out/chat-export/export \
  --out-root out/contact-activity
```

Build private wiki pages for common/high-value contacts:

```bash
python3 scripts/build_contact_wiki.py \
  --activity-report out/contact-activity/contact_activity_report.json \
  --memory-root out/customer-memory \
  --out-root out/contact-wiki \
  --max-pages 200 \
  --clean
```

Query and render a customer wiki page:

```bash
python3 scripts/query_customer_memory.py \
  --memory-root out/customer-memory \
  --query "<contact display name>" \
  --limit 3

python3 scripts/render_customer_pages.py \
  --memory-root out/customer-memory \
  --conversation "<contact display name>" \
  --limit 1
```

Generate a reply draft without sending:

```bash
python3 scripts/auto_reply_once.py \
  --source wx-cli \
  --conversation "<contact display name>" \
  --reply-source api \
  --dry-run \
  --duration 180 \
  --interval 3 \
  --context-messages 8 \
  --memory-root out/customer-memory \
  --memory-mode draft-only \
  --memory-use-policy auto \
  --service-knowledge-mode shadow \
  --emit-context-json \
  --max-replies 1
```

By default, live monitoring and reply generation only operate on private chats whose local notification state is non-muted. Group chats, official accounts, muted chats, and chats with unknown notification state are skipped before any model draft or UI send path runs. Development overrides exist for narrow tests only: `--allow-non-private-auto-reply`, `--include-muted`, and `--allow-unknown-notification-state`.

Paste a reviewed draft into the WeChat input box without sending:

```bash
python3 scripts/wechat_ui_send.py \
  --search "<contact display name>" \
  --text "<reviewed draft>" \
  --draft-only
```

For this owner-operated local workflow, automatic draft generation and draft-only paste may be pre-approved when the live worker is running. `--draft-only` stops before pressing Enter; omit it only when a real send is explicitly approved.

## Live Reply Service

The live reply service manages two local processes:

- `scripts/monitor_reply_candidates.py`: reads wx-cli new messages and writes fresh reply-ready candidates under `out/dry-run-replies/monitor/`.
- `scripts/draft_reply_worker.py`: reads fresh candidates, skips likely broadcast/promotional messages, generates a model draft, and pastes the draft into WeChat without sending.

Run a preflight check:

```bash
python3 scripts/wechat_reply_service.py doctor
```

Start the full local chain:

```bash
python3 scripts/wechat_reply_service.py start --check-wx-cli
```

Inspect or stop it:

```bash
python3 scripts/wechat_reply_service.py status
python3 scripts/wechat_reply_service.py stop
```

The service expects the user to grant the local permissions needed for WeChat access and draft-only UI paste. Actual sending is still outside the automatic path.
For UI search, draft paste prefers the contact's public WeChat ID from `out/chat-export/export/contacts.json` (`alias`) before falling back to internal `wxid_*` identifiers or display names.

See [Live Monitor and Draft-Only Worker Runbook](docs/live-monitor-and-draft-worker.md) for prerequisites, verification, and fallback process commands.

## Memory Gate

Customer memory is useful for customer-service context, but harmful when blindly injected into every model draft. The current gate defaults to `auto`:

- Use memory for order, refund, return, after-sales, logistics, address, prior-commitment, and support-action messages.
- Skip memory for broad/general questions such as career advice, learning, trends, or abstract capability questions.
- Allow diagnostic overrides with `--memory-use-policy always` or `--memory-use-policy never`.

This keeps the model focused on the latest message unless historical customer context is likely to help.

## Service Knowledge Playbooks

Customer memory and service knowledge are separate:

- Customer memory says what may have happened with a specific customer.
- Service knowledge says how the assistant should behave for common service situations.

Public playbooks live under `.project-wiki/wiki/reply-playbooks/`. They can guide dry-run drafts for order status, after-sales, logistics, refunds/replacements, address changes, human handoff, and broad general questions.

Service knowledge is disabled by default. To observe playbook matching without passing it to the model:

```bash
python3 scripts/auto_reply_once.py \
  --source wx-cli \
  --conversation "<contact display name>" \
  --reply-source api \
  --dry-run \
  --memory-root out/customer-memory \
  --memory-mode draft-only \
  --service-knowledge-mode shadow
```

To include selected playbooks in draft generation:

```bash
python3 scripts/auto_reply_once.py \
  --source wx-cli \
  --conversation "<contact display name>" \
  --reply-source api \
  --dry-run \
  --memory-root out/customer-memory \
  --memory-mode draft-only \
  --service-knowledge-mode draft-only
```

For offline comparison:

```bash
python3 scripts/compare_reply_contexts.py \
  --memory-root out/customer-memory \
  --conversation "<contact display name>" \
  --message "我上次那个订单现在怎么处理呀"
```

## WeChat Favorites Report

The original favorites visualization path is still available:

```bash
python3 scripts/parse_favorites.py \
  --input "<decrypted favorite.db>" \
  --output out/favorites/data.json

python3 scripts/generate_report.py \
  --input out/favorites/data.json \
  --output out/favorites/report.html
```

The generated HTML report includes statistics, trends, type distribution, source ranking, heatmaps, word clouds, tags, search, filtering, and item detail views.

## Architecture Notes

The auto-reply prototype follows this split:

```text
receive/read path: local WeChat database or wx-cli
decision path: deterministic rules + optional model API
memory path: structured JSON profiles + human wiki pages
send path: local WeChat UI automation
verification path: read back local history
```

It is not a cloud WeChat bot. Personal WeChat does not provide a stable public API for this use case, so the practical route is a local helper process running on a logged-in Mac.

More detail:

- [Chat Export Runbook](docs/chat-export-runbook.md)
- [Local Auto-Reply Architecture](docs/local-auto-reply-architecture.md)
- [Security and Privacy](docs/security-and-privacy.md)
- [References](docs/references.md)
- [Project Wiki](.project-wiki/index.md)

The durable project knowledge layer lives in [.project-wiki/](.project-wiki/index.md). It stores public architecture, operations, safety, and reply playbooks. Private customer memory stays under `out/customer-memory/`.

## Known Limitations

- Operating-system and WeChat version changes may break key capture, database parsing, or UI automation.
- Message direction metadata can be unreliable in older exports; recent live context should be preferred for direction-sensitive decisions.
- Deterministic customer facts can be noisy and should be treated as candidates.
- UI automation can misbehave if WeChat changes layout or focus behavior.
- The project is not a compliance wrapper and does not bypass platform policy obligations.

## License

Released under the [MIT License](LICENSE).
