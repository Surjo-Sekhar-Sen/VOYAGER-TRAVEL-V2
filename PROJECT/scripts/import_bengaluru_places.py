"""Import a broad, real Bengaluru place pool from OpenStreetMap.

NOTE (Trip 2.0): this raw OSM pass is a *fallback / research* tool. Its output
is dominated by statues, plaques and unrated temples, so the shipped dataset is
built by `scripts/build_bengaluru_dataset.py` instead (curated highlights +
live SerpAPI ratings/photos). Running this script will overwrite that dataset.

This is a one-time enrichment script, not part of the request path. It queries
the Overpass API for the categories the Trip Planner needs, maps OSM tags to the
planner's `TripPlace` shape, merges the curated seed places as overrides, and
writes `backend/data/trip_places_bengaluru.json`.

Rules:
- Only real OSM/curated data. Missing rating/fee/duration stays "unknown".
- Imported entries are `data_source="osm"` and `data_is_estimated=True`.
- Curated entries win on a name/coordinate match.
- No API keys are required for Overpass.

Usage (from PROJECT/):
    .\\backend\\venv\\Scripts\\python.exe scripts\\import_bengaluru_places.py
    .\\backend\\venv\\Scripts\\python.exe scripts\\import_bengaluru_places.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_PATH = PROJECT_ROOT / "backend" / "data" / "trip_places_bengaluru.json"

# Central Bengaluru urban area. Nearby getaways (Nandi, Bannerghatta, etc.)
# are already covered by the curated seed; keeping this box tight makes the
# Overpass import finish in minutes instead of tens of minutes.
BBOX = (12.85, 77.45, 13.10, 77.75)
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
HEADERS = {"User-Agent": "VoyagerTripPlanner/2.0 (local dev; place import)"}

QUERIES: dict[str, str] = {
    "attractions": (
        'nwr["tourism"~"^(attraction|museum|gallery|viewpoint|zoo|theme_park|artwork)$"]'
    ),
    "parks": 'nwr["leisure"~"^(park|garden|nature_reserve)$"]',
    "historic": 'nwr["historic"]',
    "worship": 'nwr["amenity"="place_of_worship"]',
    "markets": (
        '(nwr["amenity"="marketplace"];'
        'nwr["shop"~"^(mall|department_store)$"];)'
    ),
    "culture": (
        '(nwr["amenity"~"^(arts_centre|theatre|cinema|planetarium)$"];'
        'nwr["tourism"="gallery"];)'
    ),
    "food": (
        '(nwr["amenity"~"^(restaurant|cafe|food_court|ice_cream)$"];'
        'nwr["shop"~"^(bakery|confectionery)$"];)'
    ),
    "nightlife": (
        '(nwr["amenity"~"^(bar|pub|nightclub)$"];'
        'nwr["leisure"="dance"];)'
    ),
}

# Keep the food/nightlife import useful without downloading every cafe in the city.
MAX_PER_QUERY = {
    "attractions": 500,
    "parks": 300,
    "historic": 400,
    "worship": 400,
    "markets": 250,
    "culture": 250,
    "food": 500,
    "nightlife": 250,
}

CATEGORY_LABELS = {
    "nature": "nature spot",
    "heritage": "heritage site",
    "food": "food spot",
    "adventure": "adventure spot",
    "shopping": "shopping area",
    "nightlife": "nightlife spot",
    "wellness": "wellness spot",
    "offbeat": "offbeat spot",
    "religious": "religious site",
    "museum": "museum or gallery",
    "photo": "photo spot",
}

BEST_TIMES = {
    "nature": ["early_morning", "morning", "evening"],
    "heritage": ["morning", "afternoon", "evening"],
    "museum": ["morning", "afternoon"],
    "food": ["afternoon", "evening", "night"],
    "shopping": ["morning", "afternoon", "evening"],
    "nightlife": ["evening", "night"],
    "adventure": ["morning", "afternoon"],
    "wellness": ["early_morning", "morning", "evening"],
    "religious": ["early_morning", "morning", "evening"],
    "offbeat": ["morning", "afternoon", "evening"],
    "photo": ["early_morning", "evening"],
}

DURATIONS = {
    "nature": 90,
    "heritage": 60,
    "museum": 90,
    "food": 60,
    "shopping": 90,
    "nightlife": 120,
    "adventure": 180,
    "wellness": 90,
    "religious": 45,
    "offbeat": 60,
    "photo": 30,
}

PRIORITY = {"curated": 3, "google": 2, "osm": 1}


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _coords(element: dict) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def _classify(tags: dict[str, str]) -> tuple[str, list[str]] | None:
    amenity = (tags.get("amenity") or "").lower()
    shop = (tags.get("shop") or "").lower()
    tourism = (tags.get("tourism") or "").lower()
    leisure = (tags.get("leisure") or "").lower()
    historic = (tags.get("historic") or "").lower()

    if amenity in ("bar", "pub", "nightclub") or leisure == "dance":
        return "nightlife", ["nightlife", "food"]
    if amenity in ("restaurant", "cafe", "food_court", "ice_cream") or shop in ("bakery", "confectionery"):
        return "food", ["food"]
    if amenity == "place_of_worship":
        return "religious", ["religious", "heritage", "photo"]
    if amenity == "marketplace":
        return "shopping", ["shopping", "food", "photo", "offbeat"]
    if shop in ("mall", "department_store"):
        return "shopping", ["shopping", "food"]
    if tourism in ("museum", "gallery") or amenity in ("arts_centre", "theatre", "cinema", "planetarium"):
        return "museum", ["museum", "photo", "heritage"]
    if leisure in ("park", "garden", "nature_reserve"):
        return "nature", ["nature", "wellness", "photo"]
    if tourism in ("zoo", "theme_park"):
        return "adventure", ["adventure", "nature"]
    if historic:
        return "heritage", ["heritage", "photo", "offbeat"]
    if tourism == "viewpoint":
        return "photo", ["photo", "nature"]
    if tourism in ("attraction", "artwork"):
        return "photo", ["photo", "heritage", "offbeat"]
    return None


def _completeness(tags: dict[str, str]) -> int:
    keys = ("wikidata", "wikipedia", "website", "phone", "opening_hours",
            "description", "addr:street", "image", "name:en")
    return sum(1 for k in keys if tags.get(k))


def _to_place(element: dict[str, Any], source: str) -> dict | None:
    tags = element.get("tags") or {}
    name = (tags.get("name:en") or tags.get("name") or "").strip()
    if not name:
        return None
    coords = _coords(element)
    if not coords:
        return None
    lat, lng = coords
    if not (BBOX[0] <= lat <= BBOX[2] and BBOX[1] <= lng <= BBOX[3]):
        return None
    classified = _classify(tags)
    if not classified:
        return None
    category, interest_tags = classified

    osm_id = f"{element.get('type', 'node')[0]}{element.get('id')}"
    description = (tags.get("description") or "").strip()
    if not description:
        description = f"{CATEGORY_LABELS.get(category, 'place')} in Bengaluru (OpenStreetMap)."

    fee_tag = (tags.get("fee") or "").lower()
    entry_fee_known = fee_tag == "no"
    family_friendly = category != "nightlife"
    physically_demanding = False
    opening_hours = (tags.get("opening_hours") or "").strip()

    return {
        "id": f"osm_{osm_id}",
        "osm_id": osm_id,
        "place_id": "",
        "name": name,
        "category": category,
        "description": description,
        "duration_min": DURATIONS.get(category, 60),
        "duration_known": False,
        "entry_fee": 0.0,
        "entry_fee_child": None,
        "entry_fee_known": entry_fee_known,
        "rating": 0.0,
        "rating_known": False,
        "review_count": 0,
        "best_times": BEST_TIMES.get(category, ["morning", "afternoon"]),
        "crowd": {},
        "opening_hours": opening_hours,
        "weekly_closures": [],
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "tags": sorted(set(interest_tags)),
        "family_friendly": family_friendly,
        "physically_demanding": physically_demanding,
        "accessibility_notes": "",
        "destination": "bengaluru",
        "data_source": source,
        "data_is_estimated": True,
        "suitability": {
            "family_kids": family_friendly,
            "seniors": not physically_demanding,
            "physical_demand": physically_demanding,
        },
        "verified": False,
        "_completeness": _completeness(tags),
    }


def _fetch_overpass(query: str, timeout_s: int = 90) -> list[dict]:
    body = {"data": query}
    last_exc: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            t0 = time.time()
            print(f"  POST {url} ...", flush=True)
            resp = requests.post(url, data=body, headers=HEADERS, timeout=timeout_s)
            print(f"  <- {resp.status_code} in {time.time() - t0:.1f}s", flush=True)
            if resp.status_code == 200:
                return resp.json().get("elements", [])
            last_exc = RuntimeError(f"{url} -> HTTP {resp.status_code}: {resp.text[:160]}")
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            print(f"  ! {type(exc).__name__}: {exc}", flush=True)
    if last_exc:
        print(f"  ! overpass failed: {last_exc}", flush=True)
    return []


def _query_for(selector: str) -> str:
    return (
        "[out:json][timeout:90];"
        f"({selector}({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]}););"
        "out center tags 500;"
    )


def _load_curated() -> list[dict]:
    from backend.services.trip_seed import _BENGALURU_PLACES

    out = []
    for p in _BENGALURU_PLACES:
        item = p.model_dump()
        item.update({
            "data_source": "curated",
            "data_is_estimated": True,
            "verified": True,
            "rating_known": True,
            "entry_fee_known": True,
            "duration_known": True,
            "place_id": item.get("place_id") or "",
            "osm_id": item.get("osm_id") or "",
            "suitability": {
                "family_kids": bool(p.family_friendly),
                "seniors": not bool(p.physically_demanding),
                "physical_demand": bool(p.physically_demanding),
            },
        })
        out.append(item)
    return out


def _dedupe(places: list[dict]) -> list[dict]:
    """Keep one entry per name+area; curated > google > osm."""
    kept: list[dict] = []
    for place in sorted(places, key=lambda p: PRIORITY.get(p.get("data_source", "osm"), 0), reverse=True):
        name = _norm_name(place.get("name", ""))
        duplicate = False
        for existing in kept:
            if _norm_name(existing.get("name", "")) != name:
                continue
            if _haversine_km(
                float(place["lat"]), float(place["lng"]),
                float(existing["lat"]), float(existing["lng"]),
            ) <= 0.12:
                duplicate = True
                break
        if not duplicate:
            kept.append(place)
    return kept


def _merge_curated(osm_places: list[dict], curated: list[dict]) -> list[dict]:
    merged = list(osm_places)
    for c in curated:
        cname = _norm_name(c.get("name", ""))
        replaced = False
        for i, p in enumerate(merged):
            if _norm_name(p.get("name", "")) != cname:
                continue
            if _haversine_km(
                float(c["lat"]), float(c["lng"]),
                float(p["lat"]), float(p["lng"]),
            ) <= 0.2:
                merged[i] = c
                replaced = True
                break
        if not replaced:
            merged.append(c)
    return merged


def _clean(place: dict) -> dict:
    place.pop("_completeness", None)
    return place


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Bengaluru places from OSM Overpass.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch/merge but do not write the JSON.")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    print(f"Overpass bbox: {BBOX}", flush=True)
    all_places: list[dict] = []
    for name, selector in QUERIES.items():
        print(f"- querying {name} ...", flush=True)
        elements = _fetch_overpass(_query_for(selector))
        converted = [p for e in elements if (p := _to_place(e, "osm"))]
        converted.sort(key=lambda p: p.get("_completeness", 0), reverse=True)
        cap = MAX_PER_QUERY.get(name, 500)
        converted = converted[:cap]
        print(f"  {len(elements)} raw -> {len(converted)} kept", flush=True)
        all_places.extend(converted)

    curated = _load_curated()
    print(f"- curated overrides: {len(curated)}", flush=True)
    places = _dedupe(_merge_curated(all_places, curated))
    places = [_clean(p) for p in places]
    places.sort(key=lambda p: (p.get("category", ""), p.get("name", "")))

    counts = Counter(p.get("category", "unknown") for p in places)
    sources = Counter(p.get("data_source", "unknown") for p in places)
    print(f"Total places: {len(places)}", flush=True)
    print("By category:", dict(sorted(counts.items())), flush=True)
    print("By source:", dict(sorted(sources.items())), flush=True)

    if args.dry_run:
        print("Dry run — not writing.", flush=True)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bbox": BBOX,
        "counts": {"total": len(places), "by_category": dict(counts), "by_source": dict(sources)},
        "places": places,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
