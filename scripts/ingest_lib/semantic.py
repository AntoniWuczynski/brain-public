"""Semantic search over ``archive/processed/``.

Canonical-tag matching (the summarizer's ``topics``) is precise but
narrow. Paraphrased or unindexed concepts get missed. This module
complements it: every paragraph chunk gets encoded as a vector, the
query gets encoded, you sort by cosine similarity.

Implementation choices:

- Embedding model is ``BAAI/bge-small-en-v1.5`` from sentence-transformers.
  Around 100 MB of weights, 384-dim output, fast on CPU and MPS.
  Vectors are L2-normalised so cosine similarity is just a dot product.
- Chunks are greedily packed paragraphs targeting ~400 tokens, no overlap.
- Storage is two files: ``metadata/embeddings.npy`` for the vector
  matrix and ``metadata/embeddings_meta.jsonl`` for the row metadata.
  No database.
- Index is rebuilt from scratch on every call. Fine for thousands of
  chunks; add hash-keyed caching if the vault grows much larger.
- Everything is local. Search makes no network calls.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .config import VaultPaths
from .metadata import IndexRecord, latest_records_by_path


_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_TARGET_TOKENS = 400          # rough budget per chunk; English ≈ chars/4
_TARGET_CHARS = _TARGET_TOKENS * 4
_MIN_CHARS = 80               # skip tiny chunks (headings on their own line)


@dataclass(frozen=True)
class Chunk:
    """One chunk to be embedded."""
    source_relative_path: str   # the original raw path
    source_hash: str
    title: str
    chunk_idx: int              # 0-based, within the source
    text: str


@dataclass(frozen=True)
class SearchHit:
    """One search result."""
    score: float                # cosine similarity, in [-1, 1] (usually 0..1)
    source_relative_path: str
    title: str
    chunk_idx: int
    snippet: str                # the actual chunk text


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_HEADER_BLOCK_RE = re.compile(
    r"\A(?:#\s.*?\n+>\s.*?\n(?:>\s.*?\n)*\n+---\n+)",
    re.DOTALL,
)


def _strip_processed_header(text: str) -> str:
    """Drop the title + metadata block that ``write_processed_note``
    prepends, so we embed the extracted content and not our wrapper."""
    m = _HEADER_BLOCK_RE.match(text)
    return text[m.end() :] if m else text


def _split_into_blocks(text: str) -> list[str]:
    """Paragraph-level split on blank lines."""
    blocks = re.split(r"\n\s*\n+", text)
    return [b.strip() for b in blocks if b.strip()]


def chunk_markdown(text: str) -> list[str]:
    """Greedy-pack paragraphs into ~``_TARGET_CHARS`` chunks."""
    blocks = _split_into_blocks(_strip_processed_header(text))
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for block in blocks:
        block_len = len(block)
        if buf and buf_len + block_len + 2 > _TARGET_CHARS:
            chunks.append("\n\n".join(buf))
            buf = [block]
            buf_len = block_len
        elif block_len > _TARGET_CHARS:
            # Block is bigger than the budget. Flush what we've got and
            # emit the oversize block on its own. Don't split a paragraph
            # mid-sentence: things like extracted tables arrive as one
            # giant block and splitting them hurts retrieval.
            if buf:
                chunks.append("\n\n".join(buf))
                buf, buf_len = [], 0
            chunks.append(block)
        else:
            buf.append(block)
            buf_len += block_len + 2
    if buf:
        chunks.append("\n\n".join(buf))
    return [c for c in chunks if len(c) >= _MIN_CHARS]


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------

def _load_embedder():
    # Imported lazily because sentence-transformers pulls in torch and
    # transformers, and we don't want that cost on every CLI invocation.
    from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

    device = os.environ.get("BRAIN_EMBED_DEVICE") or _autodetect_device()
    model = SentenceTransformer(_MODEL_NAME, device=device)
    return model, device


def _autodetect_device() -> str:
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        return "cpu"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _iter_chunks_for_records(
    records: list[IndexRecord], paths: VaultPaths
) -> Iterator[Chunk]:
    for rec in records:
        if not rec.processed_path:
            continue
        path = paths.root / rec.processed_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        title = Path(rec.relative_path).stem.replace("_", " ").replace("-", " ").strip()
        for i, chunk in enumerate(chunk_markdown(text)):
            yield Chunk(
                source_relative_path=rec.relative_path,
                source_hash=rec.source_hash,
                title=title,
                chunk_idx=i,
                text=chunk,
            )


def build_index(
    paths: VaultPaths,
    *,
    logger: logging.Logger,
) -> int:
    """(Re)build the dense vector index from scratch. Returns chunk count."""
    paths.ensure()
    records = [
        r for r in latest_records_by_path(paths.metadata_index_jsonl).values()
        if r.status == "processed" and r.processed_path
    ]
    if not records:
        logger.info("semantic: no processed records to index")
        return 0

    chunks = list(_iter_chunks_for_records(records, paths))
    if not chunks:
        logger.info("semantic: 0 chunks after chunking")
        return 0

    logger.info(
        "semantic: encoding %d chunk(s) from %d source(s)",
        len(chunks), len(records),
    )

    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.warning("semantic: numpy not installed (%s) — skipping", exc)
        return 0

    try:
        model, device = _load_embedder()
    except ImportError as exc:
        logger.warning(
            "semantic: sentence-transformers not installed (%s) — skipping. "
            "Install with: uv pip install sentence-transformers",
            exc,
        )
        return 0
    except Exception as exc:  # noqa: BLE001 — model load can fail many ways
        logger.warning("semantic: model load failed (%r) — skipping", exc)
        return 0

    logger.info("semantic: model %s on %s", _MODEL_NAME, device)

    texts = [c.text for c in chunks]
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=32,
    )
    vectors = np.asarray(vectors, dtype=np.float32)

    vectors_path = paths.metadata / "embeddings.npy"
    meta_path = paths.metadata / "embeddings_meta.jsonl"

    # Atomic-ish writes. np.save appends `.npy` if missing, so give it
    # a path that already ends in `.npy` and rename to drop the `.tmp`.
    tmp_vec = vectors_path.with_name(vectors_path.stem + ".tmp.npy")
    np.save(tmp_vec, vectors)
    os.replace(tmp_vec, vectors_path)

    tmp_meta = meta_path.with_suffix(".jsonl.tmp")
    with tmp_meta.open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(
                json.dumps(
                    {
                        "source_relative_path": c.source_relative_path,
                        "source_hash": c.source_hash,
                        "title": c.title,
                        "chunk_idx": c.chunk_idx,
                        "text": c.text,
                        "model": _MODEL_NAME,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    os.replace(tmp_meta, meta_path)

    logger.info(
        "semantic: wrote %d vectors (dim=%d) to %s",
        vectors.shape[0],
        vectors.shape[1],
        vectors_path.relative_to(paths.root),
    )
    return len(chunks)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(
    paths: VaultPaths,
    query: str,
    *,
    top_k: int = 10,
    logger: logging.Logger | None = None,
) -> list[SearchHit]:
    log = logger or logging.getLogger(__name__)
    vectors_path = paths.metadata / "embeddings.npy"
    meta_path = paths.metadata / "embeddings_meta.jsonl"

    if not vectors_path.exists() or not meta_path.exists():
        log.warning(
            "semantic: no index yet — run 'uv run python scripts/ingest.py "
            "--rebuild-search-index' first"
        )
        return []

    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        log.warning("semantic: numpy not installed (%s)", exc)
        return []

    try:
        model, _ = _load_embedder()
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic: model load failed (%r)", exc)
        return []

    vectors = np.load(vectors_path)
    with meta_path.open("r", encoding="utf-8") as fh:
        meta = [json.loads(ln) for ln in fh if ln.strip()]
    if len(meta) != vectors.shape[0]:
        log.warning(
            "semantic: index size mismatch (vectors=%d, meta=%d) — rebuild required",
            vectors.shape[0],
            len(meta),
        )
        return []

    q_vec = model.encode(
        [query],
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    q_vec = np.asarray(q_vec, dtype=np.float32)

    # Cosine similarity on L2-normalised vectors == dot product.
    scores = (vectors @ q_vec.T).ravel()

    k = min(top_k, scores.shape[0])
    top_idx = np.argpartition(-scores, k - 1)[:k]
    top_idx = top_idx[np.argsort(-scores[top_idx])]

    hits: list[SearchHit] = []
    for i in top_idx:
        m = meta[int(i)]
        hits.append(
            SearchHit(
                score=float(scores[int(i)]),
                source_relative_path=m["source_relative_path"],
                title=m["title"],
                chunk_idx=int(m["chunk_idx"]),
                snippet=m["text"],
            )
        )
    return hits
