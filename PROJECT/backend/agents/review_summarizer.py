"""LLM review summarizer (PROMPT_4 §2.3).

Golden rule: the LLM summarizes ONLY the real review data it is handed. It
never fabricates reviews, ratings, or scores. If no API key or the call fails,
it returns None and the caller falls back to a deterministic headline.

Accuracy notes (2026-10-15):
- The model is given every sampled review as {rating, date, text} plus Google's
  real per-star distribution, so "most reviewers are positive" is grounded in
  the actual tally rather than the handful of bodies SerpAPI returns.
- Output is structured (summary + positives + concerns + verdict) so the UI can
  show which specific claims came from the reviews.
- Low-star reviews are always included in the payload (they are the ones that
  produce concerns), even when the sample is truncated.

Uses OpenRouter (OpenAI-compatible) or Google Gemini via the `openai` SDK
base-url switch. Pure explainer — no business logic here.
"""
import json
import logging
from dataclasses import dataclass, field

from .. import config
from .llm_agent import (GEMINI_FALLBACK_MODELS, OPENROUTER_FALLBACK_MODELS,
                        is_model_unavailable, mark_provider_cooldown,
                        model_candidates, parse_json_loose, provider_on_cooldown)

logger = logging.getLogger(__name__)


@dataclass
class SummaryResult:
    summary: str
    concerns: list[str] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    verdict: str = ""


def _distribution_line(distribution: dict | None) -> str:
    """Real per-star tally -> a compact, model-readable line (empty when absent)."""
    if not distribution:
        return ""
    try:
        parts = [f"{s}*:{int(distribution.get(s) or 0)}" for s in (5, 4, 3, 2, 1)]
    except (TypeError, ValueError):
        return ""
    total = sum(int(distribution.get(s) or 0) for s in (1, 2, 3, 4, 5))
    if total <= 0:
        return ""
    return f"{', '.join(parts)} (total {total} ratings)"


def _payload(reviews: list[dict], max_chars: int = 9000) -> str:
    """Review dicts -> compact JSON payload, keeping the low-star reviews.

    Truncating from the end used to drop exactly the critical reviews that
    generate the concerns list, so the worst-rated reviews are kept first.
    Individual reviews are clamped too: a few 3.5k-char essays made the model
    run out of output tokens before it finished its JSON.
    """
    items = []
    for r in reviews:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        if len(text) > 1200:
            text = text[:1200] + "…"
        items.append({"rating": r.get("rating") or 0,
                      "date": r.get("date") or "",
                      "text": text})
    critical = [i for i in items if (i["rating"] or 0) <= 3]
    rest = [i for i in items if (i["rating"] or 0) > 3]
    ordered = critical + rest
    blob = ""
    kept = []
    for item in ordered:
        candidate = json.dumps(kept + [item], ensure_ascii=False)
        if len(candidate) > max_chars:
            break
        kept.append(item)
        blob = candidate
    return blob


def summarize(review_texts: list[str], score_pct: int,
              distribution: dict | None = None) -> SummaryResult | None:
    """Back-compat wrapper: plain review texts, no ratings/distribution."""
    return summarize_reviews(
        [{"text": t, "rating": 0, "date": ""} for t in review_texts],
        score_pct, distribution=distribution,
    )


def summarize_reviews(reviews: list[dict], score_pct: int,
                      distribution: dict | None = None) -> SummaryResult | None:
    """Summarize real reviews (with their real ratings) + real distribution."""
    if not reviews:
        return None
    api_key = config.env_str("OPENROUTER_API_KEY") or config.env_str("GEMINI_API_KEY")
    if not api_key:
        logger.info("[llm] no API key — skipping review summary")
        return None
    try:
        return _call_openai_compat(reviews, score_pct, distribution)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[llm] summary failed: %s", exc)
        return None


def _call_openai_compat(reviews, score_pct, distribution) -> SummaryResult:
    """OpenRouter (primary) -> Gemini (fallback), each with model fallbacks.

    Raises only when no provider produced anything (the caller then uses the
    deterministic heuristic). A retired model id no longer kills the path — the
    next candidate model is tried (gemini-2.0-flash 404s as of 2026-10).
    """
    last_exc: Exception | None = None
    or_key = config.env_str("OPENROUTER_API_KEY")
    if or_key and provider_on_cooldown("OpenRouter"):
        or_key = ""  # rejected credentials — skip instead of paying a round trip
    if or_key:
        for model in model_candidates(config.env_str("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
                                      OPENROUTER_FALLBACK_MODELS):
            try:
                return _summarize_with(
                    api_key=or_key,
                    base_url=config.env_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                    model=model, reviews=reviews, score_pct=score_pct,
                    distribution=distribution,
                )
            except Exception as exc:  # noqa: BLE001 — out of credits / down
                last_exc = exc
                logger.warning("[llm] OpenRouter summary (%s) failed: %s", model, exc)
                if not is_model_unavailable(exc):
                    mark_provider_cooldown("OpenRouter")
                    break
    g_key = config.env_str("GEMINI_API_KEY")
    if g_key and provider_on_cooldown("Gemini"):
        g_key = ""
    if g_key:
        for model in model_candidates(config.env_str("GEMINI_MODEL", "gemini-2.5-flash"),
                                      GEMINI_FALLBACK_MODELS):
            try:
                return _summarize_with(
                    api_key=g_key,
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    model=model, reviews=reviews, score_pct=score_pct,
                    distribution=distribution,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("[llm] Gemini summary (%s) failed: %s", model, exc)
                if not is_model_unavailable(exc):
                    mark_provider_cooldown("Gemini")  # bad credentials
                    break
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("no LLM provider available")


def _summarize_with(api_key: str, base_url: str, model: str,
                    reviews: list[dict], score_pct: int,
                    distribution: dict | None) -> SummaryResult:
    import openai

    client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=25)
    blob = _payload(reviews)
    dist_line = _distribution_line(distribution)

    system = (
        "You analyze Google Maps reviews and write a short, evidence-based brief.\n"
        "Rules:\n"
        "1. Use ONLY the reviews and rating tally given. Never invent a review, "
        "rating, price, or fact. Never state a number that is not in the input.\n"
        "2. Weight recurring themes: a point raised by several reviewers matters "
        "more than a one-off. Note it plainly when the sample is small or mixed.\n"
        "3. Be specific and neutral — name what is praised and what is criticized "
        "(food, service, wait time, price, cleanliness, noise, parking, staff).\n"
        "4. If the reviews disagree, say so instead of averaging them into mush.\n"
        "5. Reply with JSON only, shape: "
        '{"summary": "2-3 sentences", "positives": ["up to 3 short points"], '
        '"concerns": ["up to 3 recurring complaints"], '
        '"verdict": "one short sentence a traveller can act on"}'
    )
    user = (
        f"Rating tally (real, all ratings): {dist_line or 'not available'}\n"
        f"Computed reliability: {score_pct}%\n"
        f"Sampled reviews ({len(json.loads(blob)) if blob else 0} of them): {blob}"
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        # Gemini 2.5 spends output tokens on reasoning before the JSON, so a
        # small cap returned a truncated object and the whole analysis silently
        # fell back to the heuristic.
        max_tokens=2600,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        timeout=30,
    )
    choice = resp.choices[0]
    data = parse_json_loose(choice.message.content or "")
    if data is None:
        raise ValueError(f"unparseable JSON from {model} (finish={choice.finish_reason})")

    def _list(key: str, limit: int = 3) -> list[str]:
        raw = data.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        out = []
        for item in raw:
            s = str(item).strip().lstrip("-•").strip()
            if s:
                out.append(s)
        return out[:limit]

    return SummaryResult(
        summary=str(data.get("summary", "")).strip(),
        concerns=_list("concerns"),
        positives=_list("positives"),
        verdict=str(data.get("verdict", "")).strip(),
    )
