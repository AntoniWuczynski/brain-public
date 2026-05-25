#!/usr/bin/env python3
"""brain ingestion CLI.

Examples:
    uv run python scripts/ingest.py --inbox
    uv run python scripts/ingest.py --inbox --dry-run
    uv run python scripts/ingest.py --raw
    uv run python scripts/ingest.py --path inbox/university/COMP0101/01_foundations.pdf

The heavy lifting lives in ``scripts/ingest_lib/``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make ``ingest_lib`` importable when running this file directly
# (i.e. without ``uv run`` having installed the package yet).
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env at the repo root (if present) before anything else, so
# ANTHROPIC_API_KEY and friends are visible to all submodules.
try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
    _repo_root = Path(__file__).resolve().parent.parent
    load_dotenv(_repo_root / ".env", override=False)
except ImportError:
    pass  # dotenv is a soft-dep; env vars from the shell still work

from ingest_lib import (  # noqa: E402
    backfill_summaries,
    build_search_index,
    default_paths,
    plan_ingest,
    rebuild_concepts,
    run_ingest,
    semantic_search,
)
from ingest_lib.logging_setup import configure_run_logger  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain-ingest",
        description="Process inbox/archive into archive/processed + knowledge/index.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--inbox",
        action="store_true",
        help="Process every supported file under inbox/ (default).",
    )
    target.add_argument(
        "--raw",
        action="store_true",
        help="Re-process files already in archive/raw/ (no copy step).",
    )
    target.add_argument(
        "--path",
        type=Path,
        help="Process a single file (relative to repo root or absolute).",
    )
    target.add_argument(
        "--backfill-summaries",
        action="store_true",
        help=(
            "Add Summary + Key points + Topics to existing 'processed' "
            "records that lack them, without re-extracting. Reads processed "
            "Markdown from disk and only calls the LLM. Auto-rebuilds "
            "concept notes after backfilling. Requires ANTHROPIC_API_KEY."
        ),
    )
    target.add_argument(
        "--rebuild-concepts",
        action="store_true",
        help=(
            "Rebuild knowledge/concepts/<topic>.md from current metadata. "
            "Walks topic tags across all records and writes one auto-"
            "generated note per topic linking every source. Free; no LLM "
            "calls. User-edited content below the AUTO-GENERATED-END marker "
            "is preserved."
        ),
    )
    target.add_argument(
        "--rebuild-search-index",
        action="store_true",
        help=(
            "(Re)build the semantic search index over archive/processed/. "
            "Encodes every paragraph chunk with BAAI/bge-small-en-v1.5 "
            "(local model, ~100 MB on first use) and writes to "
            "metadata/embeddings.{npy,_meta.jsonl}. Auto-runs after each "
            "ingest; use this to rebuild standalone. No LLM calls; free."
        ),
    )
    target.add_argument(
        "--search",
        metavar="QUERY",
        help=(
            "Search the vault for QUERY using the semantic index. Prints "
            "the top matches with citation paths and snippets. Index must "
            "exist (see --rebuild-search-index)."
        ),
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="With --search: number of results to return (default: 10).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan but do not write anything. Still creates a log file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    paths = default_paths()
    paths.ensure()

    logger, log_path = configure_run_logger(paths.logs, dry_run=args.dry_run)
    logger.info("brain-ingest start (dry_run=%s)", args.dry_run)
    logger.info("repo root: %s", paths.root)
    logger.info("log file: %s", log_path.relative_to(paths.root))

    if args.backfill_summaries:
        if args.dry_run:
            logger.error("--dry-run is not supported with --backfill-summaries")
            return 2
        stats = backfill_summaries(paths, logger=logger)
        print()
        print("Backfill summary:")
        print(f"  summarized   : {stats.processed}")
        print(f"  skipped      : {stats.skipped}")
        print(f"  log          : {log_path.relative_to(paths.root)}")
        return 0

    if args.rebuild_concepts:
        if args.dry_run:
            logger.error("--dry-run is not supported with --rebuild-concepts")
            return 2
        cs = rebuild_concepts(paths, logger=logger)
        print()
        print("Concept rebuild:")
        print(f"  written      : {cs.written}")
        print(f"  skipped      : {cs.skipped}")
        print(f"  removed      : {cs.removed}")
        print(f"  log          : {log_path.relative_to(paths.root)}")
        return 0

    if args.rebuild_search_index:
        if args.dry_run:
            logger.error("--dry-run is not supported with --rebuild-search-index")
            return 2
        n = build_search_index(paths, logger=logger)
        print()
        print("Semantic index rebuild:")
        print(f"  chunks       : {n}")
        print(f"  log          : {log_path.relative_to(paths.root)}")
        return 0

    if args.search:
        hits = semantic_search(paths, args.search, top_k=args.top_k, logger=logger)
        if not hits:
            print("No results. Has the index been built?")
            return 0
        print()
        print(f"Top {len(hits)} for: {args.search!r}")
        print("=" * 78)
        for i, h in enumerate(hits, start=1):
            preview = h.snippet.replace("\n", " ")
            if len(preview) > 220:
                preview = preview[:217] + "…"
            print(
                f"\n[{i}] score={h.score:.3f}  {h.title}  "
                f"(chunk {h.chunk_idx})"
            )
            print(f"    source: {h.source_relative_path}")
            print(f"    {preview}")
        return 0

    if args.inbox:
        sources = [paths.inbox]
        from_archive = False
    elif args.raw:
        sources = [paths.archive_raw]
        from_archive = True
    else:
        if args.path is None:
            logger.error("no target specified")
            return 2
        p = args.path if args.path.is_absolute() else (paths.root / args.path).resolve()
        sources = [p]
        # If the user pointed at a file inside archive/raw, treat as --raw mode.
        from_archive = paths.archive_raw in p.parents

    plan = plan_ingest(paths, sources=sources, from_archive=from_archive, logger=logger)
    logger.info(
        "plan: %d to process, %d already processed (skip), %d unsupported (skip)",
        len(plan.items),
        len(plan.skipped_already_processed),
        len(plan.skipped_unsupported),
    )

    if not plan.items and not plan.skipped_already_processed and not plan.skipped_unsupported:
        logger.info("nothing to do")
        print(f"No supported files found under {sources[0]}.")
        return 0

    stats = run_ingest(paths, plan, dry_run=args.dry_run, logger=logger)
    logger.info(
        "done: processed=%d partial=%d manual_review=%d skipped=%d",
        stats.processed,
        stats.partial,
        stats.manual_review,
        stats.skipped,
    )

    print()
    print(f"Run summary ({'dry-run' if args.dry_run else 'real'}):")
    print(f"  processed     : {stats.processed}")
    print(f"  partial       : {stats.partial}")
    print(f"  manual_review : {stats.manual_review}")
    print(f"  skipped       : {stats.skipped}")
    print(f"  log           : {log_path.relative_to(paths.root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
