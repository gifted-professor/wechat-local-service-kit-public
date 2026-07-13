# Public Release Guide

This repository contains tooling for local WeChat data export and supervised
reply drafting. A public release must be built from a sanitized snapshot, not
from a private working tree.

## Release Boundary

Safe public materials:

- Source code that does not embed real account IDs, local paths, keys, exports,
  or customer data.
- Documentation that uses placeholders such as `<wxid>`, `<user>`, and
  `/path/to/wechat-local-service-kit`.
- Dependency manifests such as `requirements.txt` and
  `.wx-cli-tools/package.json`.
- `LICENSE` for the public source release.

Never publish:

- `.wx-cli-*`, `.wx-cli-profile`, `all_keys.json`, `config.json`, or wx-cli
  cache databases.
- `out/`, `decrypted/`, local reports, customer memory, generated wikis, or
  chat exports.
- `.supervision/`, handoff files, local run logs, screenshots, or machine
  operation notes.
- Real `wxid_*` account IDs, `/Users/<real-user>` paths, API keys, access
  tokens, or extracted encryption keys.

## Build A Public Snapshot

Run:

```bash
python3 scripts/make_public_snapshot.py --clean
```

The snapshot is written to:

```text
out/public-release/wechat-local-service-kit-public/
```

The build runs `scripts/public_release_check.py` against the snapshot. Treat any
blocker as a stop condition.

## Check The Current Working Tree

To inspect the private working tree before choosing what belongs in public:

```bash
python3 scripts/public_release_check.py --root . --include-private-local --json
```

This may report ignored local key/profile/cache files. That is expected in the
private tree, but those files must never appear in the public snapshot.

## Publish Rule

Do not make the private GitHub repository public directly. Publish only the
sanitized snapshot or a branch created from that snapshot after reviewing the
check report.
