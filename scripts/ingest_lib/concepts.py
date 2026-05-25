"""Build cross-source concept notes under ``knowledge/concepts/``.

For every topic emitted by the summarizer, write one concept note that
links every source in the vault that mentions it. Anything the user
hand-writes below the ``AUTO-GENERATED-END`` marker is preserved on
regeneration.

Concept-note layout::

    ---
    title: <Topic Name>
    type: concept
    sources_count: N
    updated: <ISO 8601 UTC>
    aliases: []
    ---

    <!-- AUTO-GENERATED-START -->

    # <Topic Name>

    > _Auto-generated index of every source in the vault that mentions
    > this concept. Edit anything below the AUTO-GENERATED-END marker —
    > those edits survive regeneration._

    ## Sources

    - [[knowledge/index/...]] — _<one-line summary>_
    - ...

    <!-- AUTO-GENERATED-END -->

    # Notes

    _(Your hand-written notes go here; preserved across re-runs.)_
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import VaultPaths
from .metadata import IndexRecord, latest_records_by_path
from .notes import _atomic_write  # private helper, but module-internal

_AUTO_START = "<!-- AUTO-GENERATED-START -->"
_AUTO_END = "<!-- AUTO-GENERATED-END -->"

_NON_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ConceptStats:
    written: int = 0
    skipped: int = 0
    removed: int = 0    # concept notes whose sources are all gone


def slugify(topic: str) -> str:
    """Filename-safe slug for a topic name.

    Two topic strings that slugify to the same value (e.g.
    ``Behaviour-Driven Development`` and ``behaviour-driven-development``)
    collapse to one concept note, so case and punctuation drift doesn't
    fragment the index.
    """
    return _NON_SLUG.sub("-", topic.strip().lower()).strip("-")


def rebuild_concepts(
    paths: VaultPaths,
    *,
    logger: logging.Logger,
) -> ConceptStats:
    """Walk records, group by topic, write/refresh one concept note per topic.

    Returns counts of written/skipped/removed concept notes.
    """
    paths.ensure()
    concepts_dir = paths.knowledge / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)

    latest = latest_records_by_path(paths.metadata_index_jsonl)

    # Group by slug; preserve the most-common display variant per slug.
    groups: dict[str, list[IndexRecord]] = defaultdict(list)
    display_votes: dict[str, Counter[str]] = defaultdict(Counter)
    for rec in latest.values():
        for raw_topic in rec.topics or []:
            slug = slugify(raw_topic)
            if not slug:
                continue
            groups[slug].append(rec)
            display_votes[slug][raw_topic] += 1

    stats = ConceptStats()
    valid_filenames: set[str] = set()
    written_count = 0

    for slug, records in groups.items():
        display = display_votes[slug].most_common(1)[0][0]
        target = concepts_dir / f"{slug}.md"
        valid_filenames.add(target.name)
        try:
            _write_concept_note(
                target=target,
                display_name=display,
                records=records,
                paths=paths,
            )
            written_count += 1
        except OSError as exc:
            logger.warning("concept '%s': failed to write (%s)", display, exc)
            stats = ConceptStats(
                written=stats.written,
                skipped=stats.skipped + 1,
                removed=stats.removed,
            )
            continue
    stats = ConceptStats(written=written_count, skipped=stats.skipped, removed=stats.removed)

    # Drop concept notes whose topics are no longer referenced by any
    # record (e.g. the user removed a source). Only delete files we
    # generated ourselves: hand-written concept notes don't have the
    # AUTO-GENERATED markers and are left alone.
    removed = 0
    for existing in concepts_dir.glob("*.md"):
        if existing.name == ".gitkeep":
            continue
        if existing.name in valid_filenames:
            continue
        text = existing.read_text(encoding="utf-8", errors="replace")
        if _AUTO_START in text and _AUTO_END in text:
            existing.unlink()
            removed += 1
            logger.info("concept: removed orphaned %s", existing.name)
    stats = ConceptStats(written=stats.written, skipped=stats.skipped, removed=removed)
    return stats


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _write_concept_note(
    *,
    target: Path,
    display_name: str,
    records: list[IndexRecord],
    paths: VaultPaths,
) -> None:
    # Preserve user content below AUTO-GENERATED-END if the file exists.
    user_tail = ""
    existing = ""
    if target.exists():
        existing = target.read_text(encoding="utf-8", errors="replace")
        end_pos = existing.find(_AUTO_END)
        if end_pos >= 0:
            user_tail = existing[end_pos + len(_AUTO_END) :].lstrip("\n")
        # If the file exists without our markers, treat the entire body as
        # user content (i.e. the user pre-created this concept note by
        # hand). We then prepend the generated block above their content.
        elif _AUTO_START not in existing:
            user_tail = _existing_body_after_frontmatter(existing)

    if not user_tail.strip():
        user_tail = (
            "# Notes\n\n"
            "_(Your hand-written notes about this concept go here. "
            "Preserved across re-runs.)_\n"
        )

    # Sort sources by relative_path for stable, deterministic output.
    records_sorted = sorted(records, key=lambda r: r.relative_path)
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Frontmatter — preserve user-added keys (e.g. aliases) if they were
    # in the existing file.
    user_fm = _existing_frontmatter(existing) if existing else {}
    frontmatter_lines = [
        f"title: {display_name}",
        "type: concept",
        f"sources_count: {len(records_sorted)}",
        f"updated: '{now}'",
    ]
    aliases = user_fm.get("aliases") or []
    if isinstance(aliases, list):
        aliases_str = "[" + ", ".join(repr(a) for a in aliases) + "]"
    else:
        aliases_str = "[]"
    frontmatter_lines.append(f"aliases: {aliases_str}")
    # Pass through any extra user-added keys we don't manage.
    for k, v in user_fm.items():
        if k in {"title", "type", "sources_count", "updated", "aliases"}:
            continue
        frontmatter_lines.append(f"{k}: {v!r}" if not isinstance(v, list) else
                                 f"{k}: [" + ", ".join(repr(x) for x in v) + "]")

    sources_lines: list[str] = []
    for r in records_sorted:
        wikilink = _index_note_wikilink(r, paths)
        snippet = _short_snippet(r)
        sources_lines.append(f"- {wikilink}{snippet}")

    body = (
        f"{_AUTO_START}\n\n"
        f"# {display_name}\n\n"
        f"> _Auto-generated index of every source in the vault that mentions "
        f"this concept. Edit anything below the **AUTO-GENERATED-END** marker "
        f"— those edits survive regeneration._\n\n"
        f"## Sources ({len(records_sorted)})\n\n"
        + "\n".join(sources_lines)
        + "\n\n"
        f"{_AUTO_END}\n\n"
        f"{user_tail.rstrip()}\n"
    )

    full = "---\n" + "\n".join(frontmatter_lines) + "\n---\n\n" + body
    _atomic_write(target, full)


def _index_note_wikilink(rec: IndexRecord, paths: VaultPaths) -> str:
    """Return ``[[knowledge/index/<rel without .md>]]`` if available."""
    if rec.index_note_path:
        path = rec.index_note_path
        if path.endswith(".md"):
            path = path[:-3]
        return f"[[{path}]]"
    return f"`{rec.relative_path}`"


def _short_snippet(rec: IndexRecord) -> str:
    if rec.summary:
        # Truncate to a single line of reasonable length.
        s = rec.summary.replace("\n", " ").strip()
        if len(s) > 140:
            s = s[:137] + "…"
        return f" — _{s}_"
    return ""


def _existing_body_after_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    return text[end + 5 :]


def _existing_frontmatter(text: str) -> dict[str, object]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    block = text[4:end]
    try:
        import yaml  # type: ignore[import-not-found]

        loaded = yaml.safe_load(block) or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
