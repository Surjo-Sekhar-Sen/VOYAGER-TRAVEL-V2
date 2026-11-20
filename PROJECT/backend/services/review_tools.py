"""Place review pipeline (PROMPT_4 §2.3).

Chain: search_places -> place_id -> SerpAPI place_details (real Google Reviews)
       -> top-2-per-star sample -> reliability score -> LLM summary.

Golden rule: reviews are REAL (SerpAPI). The LLM only *summarizes* what the
reviews actually say — it never writes reviews, ratings, or scores. On any
failure the result degrades to what is known (possibly empty reviews), never
to fabricated data.
"""
import logging

from .clients.serpapi_client import SerpAPIClient
from .data_schema import Place, PlaceDetails, ReliabilityInput, Review
from .reliability import compute_reliability
from .sentiment import review_polarity, sentiment_avg

logger = logging.getLogger(__name__)

_REVIEW_CACHE_VERSION = 3  # bump to invalidate stale cache entries

# The details call returns only a handful of review bodies. Treat any sample
# below this as "thin" so the analysis leans on the real rating tally instead of
# pretending a 5-review sample represents 60k ratings.
THIN_SAMPLE = 8


class ReviewTools:
    def __init__(self, serpapi: SerpAPIClient):
        self._serpapi = serpapi
        self._cache: dict[str, PlaceDetails] = {}

    def enrich_place(self, place: Place, max_reviews: int = 24) -> PlaceDetails:
        """Turn a search Place into enriched PlaceDetails (reviews + reliability)."""
        place, meta = self._resolve(place)
        if not place.place_id:
            return self._degrade(place, "no place_id")
        cached = self._cache.get(place.place_id)
        if cached:
            return cached
        detail = self._serpapi.place_details(place.place_id)
        if not detail:
            # still return the resolved Google metadata (rating etc.) — not a bare degrade
            out = self._build_details(place, meta, [], None, None)
            self._cache[place.place_id] = out
            return out

        reviews = [Review(**{k: r.get(k, v) for k, v in Review().model_dump().items() if k in r})
                   for r in detail.get("reviews", [])][:max_reviews]
        if not reviews and detail.get("user_rating_count"):
            # real count exists but Google returned no review bodies -> honest empty list
            logger.info("[reviews] %s: count=%s but no review bodies from SerpAPI",
                        place.name, detail.get("user_rating_count"))

        dist = detail.get("rating_distribution") or {}
        sentiment = sentiment_avg([r.text for r in reviews]) if reviews else 0.5
        # Prefer the tally-weighted mean rating: it is computed over EVERY rating
        # of the place, where `rating` is Google's rounded display value.
        rating = _weighted_rating(dist) or (detail.get("rating") or meta.get("rating")
                                           or place.rating or 0.0)
        count = (detail.get("user_rating_count") or place.user_rating_count
                 or sum(dist.values()) or len(reviews))
        rel = compute_reliability(ReliabilityInput(
            rating=rating,
            review_count=count,
            sentiment_avg=sentiment,
            business_status=detail.get("business_status") or place.business_status,
        ))

        summary, concerns, positives, verdict, source = self._summarize(
            reviews, rel.score_pct, dist, count)

        out = self._build_details(
            place, meta, reviews, sentiment, rel,
            phone=detail.get("phone"), website=detail.get("website"),
            rating=detail.get("rating"), count=detail.get("user_rating_count"),
            status=detail.get("business_status"),
            open_now=detail.get("open_now"), hours=detail.get("weekday_hours") or [],
            summary=summary, concerns=concerns,
            positives=positives, verdict=verdict, distribution=dist,
            reviews_sampled=len([r for r in reviews if r.text.strip()]),
            summary_source=source,
            photo=detail.get("photo_url"),
        )
        self._cache[place.place_id] = out
        return out

    def _resolve(self, place: Place) -> tuple[Place, dict]:
        """Resolve an OSM/unknown place to a real Google place via SerpAPI.

        Returns (place, meta) where `meta` carries any Google-side metadata
        (rating/count/status/hours/photo) learned during resolution. Real data
        only — no resolution means `meta` is empty.
        """
        if place.place_id and not place.place_id.startswith("osm:"):
            return place, {}
        hit = self._serpapi.search_place(place.name, lat=place.lat or None, lng=place.lng or None)
        if not hit or not hit.get("place_id"):
            return place, {}
        meta = dict(hit)
        resolved = Place(
            **{**place.model_dump(), "place_id": str(hit["place_id"]),
               "rating": place.rating or hit.get("rating"),
               "user_rating_count": place.user_rating_count
               if place.user_rating_count else (hit.get("reviews") if isinstance(hit.get("reviews"), int) else None),
               "business_status": place.business_status or hit.get("business_status")})
        return resolved, meta

    def _build_details(self, place: Place, meta: dict, reviews: list[Review],
                       sentiment, rel, **extra) -> PlaceDetails:
        base = PlaceDetails(
            **place.model_dump(exclude={"place_id"}),
            place_id=place.place_id,
            reviews=reviews,
            sentiment_avg=round(sentiment, 3) if sentiment is not None else None,
            reliability_score=rel.score_pct if rel else None,
            pin_class=rel.pin_class if rel else "yellow",
            summary=extra.get("summary", "") or "",
            concerns=extra.get("concerns", []) or [],
            positives=extra.get("positives", []) or [],
            verdict=extra.get("verdict", "") or "",
            rating_distribution=extra.get("distribution") or {},
            reviews_sampled=extra.get("reviews_sampled") or 0,
            summary_source=extra.get("summary_source") or "none",
        )
        for key, src in (
            ("rating", meta.get("rating") if meta else None),
            ("user_rating_count", meta.get("reviews") if meta and isinstance(meta.get("reviews"), int) else None),
            ("business_status", meta.get("business_status") if meta else None),
            ("address", meta.get("address") if meta else None),
        ):
            if extra.get(key) is None and src is not None and getattr(base, key, None) is None:
                setattr(base, key, src)
        if extra.get("phone") is not None:
            base.phone = extra["phone"]
        if extra.get("website") is not None:
            base.website = extra["website"]
        if extra.get("rating") is not None:
            base.rating = extra["rating"]
        if extra.get("count") is not None:
            base.user_rating_count = extra["count"]
        if extra.get("status") is not None:
            base.business_status = extra["status"]
        if extra.get("open_now") is not None:
            base.open_now = extra["open_now"]
        if extra.get("hours"):
            base.weekday_hours = extra["hours"]
        # photo: the details call is authoritative, then the search hit's own
        # Google image. Without this the panel had no image to render even when
        # SerpAPI had returned one (fixed 2026-10-15).
        if extra.get("photo") is not None:
            base.photo_name = extra["photo"]
        if not base.photo_name and meta.get("thumbnail"):
            base.photo_name = meta["thumbnail"]
        return base

    def _summarize(self, reviews: list[Review], score_pct: int,
                   distribution: dict | None = None,
                   total_count: int = 0) -> tuple[str, list[str], list[str], str, str]:
        """Analysis of REAL reviews -> (summary, concerns, positives, verdict, source).

        The LLM only *reads* what it is handed (review texts + their real star
        ratings + Google's real per-star tally). When no key is configured the
        deterministic heuristic below still describes the actual data — it never
        invents a theme. Either way the numbers come from the real tally.
        """
        usable = [r for r in reviews if r.text.strip()]
        if not usable and not distribution:
            return "", [], [], "", "none"

        payload = [{"rating": r.rating, "date": r.date, "text": r.text} for r in usable]
        if payload:
            try:
                from ..agents import review_summarizer

                result = review_summarizer.summarize_reviews(
                    payload, score_pct, distribution=distribution)
                if result and result.summary:
                    return result.summary, result.concerns, result.positives, \
                        result.verdict, "llm"
            except Exception as exc:  # noqa: BLE001 — optional
                logger.warning("[reviews] LLM summary unavailable: %s", exc)

        summary, concerns, positives, verdict = _heuristic_analysis(
            usable, distribution or {}, total_count, score_pct)
        return summary, concerns, positives, verdict, "heuristic"

    @staticmethod
    def _degrade(place: Place, reason: str) -> PlaceDetails:
        logger.info("[reviews] %s: %s — returning base place", place.name, reason)
        return PlaceDetails(**place.model_dump(), reliability_score=None, pin_class="yellow")


# ------------------------------------------------------------------ analysis

# review phrases that map a recurring complaint/praise to a human topic
_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("food and drinks", ("food", "taste", "tasty", "dish", "menu", "beer", "drink",
                         "pizza", "coffee", "flavour", "flavor", "stale", "bland")),
    ("service and staff", ("staff", "service", "server", "waiter", "rude", "friendly",
                           "helpful", "polite", "manager", "courteous")),
    ("waiting and crowding", ("wait", "waiting", "queue", "crowd", "crowded", "rush",
                              "delay", "slow", "long line")),
    ("pricing", ("price", "pricing", "expensive", "overpriced", "cheap", "affordable",
                 "value", "bill", "cost")),
    ("cleanliness", ("clean", "dirty", "hygiene", "hygienic", "filthy", "damp",
                     "smell", "stale")),
    ("ambience", ("ambience", "ambiance", "vibe", "music", "noisy", "quiet", "decor",
                  "view", "seating", "space")),
    ("access and parking", ("parking", "park", "location", "accessible", "entrance",
                            "metro", "bus", "walk")),
)


def _weighted_rating(distribution: dict | None) -> float | None:
    """Tally-weighted mean star rating over ALL ratings, or None."""
    if not distribution:
        return None
    total = 0
    weighted = 0.0
    for stars in (1, 2, 3, 4, 5):
        try:
            n = int(distribution.get(stars) or 0)
        except (TypeError, ValueError):
            continue
        total += n
        weighted += stars * n
    if total <= 0:
        return None
    return round(weighted / total, 2)


def _distribution_share(distribution: dict, stars: int) -> float:
    total = sum(int(distribution.get(s) or 0) for s in (1, 2, 3, 4, 5))
    if total <= 0:
        return 0.0
    return int(distribution.get(stars) or 0) / total


def _heuristic_analysis(reviews: list[Review], distribution: dict,
                        total_count: int,
                        score_pct: int) -> tuple[str, list[str], list[str], str]:
    """Deterministic, data-grounded analysis (no LLM needed).

    Every number/theme below is derived from the real review objects or the real
    rating tally — nothing is invented.
    """
    mean = _weighted_rating(distribution)
    bits: list[str] = []
    if mean is not None:
        pos_share = _distribution_share(distribution, 5) + _distribution_share(distribution, 4)
        bits.append(f"{pos_share * 100:.0f}% of all {sum(int(distribution.get(s) or 0) for s in range(1, 6))} "
                    f"ratings are 4+ stars (average {mean:.1f}/5)")
    elif reviews:
        mean_sample = sum(r.rating for r in reviews) / len(reviews)
        bits.append(f"sampled reviews average {mean_sample:.1f}/5")

    positives, concerns = _topics(reviews)
    summary = ""
    if bits:
        summary = "; ".join(bits)
        summary = summary[0].upper() + summary[1:] + "."
    if reviews:
        tone = "positive" if sentiment_avg([r.text for r in reviews]) >= 0.55 else (
            "mixed" if sentiment_avg([r.text for r in reviews]) >= 0.45 else "negative")
        summary += (f" The {len(reviews)} review bodies returned by Google read "
                    f"{tone}. Reliability {score_pct}%.")
    elif total_count:
        summary += f" Reliability {score_pct}%."

    verdict = ""
    if mean is not None:
        if mean >= 4.3:
            verdict = "Strongly rated overall — a safe pick."
        elif mean >= 3.8:
            verdict = "Well rated overall — read the concerns before committing."
        elif mean >= 3.0:
            verdict = "Mixed ratings — check the recent 1-2 star reviews first."
        else:
            verdict = "Weakly rated overall — consider an alternative."
    return summary.strip(), concerns, positives, verdict


def _topics(reviews: list[Review]) -> tuple[list[str], list[str]]:
    """Recurring praised / criticized topics, counted from the review texts.

    A topic is only reported when at least two reviews mention it, so a single
    loud review cannot define the place.
    """
    praise: dict[str, int] = {}
    gripe: dict[str, int] = {}
    for r in reviews:
        text = (r.text or "").lower()
        if not text:
            continue
        polarity = review_polarity(r.text)
        negative = (r.rating and r.rating <= 3) or polarity < 0.45
        positive = polarity > 0.55 and not (r.rating and r.rating <= 2)
        for topic, keys in _TOPICS:
            if not any(k in text for k in keys):
                continue
            if negative:
                gripe[topic] = gripe.get(topic, 0) + 1
            elif positive:
                praise[topic] = praise.get(topic, 0) + 1
    top_pos = [f"{t} ({n} reviews)" for t, n in sorted(praise.items(), key=lambda kv: -kv[1])[:3]
               if n >= 2]
    top_con = [f"{t} ({n} reviews)" for t, n in sorted(gripe.items(), key=lambda kv: -kv[1])[:3]
               if n >= 2]
    return top_pos, top_con
