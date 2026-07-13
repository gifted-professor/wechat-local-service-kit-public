# Security and Privacy

This project works with highly sensitive local WeChat data. The default posture is local-first, dry-run-first, and operator-supervised.

## Data That Must Stay Local

Do not commit, upload, paste, or print:

- WeChat database files.
- Decrypted database copies.
- Chat exports under `out/`.
- Customer memory outputs under `out/customer-memory`.
- Frida key logs.
- `all_keys.json`, `config.json`, or any extracted key material.
- Access tokens, API keys, model provider keys, or wx-cli daemon credentials.
- Screenshots or generated wiki pages that include customer data.

The repository `.gitignore` excludes the default local output and key paths, but that is only a guardrail. Review diffs before committing.

## Reply Safety

The reply workflow is designed to separate drafting from sending.

Recommended default:

```bash
python3 scripts/auto_reply_once.py \
  --source wx-cli \
  --conversation "<contact display name>" \
  --reply-source api \
  --dry-run \
  --memory-root out/customer-memory \
  --memory-mode draft-only \
  --memory-use-policy auto
```

Rules:

- Keep `--dry-run` enabled during development and evaluation.
- For this owner-operated local workflow, automatic model draft generation and draft-only paste into WeChat may be pre-approved when the local service is running.
- Do not send a WeChat message without immediate human confirmation.
- Keep group auto-reply disabled unless a human explicitly approves a narrow test.
- Keep muted-chat monitoring disabled by default; the live gate skips muted chats and chats whose notification state cannot be determined.
- Verify generated drafts before enabling any send path.
- Do not let model output promise refunds, replacements, shipping dates, prices, or fault unless the latest chat explicitly supports it.

## Customer Memory Safety

Customer memory is structured as deterministic, conversation-scoped JSON profiles. It is not a truth database.

Rules:

- Treat extracted facts as candidates.
- Keep evidence pointers where possible.
- Keep secrets, API tokens, passwords, and URL query strings redacted before prompt use.
- Prefer structured runtime context over raw chat excerpts.
- Use the memory gate before passing memory to a model draft.
- Skip memory for broad/general questions.
- PII may be used in local customer memory and reply drafts for this owner-operated account, but secrets and API tokens stay redacted.

## Local WeChat Access

This project does not use an official personal WeChat cloud API. It relies on local WeChat data and local UI automation.

Implications:

- The Mac must be trusted and physically controlled by the operator.
- WeChat must remain logged in locally.
- Frida/key-capture workflows should only be run on accounts and machines you control.
- WeChat version changes can break assumptions.
- Operators remain responsible for platform policy, privacy obligations, and customer consent requirements.

## Public Release Checklist

Before pushing or publishing:

- Run `git status --short`.
- Check ignored local directories are not staged.
- Search for local paths and identifiers:

```bash
rg -n "/Users/|wxid_[A-Za-z0-9_]+|all_keys|key_hex|OPENAI_API_KEY|access_token|api[_-]?key" \
  --hidden \
  --glob '!out/**' \
  --glob '!.wx-cli-tools/**' \
  --glob '!__pycache__/**'
```

- Prefer publishing docs, scripts, and sanitized examples first.

## Incident Response

If sensitive data is accidentally staged:

1. Unstage it immediately.
2. Remove or redact the file.
3. Rotate any exposed key or token.
4. If committed but not pushed, rewrite the local commit before sharing.
5. If pushed to a remote, treat it as exposed and rotate secrets even if the commit is later removed.
