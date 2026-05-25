# AGENTS.md — rules for any agent operating in this repo

This file applies to **every** agent: Claude Code, Codex, future MCP-driven
agents, or any human-driven script. If you cannot satisfy these rules,
**stop and ask** instead of doing the wrong thing.

---

## Hard rules (do not violate)

1. **`archive/raw/**` is immutable.** Never modify, rename, delete or
   overwrite anything under it. Re-ingestion always writes to
   `archive/processed/` instead.
2. **No destructive operations on raw source files** (those in `inbox/`
   *or* `archive/raw/`). If you think one needs to move, ask the user.
3. **Always link outputs to their source.** Every generated Markdown note
   carries `source_file:` in its frontmatter and a "Links → Source:"
   wikilink in the body. No exceptions.
4. **Update `metadata/index.jsonl` for every processed file.** One JSON
   object per line; never rewrite the whole file.
5. **Log every operation.** Append to `logs/ingest-YYYYMMDDTHHMMSS.log`
   for ingestion runs, or to a similarly named log for other tools. Logs
   are append-only.
6. **Mark uncertainty explicitly.** If extraction is incomplete use
   `status: partial`; if it failed use `status: manual_review` and move
   the file to `archive/failed/`. **Never invent a summary** to cover for
   missing content.
7. **Be deterministic.** The same input must produce the same output.
   Avoid timestamps and random IDs in note bodies; restrict them to
   frontmatter and logs.
8. **Operate statelessly.** Do not assume any prior conversation context
   exists. Everything you need must be readable from files, logs, and
   `metadata/index.jsonl`.
9. **Respect user-edited frontmatter.** Re-ingestion merges frontmatter:
   generated keys (`source_file`, `source_hash`, `created`, `updated`,
   `status`) are refreshed; user-added keys (`topics`, `aliases`, anything
   else) are preserved.

---

## Soft rules (good practice)

- Idempotency: skip files whose `source_hash` already appears in
  `metadata/index.jsonl` with `status: processed`.
- Use atomic writes: write to a temp file, fsync, then rename.
- Never embed binary content in Markdown notes; reference assets by path.
- Keep generated Markdown agent-readable: stable headings, no novelty
  formatting, ASCII where possible.
- Wikilinks (`[[relative/path/without/extension]]`) over absolute paths;
  this keeps the vault portable.

---

## Note format (canonical)

Every generated index note must use this frontmatter:

```yaml
---
title: "<human-readable title>"
type: source_note            # source_note | concept | project | person | org
source_file: archive/raw/<rel/path>
source_hash: <sha256>
created: <ISO 8601 UTC>
updated: <ISO 8601 UTC>
status: processed            # processed | partial | manual_review
topics: []
aliases: []
---
```

Body skeleton:

```markdown
# Summary
# Key points
# Extracted content
# Links
- Source: [[archive/raw/<rel/path>]]
# Processing notes
```

If a section has no content, leave the heading and write `_(empty)_` so
later agents know it was deliberate.

---

## When you are about to do something risky

Stop and confirm with the user **before**:

- Modifying anything in `archive/raw/`.
- Removing or rewriting entries in `metadata/index.jsonl`.
- Force-pushing, rebasing or amending published commits.
- Bulk-renaming notes in `knowledge/`.
- Installing system-level dependencies (vs. Python deps via uv).
- Downloading multi-GB model weights without acknowledging the size.

---

## When something fails

1. Don't paper over it. Mark the file `manual_review`, log the error
   verbatim, and move on.
2. Append a line under `## Manual review` in [`TODO.md`](TODO.md)
   describing the file and the failure mode.
3. Continue processing other files — partial progress is valuable.
