"""Plain-text and source-code extractor."""
from __future__ import annotations

from pathlib import Path

from .base import ExtractionResult


_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB safety cap; oversize files marked partial


def extract(src: Path, _assets_dir: Path) -> ExtractionResult:
    try:
        size = src.stat().st_size
    except OSError as exc:
        return ExtractionResult(
            status="manual_review",
            extractor="text",
            markdown="",
            error=f"stat failed: {exc}",
        )

    truncated = size > _MAX_BYTES
    try:
        with src.open("r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(_MAX_BYTES)
    except OSError as exc:
        return ExtractionResult(
            status="manual_review",
            extractor="text",
            markdown="",
            error=f"read failed: {exc}",
        )

    suffix = src.suffix.lstrip(".").lower() or "text"
    fenced = f"```{suffix}\n{text}\n```\n"

    notes = []
    if truncated:
        notes.append(f"file truncated to {_MAX_BYTES} bytes (size was {size})")

    return ExtractionResult(
        status="partial" if truncated else "processed",
        extractor="text",
        markdown=fenced,
        notes=notes,
    )
