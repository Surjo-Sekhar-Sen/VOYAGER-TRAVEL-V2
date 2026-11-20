"""LLM agent (PROMPT_5 §2.3).

Provider chain: OpenRouter (primary) -> fallback model -> Gemini. JSON mode on,
per-call timeout, cross-model fallback on failure. Allowed outputs are ONLY
summaries / explanations / structured synthesis. Numeric data always comes from
tools — never from the LLM. If every provider fails -> None (caller supplies a
deterministic fallback). The agent NEVER guesses fares, timings, or scores.
"""
import json
import logging
import time
from dataclasses import dataclass, field

from .. import config

logger = logging.getLogger(__name__)

# A provider whose credentials are rejected (401/unauthorized) is skipped for a
# while instead of costing every request a round trip. Recoverable errors keep
# the provider enabled. Observed 2026-10-15: an expired OpenRouter key added ~1s
# to every summary because it was tried first on each call.
_PROVIDER_COOLDOWN: dict[str, float] = {}
_PROVIDER_COOLDOWN_S = 600.0


def provider_on_cooldown(name: str) -> bool:
    return time.time() < _PROVIDER_COOLDOWN.get(name, 0.0)


def mark_provider_cooldown(name: str) -> None:
    _PROVIDER_COOLDOWN[name] = time.time() + _PROVIDER_COOLDOWN_S
    logger.warning("[llm] %s credentials rejected — pausing it for %d min",
                   name, int(_PROVIDER_COOLDOWN_S // 60))


# Model fallbacks. Google retires model ids (gemini-2.0-flash now 404s), and
# a retired id used to kill the whole explanation path silently. On a
# "model not available" error the next candidate is tried before giving up.
GEMINI_FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash", "gemini-3.8-flash", "gemini-flash-latest",
    "gemini-flash-lite-latest", "gemini-3.6-flash",
)
OPENROUTER_FALLBACK_MODELS: tuple[str, ...] = (
    "openai/gpt-4o-mini", "google/gemini-flash-1.5", "meta-llama/llama-3.1-8b-instruct",
)


def model_candidates(configured: str, fallbacks: tuple[str, ...]) -> list[str]:
    """Configured model first, then the fallbacks (deduped, order kept)."""
    out: list[str] = []
    for m in (configured, *fallbacks):
        m = (m or "").strip()
        if m and m not in out:
            out.append(m)
    return out


def parse_json_loose(content: str) -> dict | None:
    """Parse an LLM JSON reply that may be fenced, chatty, or truncated.

    Reasoning-capable Gemini models spend output tokens on thinking, so with a
    tight max_tokens the reply arrives cut off (observed: 5 chars, "{\n  \"").
    Try strict JSON, then the first balanced {...} block, then a brace-trim.
    """
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.strip().rstrip("`").strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    if start != -1:
        candidates.append(text[start:])          # truncated mid-object
    for cand in candidates:
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            continue
    return None


def is_model_unavailable(exc: Exception) -> bool:
    """True when another model may still succeed for this request.

    Covers a retired/unknown model id (404) AND transient provider trouble
    (503 high demand, 429 quota on one model, timeout) — a different model often
    serves fine (observed 2026-10-15: gemini-flash-latest 503 while
    gemini-2.5-flash answered in ~2s).
    """
    s = str(exc).lower()
    if any(k in s for k in ("invalid api key", "api key not valid", "user not found",
                            "incorrect api key", "unauthorized")):
        return False  # credentials are wrong — another model cannot help
    return any(k in s for k in (
        "no longer available", "not found", "does not exist", "is not supported",
        "unsupported model", "unknown model", "invalid model", "does not support",
        "high demand", "overloaded", "unavailable", "rate limit", "quota",
        "timed out", "timeout", "503", "429", "unparseable json",
    ))


@dataclass
class LLMConfig:
    openrouter_key: str = ""
    openrouter_base: str = ""
    openrouter_model: str = ""
    gemini_key: str = ""
    gemini_model: str = ""
    temperature: float = 0.2
    timeout_s: float = 30.0
    fallback_models: list[str] = field(default_factory=list)


def _load_config() -> LLMConfig:
    return LLMConfig(
        openrouter_key=config.env_str("OPENROUTER_API_KEY"),
        openrouter_base=config.env_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        openrouter_model=config.env_str("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        gemini_key=config.env_str("GEMINI_API_KEY"),
        gemini_model=config.env_str("GEMINI_MODEL", "gemini-2.5-flash"),
    )


class LLMAgent:
    def __init__(self, cfg: LLMConfig | None = None):
        self._cfg = cfg or _load_config()
        self._openai = None  # lazy
        self._gemini = None

    # ------------------------------------------------------------ providers
    def _openai_client(self):
        import openai

        if self._openai is None:
            self._openai = openai.OpenAI(api_key=self._cfg.openrouter_key,
                                         base_url=self._cfg.openrouter_base, timeout=self._cfg.timeout_s)
        return self._openai

    def _gemini_client(self):
        import openai

        if self._gemini is None:
            self._gemini = openai.OpenAI(
                api_key=self._cfg.gemini_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                timeout=self._cfg.timeout_s,
            )
        return self._gemini

    # ------------------------------------------------------------- completions
    def chat_json(self, system: str, user: str) -> dict | None:
        """One JSON completion with cross-provider + cross-model fallback.

        None when every provider fails (the caller then uses its deterministic
        fallback). A retired/unknown model id moves to the next candidate model
        instead of aborting the provider.
        """
        if self._cfg.openrouter_key and not provider_on_cooldown("OpenRouter"):
            out = self._try_models(self._openai_client(), "OpenRouter",
                                  model_candidates(self._cfg.openrouter_model,
                                                   OPENROUTER_FALLBACK_MODELS),
                                  system, user)
            if out is not None:
                return out
        if self._cfg.gemini_key and not provider_on_cooldown("Gemini"):
            out = self._try_models(self._gemini_client(), "Gemini",
                                  model_candidates(self._cfg.gemini_model,
                                                   GEMINI_FALLBACK_MODELS),
                                  system, user)
            if out is not None:
                return out
        return None

    def _try_models(self, client, label: str, models: list[str],
                    system: str, user: str) -> dict | None:
        for model in models:
            try:
                out = self._complete(client, model, system, user)
                if out is not None:
                    return out
            except Exception as exc:  # noqa: BLE001 — provider/model chain
                logger.warning("[llm] %s/%s failed: %s", label, model, exc)
                if not is_model_unavailable(exc):
                    mark_provider_cooldown(label)  # bad credentials for real
                    break
        return None

    def _complete(self, client, model: str, system: str, user: str) -> dict | None:
        resp = client.chat.completions.create(
            model=model,
            temperature=self._cfg.temperature,
            # generous budget: reasoning models spend part of it before emitting
            # the JSON, and a tight cap silently truncated the answer
            max_tokens=2000,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            timeout=self._cfg.timeout_s,
        )
        choice = resp.choices[0]
        parsed = parse_json_loose(choice.message.content or "")
        if parsed is None:
            raise ValueError(
                f"unparseable JSON from {model} (finish={choice.finish_reason})")
        return parsed
