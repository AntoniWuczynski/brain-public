"""Jupyter notebook extractor: code + markdown cells, no rich outputs."""
from __future__ import annotations

from pathlib import Path

from .base import ExtractionResult


def extract(src: Path, _assets_dir: Path) -> ExtractionResult:
    try:
        import nbformat  # type: ignore[import-not-found]
    except ImportError as exc:
        return ExtractionResult(
            status="manual_review",
            extractor="notebook",
            markdown="",
            error=f"nbformat missing: {exc}",
        )

    try:
        nb = nbformat.read(str(src), as_version=nbformat.NO_CONVERT)
    except Exception as exc:  # noqa: BLE001
        return ExtractionResult(
            status="manual_review",
            extractor="notebook",
            markdown="",
            error=f"open failed: {exc}",
        )

    parts: list[str] = []
    skipped_outputs = 0
    for i, cell in enumerate(nb.cells, start=1):
        if cell.cell_type == "markdown":
            parts.append(cell.source.strip())
        elif cell.cell_type == "code":
            lang = nb.metadata.get("kernelspec", {}).get("language", "python")
            parts.append(f"```{lang}\n{cell.source}\n```")
            outs = cell.get("outputs", [])
            if outs:
                skipped_outputs += len(outs)
        elif cell.cell_type == "raw":
            parts.append(f"```\n{cell.source}\n```")
        else:
            parts.append(f"_(unknown cell type: `{cell.cell_type}` at index {i})_")
        parts.append("")

    notes: list[str] = []
    if skipped_outputs:
        notes.append(
            f"omitted {skipped_outputs} cell output(s) "
            "(images/text/streams) — see source notebook for them"
        )
    return ExtractionResult(
        status="processed",
        extractor="notebook",
        markdown="\n".join(parts),
        notes=notes,
    )
