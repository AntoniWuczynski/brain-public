"""LLM-based summarizer for processed content.

Calls an LLM to produce a faithful summary, key points, and canonical
topic tags from extracted Markdown. Four providers are supported:

- ``anthropic`` (default) — Claude via the official Anthropic SDK.
- ``openai`` — OpenAI via the official SDK.
- ``gemini`` — Google Gemini via the ``google-genai`` SDK.
- ``local`` — any OpenAI-compatible server (Ollama, LM Studio,
  llama.cpp, vLLM, etc.) reached through the OpenAI SDK with a
  custom ``base_url``.

Provider selection (in order):

1. ``BRAIN_LLM_PROVIDER`` env var, if set to one of the four names.
2. Auto-detect from whichever key is present: ``ANTHROPIC_API_KEY``,
   ``OPENAI_API_KEY``, ``GOOGLE_API_KEY`` (or ``GEMINI_API_KEY``),
   ``BRAIN_LOCAL_URL``.
3. If nothing matches, summarisation is silently disabled and the
   index notes show placeholders.

Model selection:

- ``BRAIN_LLM_MODEL`` env var overrides the default for the chosen
  provider.
- Defaults: ``claude-haiku-4-5`` (anthropic), ``gpt-4o-mini`` (openai),
  ``gemini-2.0-flash`` (gemini), value of ``BRAIN_LOCAL_MODEL``
  (local) or ``llama3.1:8b`` as a fallback.

The ``AGENTS.md`` rule against inventing summaries applies to
extraction *failure*. When extraction succeeded we have real text and
summarising it faithfully is the whole point.

The pipeline caches by ``source_hash``, so re-ingesting unchanged
content doesn't re-call the LLM. Output is deterministic per source
hash from the user's perspective.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, Field


_MAX_TOKENS: Final[int] = 1024
_LONG_INPUT_CHARS: Final[int] = 200_000

_DEFAULT_MODELS: Final[dict[str, str]] = {
    # Picked for price-to-quality on a summarization workload, current
    # as of early 2026. Override per-provider with BRAIN_LLM_MODEL.
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5-mini",
    "gemini": "gemini-2.5-flash",
    "local": "llama3.1:8b",
}

_VALID_PROVIDERS: Final[frozenset[str]] = frozenset(_DEFAULT_MODELS.keys())


_SYSTEM_PROMPT: Final[str] = (
    "You are an editor for a personal knowledge vault. The user gives you "
    "the extracted Markdown of one source document (lecture slides, paper, "
    "notes, etc.). Produce a faithful summary, the most useful key points, "
    "and a list of canonical topic tags, using ONLY the provided text. "
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
    "could plausibly be shared across documents, not document-specific "
    "phrases like 'Lecture 4 examples'. If a list of EXISTING TOPICS is "
    "provided in the user message, prefer reusing exact strings from it "
    "when they fit, to keep the vault canonicalised. Only invent a new "
    "topic when none of the existing ones fit."
)


class DocSummary(BaseModel):
    """Schema for the LLM's structured response."""

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
    notes: list[str]   # processing-notes lines, e.g. "summary: anthropic/claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def _select_provider() -> str | None:
    """Pick a provider from env. Returns None if nothing is configured."""
    explicit = (os.environ.get("BRAIN_LLM_PROVIDER") or "").lower().strip()
    if explicit:
        if explicit in _VALID_PROVIDERS:
            return explicit
        return None  # explicit but invalid: refuse to silently fall back
    # Auto-detect by looking for whichever provider's key is present.
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("BRAIN_LOCAL_URL"):
        return "local"
    return None


def _select_model(provider: str) -> str:
    override = os.environ.get("BRAIN_LLM_MODEL")
    if override:
        return override
    if provider == "local":
        return os.environ.get("BRAIN_LOCAL_MODEL") or _DEFAULT_MODELS["local"]
    return _DEFAULT_MODELS[provider]


def is_enabled() -> bool:
    if os.environ.get("BRAIN_SKIP_SUMMARY") == "1":
        return False
    return _select_provider() is not None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def summarize(
    markdown: str,
    *,
    title: str,
    source_relative_path: str,
    existing_topics: list[str] | None = None,
    logger: logging.Logger | None = None,
) -> SummaryResult | None:
    """Call the configured LLM provider to produce summary + key points + topics.

    ``existing_topics``, if provided, is the list of canonical topic
    names already in the vault. The prompt asks the model to prefer
    reusing those names verbatim when they fit, to keep concept notes
    canonical.

    Returns ``None`` if summarisation is disabled, the input is empty,
    or the provider call fails.
    """
    log = logger or logging.getLogger(__name__)
    if not is_enabled():
        return None

    body = (markdown or "").strip()
    if not body:
        return None

    provider = _select_provider()
    if provider is None:
        return None
    model = _select_model(provider)

    long_input = len(body) > _LONG_INPUT_CHARS
    extra_notes: list[str] = []
    if long_input:
        extra_notes.append(
            f"summary: input is very long ({len(body)} chars); "
            "model summarized the head — consider chunking for full coverage"
        )

    user_block = _build_user_block(
        title=title,
        source_relative_path=source_relative_path,
        body=body,
        existing_topics=existing_topics,
    )

    try:
        parsed, provider_notes = _call_provider(
            provider=provider,
            model=model,
            system=_SYSTEM_PROMPT,
            user=user_block,
            log=log,
        )
    except Exception as exc:  # noqa: BLE001 — never let summarisation crash ingestion
        log.warning("summary: %s/%s unexpected error (%r) — skipping",
                    provider, model, exc)
        return None

    if parsed is None:
        return None

    notes = [f"summary: {provider}/{model}", *provider_notes, *extra_notes]
    return SummaryResult(
        summary=parsed.summary.strip(),
        key_points=[p.strip() for p in parsed.key_points if p and p.strip()],
        topics=[t.strip() for t in (parsed.topics or []) if t and t.strip()],
        notes=notes,
    )


def _build_user_block(
    *,
    title: str,
    source_relative_path: str,
    body: str,
    existing_topics: list[str] | None,
) -> str:
    topic_hint = ""
    if existing_topics:
        # Cap the list so very large vaults don't blow up the user message.
        # Sort alphabetically for a deterministic, cache-friendly prompt.
        capped = sorted(set(existing_topics))[:200]
        topic_hint = (
            "EXISTING TOPICS (prefer reusing exact strings from this list "
            "when they fit; only invent a new topic when none fit):\n"
            + "\n".join(f"- {t}" for t in capped)
            + "\n\n"
        )
    return (
        f"# {title}\n"
        f"_(source: `{source_relative_path}`)_\n\n"
        f"{topic_hint}"
        f"{body}"
    )


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def _call_provider(
    *,
    provider: str,
    model: str,
    system: str,
    user: str,
    log: logging.Logger,
) -> tuple[DocSummary | None, list[str]]:
    """Route to the right provider. Returns (parsed, extra_notes)."""
    if provider == "anthropic":
        return _call_anthropic(model=model, system=system, user=user, log=log)
    if provider == "openai":
        return _call_openai(model=model, system=system, user=user, log=log)
    if provider == "gemini":
        return _call_gemini(model=model, system=system, user=user, log=log)
    if provider == "local":
        return _call_local(model=model, system=system, user=user, log=log)
    log.warning("summary: unknown provider %r", provider)
    return None, []


def _call_anthropic(
    *, model: str, system: str, user: str, log: logging.Logger
) -> tuple[DocSummary | None, list[str]]:
    try:
        import anthropic
    except ImportError as exc:
        log.warning("summary: anthropic SDK missing (%s)", exc)
        return None, []
    client = anthropic.Anthropic()
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": system,
                    # Cacheable when above the model's minimum prefix; below
                    # that it just doesn't cache, no error.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
            output_format=DocSummary,
        )
    except anthropic.APIError as exc:
        log.warning("summary: anthropic API error (%s)", exc)
        return None, []

    parsed = response.parsed_output
    if parsed is None:
        stop = getattr(response, "stop_reason", "unknown")
        log.warning("summary: anthropic parse failed (stop_reason=%s)", stop)
        return None, []

    notes: list[str] = []
    cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
    if cache_read:
        notes.append(f"summary: cache hit ({cache_read} input tokens)")
    return parsed, notes


def _call_openai(
    *, model: str, system: str, user: str, log: logging.Logger
) -> tuple[DocSummary | None, list[str]]:
    return _call_openai_compatible(
        model=model, system=system, user=user, log=log,
        base_url=None, api_key=None, label="openai",
    )


def _call_local(
    *, model: str, system: str, user: str, log: logging.Logger
) -> tuple[DocSummary | None, list[str]]:
    base_url = os.environ.get("BRAIN_LOCAL_URL")
    if not base_url:
        log.warning("summary: local provider requires BRAIN_LOCAL_URL")
        return None, []
    # Most local servers (Ollama, LM Studio, llama.cpp) accept any string
    # as an API key. Allow override for ones that enforce auth.
    api_key = os.environ.get("BRAIN_LOCAL_API_KEY") or "not-needed"
    return _call_openai_compatible(
        model=model, system=system, user=user, log=log,
        base_url=base_url, api_key=api_key, label="local",
    )


def _call_openai_compatible(
    *,
    model: str,
    system: str,
    user: str,
    log: logging.Logger,
    base_url: str | None,
    api_key: str | None,
    label: str,
) -> tuple[DocSummary | None, list[str]]:
    """Shared code path for OpenAI and any OpenAI-compatible local server."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        log.warning("summary: openai SDK missing (%s) — needed for %s provider",
                    exc, label)
        return None, []
    client_kwargs: dict[str, object] = {}
    if base_url:
        client_kwargs["base_url"] = base_url
    if api_key:
        client_kwargs["api_key"] = api_key
    client = OpenAI(**client_kwargs)
    try:
        completion = client.chat.completions.parse(
            model=model,
            max_completion_tokens=_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=DocSummary,
        )
    except Exception as exc:  # OpenAI SDK exception hierarchy varies by version
        hint = ""
        if label == "local":
            hint = (
                " — verify BRAIN_LOCAL_URL is reachable and the server "
                "supports OpenAI's structured-outputs (response_format with "
                "Pydantic). Recent Ollama (≥0.5), LM Studio, and vLLM do."
            )
        log.warning("summary: %s call failed (%r)%s", label, exc, hint)
        return None, []

    if not completion.choices:
        log.warning("summary: %s returned no choices", label)
        return None, []
    msg = completion.choices[0].message
    refusal = getattr(msg, "refusal", None)
    if refusal:
        log.warning("summary: %s refused (%s)", label, refusal)
        return None, []
    parsed = getattr(msg, "parsed", None)
    if parsed is None:
        log.warning("summary: %s returned no parsed output", label)
        return None, []
    return parsed, []


def _call_gemini(
    *, model: str, system: str, user: str, log: logging.Logger
) -> tuple[DocSummary | None, list[str]]:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        log.warning("summary: google-genai SDK missing (%s)", exc)
        return None, []
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.warning("summary: gemini requires GOOGLE_API_KEY or GEMINI_API_KEY")
        return None, []
    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=DocSummary,
                max_output_tokens=_MAX_TOKENS,
            ),
        )
    except Exception as exc:  # genai exception types vary
        log.warning("summary: gemini call failed (%r)", exc)
        return None, []

    parsed = getattr(response, "parsed", None)
    if parsed is None:
        log.warning("summary: gemini returned no parsed output")
        return None, []
    return parsed, []
