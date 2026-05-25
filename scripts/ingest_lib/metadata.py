"""Read/write ``metadata/index.jsonl``. Append-mostly with atomic writes."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Literal

Status = Literal["processed", "partial", "manual_review", "skipped"]


@dataclass(frozen=True)
class IndexRecord:
    """One line in ``metadata/index.jsonl``.

    ``relative_path`` is repo-root-relative. ``source_hash`` keys the
    record against re-ingestion: identical hash means same content.
    """

    relative_path: str
    source_hash: str
    size_bytes: int
    extension: str
    extractor: str
    status: Status
    raw_path: str
    processed_path: str | None
    index_note_path: str | None
    assets: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    # LLM-generated, faithful to the extracted content. Empty string / list
    # when no summary was produced (no API key, opted out, or call failed).
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    # Canonical topic tags this document covers. Used by the concept-note
    # generator to build cross-source links under ``knowledge/concepts/``.
    topics: list[str] = field(default_factory=list)

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def iter_records(jsonl_path: Path) -> Iterator[IndexRecord]:
    if not jsonl_path.exists():
        return
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines but don't lose the others.
                continue
            yield IndexRecord(**data)


def latest_records_by_path(jsonl_path: Path) -> dict[str, IndexRecord]:
    """Return the *latest* record per relative_path. Later lines win."""
    out: dict[str, IndexRecord] = {}
    for rec in iter_records(jsonl_path):
        out[rec.relative_path] = rec
    return out


def append_record(jsonl_path: Path, record: IndexRecord) -> None:
    """Append a record to the JSONL. Creates the file if missing.

    Uses a tempfile + os.replace dance only when the file doesn't yet
    exist; subsequent writes use a plain append-with-fsync, which is
    atomic at the line level on POSIX as long as the line is < PIPE_BUF
    (4 KiB), and our records are well under that.
    """
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    line = record.to_json_line() + "\n"
    if not jsonl_path.exists():
        # Atomic create: write to temp and rename.
        fd, tmp = tempfile.mkstemp(prefix=".index-", suffix=".jsonl", dir=str(jsonl_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, jsonl_path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
    else:
        with jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
