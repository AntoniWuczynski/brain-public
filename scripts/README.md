# scripts/ — ingestion tooling

The pipeline turns files in `inbox/` (or files already in `archive/raw/`)
into:

- a verbatim copy at `archive/raw/<rel>` (immutable),
- an extracted Markdown artefact at `archive/processed/<rel>.md`,
- an Obsidian-friendly index note at `knowledge/index/<rel>.md`,
- one JSON line in `metadata/index.jsonl`,
- a per-run log under `logs/ingest-<UTC-timestamp>.log`.

Everything is keyed by SHA-256 hash, so re-running ingestion on
unchanged files is a no-op.

## Running

```bash
uv sync                                                   # one-time setup
uv run python scripts/ingest.py --dry-run --inbox         # see the plan
uv run python scripts/ingest.py --inbox                   # do it
uv run python scripts/ingest.py --raw                     # re-process archive
uv run python scripts/ingest.py --path inbox/foo.pdf      # single file
uv run python scripts/ingest.py --backfill-summaries      # fill missing summaries (LLM)
uv run python scripts/ingest.py --rebuild-concepts        # refresh concept index (free)
uv run python scripts/ingest.py --rebuild-search-index    # rebuild semantic index (free)
uv run python scripts/ingest.py --search "query" --top-k 5  # semantic search the vault
```

## Semantic search

Complements the canonical-tag concept layer for cases where the
query phrasing doesn't match any tag exactly. Uses
``BAAI/bge-small-en-v1.5`` locally (no API calls, no cost).

- First run downloads ~100 MB of model weights to ``~/.cache/huggingface/``.
- The index lives at ``metadata/embeddings.{npy,_meta.jsonl}`` and is
  auto-rebuilt after every ``--inbox`` / ``--raw`` run.
- Encoding throughput on an M-series CPU is ~100 chunks/sec on MPS;
  full reindex of 1500 chunks takes ~15 s.
- CLI search has a one-off ~5 s model-load cost per invocation; for
  sub-second queries, embed the same module in a long-running process
  (e.g. the planned MCP server).
- Override the inference device with ``BRAIN_EMBED_DEVICE=cpu|mps|cuda``.

## Autonomous curation (concept notes)

When summarization is enabled, the LLM emits 3-8 canonical **topic tags**
per document alongside the summary. The pipeline then writes one
`knowledge/concepts/<slug>.md` note per distinct topic, listing every
source in the vault that mentions it — that's the cross-source
auto-linking layer.

- Concept notes are auto-refreshed after every ingest run, and after every
  `--backfill-summaries` run. Use `--rebuild-concepts` to refresh manually.
- Topic canonicalisation: the prompt is given the current vault's topic
  list and asked to reuse exact strings when they fit. Topics that
  slugify identically (`Behaviour-Driven Development`,
  `behaviour-driven-development`) collapse into one note.
- Each concept note has an auto-generated block (sources list) and a
  `# Notes` block below the `<!-- AUTO-GENERATED-END -->` marker that is
  **preserved** across re-runs — that's where you write your own thoughts.
- Concept notes whose topics no longer appear in any source are removed
  on the next rebuild — but only if they still carry the
  `AUTO-GENERATED-START` marker (hand-written concept notes are never
  deleted).

## Optional: full PDF extraction with MinerU

Out of the box PDFs are extracted with `pypdf` (text only) and notes are
marked `status: partial`. For full extraction — including figures,
tables and formulas exported as separate image files — install MinerU:

```bash
uv pip install --prerelease=allow "mineru[pipeline]"
```

That's it. The `mineru` package (built on PaddleOCR's PP-Structure for
layout, PaddleOCR for OCR, and UniMerNet for formulas) auto-downloads
its model weights from Hugging Face on first run — about 14 GB into
`~/.cache/huggingface/`. No config file required.

Knobs (env vars, all optional):

- `MINERU_DEVICE_MODE` — `cpu` (default), `mps` (Apple Silicon), or
  `cuda`. The extractor picks `mps` automatically when PyTorch reports
  it available.
- `MINERU_MODEL_SOURCE` — `huggingface` (default) or `modelscope` (use
  Alibaba's mirror if HF is blocked).

MinerU is deliberately *not* in `pyproject.toml`'s lockfile because
some of its transitive deps are pre-releases. The ingestion script
checks whether the `mineru` CLI is on PATH; if it isn't, or if it
errors on a specific PDF, the script transparently falls back to
`pypdf` and records the MinerU error verbatim in the note's
`Processing notes` section.

## Internals

```
scripts/
├── ingest.py                       # argparse CLI
├── README.md                       # this file (you are here)
└── ingest_lib/
    ├── __init__.py                 # public re-exports
    ├── config.py                   # paths
    ├── hashing.py                  # SHA-256
    ├── logging_setup.py            # per-run log
    ├── metadata.py                 # IndexRecord + JSONL I/O
    ├── notes.py                    # processed + index note writers
    ├── pipeline.py                 # plan/run
    └── extractors/
        ├── __init__.py             # extension → extractor registry
        ├── base.py                 # ExtractionResult dataclass
        ├── text.py                 # plain text + code
        ├── docx.py                 # python-docx
        ├── pptx.py                 # python-pptx
        ├── notebook.py             # nbformat
        ├── dataset.py              # CSV/TSV/JSONL schema-only
        └── pdf.py                  # MinerU primary, pypdf fallback
```

## Adding a new file type

1. Create `ingest_lib/extractors/<name>.py` with a function

   ```python
   def extract(src: Path, assets_dir: Path) -> ExtractionResult: ...
   ```

   The function must:
   - never modify or delete `src`,
   - return one of `status="processed" | "partial" | "manual_review"`,
   - put any auxiliary files (extracted images, side-files) under
     `assets_dir`,
   - return them in `ExtractionResult.assets` so they get tracked.

2. Register it under the extensions it handles in
   `ingest_lib/extractors/__init__.py`.

3. Run the smoke test against a real file.

## Idempotency rules

A file is **skipped** when:

- the most recent `metadata/index.jsonl` record for that
  `relative_path` has `status: processed`, **and**
- the source's SHA-256 matches the recorded `source_hash`, **and**
- (cheap pre-check) the file size matches.

A file is **always re-processed** when its hash differs from the latest
recorded hash (replaces the previous note; new metadata line appended).

Files in `archive/raw/` whose content differs from an incoming
`inbox/` file with the same path are *not* overwritten — the run logs a
hash clash and surfaces the file as `manual_review`. This is on
purpose: raw is immutable.

## What this script will not do

- It will not OCR images outside of MinerU's pipeline. Install MinerU
  for OCR.
- It will not generate "summaries" for content it could not extract.
- It will not modify files in `archive/raw/` or `inbox/`.
- It will not delete or rename the user's hand-written notes.
