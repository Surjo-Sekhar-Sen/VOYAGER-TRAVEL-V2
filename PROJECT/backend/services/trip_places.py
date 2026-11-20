"""Trip Planner place-data loader.

The curated seed (`trip_seed.py`) remains the source of truth for the original
23 high-quality Bengaluru entries. `scripts/import_bengaluru_places.py` writes a
broader OSM-backed pool to `backend/data/trip_places_bengaluru.json`; this module
loads that file once, validates each row into `TripPlace`, and falls back to the
curated seed when the file is missing or unreadable.

Nothing here invents data: rows with missing ratings/fees/durations keep their
`*_known=False` flags and are treated as neutral by the ranking engine.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from .. import config
from . import trip_seed
from .data_schema import TripPlace

logger = logging.getLogger(__name__)

DATA_PATH = config.PROJECT_ROOT / "backend" / "data" / "trip_places_bengaluru.json"


def _read_rows(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        rows = raw.get("places", [])
    else:
        rows = raw
    return rows if isinstance(rows, list) else []


@lru_cache(maxsize=1)
def _load_bengaluru() -> tuple[list[TripPlace], str]:
    """Return (places, source_label). Cached for the process lifetime."""
    if DATA_PATH.exists():
        try:
            rows = _read_rows(DATA_PATH)
            places: list[TripPlace] = []
            for row in rows:
                try:
                    places.append(TripPlace(**row))
                except Exception as exc:  # one bad row must not kill the pool
                    logger.warning("trip_places: skipping invalid row: %s", exc)
            if places:
                logger.info("trip_places: loaded %d places from %s", len(places), DATA_PATH.name)
                return places, "imported"
            logger.warning("trip_places: %s contained no valid places; using seed", DATA_PATH)
        except Exception as exc:
            logger.warning("trip_places: could not read %s: %s; using seed", DATA_PATH, exc)

    return list(trip_seed._BENGALURU_PLACES), "seed"


def places_for_destination(slug: str) -> list[TripPlace]:
    """Candidate pool for a destination slug. Only Bengaluru is populated."""
    if slug == "bengaluru":
        return list(_load_bengaluru()[0])
    return []


def source_label() -> str:
    return _load_bengaluru()[1]


def dataset_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for place in _load_bengaluru()[0]:
        counts[place.category] = counts.get(place.category, 0) + 1
    return counts


def destinations() -> list[dict]:
    """Destination catalogue with the live pool size for Bengaluru."""
    out = trip_seed.destinations()
    count = len(_load_bengaluru()[0])
    for dest in out:
        if dest.get("slug") == "bengaluru":
            dest["place_count"] = count
            dest["data_source"] = _load_bengaluru()[1]
    return out


def refresh() -> None:
    """Drop the cache so a newly imported JSON file is picked up without a restart."""
    _load_bengaluru.cache_clear()
