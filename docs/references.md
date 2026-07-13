# References and Inspirations

This project combines local WeChat data access, deterministic customer memory, supervised model drafting, and local-first automation practices.

## Local WeChat Tooling

- [`jackwener/wx-cli`](https://github.com/jackwener/wx-cli): reference and integration target for local WeChat session/history/message access. The repository-local adapter lives in `scripts/wx_cli_adapter.py`.
- Frida: used for local instrumentation during PBKDF2 key-capture workflows.
- SQLCipher concepts: relevant to understanding encrypted WeChat SQLite database preparation.
- PyCryptodome: used by the local database preparation path.

## Visualization

- Apache ECharts: used by the original WeChat Favorites HTML report.
- `echarts-wordcloud`: used for word-cloud visualization in the favorites report.

## Model Drafting

- OpenAI-compatible chat/completions APIs: used by `scripts/reply_api.py` for draft generation.
- The model layer is intentionally replaceable; local data access and local send verification are separate concerns.

## Local-First Customer Memory

The customer memory layer is designed around these principles:

- Build deterministic profiles from local exports.
- Keep JSON as the source of truth.
- Render Markdown wiki pages as a human review layer.
- Pass compact, redacted runtime context to models only when a message-specific gate says memory is useful.

## Design Influences

The project is also influenced by broader local-first automation ideas:

- Keep private operational data local by default.
- Split "thinking/drafting" from "acting/sending".
- Make intermediate state inspectable through files.
- Prefer dry-run and verification loops before enabling side effects.
- Build small deterministic layers before adding broader model behavior.

## Attribution Notes

This document lists conceptual and tooling references. It does not imply endorsement by any upstream project, model provider, or platform.
