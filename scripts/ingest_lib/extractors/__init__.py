"""Extractor registry: file-extension → callable.

Each extractor returns an :class:`ExtractionResult`. Extractors must be
deterministic and must never mutate ``src``.
"""
from __future__ import annotations

from pathlib import Path

from .base import ExtractionResult, Extractor
from . import dataset as _dataset_mod
from . import docx as _docx_mod
from . import notebook as _notebook_mod
from . import pdf as _pdf_mod
from . import pptx as _pptx_mod
from . import text as _text_mod


# Map of *lowercase* extension (with the leading dot) to the extractor.
_REGISTRY: dict[str, Extractor] = {
    # PDFs use the pluggable PDF extractor (MinerU primary, pypdf fallback).
    ".pdf": _pdf_mod.extract,
    # DOCX (modern Word). Old .doc not supported.
    ".docx": _docx_mod.extract,
    # PPTX (modern PowerPoint). Old .ppt not supported.
    ".pptx": _pptx_mod.extract,
    # Jupyter notebooks.
    ".ipynb": _notebook_mod.extract,
    # Datasets (schema-only extraction, never dump rows).
    ".csv": _dataset_mod.extract,
    ".tsv": _dataset_mod.extract,
    ".jsonl": _dataset_mod.extract,
    ".parquet": _dataset_mod.extract_parquet_stub,
    # Plain text and code: read directly. Extend as needed.
    ".txt": _text_mod.extract,
    ".md": _text_mod.extract,
    ".markdown": _text_mod.extract,
    ".rst": _text_mod.extract,
    ".py": _text_mod.extract,
    ".js": _text_mod.extract,
    ".ts": _text_mod.extract,
    ".tsx": _text_mod.extract,
    ".jsx": _text_mod.extract,
    ".go": _text_mod.extract,
    ".rs": _text_mod.extract,
    ".java": _text_mod.extract,
    ".kt": _text_mod.extract,
    ".rb": _text_mod.extract,
    ".sh": _text_mod.extract,
    ".bash": _text_mod.extract,
    ".zsh": _text_mod.extract,
    ".sql": _text_mod.extract,
    ".html": _text_mod.extract,
    ".css": _text_mod.extract,
    ".yaml": _text_mod.extract,
    ".yml": _text_mod.extract,
    ".toml": _text_mod.extract,
    ".json": _text_mod.extract,
    ".log": _text_mod.extract,
}


def dispatch_extractor(path: Path) -> Extractor | None:
    return _REGISTRY.get(path.suffix.lower())


def registered_extensions() -> list[str]:
    return sorted(_REGISTRY)


__all__ = [
    "ExtractionResult",
    "Extractor",
    "dispatch_extractor",
    "registered_extensions",
]
