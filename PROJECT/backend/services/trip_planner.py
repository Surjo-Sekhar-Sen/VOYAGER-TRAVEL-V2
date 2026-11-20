"""Trip Planner discovery + ranking engine (PROMPT_8 Phase 2).

Builds the candidate pool of places for a destination and scores each one with
the locked formula (spec 2.3):

    score = interest_match*0.35 + rating_normalized*0.25
          + group_type_fit*0.20 + crowd_penalty_inverse*0.10
          + uniqueness_bonus*0.10

Every recommended place carries a `why` line (no bare ranked lists) and the
pool is post-processed by the diversity rule (2.4) so a single category never
dominates. Transport logic is deliberately NOT touched here — the integration
rule (9) keeps that entirely in the segment engine.

Data source today is static seed data (`trip_seed`) which is labelled
ESTIMATED, never fabricated (golden rule #1). A live API can swap in later
behind the same `TripPlace` shape.
"""
import logging
import math

from . import trip_places, trip_seed
from .data_schema import GROUP_TYPES, GroupType, INTEREST_TAGS, RankedPlace

logger = logging.getLogger(__name__)

_EPS = 1e-9
_CROWD_VALUE = {"low": 1.0, "medium": 0.6, "high": 0.2, "unknown": 0.5}
_PACE_PER_DAY = {"relaxed": 2, "balanced": 4, "packed": 6}
# Rating confidence: a 4.8 with 80 ratings should not beat a 4.6 with 180k.
_RATING_PRIOR_COUNT = 300.0
_RATING_PRIOR_MEAN = 4.0

# Time-of-day scheduling (Phase C). A day is ordered by when a place is best
# visited, not just by distance: sunrise spots first, museums/markets in the
# afternoon, food at meals, nightlife last.
_SLOT_ORDER = {"early_morning": 0, "morning": 1, "afternoon": 2, "evening": 3, "night": 4}
_SLOT_START_MIN = {"early_morning": 7 * 60, "morning": 9 * 60,
                   "afternoon": 13 * 60, "evening": 17 * 60, "night": 20 * 60}
_CATEGORY_SLOT = {
    "nature": "early_morning", "photo": "morning", "wellness": "morning",
    "heritage": "morning", "museum": "morning", "religious": "morning",
    "adventure": "morning", "offbeat": "morning",
    "shopping": "afternoon", "food": "afternoon",
    "nightlife": "evening",
}
_MEAL_GAP_MIN = 20  # coffee/washroom buffer between two places
# Day plans stay inside the city so a "Bengaluru trip" is not four 40 km hops.
# Far getaways (Nandi Hills, Devanahalli) remain in the discovery pool, and the
# planner widens this automatically if too few city places are available.
_PLAN_RADIUS_KM = 16.0
# How many stops of each kind a mixed itinerary should carry. Without this a
# round-robin can return two malls and two breweries but no park.
_CATEGORY_CAP = {
    "food": 2, "shopping": 1, "nightlife": 1, "wellness": 1,
    "adventure": 1, "museum": 2, "heritage": 2, "nature": 2,
    "religious": 2, "photo": 1, "offbeat": 1,
}
_CATEGORY_LABELS = {
    "nature": "nature", "heritage": "heritage", "food": "food",
    "shopping": "shopping", "nightlife": "nightlife", "adventure": "adventure",
    "wellness": "wellness", "religious": "temples", "museum": "museums",
    "photo": "photo spots", "offbeat": "offbeat spots",
}


class TripPlannerService:
    def destinations(self) -> list[dict]:
        return trip_places.destinations()

    def discover_places(
        self,
        destination: str,
        interests: list[str] | None = None,
        group_type: str = "friends",
        limit: int = 12,
        budget: float | None = None,
        categories: list[str] | None = None,
    ) -> dict:
        """Rank the destination pool for the given interests + categories.

        Returns the top `limit` places (diversity-capped) with per-place score,
        component breakdown and a human `why` line, plus a disclaimer flag and
        an optional `relaxed` note when interests/categories had to be widened.
        """
        interests = _normalize_interests(interests)
        categories = _normalize_categories(categories)
        pool = trip_places.places_for_destination(destination)
        group_type = group_type if group_type in GROUP_TYPES else "friends"

        category_relaxed = False
        if categories:
            # Filter on the place's primary category so the chips in the UI do
            # what they say: "Temples" means temples, not every heritage-tagged
            # statue. Tags remain available for interest scoring.
            filtered = [p for p in pool if p.category in categories]
            if len(filtered) >= 3:
                pool = filtered
            else:
                category_relaxed = True

        ranked = [self._rank(p, interests, group_type) for p in pool]
        # Most-reviewed first on ties: a 4.6 park with 180k ratings is a safer
        # recommendation than a 4.6 lane with 40 ratings.
        ranked.sort(key=lambda rp: (-rp.score, -rp.rating, -rp.review_count))

        relaxed = category_relaxed or _should_relax(ranked, interests)
        if relaxed and not category_relaxed:
            ranked = _rank_ignoring_interest(ranked)
            ranked.sort(key=lambda rp: (-rp.score, -rp.rating, -rp.review_count))

        pool_limit = max(3, limit)
        # A category-filtered pool is intentionally narrow: do not let the 40%
        # diversity cap erase it down to a couple of token entries.
        narrow_pool = bool(categories) and len(pool) <= pool_limit
        selected = _enforce_diversity(
            ranked, pool_limit,
            narrow=narrow_pool or (len(interests) == 1 and "food" in interests))
        _assign_ranks(selected)

        return {
            "destination": destination,
            "group_type": group_type,
            "interests": interests,
            "categories": categories,
            "data_source": trip_places.source_label(),
            "disclaimer": (
                "Place data is a mix of curated and OpenStreetMap entries — fees, "
                "timings, ratings and crowd are approximate unless marked live. "
                "Please verify details with the venue before your trip."
            ),
            "relaxed": relaxed,
            "places": [p.model_dump(mode="json") for p in selected],
        }

    def generate_plan(
        self,
        destination: str,
        interests: list[str] | None = None,
        group_type: str = "friends",
        days: int = 3,
        pace: str = "balanced",
        budget: float | None = None,
        categories: list[str] | None = None,
    ) -> dict:
        """Day-wise itinerary for a destination (Phase 3 of the build).

        Ranks the pool, sizes it by pace x days, splits places into `days`
        spatially-coherent clusters (k-means on lat/lng so a day never zig-zags
        across the city) and orders each day by nearest-neighbour to avoid
        backtracking. Transport (Phase 4) + budget (Phase 5) slot in later —
        this endpoint returns place order + activity time only.
        """
        # Pull the full ranked pool (not just the diversity-capped discover
        # window) so an iconic-but-crowded landmark is still available to the
        # itinerary picker.
        res = self.discover_places(
            destination, interests, group_type, limit=100, budget=budget,
            categories=categories)
        ranked = res["places"]
        interests = res["interests"]
        pace = pace if pace in _PACE_PER_DAY else "balanced"
        k = max(1, int(days))

        # Keep the day plan city-scale. Distances are real great-circle km from
        # the destination centre, not a guess; if the filter would leave too
        # little to work with we fall back to the unfiltered pool.
        center = _destination_center(destination)
        if center:
            city = [p for p in ranked
                    if _haversine_km(center[0], center[1], p["lat"], p["lng"]) <= _PLAN_RADIUS_KM]
            if len(city) >= max(6, k * 2):
                ranked = city

        if not ranked:
            return {
                "destination": destination, "group_type": group_type,
                "interests": interests, "categories": res["categories"],
                "pace": pace, "days": [],
                "total_places": 0, "disclaimer": res["disclaimer"],
                "relaxed": res["relaxed"],
                "warning": (
                    f"No places available yet for '{destination}'. Only Bengaluru "
                    "is populated."
                ),
            }

        per_day = _PACE_PER_DAY[pace]
        max_total = max(3, per_day * k)
        # A plain "top N by score" itinerary becomes a wall of the highest-rated
        # temples. Pick round-robin across categories so every day mixes a
        # landmark, a green space, food and a museum where the pool allows it.
        selected = _balanced_pick(ranked, max_total, narrow=bool(categories))
        if k > len(selected):
            k = len(selected)

        clusters = _balanced_clusters(selected, k)
        days_out: list[dict] = []
        for i, cluster in enumerate(clusters, start=1):
            ordered = _schedule_day(cluster)
            distance = sum(
                _h(nxt, prev) for prev, nxt in zip(ordered, ordered[1:])
            )
            days_out.append({
                "day": i,
                "theme": _day_theme(ordered),
                "place_count": len(ordered),
                "total_activity_min": sum(p["duration_min"] for p in ordered),
                "total_distance_km": round(distance, 2),
                "start_time": ordered[0]["suggested_start"] if ordered else "",
                "end_time": ordered[-1]["suggested_end"] if ordered else "",
                "places": ordered,
            })

        return {
            "destination": destination,
            "group_type": group_type,
            "interests": interests,
            "categories": res["categories"],
            "data_source": res["data_source"],
            "pace": pace,
            "days": days_out,
            "total_places": len(selected),
            "total_activity_min": sum(d["total_activity_min"] for d in days_out),
            "total_distance_km": round(sum(d["total_distance_km"] for d in days_out), 2),
            "disclaimer": res["disclaimer"],
            "relaxed": res["relaxed"],
        }

    # ---------------------------------------------------------- ranking (2.3)
    @staticmethod
    def _rank(p, interests: list[str], group_type: str) -> RankedPlace:
        interest_match = _interest_match(p, interests)
        rating_norm = (
            max(0.0, min(1.0, _bayesian_rating(p.rating, p.review_count) / 5.0))
            if getattr(p, "rating_known", True) and p.rating > 0
            else 0.5  # unknown rating -> neutral, never a confident guess
        )
        group_fit = _group_fit(p, group_type)
        crowd = _crowd_penalty_inverse(p)
        uniqueness = _uniqueness_bonus(p, interests)

        score = (
            0.35 * interest_match
            + 0.25 * rating_norm
            + 0.20 * group_fit
            + 0.10 * crowd
            + 0.10 * uniqueness
        )
        return RankedPlace(
            **p.model_dump(),
            score=round(score, 4),
            components={
                "interest_match": round(interest_match, 4),
                "rating_normalized": round(rating_norm, 4),
                "group_type_fit": round(group_fit, 4),
                "crowd_penalty_inverse": round(crowd, 4),
                "uniqueness_bonus": round(uniqueness, 4),
            },
            why=_why_line(p, interests, group_type, interest_match, group_fit, crowd),
        )


# --------------------------------------------------------------- sub-scores
def _bayesian_rating(rating: float, count: int) -> float:
    """Rating pulled toward the city mean when the review count is small.

    This keeps a handful of 5-star reviews from outranking Bengaluru's
    well-established landmarks. The raw rating is still what the UI shows.
    """
    count = max(0, int(count or 0))
    return (rating * count + _RATING_PRIOR_MEAN * _RATING_PRIOR_COUNT) / (count + _RATING_PRIOR_COUNT)


def _interest_match(p, interests: list[str]) -> float:
    if not interests:
        return 0.5  # no interests chosen -> neutral
    matched = set(p.tags) & set(interests)
    return len(matched) / len(interests)


def _group_fit(p, group_type: str) -> float:
    if group_type in ("family_kids", "family"):
        if p.category == "nightlife":
            return 0.0 if not p.family_friendly else 0.5
        if not p.family_friendly:
            return 0.0
        return 1.0
    if group_type == "senior":
        if p.physically_demanding:
            return 0.0
        if p.category == "nightlife":
            return 0.5
        if not p.family_friendly:
            return 0.5
        return 1.0
    # solo / couple / friends ride freely
    return 1.0


def _crowd_penalty_inverse(p) -> float:
    if not p.crowd:
        return 0.5  # Unknown -> neutral, never confident (2.1)
    slots = p.best_times if p.best_times else list(p.crowd.keys())
    values = [_CROWD_VALUE.get(p.crowd.get(s, "unknown"), 0.5) for s in slots]
    return sum(values) / len(values)


def _uniqueness_bonus(p, interests: list[str]) -> float:
    is_offbeat = "offbeat" in p.tags
    if is_offbeat and "offbeat" in interests:
        return 1.0
    if is_offbeat:
        return 0.5
    return 0.2


def _why_line(p, interests, group_type, interest_match, group_fit, crowd) -> str:
    parts: list[str] = []
    matched = sorted(set(p.tags) & set(interests))
    if matched:
        parts.append("matches your " + _pretty_list(matched))
    if getattr(p, "rating_known", True) and p.rating >= 4.3:
        parts.append(f"highly rated ({p.rating:.1f})")
    if p.category == "nightlife" and group_type in ("solo", "couple", "friends"):
        parts.append("nightlife pick")
    if "offbeat" in p.tags:
        parts.append("an offbeat gem")
    if group_fit == 1.0 and group_type in ("family_kids", "family"):
        parts.append("family-friendly")
    if crowd >= 0.8:
        parts.append("quiet/low-crowd windows")
    if not parts:
        parts.append("good fit for your trip")
    return "; ".join(parts)


def _pretty_list(items: list[str]) -> str:
    items = [i.replace("_", " ") for i in items]
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ----------------------------------------------------------- helpers
def _normalize_interests(interests: list[str] | None) -> list[str]:
    if not interests:
        return []
    seen: list[str] = []
    for i in interests:
        i = i.strip().lower().replace(" ", "_").replace("-", "_")
        if i and i not in seen and i in INTEREST_TAGS:
            seen.append(i)
    return seen


def _normalize_categories(categories: list[str] | None) -> list[str]:
    if not categories:
        return []
    seen: list[str] = []
    for c in categories:
        c = c.strip().lower().replace(" ", "_").replace("-", "_")
        if c and c not in seen and c in INTEREST_TAGS:
            seen.append(c)
    return seen


def _should_relax(ranked: list[RankedPlace], interests: list[str]) -> bool:
    """Few matching places -> auto-widen instead of returning a thin pool (8.1)."""
    if not interests:
        return False
    matching = sum(1 for rp in ranked if set(rp.tags) & set(interests))
    return matching < 4


def _rank_ignoring_interest(ranked: list[RankedPlace]) -> list[RankedPlace]:
    """Rerank by rating/group-fit only when interest filter was too narrow."""
    for rp in ranked:
        rp.components["interest_match"] = 0.0
        rp.score = round(
            0.25 * rp.components["rating_normalized"]
            + 0.20 * rp.components["group_type_fit"]
            + 0.10 * rp.components["crowd_penalty_inverse"]
            + 0.10 * rp.components["uniqueness_bonus"]
            + 0.35 * 0.5,  # neutral interest term
            4,
        )
        rp.why = "Interest filter was thin — widened to nearby options. " + rp.why
    return ranked


def _enforce_diversity(ranked: list[RankedPlace], limit: int, narrow: bool) -> list[RankedPlace]:
    """Cap any single category at ~40% of the returned pool (2.4).

    `narrow` (basically "only Food" selected) relaxes the cap to avoid flattening
    an intentionally single-focused trip, only guarding against pure repetition.
    """
    out: list[RankedPlace] = []
    counts: dict[str, int] = {}
    for rp in ranked:
        if len(out) >= limit:
            break
        cap = limit if narrow else max(1, round(0.4 * limit))
        if counts.get(rp.category, 0) >= cap and len(out) + 1 > cap:
            # category saturated; try another category instead of duplicating
            if any(c for c in _cat_of_all(ranked)
                   if counts.get(c, 0) < cap):
                continue
        out.append(rp)
        counts[rp.category] = counts.get(rp.category, 0) + 1
    return out


def _cat_of_all(ranked: list[RankedPlace]) -> list[str]:
    return [rp.category for rp in ranked]


def _assign_ranks(selected: list[RankedPlace]) -> None:
    for i, rp in enumerate(sorted(selected, key=lambda r: -r.score), start=1):
        rp.rank = i


# ------------------------------------------------------- Phase 3: day clustering
def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _hd(a: tuple, b: tuple) -> float:
    return _haversine_km(a[0], a[1], b[0], b[1])


def _kmeans(points: list[dict], k: int, iters: int = 12) -> list[list[dict]]:
    """k-means on (lat, lng) to build spatially-coherent day clusters (3.1).

    Prefer `_balanced_clusters` for the public plan (this one can over-fill a
    day when city-centre places dominate and a couple of far outliers exist).
    """
    coords = [(p["lat"], p["lng"]) for p in points]
    if k >= len(coords):
        return [[p] for p in points]

    # farthest-point seeding (k-means++) for stable, spread-out clusters.
    seeds = [coords[0]]
    while len(seeds) < k:
        seeds.append(max(coords, key=lambda c: min(_hd(c, s) for s in seeds)))

    cents = [list(s) for s in seeds]
    for _ in range(iters):
        groups: list[list[tuple]] = [[] for _ in range(k)]
        for c in coords:
            groups[min(range(k), key=lambda j: _hd(c, cents[j]))].append(c)
        next_cents = []
        for g in groups:
            if not g:
                next_cents.append(list(cents[len(next_cents)]))
            else:
                next_cents.append([sum(p[0] for p in g) / len(g),
                                   sum(p[1] for p in g) / len(g)])
        cents = next_cents

    clusters: list[list[dict]] = [[] for _ in range(k)]
    for p in points:
        c = (p["lat"], p["lng"])
        clusters[min(range(k), key=lambda j: _hd(c, cents[j]))].append(p)
    clusters = [c for c in clusters if c]
    clusters.sort(key=len, reverse=True)  # fullest days first
    return clusters


def _greedy_chain(points: list[dict]) -> list[dict]:
    """Order points by nearest-neighbour from the centroid (start of a route)."""
    pts = list(points)
    clat = sum(p["lat"] for p in pts) / len(pts)
    clng = sum(p["lng"] for p in pts) / len(pts)
    start = min(pts, key=lambda p: _hd((clat, clng), (p["lat"], p["lng"])))
    pts.remove(start)
    chain = [start]
    while pts:
        last = chain[-1]
        nxt = min(pts, key=lambda p: _hd(
            (last["lat"], last["lng"]), (p["lat"], p["lng"])))
        pts.remove(nxt)
        chain.append(nxt)
    return chain


def _balanced_clusters(points: list[dict], k: int) -> list[list[dict]]:
    """Spatially-coherent + size-balanced day clusters (3.1).

    k-means keeps each day geographically tight; a balancing pass then moves
    points out of over-full clusters so every day gets a usable number of
    stops. Slicing one greedy chain (the old approach) could put a south-west
    wellness retreat and a north grassland in the same day.
    """
    n = len(points)
    if k >= n:
        return [[p] for p in points]
    clusters = _kmeans(points, k)
    if not clusters:
        return _balanced_clusters_from_chain(points, k)
    return _balance_sizes(clusters, k, n)


def _balance_sizes(clusters: list[list[dict]], k: int, n: int) -> list[list[dict]]:
    """Move points out of oversized clusters into the nearest cluster with room.

    k-means clustering is geographically sensible but can be very uneven; a day
    with 7 stops and a day with 1 is not a usable itinerary.
    """
    if not clusters:
        return clusters
    cap = -(-n // k)  # ceil
    clusters = [list(c) for c in clusters]
    while len(clusters) < k:
        clusters.append([])

    def centroid(c: list[dict]) -> tuple[float, float]:
        return (sum(p["lat"] for p in c) / len(c),
                sum(p["lng"] for p in c) / len(c))

    for _ in range(n * 2):
        over = [i for i, c in enumerate(clusters) if len(c) > cap]
        if not over:
            break
        moved = False
        for i in over:
            cents = [centroid(c) if c else (0.0, 0.0) for c in clusters]
            src = clusters[i]
            src_cent = centroid(src)
            src.sort(key=lambda p: -_hd((p["lat"], p["lng"]), src_cent))
            point = src[0]
            targets = [j for j, c in enumerate(clusters) if j != i and len(c) < cap]
            if not targets:
                continue
            j = min(targets, key=lambda t: _hd((point["lat"], point["lng"]), cents[t]))
            src.remove(point)
            clusters[j].append(point)
            moved = True
        if not moved:
            break
    return [c for c in clusters if c]


def _balanced_clusters_from_chain(points: list[dict], k: int) -> list[list[dict]]:
    """Fallback: slice a nearest-neighbour chain into `k` chunks."""
    chain = _greedy_chain(points)
    n = len(chain)
    if k >= n:
        return [[p] for p in chain]
    size = n // k
    rem = n % k
    clusters: list[list[dict]] = []
    idx = 0
    for j in range(k):
        sz = size + (1 if j < rem else 0)
        clusters.append(chain[idx:idx + sz])
        idx += sz
    return clusters


def _balanced_pick(ranked: list, limit: int, narrow: bool = False) -> list:
    """Round-robin across categories, best-scoring category first.

    Keeps the ranking formula intact while stopping a single category (usually
    temples) from filling the whole trip. Once every category has one entry the
    cycle continues with the next-best place per category. Works on the
    serialised dicts returned by `discover_places`.
    """
    buckets: dict[str, list] = {}
    for rp in ranked:
        cat = rp["category"] if isinstance(rp, dict) else rp.category
        buckets.setdefault(cat, []).append(rp)

    def sort_key(item):
        # Landmark prominence within a category: a well-reviewed 4.4 is a safer
        # holiday pick than a barely-reviewed 4.6. The spec score still breaks
        # ties and is what the result card shows.
        if isinstance(item, dict):
            rating, count, score = item["rating"], item["review_count"], item["score"]
        else:
            rating, count, score = item.rating, item.review_count, item.score
        prominence = rating * math.log10(1 + max(0, count))
        return (-prominence, -score, -rating)

    def score_of(item):
        return item["score"] if isinstance(item, dict) else item.score

    for items in buckets.values():
        items.sort(key=sort_key)
    order = sorted(buckets, key=lambda c: (-score_of(buckets[c][0]), c))
    picked: list = []
    per_category: dict[str, int] = {}

    def cap_for(cat: str) -> int:
        if narrow:
            return limit
        return _CATEGORY_CAP.get(cat, 2)

    idx = 0
    while len(picked) < limit:
        if not any(buckets[c] and per_category.get(c, 0) < cap_for(c) for c in order):
            break
        cat = order[idx % len(order)]
        if buckets[cat] and per_category.get(cat, 0) < cap_for(cat):
            picked.append(buckets[cat].pop(0))
            per_category[cat] = per_category.get(cat, 0) + 1
        idx += 1
    return picked


def _destination_center(slug: str) -> tuple[float, float] | None:
    for dest in trip_seed.destinations():
        if dest.get("slug") == slug:
            try:
                return float(dest["lat"]), float(dest["lng"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _h(a: dict, b: dict) -> float:
    return _haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])


def _slot_for(p: dict, food_index: int, food_only: bool = False) -> str:
    """Best time-of-day window for a place; meals are pinned to meal times."""
    category = p.get("category", "")
    available = [s for s in (p.get("best_times") or []) if s in _SLOT_ORDER]
    if category == "food":
        if food_only:
            # food crawl: breakfast, lunch, snack, dinner
            pattern = ["morning", "afternoon", "evening", "night"]
            desired = pattern[food_index % len(pattern)]
        else:
            desired = "afternoon" if food_index == 0 else "night"
        if not available or desired in available:
            return desired
        # keep the venue's own stated window when the ideal slot is unavailable
        return min(available, key=lambda s: abs(_SLOT_ORDER.get(s, 9) - _SLOT_ORDER.get(desired, 9)))
    default = _CATEGORY_SLOT.get(category, "morning")
    if not available or default in available:
        return default
    return min(available, key=lambda s: _SLOT_ORDER.get(s, 9))


def _fmt_minutes(minutes: int) -> str:
    minutes = max(0, min(23 * 60 + 59, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _schedule_day(points: list[dict]) -> list[dict]:
    """Order a day by time-of-day, then nearest-neighbour inside each window.

    Adds a suggested clock time to every place plus the straight-line distance
    from the previous stop. The clock times are schedule suggestions (the
    per-hop travel itself is fetched on demand by the UI), so the frontend
    labels them "suggested", never as a live ETA.
    """
    if not points:
        return []

    food_only = bool(points) and all(p.get("category") == "food" for p in points)
    slots: dict[str, str] = {}
    food_seen = 0
    for p in points:
        slots[p["id"]] = _slot_for(p, food_seen, food_only)
        if p.get("category") == "food":
            food_seen += 1

    buckets: dict[str, list[dict]] = {}
    for p in points:
        buckets.setdefault(slots[p["id"]], []).append(p)

    chain: list[dict] = []
    for slot in sorted(buckets, key=lambda s: _SLOT_ORDER.get(s, 9)):
        chain.extend(_order_nearest_neighbour(buckets[slot]))

    first_slot = slots[chain[0]["id"]]
    cursor = _SLOT_START_MIN.get(first_slot, 9 * 60)
    out: list[dict] = []
    prev: dict | None = None
    for place in chain:
        slot = slots[place["id"]]
        travel_km = round(_h(prev, place), 2) if prev else 0.0
        travel_min = max(10, round(travel_km * 3.0)) if prev else 0
        arrival = max(cursor + travel_min, _SLOT_START_MIN.get(slot, 9 * 60))
        if place.get("category") == "food":
            meal_floor = {
                "morning": 8 * 60 + 30,
                "afternoon": 12 * 60 + 30,
                "evening": 17 * 60,
                "night": 19 * 60 + 30,
            }.get(slot, 12 * 60 + 30)
            arrival = max(arrival, meal_floor)
        end = arrival + max(20, int(place.get("duration_min") or 60))
        item = dict(place)
        item.update({
            "slot": slot,
            "suggested_start": _fmt_minutes(arrival),
            "suggested_end": _fmt_minutes(end),
            "travel_from_prev_km": travel_km,
        })
        out.append(item)
        cursor = end + _MEAL_GAP_MIN
        prev = place
    return out


def _day_theme(points: list[dict]) -> str:
    counts: dict[str, int] = {}
    for p in points:
        counts[p.get("category", "")] = counts.get(p.get("category", ""), 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
    labels = [_CATEGORY_LABELS.get(cat, cat.replace("_", " ")) for cat, _ in top if cat]
    if not labels:
        return "Mixed day"
    if len(labels) == 1:
        return labels[0].capitalize() + " day"
    return labels[0].capitalize() + " & " + labels[1]


def _order_nearest_neighbour(points: list[dict]) -> list[dict]:
    """Greedy nearest-neighbour TSP within a day to avoid backtracking (3.2)."""
    if len(points) <= 1:
        return list(points)
    pts = list(points)  # already score-descending; start from the best
    start = pts.pop(0)
    path = [start]
    while pts:
        last = path[-1]
        nxt = min(pts, key=lambda p: _hd(
            (last["lat"], last["lng"]), (p["lat"], p["lng"])))
        pts.remove(nxt)
        path.append(nxt)
    return path