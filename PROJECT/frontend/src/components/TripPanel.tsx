import { useCallback, useEffect, useMemo, useState } from "react";
import { useApp } from "../context/AppContext";
import { api, type TripPlanRequest } from "../services/api";
import type {
  BudgetSplits, GroupType, Pace, TripInputData, TripPlanDay, TripPlanPlace,
} from "../types";
import { categoryMeta, dayColor, fmtFee, fmtMins } from "./tripTheme";
import { photoUrl } from "./PlaceBits";
import TripHopBlock from "./TripHopBlock";
import "./TripPanel.css";

const BENGALURU = { name: "Bengaluru", lat: 12.9716, lng: 77.5946 };

// ---------------------------------------------------------------- option sets
const CATEGORIES: { key: string; label: string; icon: string }[] = [
  { key: "heritage", label: "Heritage", icon: "account_balance" },
  { key: "religious", label: "Temples", icon: "temple_hindu" },
  { key: "nature", label: "Nature & parks", icon: "park" },
  { key: "food", label: "Food & cafes", icon: "restaurant" },
  { key: "museum", label: "Museums & art", icon: "museum" },
  { key: "shopping", label: "Shopping", icon: "shopping_bag" },
  { key: "adventure", label: "Adventure", icon: "attractions" },
  { key: "nightlife", label: "Nightlife", icon: "local_bar" },
  { key: "wellness", label: "Wellness", icon: "spa" },
  { key: "offbeat", label: "Offbeat", icon: "explore" },
  { key: "photo", label: "Photo spots", icon: "photo_camera" },
];

const GROUP_TYPES: { key: GroupType; label: string; icon: string }[] = [
  { key: "solo", label: "Solo", icon: "person" },
  { key: "couple", label: "Couple", icon: "favorite" },
  { key: "friends", label: "Friends", icon: "groups" },
  { key: "family", label: "Family", icon: "family_restroom" },
  { key: "seniors", label: "Seniors", icon: "elderly" },
];

const PACES: { key: Pace; label: string; desc: string; icon: string }[] = [
  { key: "relaxed", label: "Relaxed", desc: "2 spots/day, slow", icon: "spa" },
  { key: "balanced", label: "Balanced", desc: "4 spots/day", icon: "balance" },
  { key: "packed", label: "Packed", desc: "6 spots/day, fast", icon: "bolt" },
];

const AUTO_SPLITS: BudgetSplits = { stay: 35, food: 25, transport: 15, attractions: 20, misc: 5 };
const SPLIT_KEYS: (keyof BudgetSplits)[] = ["stay", "food", "transport", "attractions", "misc"];

const DEFAULT_DRAFT: TripInputData = {
  destination: BENGALURU,
  suggestDestination: false,
  durationMode: "days",
  startDate: null,
  endDate: null,
  days: 3,
  groupSize: 2,
  groupType: "friends",
  hasKids: false,
  budget: 10000,
  budgetShape: "total",
  budgetSplits: { ...AUTO_SPLITS },
  categories: ["heritage", "food", "nature"],
  interests: [],
  pace: "balanced",
};

const STEP_TITLES = [
  "Where to?", "How long?", "Your group", "Budget", "What you love", "Pace", "Review & generate",
];

function daysBetween(start: string | null, end: string | null): number {
  if (!start || !end) return 0;
  const a = new Date(start).getTime();
  const b = new Date(end).getTime();
  if (!a || !b || b < a) return 0;
  return Math.round((b - a) / 86400000) + 1;
}

function splitSum(s: BudgetSplits): number {
  return SPLIT_KEYS.reduce((sum, k) => sum + Math.round(s[k] || 0), 0);
}

function adjustSplit(prev: BudgetSplits, key: keyof BudgetSplits, value: number): BudgetSplits {
  const clamped = Math.max(0, Math.min(100, value));
  const others = SPLIT_KEYS.filter((k) => k !== key);
  const otherSum = SPLIT_KEYS.reduce((s, k) => (k === key ? s : s + (prev[k] || 0)), 0);
  const remaining = 100 - clamped;
  const next = { ...prev, [key]: clamped };
  const scale = otherSum > 0 ? remaining / otherSum : 0;
  others.forEach((k) => { next[k] = Math.round((prev[k] || 0) * scale); });
  const delta = 100 - (clamped + others.reduce((s, k) => s + next[k], 0));
  next[others[0]] = Math.max(0, (next[others[0]] || 0) + delta);
  return next;
}

const SPLIT_LABELS: Record<keyof BudgetSplits, string> = {
  stay: "Stay", food: "Food", transport: "Transport", attractions: "Attractions", misc: "Misc",
};

// ---------------------------------------------------------------- planning glue
function requestFromTrip(t: TripInputData): TripPlanRequest {
  const group = t.groupType === "seniors" ? "senior" : t.groupType;
  const totalBudget = t.budgetShape === "perPerson" ? t.budget * t.groupSize : t.budget;
  return {
    destination: "bengaluru",
    interests: [],
    categories: t.categories,
    group_type: group === "family" ? (t.hasKids ? "family_kids" : "family") : group,
    days: Math.max(1, t.days),
    pace: t.pace,
    budget: totalBudget,
  };
}

function requestKey(t: TripInputData): string {
  return JSON.stringify({
    c: t.categories, d: t.days, g: t.groupType, k: t.hasKids,
    n: t.groupSize, b: t.budget, s: t.budgetShape, p: t.pace,
  });
}

function categoryLabel(key: string): string {
  return CATEGORIES.find((c) => c.key === key)?.label ?? key;
}

// ---------------------------------------------------------------- place card
function TripPlaceCard({ place, index, color, onDetails }: {
  place: TripPlanPlace;
  index: number;
  color: string;
  onDetails: () => void;
}) {
  const meta = categoryMeta(place.category);
  const img = photoUrl({ photo_url: place.photo_url });
  const time = place.suggested_start
    ? `${place.suggested_start}${place.suggested_end ? ` – ${place.suggested_end}` : ""}`
    : "";

  return (
    <article className="tp-place glass">
      <div className="tp-place-top">
        <span className="tp-num" style={{ background: color }}>{index + 1}</span>
        <div className="tp-photo">
          {img ? (
            <img src={img} alt={place.name} loading="lazy" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
          ) : (
            <span className="material-symbols-outlined">{meta.icon}</span>
          )}
        </div>
        <div className="tp-main">
          <div className="tp-time small">
            {time && <><span className="material-symbols-outlined">schedule</span>{time}</>}
            <span className="tp-cat" style={{ color: meta.color, background: `${meta.color}18` }}>{meta.label}</span>
          </div>
          <h4 className="tp-name">{place.name}</h4>
          <div className="tp-meta tiny">
            {place.rating_known && place.rating > 0
              ? <span className="tp-rating">★ {place.rating.toFixed(1)}{place.review_count ? ` (${place.review_count.toLocaleString()})` : ""}</span>
              : <span className="muted">Rating not listed</span>}
            <span>{fmtMins(place.duration_min)}</span>
            <span>{fmtFee(place)}</span>
          </div>
        </div>
      </div>
      <p className="tp-why tiny">{place.why}</p>
      <div className="tp-actions row">
        <button className="tp-link" onClick={onDetails}>
          <span className="material-symbols-outlined">info</span> Place details
        </button>
        {place.place_id && <span className="badge live tiny">LIVE DATA</span>}
        <span className="muted tiny tp-source">
          {place.verified ? "Google-verified" : place.data_source === "curated" ? "Curated" : place.data_source}
        </span>
      </div>
    </article>
  );
}

// ---------------------------------------------------------------- day section
function TripDaySection({ day, groupSize, budget, single }: {
  day: TripPlanDay;
  groupSize: number;
  budget: number;
  single: boolean;
}) {
  const { setTripSelectedPlace } = useApp();
  const color = dayColor(day.day);
  const entry = day.places.reduce((s, p) => s + (p.entry_fee_known ? p.entry_fee : 0), 0);

  return (
    <section className={`tp-day ${single ? "single" : ""}`} style={{ ["--day-color" as string]: color }}>
      <header className="tp-day-head">
        <div className="row">
          <span className="tp-day-dot" />
          <div>
            <div className="tp-day-title">Day {day.day}{day.theme ? ` · ${day.theme}` : ""}</div>
            <div className="muted tiny">
              {day.start_time && day.end_time ? `${day.start_time} – ${day.end_time} · ` : ""}
              {day.place_count} stops · {fmtMins(day.total_activity_min)} sightseeing
              {day.total_distance_km ? ` · ${day.total_distance_km.toFixed(1)} km` : ""}
            </div>
          </div>
        </div>
        <span className="tp-day-cost tiny">
          {entry > 0 ? `₹${Math.round(entry * groupSize)} entry` : "no entry fee"}
        </span>
      </header>

      <div className="tp-timeline">
        {day.places.map((place, i) => {
          const next = day.places[i + 1];
          return (
            <div className="tp-stop" key={place.id}>
              <TripPlaceCard
                place={place}
                index={i}
                color={color}
                onDetails={() => setTripSelectedPlace(place)}
              />
              {next && (
                <TripHopBlock
                  day={day.day}
                  from={place}
                  to={next}
                  groupSize={groupSize}
                  budget={Math.max(200, Math.round(budget / Math.max(1, day.place_count * 2)))}
                  departAt={place.suggested_end}
                />
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- results window
export function TripResults() {
  const {
    trip, setTrip, tripPlan, setTripPlan, tripPlanKey, setTripPlanKey,
    tripDay, setTripDay, tripTransports, clearTripTransports, setTripSelectedPlace,
  } = useApp();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchPlan = useCallback(async (t: TripInputData, force = false) => {
    const key = requestKey(t);
    if (!force && tripPlan && tripPlanKey === key) return;
    setLoading(true);
    setError(null);
    try {
      const plan = await api.tripPlan(requestFromTrip(t));
      setTripPlan(plan);
      setTripPlanKey(key);
      setTripDay(null);
      // Old hop choices point at places from the previous plan.
      clearTripTransports();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not generate the trip plan.");
    } finally {
      setLoading(false);
    }
  }, [tripPlan, tripPlanKey, setTripPlan, setTripPlanKey, setTripDay, clearTripTransports]);

  useEffect(() => {
    if (!trip) return;
    // Restored plans carry their request key; only regenerate when the input
    // actually changed (or there is no plan yet).
    fetchPlan(trip);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trip]);

  const plan = tripPlan;
  const days = plan?.days ?? [];
  const visibleDays = useMemo(
    () => (tripDay == null ? days : days.filter((d) => d.day === tripDay)),
    [days, tripDay],
  );

  const groupSize = trip?.groupSize ?? 2;
  const budget = trip ? (trip.budgetShape === "perPerson" ? trip.budget * trip.groupSize : trip.budget) : 0;
  const placeCount = days.reduce((s, d) => s + d.place_count, 0);
  const entryTotal = days.reduce(
    (s, d) => s + d.places.reduce((x, p) => x + (p.entry_fee_known ? p.entry_fee : 0), 0), 0,
  );
  const unknownEntries = days.reduce((s, d) => s + d.places.filter((p) => !p.entry_fee_known).length, 0);
  const plannedHops = Object.keys(tripTransports).length;
  const totalHops = days.reduce((s, d) => s + Math.max(0, d.place_count - 1), 0);

  const reset = () => {
    setTripPlan(null);
    setTripPlanKey(null);
    setTripDay(null);
    setTripSelectedPlace(null);
    clearTripTransports();
    setTrip(null);
  };

  if (!trip && !plan) {
    return (
      <div className="trip-results">
        <div className="tp-empty-hero glass">
          <span className="tp-empty-icon material-symbols-outlined">luggage</span>
          <h3>Build a Bengaluru trip that actually makes sense</h3>
          <p className="small muted">
            Pick your interests and pace. We group nearby places into days, order them by the
            best time to visit, and let you plan every hop — metro, bus, cab or walk.
          </p>
          <div className="row wrap gap">
            {["Day-by-day map", "Real ratings & photos", "Hop-by-hop transit", "Cab & fuel costs"].map((f) => (
              <span className="chip" key={f}>{f}</span>
            ))}
          </div>
        </div>
      </div>
    );
  }

  const destination = trip?.destination?.name ?? BENGALURU.name;

  return (
    <div className="trip-results">
      <div className="tp-hero anim-in">
        <div className="tp-hero-bg" />
        <div className="tp-hero-top row spread">
          <span className="row tp-hero-place">
            <span className="material-symbols-outlined">location_city</span>
            <span>
              <span className="tiny tp-hero-kicker">BENGALURU ITINERARY</span>
              <b>{destination}</b>
            </span>
          </span>
          <span className="row gap">
            <button className="tp-icon-btn" title="Regenerate plan" onClick={() => trip && fetchPlan(trip, true)} disabled={loading}>
              <span className={`material-symbols-outlined ${loading ? "spin" : ""}`}>refresh</span>
            </button>
            <button className="tp-icon-btn" title="Clear plan" onClick={reset}>
              <span className="material-symbols-outlined">close</span>
            </button>
          </span>
        </div>

        <div className="tp-hero-stats">
          <div><b>{plan?.total_places ?? placeCount}</b><span>places</span></div>
          <div><b>{days.length}</b><span>days</span></div>
          <div><b>{plan?.total_distance_km ? plan.total_distance_km.toFixed(0) : "—"}</b><span>km between stops</span></div>
          <div><b>{fmtMins(plan?.total_activity_min ?? 0)}</b><span>sightseeing</span></div>
        </div>

        <div className="row wrap gap mt8">
          {(trip?.categories ?? plan?.categories ?? []).map((c) => (
            <span className="tp-hero-chip" key={c}>{categoryLabel(c)}</span>
          ))}
          <span className="tp-hero-chip">{groupSize} traveller{groupSize === 1 ? "" : "s"}</span>
          {budget > 0 && <span className="tp-hero-chip">₹{budget.toLocaleString()} budget</span>}
          {entryTotal > 0 && <span className="tp-hero-chip">₹{Math.round(entryTotal * groupSize)} entry est.</span>}
          {unknownEntries > 0 && <span className="tp-hero-chip warn">{unknownEntries} fees unlisted</span>}
        </div>

        {totalHops > 0 && (
          <div className="tp-hop-progress small">
            <span className="material-symbols-outlined">route</span>
            {plannedHops} of {totalHops} hops planned
            <span className="tp-progress-track"><span style={{ width: `${Math.round((plannedHops / totalHops) * 100)}%` }} /></span>
          </div>
        )}
      </div>

      {loading && (
        <div className="tp-loading glass">
          <span className="spinner inline" />
          <span className="small">Building your Bengaluru days…</span>
        </div>
      )}

      {!loading && error && (
        <div className="empty glass warn">
          <span className="material-symbols-outlined">error</span>
          <div className="small">{error}</div>
        </div>
      )}

      {!loading && !error && plan && days.length === 0 && (
        <div className="empty glass warn">
          <span className="material-symbols-outlined">info</span>
          <div className="small">{plan.warning ?? "No places matched those filters. Try widening your categories."}</div>
        </div>
      )}

      {!loading && plan && days.length > 0 && (
        <>
          <div className="tp-day-tabs" role="tablist">
            <button
              className={`tp-day-tab ${tripDay == null ? "active" : ""}`}
              onClick={() => setTripDay(null)}
              role="tab"
              aria-selected={tripDay == null}
            >
              <span className="material-symbols-outlined">map</span> Full trip
            </button>
            {days.map((d) => (
              <button
                key={d.day}
                className={`tp-day-tab ${tripDay === d.day ? "active" : ""}`}
                onClick={() => setTripDay(d.day)}
                role="tab"
                aria-selected={tripDay === d.day}
                style={{ ["--day-color" as string]: dayColor(d.day) }}
              >
                <span className="tp-tab-dot" /> Day {d.day}
              </button>
            ))}
          </div>

          <div className="tp-days">
            {visibleDays.map((d) => (
              <TripDaySection
                key={d.day}
                day={d}
                groupSize={groupSize}
                budget={budget}
                single={tripDay != null}
              />
            ))}
          </div>

          <div className="disclaimer small mt8">
            <span className="material-symbols-outlined">verified</span>
            {plan.disclaimer}
            {plan.relaxed && " Your category filter was thin, so nearby options were included."}
          </div>
          <div className="muted tiny tp-footnote">
            Times are suggested from opening windows and straight-line travel — open a leg to fetch
            real transit, cab and walking options. Nothing on this page is a live ETA unless badged LIVE.
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- wizard
export default function TripInput() {
  const { setTrip } = useApp();
  const [draft, setDraft] = useState<TripInputData>({ ...DEFAULT_DRAFT, budgetSplits: { ...AUTO_SPLITS } });
  const [step, setStep] = useState(0);

  const set = (patch: Partial<TripInputData>) => setDraft((d) => ({ ...d, ...patch }));
  const daysCount = useMemo(() => (
    draft.durationMode === "days" ? draft.days : daysBetween(draft.startDate, draft.endDate)
  ), [draft]);

  const canNext = useMemo(() => {
    switch (step) {
      case 0: return true;
      case 1: return draft.durationMode === "days" ? draft.days >= 1 : daysCount >= 1;
      case 2: return draft.groupSize >= 1;
      case 3: return draft.budget > 0;
      case 4: return draft.categories.length > 0;
      case 5: return true;
      default: return true;
    }
  }, [step, draft, daysCount]);

  const toggleCategory = (key: string) => {
    set({ categories: draft.categories.includes(key)
      ? draft.categories.filter((c) => c !== key)
      : [...draft.categories, key] });
  };

  const generate = () => {
    setTrip({ ...draft, destination: BENGALURU, days: daysCount > 0 ? daysCount : draft.days });
  };

  const goEdit = (s: number) => setStep(s);

  return (
    <div className="trip-wizard">
      <div className="trip-steps row">
        {STEP_TITLES.map((t, i) => (
          <button
            key={t}
            className={`step-chip ${i === step ? "active" : ""} ${i < step ? "done" : ""}`}
            onClick={() => i < step && setStep(i)}
            title={t}
          >
            {i < step ? <span className="material-symbols-outlined">check</span> : <span>{i + 1}</span>}
          </button>
        ))}
      </div>
      <div className="step-title">{STEP_TITLES[step]}</div>

      {step === 0 && (
        <div className="step-body">
          <div className="tp-city-card">
            <span className="material-symbols-outlined">location_city</span>
            <span>
              <b>Bengaluru</b>
              <div className="muted small">Garden city · heritage, food streets, markets, parks</div>
            </span>
            <span className="badge live">68 PLACES</span>
          </div>
          <div className="muted tiny">
            Bengaluru is the only city with a full place pool right now. More cities can plug into
            the same dataset later.
          </div>
        </div>
      )}

      {step === 1 && (
        <div className="step-body">
          <div className="mode-tabs row">
            <button className={`chip ${draft.durationMode === "dates" ? "active" : ""}`} onClick={() => set({ durationMode: "dates" })}>Date range</button>
            <button className={`chip ${draft.durationMode === "days" ? "active" : ""}`} onClick={() => set({ durationMode: "days" })}>Just # of days</button>
          </div>
          {draft.durationMode === "dates" ? (
            <div className="params col">
              <label className="param">
                <span className="muted small">Start</span>
                <input type="date" className="text-input" value={draft.startDate ?? ""} onChange={(e) => set({ startDate: e.target.value || null })} />
              </label>
              <label className="param">
                <span className="muted small">End</span>
                <input type="date" className="text-input" value={draft.endDate ?? ""} onChange={(e) => set({ endDate: e.target.value || null })} />
              </label>
              {daysCount > 0 && <div className="muted small">→ {daysCount} day{daysCount === 1 ? "" : "s"}</div>}
            </div>
          ) : (
            <label className="param">
              <span className="muted small">Days</span>
              <input type="number" min={1} max={30} className="text-input" value={draft.days} onChange={(e) => set({ days: Math.max(1, Number(e.target.value)) })} />
            </label>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="step-body">
          <div className="group-chips row wrap">
            {GROUP_TYPES.map((g) => (
              <button key={g.key} className={`chip ${draft.groupType === g.key ? "active" : ""}`} onClick={() => set({ groupType: g.key })}>
                <span className="material-symbols-outlined">{g.icon}</span> {g.label}
              </button>
            ))}
          </div>
          <label className="param">
            <span className="muted small">Travellers</span>
            <input type="number" min={1} max={40} className="text-input" value={draft.groupSize} onChange={(e) => set({ groupSize: Math.max(1, Number(e.target.value)) })} />
          </label>
          <label className="check-row">
            <input type="checkbox" checked={draft.hasKids} onChange={(e) => set({ hasKids: e.target.checked })} />
            <span>Traveling with children</span>
          </label>
        </div>
      )}

      {step === 3 && (
        <div className="step-body">
          <div className="mode-tabs row">
            <button className={`chip ${draft.budgetShape === "total" ? "active" : ""}`} onClick={() => set({ budgetShape: "total" })}>Total</button>
            <button className={`chip ${draft.budgetShape === "perPerson" ? "active" : ""}`} onClick={() => set({ budgetShape: "perPerson" })}>Per person</button>
          </div>
          <label className="param">
            <span className="muted small">Budget ₹ ({draft.budgetShape === "total" ? "trip total" : "per person"})</span>
            <input type="number" min={0} className="text-input" value={draft.budget} onChange={(e) => set({ budget: Math.max(0, Number(e.target.value)) })} />
          </label>
          <div className="split-head row"><span className="muted small">Spend split (%)</span>
            <button className="link-btn" onClick={() => set({ budgetSplits: { ...AUTO_SPLITS } })}>Reset auto</button>
          </div>
          {SPLIT_KEYS.map((k) => (
            <label key={k} className="split-row">
              <span className="muted small split-label">{SPLIT_LABELS[k]}</span>
              <input type="range" min={0} max={100} value={draft.budgetSplits[k] ?? 0}
                onChange={(e) => set({ budgetSplits: adjustSplit(draft.budgetSplits, k, Number(e.target.value)) })} />
              <span className="muted small split-val">{draft.budgetSplits[k] ?? 0}%</span>
            </label>
          ))}
          <div className={`split-total ${splitSum(draft.budgetSplits) === 100 ? "ok" : "warn"}`}>
            {splitSum(draft.budgetSplits)}% {splitSum(draft.budgetSplits) === 100 ? "" : "— sliders share 100, auto-adjusted"}
          </div>
        </div>
      )}

      {step === 4 && (
        <div className="step-body">
          <div className="muted small">Pick the kinds of places you want. The planner mixes them across days.</div>
          <div className="tp-cat-grid">
            {CATEGORIES.map((c) => (
              <button
                key={c.key}
                className={`tp-cat-chip ${draft.categories.includes(c.key) ? "active" : ""}`}
                onClick={() => toggleCategory(c.key)}
              >
                <span className="material-symbols-outlined">{c.icon}</span>
                <span>{c.label}</span>
                {draft.categories.includes(c.key) && <span className="material-symbols-outlined check">check_circle</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {step === 5 && (
        <div className="step-body">
          <div className="pace-list">
            {PACES.map((p) => (
              <button key={p.key} className={`pace-card glass ${draft.pace === p.key ? "active" : ""}`} onClick={() => set({ pace: p.key })}>
                <span className="material-symbols-outlined">{p.icon}</span>
                <span><b>{p.label}</b><div className="muted small">{p.desc}</div></span>
              </button>
            ))}
          </div>
        </div>
      )}

      {step === 6 && (
        <div className="step-body">
          <SummaryRow label="Destination" icon="location_city" onEdit={() => goEdit(0)} value="Bengaluru" />
          <SummaryRow label="Duration" icon="calendar_month" onEdit={() => goEdit(1)}
            value={daysCount > 0 ? `${daysCount} day${daysCount === 1 ? "" : "s"}` : `${draft.days} day${draft.days === 1 ? "" : "s"}`}
            hint={draft.durationMode === "dates" && draft.startDate ? `${draft.startDate} → ${draft.endDate ?? "…"}` : undefined} />
          <SummaryRow label="Group" icon="groups" onEdit={() => goEdit(2)}
            value={`${draft.groupSize} traveller${draft.groupSize === 1 ? "" : "s"} • ${GROUP_TYPES.find((g) => g.key === draft.groupType)?.label}${draft.hasKids ? " • with kids" : ""}`} />
          <SummaryRow label="Budget" icon="account_balance_wallet" onEdit={() => goEdit(3)}
            value={`₹${draft.budget.toLocaleString()} ${draft.budgetShape === "perPerson" ? "per person" : "total"}`} />
          <SummaryRow label="Categories" icon="favorite" onEdit={() => goEdit(4)}
            value={draft.categories.length ? draft.categories.map(categoryLabel).join(" · ") : "None selected"} />
          <SummaryRow label="Pace" icon="balance" onEdit={() => goEdit(5)}
            value={PACES.find((p) => p.key === draft.pace)?.label ?? draft.pace} />
        </div>
      )}

      <div className="nav-row row">
        <button className="btn ghost" onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}>
          <span className="material-symbols-outlined">chevron_left</span> Back
        </button>
        {step < 6 ? (
          <button className="btn" onClick={() => setStep((s) => s + 1)} disabled={!canNext}>
            Next <span className="material-symbols-outlined">chevron_right</span>
          </button>
        ) : (
          <button className="btn primary" onClick={generate} disabled={!canNext}>
            <span className="material-symbols-outlined">auto_awesome</span> Generate My Trip
          </button>
        )}
      </div>
    </div>
  );
}

function SummaryRow({ label, icon, value, hint, onEdit }: {
  label: string; icon: string; value: string; hint?: string; onEdit: () => void;
}) {
  return (
    <div className="summary-row glass">
      <span className="material-symbols-outlined" style={{ color: "var(--primary)" }}>{icon}</span>
      <span className="summary-main">
        <span className="muted small">{label}</span>
        <span className="summary-val">{value}</span>
        {hint && <span className="muted small">{hint}</span>}
      </span>
      <button className="icon-btn" onClick={onEdit} title={`Edit ${label}`}>
        <span className="material-symbols-outlined">edit</span>
      </button>
    </div>
  );
}
