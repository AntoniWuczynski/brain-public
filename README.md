# brain

A personal knowledge vault you can drop files into and query like an extension of your own memory. PDFs, slides, notes, code, datasets — anything you'd want to remember later. Optimised for use by humans (Obsidian), AI agents (Claude, Codex, future MCP clients), and your future self.

## What it gives you

When you drop a file in `inbox/`, the pipeline:

1. Stores a verbatim copy in `archive/raw/` and tracks it by SHA-256.
2. Extracts the content. PDFs go through [MinerU](https://github.com/opendatalab/MinerU) for text, figures, tables, and formulas. Other formats use lighter extractors.
3. Writes a Markdown artefact to `archive/processed/`, with extracted images saved alongside.
4. Generates an Obsidian-friendly index note at `knowledge/index/`, transcluding the processed content.
5. Calls an LLM (optional) to produce a faithful summary, key points, and canonical topic tags.
6. Auto-builds concept notes at `knowledge/concepts/` — one per topic, listing every source that mentions it. This is the cross-source linking layer.
7. Updates a semantic search index (local embeddings, no API cost) so you can query the whole vault by meaning, not just by tag.

The result is a vault that grows by drop-and-run, organises itself, and stays queryable from the terminal, from Obsidian, and from any agent you point at it.

## How it's structured

```
brain/
├── archive/
│   ├── raw/             ← immutable copies of every source file
│   ├── processed/       ← extracted Markdown + extracted images
│   └── failed/          ← files that couldn't be extracted; need manual review
├── inbox/               ← drop new files here
├── knowledge/
│   ├── index/           ← one Obsidian note per source, with summary + topics
│   ├── concepts/        ← auto-generated cross-source topic notes
│   ├── projects/        notes/        research/
│   └── people/  organisations/ university/   ← hand-written notes go here
├── metadata/
│   ├── index.jsonl      ← machine record of every processed file
│   └── embeddings.npy   ← semantic search index
├── logs/                ← one log per ingest run
├── scripts/
│   ├── ingest.py        ← the CLI
│   └── ingest_lib/      ← extractors, summarizer, concept builder, search
├── mcp/                 ← (design only) remote MCP server spec
└── pyproject.toml
```

Four layers, four concerns: **archive** is ground truth, **processed** is regenerable extraction, **knowledge** is the curated face, **metadata** is machine state. Agents are expected to read everywhere and write only under `knowledge/`.

## Requirements

- **Python 3.12** (PaddlePaddle, which MinerU uses, doesn't yet ship wheels for 3.13+)
- **macOS or Linux**. Apple Silicon and CUDA both work for MinerU; CPU works but is slow on long PDFs.
- **[uv](https://docs.astral.sh/uv/)** for environment management.
- **Obsidian** if you want the human-facing UI. The vault is plain Markdown, so any editor works, but Obsidian is what the wikilink and transclusion conventions assume.
- **[Optional] An Anthropic API key** for summaries, key points, and topic tagging. Without it, ingestion still works but the index notes show placeholders instead of summaries.

## Setup

```bash
git clone <your-fork-url> brain
cd brain
uv sync
```

That's enough to ingest text-only files (Markdown, code, notebooks, CSVs). For full PDF extraction with figures and tables:

```bash
uv pip install --prerelease=allow "mineru[pipeline]"
```

`uv sync` will prune MinerU on every subsequent run because it isn't in the lockfile (its transitive deps include pre-releases that break `uv`'s resolver). Re-run the line above after each sync, or wrap both in a `scripts/setup.sh` of your own.

To enable summaries and topic tagging, copy `.env.example` to `.env` and add your Anthropic key:

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY
```

## Daily use

### Add files

```bash
cp ~/Downloads/lecture.pdf inbox/university/COMP0123/
cp ~/Notes/meeting.md inbox/projects/acme/
uv run python scripts/ingest.py --inbox
```

Sub-directory structure under `inbox/` is preserved end-to-end. The same file dropped twice is skipped (SHA-256 idempotency).

### Search the vault

```bash
uv run python scripts/ingest.py --search "what happens when a packet is dropped" --top-k 5
```

Returns the most semantically similar passages across every processed source, with citation paths. The first call after a fresh clone downloads a ~100 MB embedding model to `~/.cache/huggingface/`.

### Browse in Obsidian

Open the repo root as an Obsidian vault. `knowledge/index/Home.md` is your entry point. Every concept under `knowledge/concepts/` is a pre-built index of every source that touches that concept. Click into any source's index note and the full extracted content (figures and all) appears inline via transclusion.

### Useful commands

| Command | What it does |
|---|---|
| `--inbox` | Process every supported file under `inbox/` |
| `--raw` | Re-process files already in `archive/raw/` (no copy step) |
| `--path <file>` | Process a single file |
| `--dry-run --inbox` | Show the plan, don't write anything |
| `--backfill-summaries` | Add Summary + Key points + Topics to existing records that lack them |
| `--rebuild-concepts` | Regenerate concept notes from current metadata (free, no LLM) |
| `--rebuild-search-index` | Re-encode every chunk and overwrite the search index |
| `--search "query" --top-k N` | Semantic search the vault |

## Configuration

Everything is via environment variables in `.env`:

| Variable | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Required for summaries / topic tags |
| `BRAIN_SKIP_SUMMARY=1` | Skip summarization even with a key set |
| `MINERU_DEVICE_MODE` | `cpu` / `mps` / `cuda` for MinerU inference |
| `BRAIN_EMBED_DEVICE` | Same for the semantic-search embedder |
| `MINERU_MODEL_SOURCE` | `huggingface` (default) or `modelscope` |

## How agents use it

Two patterns work well today:

**As context for a project.** Drop a small `CLAUDE.md` in your project repo that says `reference vault at ~/brain/`. Then Claude Code sessions in that project will pull from the vault on demand — concepts, summaries, your own notes.

**As an oracle in this repo.** Ask Claude Code from `~/brain/` itself: *"What does my vault say about X?"*, *"Quiz me on COMP0023"*, *"Find sources that connect Y and Z"*. The agent has read access to everything; it can grep, read processed Markdown, follow wikilinks, and synthesise across sources with citations.

Both rely on you running the agent locally. For remote agents (claude.ai, third-party MCP clients), see `mcp/README.md` — there's a design for a self-hosted MCP server but no implementation yet.

## Extending

### Add a new file type

Each extractor is a function with this signature:

```python
def extract(src: Path, assets_dir: Path) -> ExtractionResult: ...
```

Put it under `scripts/ingest_lib/extractors/<name>.py`, register it against the extensions it handles in `scripts/ingest_lib/extractors/__init__.py`, and the rest of the pipeline picks it up. The existing `text.py`, `docx.py`, `pptx.py`, `notebook.py`, and `dataset.py` are good references.

### Swap the embedding model

Edit `_MODEL_NAME` in `scripts/ingest_lib/semantic.py` to any `sentence-transformers`-compatible model. The default `BAAI/bge-small-en-v1.5` is a good balance of size and quality for English; `bge-m3` or `bge-large-en-v1.5` are larger and slower but better for retrieval.

### Customise summary style

The system prompt for summarization lives in `scripts/ingest_lib/summarize.py`. The schema is enforced via Pydantic, so changing the prompt won't break parsing as long as the returned JSON still matches `DocSummary`.

## Honest limitations

- **PDF extraction quality depends on MinerU.** Scanned PDFs without text layers need OCR, which MinerU handles but is slow. Mathematical typesetting is hit-or-miss.
- **Summaries cost money.** Cheap (about $0.005 per typical lecture slide deck at Haiku 4.5 prices), but not free. Disable with `BRAIN_SKIP_SUMMARY=1` or by not setting `ANTHROPIC_API_KEY`.
- **Concept canonicalization isn't perfect.** The summarizer is asked to reuse existing topic names but occasionally drifts. Slugification catches case and punctuation variants; semantic drift across paraphrases doesn't.
- **No write-side MCP yet.** Agents on other machines can't add to the vault without a deployed MCP server (see `mcp/README.md` for the design).
- **Repo size grows with `archive/raw/`.** PDFs aren't diff-friendly, so git history bloats. Long-term, you'll want git-lfs or a separate object store for the raw archive.

## License

MIT. See `LICENSE`.

## Credits

Built on:
- [MinerU](https://github.com/opendatalab/MinerU) for PDF extraction
- [sentence-transformers](https://www.sbert.net) and [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) for semantic search
- [Anthropic Claude](https://docs.anthropic.com) for summaries and topic tags
- [Obsidian](https://obsidian.md) for the human interface
- [uv](https://docs.astral.sh/uv/) for environment management
