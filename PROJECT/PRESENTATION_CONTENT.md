# VOYAGER v2 — Presentation Content

A multimodal travel & trip-planning platform for Bengaluru.
Built React + FastAPI + GraphHopper, real data only, deploy-ready.

---

## 1. Project Overview

**What it is**
VOYAGER v2 is a single-page web app that helps you get around Bengaluru —
and plan a whole trip to it — using **real transport data**, **real reviews**,
**live weather/trains/news**, and a fair, explainable ranking engine.

**The three working modules (bottom-nav tabs)**
1. **Search** — find/discover places with real Google/OSM results, real reviews,
   a reliability score (green/yellow/red), photos, hours.
2. **A→B Route Builder** — the core engine. Between any two points it builds a
   **hop-by-hop** public-transit tree: bus (nearest stop + number + time + fare),
   Namma Metro Purple/Green, walk, and direct cab/auto/bike routes. The user
   picks one hop at a time and the system chains the next.
3. **Trip Planner** — a 7-step guided wizard that turns your destination, days,
   budget, interests, group and pace into a **day-by-day itinerary** with
   geographically coherent, backtracking-free ordering — reusing the A→B engine
   for every movement (in progress).

**Key decisions**
- **No fake data, ever.** Missing info is shown as *Unavailable / Estimated*,
  never invented. Every fallback is labelled (dashed/interpolated map paths,
  "Live vs Estimated" pricing).
- **Backend does all the thinking; the frontend is a dumb renderer.**
- **Explainable scoring** — every recommendation shows *why* (factor breakdown),
  never a bare ranked list.

---

## 2. Methodology

**Prompt-driven, phase-by-phase build.** The project was built in 9 locked
spec prompts (PROMPT_1 … PROMPT_9), each ship + test before the next:

| Phase | Scope | St |
|-------|-------|----|
| 1 | Data layer (GTFS, fares, station DB, GraphHopper client) | Done |
| 2 | Routing graph + A* route finder | Done |
| 3 | Segment builder (hop-by-hop engine) — the centrepiece | Done |
| 4 | Search, reliability scoring, TOPSIS ranking, ride pricing | Done |
| 5 | Live LangGraph layer (weather, news, trains, LLM summaries) | Done |
| 6 | React frontend (3-tab glass shell, Leaflet map) | Done |
| 7 | Traffic/crowd model + hardening | Done |
| 8 | Trip Planner (new module) | In progress |
| 9 | Deploy — Render + Neon Postgres | Planned |

**Design principles**
- **Test-first**: every prompt shipped with unit tests; repo gate is *green
  tests before push*.
- **Deterministic + transparent scoring** over black-box models, so the user
  can trust and re-create a decision.
- **Lazy singletons** (`app_state.py`): app boots instantly; heavy GTFS/graph
  work runs once on first request.
- **Real, verifiable data sources** chosen deliberately — GTFS schedules over
  guesses, SerpAPI real reviews, Open-Meteo weather, eRail.in live trains.
- **Single source of truth** for every data shape (`data_schema.py`); no module
  redefines a model.

---

## 3. Implementation

**Stack**
| Layer | Tech |
|-------|------|
| Backend | FastAPI (Python 3.12, uvicorn), Pydantic models, lazy services |
| Frontend | Vite + React 19 + TypeScript + Leaflet / react-leaflet, axios |
| Routing | GraphHopper (local Docker) — car + foot real road paths |
| Data | BMTC GTFS (pickled), Namma Metro **Purple + Green only**, Karnataka rail |
| Live | Open-Meteo, SerpAPI, eRail.in, Reddit/DDG via DataImpulse, OpenRouter→Gemini |

**Backend services (`backend/services/`)**
- `gtfs_service.py` — GTFS loader/cache + fuzzy stop-name resolution.
- `fare_engine.py` — pure fare functions (no I/O inside).
- `database.py` — in-memory station DB + spatial index (2,970 bus stops,
  69 metro stations, 48 rail stations).
- `transit_graph.py` + `route_finder.py` — static transit topology + best-first
  top-K search (this powers the one-shot Quick Suggestion).
- `segment_builder.py` — the hop engine: for any pair it returns Bus /
  Cab-Auto-Bike / Walk as separated top-level choices, then lets the user chain
  the next hop.
- `search_service.py`, `reliability.py`, `topsis_engine.py`, `ride_pricing.py` —
  place discovery + enrichment + fair ranking + ride cost.
- `trip_planner.py` + `trip_seed.py` — destination discovery + weighted ranking
  + day clustering (new).
- `langgraph/agent.py` — live agent that gathers weather, traffic, news,
  reviews and train context for a route.
- `traffic_model.py` — crowd/traffic slowdown index.

**Frontend (`frontend/src/`)**
- `pages/MainPage.tsx` — 3-tab shell (Search / A→B / Trip).
- `context/AppContext.tsx` — global state; `services/api.ts` — typed axios client.
- `components/MapView.tsx` — Leaflet map with route highlighting.
- `SearchPanel`, `AToBPanel`, `SegmentFlowView`, `HopTreeView` — search results
  and the interactive hop-by-hop builder.
- `DiscoveryPanel`, `NewsPopup`, `TripPanel` — side glass panel, live news, and
  the 7-step Trip wizard + day-wise results.

**Key algorithms**
- **Reliability score** (0–100): rating + sentiment + review-count, capped to a
  pin class (green/yellow/red) — dynamic and explainable.
- **TOPSIS 8-factor route ranking** (real numpy): time-of-day, cost, weather,
  traffic crowd, availability, walking, group size, safety.
- **Trip ranking formula** (spec 2.3):
  `score = interest_match(0.35) + rating_norm(0.25) + group_fit(0.20)
           + crowd_inverse(0.10) + uniqueness(0.10)`
- **Day clustering**: greedy nearest-neighbour chain sliced into balanced,
  spatially-coherent day chunks, only re-ordered to avoid backtracking.
- **LLM policy**: OpenRouter→Gemini chain used for *summaries only* — LLMs never
  write numbers.

---

## 4. Testing and Validation

**Suite: 122 tests, all green** (`python -m pytest tests/ -q`).

| Suit | Covers |
|------|--------|
| `test_data_layer` | GTFS/fares/DB correctness |
| `test_segment_builder` | hop correctness, bus→metro transfers, fares, warnings |
| `test_route_finder` | top-K pathfinding, transfer penalties |
| `test_prompt4` | reliability, sentiment, TOPSIS, ride pricing |
| `test_prompt5` | LangGraph live layer contracts |
| `test_traffic_model` | honest model info + factor contracts |
| `test_trip_planner` | ranking formula, diversity cap, day clustering (new) |
| `test_no_fake_data` | enforces the golden rule across the codebase |

**Frontend gate**: `npx tsc --noEmit` → **0 errors**; `npx vite build` → green.

**Approach**
- **Network is stubbed by default** — tests are fast, offline and hermetic;
  real API/integration tests opt in via `LIVE_API=1`. This keeps CI deterministic.
- Tests assert the *formulas* and *contracts* (e.g., a perfect place scores ≥ 80 /
  green; permanently-closed is always red; a paid attraction for family-with-kids
  gets group-fit 0; day counts stay balanced).
- A dedicated **"no fake data"** test scans for fabrication — the project's most
  important invariant.

---

## 5. Results and Discussion

**What works today**
- End-to-end multimodal routing with real BMTC schedules, metro Purple/Green,
  walk and direct rides — with live-vs-estimated labels throughout.
- Real place discovery + real Google/OSM reviews, reliability pills, photos and
  hours (via a server-side photo proxy so the API key never leaks to the browser).
- Live weather (Open-Meteo), corridor news, live trains (eRail.in) and a
  traffic/crowd model feeding an 8-factor TOPSIS recommendation.
- A working **Trip Planner** that locks inputs → ranks a real, curated place
  pool → produces a balanced, spatially-coherent day-wise plan with a *why* for
  every place and a clear "static seed data is approximate — verify" disclaimer.
- The whole stack runs locally (backend :8000, frontend :3000, GraphHopper :8080).

**Limitations & honest gaps**
- **Google Places billing is currently off**, so ratings/photos fall back to OSM
  + SerpAPI until billing is enabled — real data, but a reduced signal.
- Trip Planner **transport/budget/stays** (phases 4–6) are still pending — today
  it shows place order + sightseeing time, not yet real per-hop fares.
- GTFS / schedule data is static; BMTC has no live feed, so times are printed
  as schedule-based (honestly labelled).
- GraphHopper currently requires local Docker — a deployment consideration.
- The discovery pool is manually curated (Bengaluru only) until a live API is
  wired in.

**Discussion takeaways**
- *Explainability is the differentiator*: deterministic, factorised scores make
  decisions trustable and testable, unlike a black-box recommendation.
- *Real-data discipline* costs integration effort but pays in trust and QA
  simplicity — the no-fake-data invariant is enforced by an actual test.
- *Separation of concerns* (backend-thinks / frontend-renders; data shapes in one
  schema) is what let new modules (Trip Planner) plug in without touching
  existing engines.

---

## 6. Conclusion

VOYAGER v2 delivers a working, tested, real-data travel platform for Bengaluru:
reliable multimodal routing, trustworthy place discovery and ranking, and a
live context layer — with strict honesty about what's known vs estimated.

The architecture (lazy singletons, single data-schema source of truth, dumb
renderer, test-gated pushes) makes it **extensible and verifiable**. The trip
planner now produces real day-wise itineraries on top of the existing route
engine, keeping transport logic in exactly one place.

**Next steps**
1. Enable Google billing → live ratings, photos, and richer place data.
2. Complete Trip Planner phases 4–7 (transport fees, budget engine, stays,
   customisation/recalc, final timeline + map summary).
3. Deploy to Render + Neon (PROMPT_9; CORS is pre-wired).

---

## 7. References

1. **BMTC — Bengaluru Metropolitan Transport Corporation**, GTFS schedule dataset,
   https://www.bmticweb.com / transit data feeds.
2. **Namma Metro (Bangalore Metro Rail Corporation)**, Purple & Green line data
   (GTFS assets), https://english.bmrc.co.in.
3. **GraphHopper Routing Engine** (Open Source), car & foot profiles,
   https://www.graphhopper.com / https://github.com/graphhopper/graphhopper.
4. **OpenStreetMap** + **Nominatim** geocoding/search fallback,
   https://www.openstreetmap.org, https://nominatim.org.
5. **Google Places API (New)** — place text/nearby search & photos,
   https://developers.google.com/maps/documentation/places/web-service.
6. **SerpAPI — Google Maps Reviews & Local Pack**, https://serpapi.com.
7. **Open-Meteo** — free weather API, https://open-meteo.com.
8. **eRail.in** — live Indian Railway train status API, https://erail.in.
9. **Hwang, C.-L. & Yoon, K. (1981).** *Multiple Attribute Decision Making:
   Methods and Applications*. Springer (TOPSIS method).
10. **LangGraph** — orchestration framework for the live agent layer,
    https://www.langchain.com/langgraph.
11. **FastAPI** — Python web framework, https://fastapi.tiangolo.com.
12. **React** — https://react.dev; **Leaflet / react-leaflet** — https://leafletjs.com.

---

*Prepared from the running VOYAGER v2 codebase — 122 backend tests green, tsc 0 errors, build green.*