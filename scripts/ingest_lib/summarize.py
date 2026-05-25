"""LLM-based summarizer for processed content.

Calls Claude Haiku 4.5 to produce a summary, key points, and canonical
topic tags from extracted Markdown. The ``AGENTS.md`` rule against
inventing summaries applies to extraction *failure*; when extraction
succeeded we have real text and summarising it faithfully is the whole
point.

Opt-in via environment:

- No ``ANTHROPIC_API_KEY`` set: ``summarize`` returns ``None`` and the
  index note shows a placeholder.
- ``BRAIN_SKIP_SUMMARY=1`` set: same.
- API call fails: same, with the error in the run log.

The pipeline caches by ``source_hash``, so re-ingesting unchanged
content doesn't re-call the LLM. From the user's perspective the
output is deterministic per source hash.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, Field


_MODEL: Final[str] = "claude-haiku-4-5"
_MAX_TOKENS: Final[int] = 1024  # plenty for a 4-sentence summary + 7 bullets

# Hard input cap — prevents accidental whole-textbook summarization. Above
# this we still call the API but flag in processing notes that the input
# was very long; we don't silently truncate.
_LONG_INPUT_CHARS: Final[int] = 200_000


_SYSTEM_PROMPT: Final[str] = (
    "You are an editor for a personal knowledge vault. The user gives you "
    "the extracted Markdown of one source document (lecture slides, paper, "
    "notes, etc.). Produce a faithful summary, the most useful key points, "
    "and a list of canonical topic tags — using ONLY the provided text. "
    "Do not invent facts, names, dates, formulae, or sources. If the input "
    "is incomplete, summarize what is there and say nothing about what "
    "isn't.\n\n"
    "Return:\n"
    "- summary: 2-4 sentences capturing the document's purpose and main "
    "claims. Plain prose, no headings, no markdown.\n"
    "- key_points: 3 to 8 bullet-sized takeaways. Each one short (≤ 25 "
    "words), specific, and self-contained. Order roughly by importance. "
    "Do not duplicate the summary verbatim. If the document has fewer "
    "than 3 distinct ideas, return fewer bullets rather than padding.\n"
    "- topics: 3 to 8 short canonical topic tags this document covers. "
    "Each topic is a noun phrase in Title Case (e.g. 'Behaviour-Driven "
    "Development', 'NHS COVID-19 App'). Topics are durable concepts that "
    "could plausibly be shared across documents — not document-specific "
    "phrases like 'Lecture 4 examples'. If a list of EXISTING TOPICS is "
    "provided in the user message, prefer reusing exact strings from it "
    "when they fit, to keep the vault canonicalised. Only invent a new "
    "topic when none of the existing ones fit."
)


class DocSummary(BaseModel):
    """Schema enforced via ``messages.parse()``."""

    summary: str = Field(..., description="2-4 sentence faithful summary.")
    key_points: list[str] = Field(
        ...,
        description="3-8 short bullet-sized takeaways.",
    )
    topics: list[str] = Field(
        ...,
        description="3-8 canonical topic tags (Title Case noun phrases).",
    )


@dataclass(frozen=True)
class SummaryResult:
    summary: str
    key_points: list[str]
    topics: list[str]
    notes: list[str]   # processing-notes lines, e.g. "summary: claude-haiku-4-5"


def is_enabled() -> bool:
    if os.environ.get("BRAIN_SKIP_SUMMARY") == "1":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def summarize(
    markdown: str,
    *,
    title: str,
    source_relative_path: str,
    existing_topics: list[str] | None = None,
    logger: logging.Logger | None = None,
) -> SummaryResult | None:
    """Call Claude to produce a summary + key points + topics.

    ``existing_topics``, if provided, is the list of canonical topic
    names already in the vault. The prompt asks the model to prefer
    reusing those names verbatim when they fit, to keep concept notes
    canonical.

    Returns ``None`` on opt-out or failure.
    """
    log = logger or logging.getLogger(__name__)
    if not is_enabled():
        return None

    body = (markdown or "").strip()
    if not body:
        return None

    long_input = len(body) > _LONG_INPUT_CHARS
    extra_notes: list[str] = []
    if long_input:
        extra_notes.append(
            f"summary: input is very long ({len(body)} chars); "
            "model summarized the head — consider chunking for full coverage"
        )

    try:
        import anthropic
    except ImportError as exc:
        log.warning("summary: anthropic SDK not installed (%s) — skipping", exc)
        return None

    client = anthropic.Anthropic()

    topic_hint = ""
    if existing_topics:
        # Cap the canonical list so we don't blow up the user message on
        # huge vaults. Cheapest sort: alphabetic (deterministic).
        capped = sorted(set(existing_topics))[:200]
        topic_hint = (
            "EXISTING TOPICS (prefer reusing exact strings from this list "
            "when they fit; only invent a new topic when none fit):\n"
            + "\n".join(f"- {t}" for t in capped)
            + "\n\n"
        )

    user_block = (
        f"# {title}\n"
        f"_(source: `{source_relative_path}`)_\n\n"
        f"{topic_hint}"
        f"{body}"
    )

    try:
        # Prompt caching on the system prompt: stable across every call.
        # Below the model's minimum cacheable prefix it silently won't cache,
        # which is fine — the call still succeeds.
        response = client.messages.parse(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_block}],
            output_format=DocSummary,
        )
    except anthropic.APIError as exc:
        log.warning("summary: API error (%s) — skipping", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - never let summarization crash ingestion
        log.warning("summary: unexpected error (%r) — skipping", exc)
        return None

    parsed: DocSummary | None = response.parsed_output
    if parsed is None:
        # parse() failed validation; surface the model's text-block stop reason.
        stop = getattr(response, "stop_reason", "unknown")
        log.warning("summary: parse failed (stop_reason=%s) — skipping", stop)
        return None

    notes = [f"summary: {_MODEL}"]
    cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    if cache_read:
        notes.append(f"summary: cache hit ({cache_read} input tokens)")
    notes.extend(extra_notes)

    return SummaryResult(
        summary=parsed.summary.strip(),
        key_points=[p.strip() for p in parsed.key_points if p and p.strip()],
        topics=[t.strip() for t in (parsed.topics or []) if t and t.strip()],
        notes=notes,
    )
