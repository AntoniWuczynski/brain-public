"""DOCX extractor (paragraphs + tables, in document order)."""
from __future__ import annotations

from pathlib import Path

from .base import ExtractionResult


def extract(src: Path, _assets_dir: Path) -> ExtractionResult:
    try:
        from docx import Document  # type: ignore[import-not-found]
        from docx.oxml.ns import qn  # type: ignore[import-not-found]
    except ImportError as exc:
        return ExtractionResult(
            status="manual_review",
            extractor="docx",
            markdown="",
            error=f"python-docx missing: {exc}",
        )

    try:
        doc = Document(str(src))
    except Exception as exc:  # noqa: BLE001
        return ExtractionResult(
            status="manual_review",
            extractor="docx",
            markdown="",
            error=f"open failed: {exc}",
        )

    parts: list[str] = []
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            # Paragraph: collect runs.
            text = "".join(t.text or "" for t in child.iter(qn("w:t")))
            if text.strip():
                parts.append(text)
            else:
                parts.append("")
        elif tag == qn("w:tbl"):
            parts.append(_table_to_markdown(child))
    markdown = "\n\n".join(p for p in parts if p is not None) + "\n"

    return ExtractionResult(
        status="processed",
        extractor="docx",
        markdown=markdown,
    )


def _table_to_markdown(tbl_elem) -> str:
    from docx.oxml.ns import qn  # type: ignore[import-not-found]

    rows: list[list[str]] = []
    for row in tbl_elem.iter(qn("w:tr")):
        cells = [
            "".join(t.text or "" for t in cell.iter(qn("w:t"))).strip()
            for cell in row.iter(qn("w:tc"))
        ]
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = rows[0]
    sep = ["---"] * width
    body = rows[1:] if len(rows) > 1 else []
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)
