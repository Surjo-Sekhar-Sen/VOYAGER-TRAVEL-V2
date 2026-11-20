"""SerpAPI client (PROMPT_4 §2.3 + §4) for Google Maps data.

Used for:
- place_id resolution (Google Maps search)
- real Google Reviews (place_details -> user_reviews.most_relevant)
- live ride prices / directions (engine=google_maps_directions)

Rules:
- REAL data only. Every field maps to the actual SerpAPI response keys
  (`username`/`description`, NOT the wrong `user.name`/`snippet` — see
  MASTER_KNOWLEDGE_BASE §16.1 for the v1 bug that must not regress).
- No API key / any error -> empty result (never fabricated).
- Results are cached (24h for places/reviews, 15min for prices) to protect
  the free quota (~1250 searches/mo across friend keys).
"""
import json
import logging
import time

import requests

from ... import config

logger = logging.getLogger(__name__)

_SERP_BASE = "https://serpapi.com/search.json"
_DEFAULT_TIMEOUT = 6.0
_REVIEW_CACHE_S = 24 * 3600
_PRICE_CACHE_S = 15 * 60
_CACHE_VERSION = 3  # bump when the parsed schema changes (PROMPT_4 §2.3)


class SerpAPIClient:
    def __init__(self, api_key: str | None = None, timeout_s: float = _DEFAULT_TIMEOUT):
        self._key = api_key or config.env_str("SERPAPI_API_KEY")
        self._timeout = timeout_s
        self._cache: dict[str, tuple[float, object]] = {}
        if not self._key:
            logger.warning("[serpapi] SERPAPI_API_KEY missing — reviews/live prices unavailable")

    def _cached(self, key: str, ttl_s: float):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl_s:
            return hit[1]
        return None

    def _store(self, key: str, value, ttl_s: float) -> None:
        self._cache[key] = (time.time(), value)
        if len(self._cache) > 2000:
            oldest = min(self._cache.items(), key=lambda kv: kv[1][0])
            self._cache.pop(oldest[0], None)

    def _get(self, params: dict) -> dict | None:
        if not self._key:
            return None
        try:
            params["api_key"] = self._key
            resp = requests.get(_SERP_BASE, params=params, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("[serpapi] request failed: %s", exc)
            return None

    # ------------------------------------------------------------ place search
    def search_place(self, query: str, lat: float | None = None, lng: float | None = None) -> dict | None:
        """Google Maps search -> first result {place_id, title, ...}. None if nothing."""
        key = f"place:{query.strip().lower()}"
        cached = self._cached(key, _REVIEW_CACHE_S)
        if cached is not None:
            return cached
        data = self._get({"engine": "google_maps", "q": query, "type": "search"})
        out = None
        if not data:
            self._store(key, out, _REVIEW_CACHE_S)
            return out
        # SerpAPI returns `place_results` (dict) for a specific place search and
        # `local_results` (list) for a category search — support both shapes.
        if data.get("place_results") and isinstance(data["place_results"], dict):
            pr = data["place_results"]
            gps = pr.get("gps_coordinates") or {}
            out = {
                "place_id": pr.get("place_id"),
                "title": pr.get("title"),
                "address": pr.get("address"),
                "lat": gps.get("latitude"),
                "lng": gps.get("longitude"),
                "rating": pr.get("rating"),
                "reviews": pr.get("reviews"),
                # real Google photo URL (the result's own image) — the client had
                # been dropping this, so every search result rendered the grey
                # "no photo" placeholder even though Google returned one.
                "thumbnail": pr.get("thumbnail"),
                "type": pr.get("type"),
                "status": pr.get("status") or _open_state_text(pr.get("open_state")),
            }
        elif data.get("local_results"):
            first = data["local_results"][0]
            gps = first.get("gps_coordinates") or {}
            out = {
                "place_id": first.get("place_id"),
                "title": first.get("title"),
                "address": first.get("address"),
                "lat": gps.get("latitude") or first.get("latitude"),
                "lng": gps.get("longitude") or first.get("longitude"),
                "rating": first.get("rating"),
                "reviews": first.get("reviews"),
                "thumbnail": first.get("thumbnail"),
                "type": first.get("type"),
                "status": first.get("status"),
            }
        self._store(key, out, _REVIEW_CACHE_S)
        return out

    # -------------------------------------------------------------- place details
    def place_details(self, place_id: str) -> dict | None:
        """Google Maps place details -> reviews + hours + status. None on failure.

        Review fields are `username`/`description` (the real SerpAPI keys).
        """
        key = f"detail:{place_id}:v{_CACHE_VERSION}"
        cached = self._cached(key, _REVIEW_CACHE_S)
        if cached is not None:
            return cached
        data = self._get({"engine": "google_maps", "type": "place_details", "place_id": place_id})
        out = None
        if data and (data.get("place_results") or data.get("place")):  # place_results is the real key
            pr = data.get("place_results") or data.get("place") or {}
            reviews = []
            for r in (pr.get("user_reviews") or {}).get("most_relevant", []) or []:
                reviews.append({
                    "author_name": r.get("username", ""),
                    "rating": r.get("rating", 0),
                    "text": r.get("description", ""),
                    "date": r.get("date", ""),
                    "source": "serpapi",
                    "avatar": r.get("user_thumbnail") or "",
                    "author_review_count": r.get("user_review_count"),
                })
            # Google's own per-star tally over ALL ratings of the place, e.g.
            # [{stars: 5, amount: 38910}, ...] — the real distribution, not just
            # the handful of review bodies SerpAPI returns with the details.
            dist = {}
            for row in pr.get("rating_summary") or []:
                try:
                    stars = int(row.get("stars"))
                    amount = int(row.get("amount"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if 1 <= stars <= 5 and amount >= 0:
                    dist[stars] = amount
            out = {
                "place_id": place_id,
                "name": pr.get("title") or pr.get("name", ""),
                "address": pr.get("address", ""),
                "rating": pr.get("rating"),
                "user_rating_count": pr.get("reviews"),  # int count, not the review list
                "rating_distribution": dist,
                "business_status": pr.get("status", pr.get("business_status")),
                "open_now": (pr.get("open_state") or {}).get("open_now") if isinstance(
                    pr.get("open_state"), dict) else None,
                "weekday_hours": (pr.get("hours") or {}).get("weekday_text", []) if isinstance(
                    pr.get("hours"), dict) else [],
                "phone": pr.get("phone"),
                "website": pr.get("website"),
                # photos the details call already returned: `thumbnail` is the
                # place's own image URL, `photos_link` the gallery
                "photo_url": pr.get("thumbnail"),
                "photos_link": pr.get("photos_link"),
                "reviews": reviews,
            }
        self._store(key, out, _REVIEW_CACHE_S)
        return out

    # ----------------------------------------------------------- live prices
    def directions(self, origin: str, dest: str) -> dict | None:
        """Google Maps directions via SerpAPI -> drive_time (live).

        Returns {"duration_s": ..., "distance_m": ..., "ride_options": [...]}
        where ride options are present only when Google Maps exposes real
        prices. None on failure.

        SerpAPI renamed the start/end params (`start_addr`/`end_addr`, not
        `q`/`destination`) and the response shape changed to a `directions[]`
        list (one entry per travel mode) — parse that, keeping the `routes`
        shape only as a legacy fallback.
        """
        key = f"price:{origin.strip().lower()}:{dest.strip().lower()}"
        cached = self._cached(key, _PRICE_CACHE_S)
        if cached is not None:
            return cached
        data = self._get({
            "engine": "google_maps_directions",
            "start_addr": origin,
            "end_addr": dest,
        })
        out = None
        if data:
            if data.get("directions"):
                # new shape: list of {travel_mode, distance, duration, ...}
                driving = next(
                    (d for d in data["directions"] if (d.get("travel_mode") or "").lower() == "driving"),
                    data["directions"][0],
                )
                out = {
                    "duration_s": driving.get("duration"),
                    "distance_m": driving.get("distance"),
                    "ride_options": data.get("ride_options", []),
                    "source": "live",
                }
            elif data.get("routes"):
                route = data["routes"][0]
                leg = (route.get("legs") or [{}])[0]
                duration_s = None
                dur = leg.get("duration") or leg.get("duration_in_traffic")
                if isinstance(dur, dict):
                    try:
                        duration_s = int(dur.get("value", 0)) or None
                    except (ValueError, TypeError):
                        duration_s = None
                out = {
                    "duration_s": duration_s,
                    "distance_m": (leg.get("distance") or {}).get("value"),
                    "ride_options": data.get("ride_options", []),
                    "source": "live",
                }
        self._store(key, out, _PRICE_CACHE_S)
        return out


def _open_state_text(open_state) -> str | None:
    """SerpAPI returns open_state either as a dict or as a plain string."""
    if isinstance(open_state, dict):
        return open_state.get("open_state")
    return open_state if isinstance(open_state, str) else None


def _json_dumps(obj) -> str:
    return json.dumps(obj, default=str)
