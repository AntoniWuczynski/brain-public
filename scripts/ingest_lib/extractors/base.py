"""Common types for extractors."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal


@dataclass(frozen=True)
class ExtractionResult:
    """The output of one extractor invocation.

    - ``status="processed"`` — full content extracted.
    - ``status="partial"``  — some content extracted, some lost.
    - ``status="manual_review"`` — extraction failed; ``markdown`` may be empty.
    """

    status: Literal["processed", "partial", "manual_review"]
    extractor: str
    markdown: str
    assets: list[Path] = field(default_factory=list)
    error: str | None = None
    notes: list[str] = field(default_factory=list)


# An extractor takes the source path plus a directory it may write
# auxiliary assets into (figures, tables...). It must never modify
# ``src`` or read outside of it.
Extractor = Callable[[Path, Path], ExtractionResult]
