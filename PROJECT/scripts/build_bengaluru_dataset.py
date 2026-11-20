"""Build the Bengaluru Trip Planner dataset from a curated highlights list.

The raw OpenStreetMap import (`import_bengaluru_places.py`) is useful for broad
coverage but is dominated by statues, plaques and unrated temples — bad input for
an itinerary. This builder keeps a hand-picked list of *real, notable* Bengaluru
places (category, description, visit length, suitability, best time) and resolves
each one against Google Maps through SerpAPI to attach real coordinates, rating,
review count, `place_id` and photo.

Rules (same as the rest of the app):
- Every numeric fact is either curated (marked `data_is_estimated`) or comes from
  the live SerpAPI response. Nothing is invented.
- A paid venue's fee stays *unknown* unless the curated seed already knows it;
  only genuinely free places are marked "free" with `entry_fee_known=True`.
- If SerpAPI cannot resolve a place, the builder skips it instead of guessing
  coordinates.

Usage (from PROJECT/):
    backend/venv/Scripts/python.exe scripts/build_bengaluru_dataset.py
    backend/venv/Scripts/python.exe scripts/build_bengaluru_dataset.py --offline
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_PATH = PROJECT_ROOT / "backend" / "data" / "trip_places_bengaluru.json"
CACHE_PATH = PROJECT_ROOT / "backend" / "data" / "trip_enrichment_cache.json"
CITY_CENTER = (12.9716, 77.5946)
MAX_DISTANCE_KM = 80.0

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

DURATION = {
    "nature": 90, "heritage": 60, "museum": 90, "food": 60, "shopping": 90,
    "nightlife": 120, "adventure": 180, "wellness": 90, "religious": 45,
    "offbeat": 60, "photo": 30,
}

CROWD = {
    "nature": {"early_morning": "low", "morning": "medium", "afternoon": "medium", "evening": "medium"},
    "heritage": {"morning": "medium", "afternoon": "medium", "evening": "high"},
    "museum": {"morning": "low", "afternoon": "medium"},
    "food": {"afternoon": "high", "evening": "high", "night": "medium"},
    "shopping": {"morning": "low", "afternoon": "medium", "evening": "high"},
    "nightlife": {"evening": "medium", "night": "high"},
    "adventure": {"morning": "medium", "afternoon": "high"},
    "wellness": {"early_morning": "low", "morning": "low", "evening": "low"},
    "religious": {"early_morning": "low", "morning": "high", "evening": "medium"},
    "offbeat": {"morning": "low", "afternoon": "low", "evening": "low"},
    "photo": {"early_morning": "low", "evening": "medium"},
}


def P(
    name: str,
    query: str,
    category: str,
    description: str,
    *,
    minutes: int | None = None,
    tags: tuple[str, ...] = (),
    family: bool = True,
    demand: bool = False,
    free: bool = False,
    fee: float = 0.0,
    fee_child: float | None = None,
    hours: str = "",
    access: str = "",
    best: tuple[str, ...] | None = None,
    crowd: dict[str, str] | None = None,
) -> dict:
    return {
        "name": name,
        "query": query,
        "category": category,
        "description": description,
        "duration_min": minutes or DURATION.get(category, 60),
        "tags": sorted(set((category, *tags))),
        "family_friendly": family,
        "physically_demanding": demand,
        "free": free,
        "entry_fee": fee,
        "entry_fee_child": fee_child,
        "opening_hours": hours,
        "accessibility_notes": access,
        # Deliberately empty when not supplied: the planner/UI labels missing
        # detail as unknown instead of pretending.
        "best_times": list(best) if best else [t for t in BEST_TIMES.get(category, ["morning"]) if t in CROWD.get(category, {})],
        "crowd": crowd or CROWD.get(category, {}),
    }


HIGHLIGHTS: list[dict] = [
    # ---------------------------------------------------------------- nature
    P("Lalbagh Botanical Garden", "Lalbagh Botanical Garden Bengaluru", "nature",
      "240-acre botanical garden with a glasshouse, century-old trees and a lake — best at sunrise when the walking paths are quiet.",
      minutes=120, tags=("photo", "walk"), access="Paved main loop; benches and shade throughout."),
    P("Cubbon Park", "Cubbon Park Bengaluru", "nature",
      "The city's green lung: 300 acres of lawns, bamboo groves and heritage buildings, ideal for an easy morning walk.",
      minutes=90, tags=("photo", "walk"), free=True, access="Flat, paved paths; wheelchair accessible."),
    P("Sankey Tank", "Sankey Tank Bengaluru", "nature",
      "A calm lake ringed by a 3 km walking path, popular for sunset and birdwatching in the city's north-west.",
      minutes=60, tags=("photo", "walk"), free=True, access="Level lakeside path."),
    P("Ulsoor Lake", "Ulsoor Lake Bengaluru", "nature",
      "One of Bengaluru's oldest lakes, with a boating jetty and a shaded perimeter walk in the middle of the city.",
      minutes=60, tags=("photo", "walk"), free=True),
    P("Hebbal Lake", "Hebbal Lake Bengaluru", "nature",
      "A birdwatcher's lake on the northern edge of the city — herons, egrets and cormorants at first light.",
      minutes=60, tags=("photo", "offbeat"), free=True, best=("early_morning", "morning")),
    P("Turahalli Forest", "Turahalli Forest Bengaluru", "nature",
      "The last scrub forest inside Bengaluru, with a short rocky trail, boulders and a quiet sunrise viewpoint.",
      minutes=120, tags=("adventure", "photo", "offbeat", "trek"), demand=True, free=True,
      access="Uneven rocky trail; not suitable for strollers.", best=("early_morning", "morning")),
    P("Big Banyan Tree", "Big Banyan Tree Bengaluru", "nature",
      "A 400-year-old banyan whose aerial roots spread over three acres — a cool, photogenic stop on the way to Mysuru.",
      minutes=60, tags=("photo", "offbeat", "heritage"), free=True),
    P("Hesaraghatta Grasslands", "Hesaraghatta Grasslands Bengaluru", "nature",
      "Open grasslands and a historic reservoir north of the city, known for raptors, silence and big skies.",
      minutes=90, tags=("photo", "offbeat", "birding"), free=True, best=("early_morning", "evening")),
    P("Nandi Hills", "Nandi Hills Bengaluru", "nature",
      "A hill fort an hour north of the city, famous for its sunrise sea of clouds, Tipu's Drop and old temple steps.",
      minutes=180, tags=("heritage", "photo", "adventure", "sunrise"), demand=True,
      access="Steep steps and ramps; go early and carry water.", best=("early_morning", "morning")),
    P("Bugle Rock Park", "Bugle Rock Park Basavanagudi Bengaluru", "nature",
      "A rocky park in Basavanagudi with an ancient watchtower rock, shaded paths and a local evening crowd.",
      minutes=45, tags=("heritage", "photo", "walk"), free=True),

    # ------------------------------------------------------------- heritage
    P("Bengaluru Palace", "Bengaluru Palace Bengaluru", "heritage",
      "A Tudor-style royal palace with wood-panelled halls, family portraits and sprawling grounds — the city's most photographed interior.",
      minutes=90, tags=("photo", "museum")),
    P("Tipu Sultan's Summer Palace", "Tipu Sultan's Summer Palace Bengaluru", "heritage",
      "A 1791 teak-and-plaster palace with carved balconies and a small museum on Tipu Sultan's rule.",
      minutes=45, tags=("photo", "museum")),
    P("Bangalore Fort", "Bangalore Fort Bengaluru", "heritage",
      "The surviving gateway and ramparts of Kempegowda's 16th-century mud fort, later rebuilt in stone by Tipu Sultan.",
      minutes=30, tags=("photo", "offbeat"), free=True),
    P("Vidhana Soudha", "Vidhana Soudha Bengaluru", "heritage",
      "The monumental Neo-Dravidian seat of Karnataka's legislature — view it from the lawns and photograph it floodlit at night.",
      minutes=30, tags=("photo",), best=("evening", "night")),
    P("Attara Kacheri", "Attara Kacheri Karnataka High Court Bengaluru", "heritage",
      "The crimson Neo-Classical High Court building facing Vidhana Soudha, one of the city's grandest colonial facades.",
      minutes=20, tags=("photo",)),
    P("Mayo Hall", "Mayo Hall Bengaluru", "heritage",
      "A restored 1883 colonial hall on MG Road with arched verandas and a pocket museum of city history.",
      minutes=30, tags=("photo", "offbeat")),
    P("Devanahalli Fort", "Devanahalli Fort Bengaluru", "heritage",
      "A sprawling ruined fort near the airport, with stone gateways, temples and almost no crowds.",
      minutes=90, tags=("photo", "offbeat"), demand=True),
    P("Freedom Park", "Freedom Park Bengaluru", "heritage",
      "A former central jail turned public park, with the old prison walls, watchtowers and a graffiti-scarred facade.",
      minutes=60, tags=("photo", "offbeat", "walk"), free=True),

    # ------------------------------------------------------------ religious
    P("Bull Temple", "Bull Temple Basavanagudi Bengaluru", "religious",
      "Home to a 4.5 m monolithic Nandi bull carved in the 16th century — a Basavanagudi landmark, especially during Kadalekai Parishe.",
      minutes=45, tags=("heritage", "photo"), free=True),
    P("ISKCON Bengaluru", "ISKCON Temple Rajajinagar Bengaluru", "religious",
      "A hilltop Krishna temple complex with a grand hall, rooftop views and an evening aarti worth timing a visit for.",
      minutes=60, tags=("photo", "heritage"), free=True, best=("morning", "evening")),
    P("Dodda Ganesha Temple", "Dodda Ganesha Temple Basavanagudi Bengaluru", "religious",
      "A huge seated Ganesha idol beside the Bull Temple, smeared with red kumkuma and busy on weekends.",
      minutes=30, tags=("heritage",), free=True),
    P("Kadu Malleshwara Temple", "Kadu Malleshwara Temple Malleshwaram Bengaluru", "religious",
      "A 17th-century Shiva temple in Malleshwaram, the anchor of the old neighbourhood's temple street.",
      minutes=30, tags=("heritage", "offbeat"), free=True),
    P("Someshwara Temple, Halasuru", "Someshwara Swamy Temple Halasuru Bengaluru", "religious",
      "A Chola-era Shiva temple with intricate granite pillars and a quiet courtyard hidden behind Ulsoor's markets.",
      minutes=30, tags=("heritage", "photo", "offbeat"), free=True),
    P("St. Mary's Basilica", "St. Mary's Basilica Bengaluru", "religious",
      "A Gothic revival basilica near Shivajinagar, famous for its stained glass and the September St. Mary's Feast.",
      minutes=30, tags=("heritage", "photo"), free=True),
    P("Infant Jesus Church", "Infant Jesus Church Viveknagar Bengaluru", "religious",
      "One of Bengaluru's most visited churches, known for its votive offerings and Thursday novena crowds.",
      minutes=30, tags=("heritage",), free=True),
    P("Tawakkal Mastan Dargah", "Tawakkal Mastan Dargah Bengaluru", "religious",
      "A 17th-century Sufi dargah in Cottonpet, visited across faiths and especially during the Karaga procession.",
      minutes=30, tags=("heritage", "offbeat"), free=True),

    # --------------------------------------------------------------- museum
    P("Visvesvaraya Industrial & Technological Museum", "Visvesvaraya Industrial and Technological Museum Bengaluru", "museum",
      "Hands-on science galleries, a real Wright Flyer replica and engine exhibits — the city's best rainy-day family stop.",
      minutes=120, tags=("family",)),
    P("HAL Heritage Centre & Aerospace Museum", "HAL Heritage Centre and Aerospace Museum Bengaluru", "museum",
      "Aircraft, engines and flight simulators tracing India's aerospace history, with real fighters parked outside.",
      minutes=90, tags=("family", "photo")),
    P("National Gallery of Modern Art", "National Gallery of Modern Art Bengaluru", "museum",
      "Modern and contemporary Indian art in a restored heritage mansion on Palace Road, with a peaceful sculpture garden.",
      minutes=90, tags=("photo", "heritage")),
    P("Indian Music Experience Museum", "Indian Music Experience Museum Bengaluru", "museum",
      "An interactive music museum in JP Nagar where you can play instruments, trace ragas and record a track.",
      minutes=120, tags=("family", "photo")),
    P("Museum of Art & Photography (MAP)", "Museum of Art and Photography Bengaluru", "museum",
      "A contemporary museum on Kasturba Road with rotating photography and art shows and a good cafe.",
      minutes=90, tags=("photo", "food")),
    P("Kempegowda Museum", "Kempegowda Museum Bengaluru", "museum",
      "A small museum in Mayo Hall telling the story of Bengaluru's founder, Kempegowda, and the city's early layout.",
      minutes=45, tags=("heritage", "offbeat")),
    P("Bangalore Aquarium", "Bangalore Aquarium Cubbon Park Bengaluru", "museum",
      "A compact aquarium beside Cubbon Park with freshwater and marine tanks — a quick stop with kids.",
      minutes=45, tags=("family",)),
    P("Rangoli Metro Art Center", "Rangoli Metro Art Center MG Road Bengaluru", "museum",
      "A free art space under the MG Road metro viaduct with murals, installations and rotating exhibitions.",
      minutes=45, tags=("museum", "offbeat"), free=True),

    # ----------------------------------------------------------------- food
    P("Mavalli Tiffin Rooms (MTR)", "MTR Restaurant Lalbagh Road Bengaluru", "food",
      "The 1924 Lalbagh Road institution: rava idli, masala dosa and filter coffee served in a fast-moving heritage dining hall.",
      minutes=60, tags=("heritage", "breakfast"), hours="Open ~6:30 AM - 9:00 PM; closed some festival days"),
    P("V V Puram Food Street", "V V Puram Food Street Bengaluru", "food",
      "A short lane of legendary street stalls — idli, dosa, pav bhaji and holige — best visited hungry after 6 PM.",
      minutes=60, tags=("street_food", "offbeat"), best=("evening", "night")),
    P("CTR (Shri Sagar CTR)", "Central Tiffin Room Malleshwaram Bengaluru", "food",
      "Malleshwaram's beloved benne masala dosa joint, crisp and butter-heavy, with a queue that moves fast.",
      minutes=45, tags=("breakfast", "heritage"), hours="Open ~7:30 AM - 12:30 PM, 4:00 PM - 9:00 PM"),
    P("Brahmin's Coffee Bar", "Brahmin's Coffee Bar Basavanagudi Bengaluru", "food",
      "A tiny, cash-only Basavanagudi counter famous for idli, vada and some of the city's best filter coffee.",
      minutes=30, tags=("breakfast", "offbeat"), best=("early_morning", "morning"), hours="Open ~6:30 AM - 12:00 PM"),
    P("Vidyarthi Bhavan", "Vidyarthi Bhavan Basavanagudi Bengaluru", "food",
      "A 1943 Basavanagudi institution serving thick, ghee-laden dosas with chutney — expect a queue at breakfast.",
      minutes=45, tags=("breakfast", "heritage"), hours="Open ~6:30 AM - 11:30 AM, 2:00 PM - 8:00 PM"),
    P("The Rameshwaram Cafe", "The Rameshwaram Cafe Indiranagar Bengaluru", "food",
      "A wildly popular Indiranagar counter for ghee podi idli, rava dosa and quick filter coffee.",
      minutes=45, tags=("breakfast", "street_food")),
    P("Toit Brewpub", "Toit Brewpub Indiranagar Bengaluru", "food",
      "Indiranagar's original craft brewery, with house-brewed beers, wood-fired pizzas and a lively upstairs bar.",
      minutes=120, tags=("nightlife", "brewery"), family=False, best=("evening", "night")),
    P("Truffles", "Truffles Koramangala Bengaluru", "food",
      "The student-favourite burger joint off 5th Block, known for its all-American burgers and thick shakes.",
      minutes=60, tags=("fast_food",)),
    P("Empire Restaurant", "Empire Restaurant Bengaluru", "food",
      "A late-night Bengaluru chain for kebabs, biryani and roomali roti — reliable after a long evening.",
      minutes=60, tags=("biryani", "late_night"), best=("evening", "night")),
    P("Koshy's", "Koshy's Restaurant Bengaluru", "food",
      "A colonial-era coffee house on St. Mark's Road where politicians and writers still argue over breakfast.",
      minutes=60, tags=("heritage", "breakfast")),
    P("Airlines Hotel", "Airlines Hotel Bengaluru", "food",
      "A leafy old-school garden restaurant on Lavelle Road, famous for dosas, filter coffee and unhurried mornings.",
      minutes=60, tags=("heritage", "breakfast"), best=("early_morning", "morning")),
    P("Corner House Ice Cream", "Corner House Ice Cream Residency Road Bengaluru", "food",
      "A Bengaluru dessert institution — order a Death by Chocolate and share it.",
      minutes=30, tags=("dessert", "family"), best=("evening", "night")),
    P("Shivaji Military Hotel", "Shivaji Military Hotel Jayanagar Bengaluru", "food",
      "A no-frills Jayanagar institution for donne biryani and mutton dishes, served fast and cheap.",
      minutes=45, tags=("biryani", "heritage"), family=False),
    P("Albert Bakery", "Albert Bakery Frazer Town Bengaluru", "food",
      "A Frazer Town bakery legendary for its evening kebabs and mutton samosas, sold from a tiny counter.",
      minutes=30, tags=("street_food", "offbeat"), best=("evening", "night")),

    # ------------------------------------------------------------- shopping
    P("Commercial Street", "Commercial Street Bengaluru", "shopping",
      "The city's classic shopping street — sarees, jewellery, shoes and street snacks packed into a pedestrian-friendly stretch.",
      minutes=90),
    P("Chickpete & K.R. Market", "K R Market Bengaluru", "shopping",
      "Bengaluru's oldest market district: flower stalls, wholesale produce and a maze of old trading lanes.",
      minutes=90, tags=("photo", "offbeat", "heritage"), best=("early_morning", "morning")),
    P("UB City Mall", "UB City Mall Bengaluru", "shopping",
      "The luxury mall on Vittal Mallya Road with high-end brands, art installations and rooftop restaurants.",
      minutes=90, tags=("food", "nightlife")),
    P("Phoenix Marketcity", "Phoenix Marketcity Whitefield Bengaluru", "shopping",
      "A large Whitefield mall with international brands, a food court and a multiplex.",
      minutes=120, tags=("family",)),
    P("Orion Mall", "Orion Mall Rajajinagar Bengaluru", "shopping",
      "A big north-Bengaluru mall beside Sankey Tank, with brands, cinema and lakeside dining.",
      minutes=120, tags=("family",)),
    P("Forum Mall Koramangala", "Forum Mall Koramangala Bengaluru", "shopping",
      "The original Koramangala mall, still a convenient stop for shopping and a movie.",
      minutes=90),
    P("Brigade Road", "Brigade Road Bengaluru", "shopping",
      "A central shopping stretch linking MG Road and Residency Road, good for brands, cafes and people-watching.",
      minutes=60),
    P("Avenue Road", "Avenue Road Bengaluru", "shopping",
      "A busy old-city lane for books, brassware, temple supplies and second-hand treasures.",
      minutes=60, tags=("offbeat", "heritage", "photo")),
    P("Cauvery Handicrafts Emporium", "Cauvery Handicrafts Emporium Bengaluru", "shopping",
      "A Karnataka government emporium for sandalwood, silk, rosewood and handmade souvenirs.",
      minutes=45, tags=("heritage",)),

    # ------------------------------------------------------------ nightlife
    P("Indiranagar 100 Feet Road", "100 Feet Road Indiranagar Bengaluru", "nightlife",
      "The spine of Indiranagar's nightlife — bars, breweries and restaurants within a short walk of each other.",
      minutes=120, tags=("food",), family=False, best=("evening", "night")),
    P("Koramangala 5th Block", "Koramangala 5th Block Bengaluru", "nightlife",
      "Bengaluru's densest cluster of pubs, cafes and late-night eats, favourite of students and startup crews.",
      minutes=120, tags=("food",), family=False, best=("evening", "night")),
    P("Church Street", "Church Street Bengaluru", "nightlife",
      "A leafy central street of bookshops, cafes and bars, closed to traffic on weekends.",
      minutes=90, tags=("food", "shopping"), family=False, best=("evening", "night")),
    P("Byg Brewski Brewing Company", "Byg Brewski Brewing Company Hennur Bengaluru", "nightlife",
      "A huge open-air brewery with a lake, live music and long tables — a full evening out.",
      minutes=150, tags=("food", "brewery"), family=False, best=("evening", "night")),
    P("Skyye Lounge", "Skyye Lounge UB City Bengaluru", "nightlife",
      "A rooftop lounge on UB City's upper floors with city views and a cocktail-heavy menu.",
      minutes=120, tags=("food", "photo"), family=False, best=("evening", "night")),

    # ------------------------------------------------------------- wellness
    P("Art of Living International Centre", "Art of Living International Centre Bengaluru", "wellness",
      "A vast ashram campus on Kanakapura Road with meditation halls, gardens and yoga programmes.",
      minutes=120, tags=("nature", "photo")),
    P("Pyramid Valley International", "Pyramid Valley International Bengaluru", "wellness",
      "A meditation campus near Harohalli built around a large granite pyramid, with quiet gardens and vegetarian food.",
      minutes=120, tags=("offbeat", "photo")),
    P("Ayurveda Gram", "Ayurvedagram Bengaluru", "wellness",
      "A wellness retreat on the city's edge offering Ayurvedic consultations, treatments and a peaceful campus.",
      minutes=120, tags=("nature",)),

    # ------------------------------------------------------------ adventure
    P("Wonderla Amusement Park", "Wonderla Amusement Park Bengaluru", "adventure",
      "Bengaluru's big amusement and water park on Mysuru Road — plan a full day and carry a change of clothes.",
      minutes=300, tags=("family", "water_park")),
    P("Bannerghatta Biological Park", "Bannerghatta Biological Park Bengaluru", "adventure",
      "Zoo, safari and butterfly park on the city's southern edge, where a safari bus passes big cats in large enclosures.",
      minutes=240, tags=("nature", "family", "wildlife")),
    P("Snow City", "Snow City Bengaluru", "adventure",
      "An indoor snow park with slides and snow play on Jayamahal Road — a quick, air-conditioned family break.",
      minutes=120, tags=("family",)),
]


def _norm(name: str) -> str:
    text = (name or "").lower().replace("bangalore", "bengaluru")
    text = re.sub(r"\bft\b", "feet", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _tokens(name: str) -> set[str]:
    stop = {"the", "bengaluru", "bangalore", "and", "of", "at", "in", "road", "street"}
    return {t for t in _norm(name).split() if len(t) > 2 and t not in stop}


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve(client, query: str, cache: dict, offline: bool) -> dict | None:
    # A cached `None` is a previous transient failure (timeout/quota), not a
    # permanent fact — retry it on the next run unless we are offline.
    if query in cache and (cache[query] is not None or offline):
        return cache[query]
    if offline:
        return None
    result = None
    for _ in range(2):
        result = client.search_place(query, lat=CITY_CENTER[0], lng=CITY_CENTER[1])
        if result and result.get("lat") and result.get("lng"):
            break
        time.sleep(1.2)
    if result and result.get("lat") and result.get("lng"):
        lat, lng = float(result["lat"]), float(result["lng"])
        if _haversine_km(CITY_CENTER[0], CITY_CENTER[1], lat, lng) > MAX_DISTANCE_KM:
            print(f"  ! {query}: resolved {lat:.4f},{lng:.4f} is outside Bengaluru — skipped")
            result = None
        elif not (_tokens(query) & _tokens(result.get("title", ""))):
            print(f"  ! {query}: got '{result.get('title')}' — name mismatch, skipped")
            result = None
    cache[query] = result
    _save_cache(cache)
    time.sleep(0.35)
    return result


def _seed_by_name() -> dict[str, dict]:
    from backend.services.trip_seed import _BENGALURU_PLACES

    out: dict[str, dict] = {}
    for place in _BENGALURU_PLACES:
        out[_norm(place.name)] = place.model_dump()
    return out


def _build_place(highlight: dict, resolved: dict, seed: dict | None) -> dict:
    name = highlight["name"]
    category = highlight["category"]
    seed = seed or {}
    free = highlight["free"]
    fee = float(seed.get("entry_fee") or 0.0)
    fee_known = bool(seed.get("entry_fee_known", False))
    if free:
        fee, fee_known = 0.0, True
    elif fee and not fee_known:
        fee_known = True
    elif not fee:
        fee_known = False
    child = seed.get("entry_fee_child", highlight.get("entry_fee_child"))

    rating = resolved.get("rating")
    rating_known = isinstance(rating, (int, float)) and 0 < float(rating) <= 5
    reviews = resolved.get("reviews")
    review_count = int(reviews) if isinstance(reviews, (int, float)) else 0

    return {
        "id": seed.get("id") or f"bg_{category}_{_slug(name)}",
        "osm_id": seed.get("osm_id", ""),
        "place_id": resolved.get("place_id") or "",
        "name": name,
        "category": category,
        "description": seed.get("description") or highlight["description"],
        "address": resolved.get("address") or seed.get("address", ""),
        "photo_url": resolved.get("thumbnail") or "",
        "duration_min": int(seed.get("duration_min") or highlight["duration_min"]),
        "duration_known": True,
        "entry_fee": fee,
        "entry_fee_child": child,
        "entry_fee_known": fee_known,
        "rating": round(float(rating), 2) if rating_known else 0.0,
        "rating_known": rating_known,
        "review_count": review_count,
        "best_times": seed.get("best_times") or highlight["best_times"],
        "crowd": seed.get("crowd") or highlight["crowd"],
        "opening_hours": seed.get("opening_hours") or highlight["opening_hours"],
        "weekly_closures": seed.get("weekly_closures", []),
        "lat": round(float(resolved["lat"]), 6),
        "lng": round(float(resolved["lng"]), 6),
        "tags": sorted(set(seed.get("tags") or highlight["tags"])),
        "family_friendly": bool(seed.get("family_friendly", highlight["family_friendly"])),
        "physically_demanding": bool(seed.get("physically_demanding", highlight["physically_demanding"])),
        "accessibility_notes": seed.get("accessibility_notes") or highlight["accessibility_notes"],
        "destination": "bengaluru",
        "data_source": "curated",
        "data_is_estimated": True,
        "verified": bool(resolved.get("place_id")),
        "suitability": {
            "family_kids": bool(highlight["family_friendly"]),
            "seniors": not bool(highlight["physically_demanding"]),
            "physical_demand": bool(highlight["physically_demanding"]),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Bengaluru trip dataset.")
    parser.add_argument("--offline", action="store_true", help="Use only the enrichment cache.")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    from backend.services.clients.serpapi_client import SerpAPIClient

    client = SerpAPIClient(timeout_s=20.0)
    cache = _load_cache()
    seeds = _seed_by_name()

    places: list[dict] = []
    missing: list[str] = []
    for i, highlight in enumerate(HIGHLIGHTS, start=1):
        print(f"[{i}/{len(HIGHLIGHTS)}] {highlight['name']}", flush=True)
        resolved = _resolve(client, highlight["query"], cache, args.offline)
        if not resolved:
            missing.append(highlight["name"])
            continue
        seed = seeds.get(_norm(highlight["name"]))
        places.append(_build_place(highlight, resolved, seed))

    # Merge the curated seed's real fees/hours into the enriched highlights and
    # append any seed place the highlight list missed (matched by name OR by
    # proximity + shared tokens, so "Bangalore Palace" and "Bengaluru Palace"
    # do not become two pins).
    for name, seed in seeds.items():
        best = None
        best_score = -1e9
        for place in places:
            dist = _haversine_km(seed["lat"], seed["lng"], place["lat"], place["lng"])
            overlap = len(_tokens(seed["name"]) & _tokens(place["name"]))
            same = _norm(seed["name"]) == _norm(place["name"])
            # Distinctive names (Bull Temple) may sit 2 km from the seed's
            # rounded coordinates; generic one-word overlaps still need to be
            # physically close to avoid merging two different places.
            if not same and not (overlap >= 2 or (overlap >= 1 and dist <= 0.6)):
                continue
            score = overlap + (3 if same else 0) - dist
            if score > best_score:
                best, best_score = place, score
        if best is None:
            places.append(seed)
            continue
        for key in ("entry_fee", "entry_fee_child", "entry_fee_known", "opening_hours",
                    "weekly_closures", "description", "accessibility_notes"):
            if key in ("entry_fee", "entry_fee_child", "entry_fee_known"):
                if seed.get(key) and not best.get(key):
                    best[key] = seed[key]
            elif seed.get(key) and not best.get(key):
                best[key] = seed[key]
        if seed.get("photo_url") and not best.get("photo_url"):
            best["photo_url"] = seed["photo_url"]
        if seed.get("place_id") and not best.get("place_id"):
            best["place_id"] = seed["place_id"]

    # Final safety: drop near-duplicate names that survived the merge.
    deduped: list[dict] = []
    for place in places:
        dup = any(
            _haversine_km(place["lat"], place["lng"], other["lat"], other["lng"]) < 0.15
            and _norm(place["name"]) == _norm(other["name"])
            for other in deduped
        )
        if not dup:
            deduped.append(place)
    places = deduped

    places.sort(key=lambda p: (p["category"], p["name"]))
    counts = Counter(p["category"] for p in places)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bbox": [12.85, 77.45, 13.10, 77.75],
        "source_note": (
            "Curated Bengaluru highlights. Coordinates, ratings and photos come "
            "from Google Maps via SerpAPI when resolved; descriptions, durations "
            "and crowd windows are curated estimates (data_is_estimated=True)."
        ),
        "counts": {"total": len(places), "by_category": dict(sorted(counts.items()))},
        "places": places,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(places)} places -> {args.out}")
    print("By category:", dict(sorted(counts.items())))
    if missing:
        print(f"Unresolved ({len(missing)}):", ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
