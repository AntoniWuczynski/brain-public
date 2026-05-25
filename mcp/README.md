# mcp/ — design for a future MCP server

> **Status: design only.** No implementation yet. This document captures
> the contract a future MCP server should expose so agents can build
> against it deterministically.

The Model Context Protocol (MCP) lets agents (Claude, Codex, others) talk
to tools and resources over a uniform interface. The plan is to expose
this vault as one such resource so any MCP-aware agent can search, read,
write, and ingest without per-agent custom integrations.

## Goals

1. **Single source of truth.** The vault filesystem (this repo) is
   authoritative; the MCP server is a thin, stateless adapter on top.
2. **Multi-agent.** Multiple agents may connect concurrently, possibly
   from different devcontainers or machines.
3. **Multi-devcontainer.** The server must run in an isolated
   environment without leaking host paths.
4. **Determinism.** Identical inputs produce identical outputs. All
   mutating tools log to `logs/` and update `metadata/index.jsonl`.

## Tools (planned)

| Name | Kind | Description | I/O |
|---|---|---|---|
| `vault.search` | resource | Full-text search over `archive/processed/` and `knowledge/`. | `query: string, limit?: number` → `[{path, snippet, score}]` |
| `vault.read` | resource | Read any file under the vault. | `path: string` → `{content, mime}` |
| `vault.list` | resource | Directory listing with frontmatter peek. | `path: string` → `[{path, type, title?}]` |
| `vault.write_note` | tool | Create or update a note under `knowledge/` (never `archive/raw/`). Atomic. | `path: string, content: string` → `{path, hash}` |
| `vault.ingest` | tool | Run ingestion against `inbox/` or a single path. Streams progress. | `target: "inbox" \| "raw" \| {path: string}, dry_run?: boolean` → `{processed, partial, failed}` |
| `vault.metadata_query` | resource | Query `metadata/index.jsonl` (by hash, by path, by status). | `{by: "hash" \| "path" \| "status", value: string}` → `[record]` |

`archive/raw/` is exposed read-only at all times. There is no MCP tool
that mutates it.

## Authentication and isolation

- The server runs inside a devcontainer; the host path is mounted
  read-write only for `inbox/`, `archive/processed/`, `knowledge/`,
  `metadata/`, and `logs/`. `archive/raw/` is mounted read-only.
- Multiple devcontainers can share a vault by mounting the same git
  worktree. Coordination is via `metadata/index.jsonl` (atomic
  append-then-rename) and not via in-memory state.
- Auth is out of scope for v1 — assume trusted local clients only.
  v2 may introduce token-based auth if the server is exposed over the
  network.

## Non-goals (deliberately)

- Vector search. The search tool is plain-text first; semantic search
  can be added later as a separate `vault.search_semantic` tool.
- Editing `archive/raw/`. Even with auth, this isn't supported. Re-add
  the source via `inbox/` and let ingestion overwrite the processed
  copy.
- A web UI. Obsidian is the human interface.

## Implementation notes (when this gets built)

- Reference implementation: the official Anthropic MCP SDK
  (Python or TS). Match the language to the rest of the tooling
  (currently Python).
- Reuse `scripts/ingest_lib/` for `vault.ingest` so behaviour is
  identical between CLI and MCP.
- Stream progress events for `vault.ingest`. Long runs must be
  cancellable.
- All mutating handlers must take a write-lock on `metadata/index.jsonl`
  for the duration of their write to keep concurrent agents safe.

When you actually build this, add a sub-folder `mcp/server/` with the
implementation, and link it from this file.
