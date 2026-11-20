import { useCallback, useEffect, useMemo, useState } from "react";
import { useApp } from "../context/AppContext";
import { api } from "../services/api";
import type { Coord, HopOption, RidePrice, Segment, TripPlanPlace, TripTransportChoice } from "../types";
import { optionsForLevel } from "./HopTreeView";
import { fmtMins, haversineKm, hopKey, joinGeometry, walkEstimateKm } from "./tripTheme";
import "./TripHopBlock.css";

type HopTab = "transit" | "private" | "walk";
type QuickPlanLike = {
  legs: { mode: string; route_number?: string | null; geometry?: number[][] | null; fare?: number; per_person_fare?: number; duration_min?: number }[];
  total_fare: number;
  per_person_fare: number;
  total_duration_min: number;
  transfers: number;
  summary: string;
};

const MODE_ICON: Record<string, string> = {
  bus: "directions_bus", metro: "subway", train: "train", walk: "directions_walk", ride: "local_taxi",
};

function modeLabel(o: HopOption): string {
  const base = o.mode === "bus" ? "Bus" : o.mode === "metro" ? "Metro" : o.mode === "train" ? "Train" : o.mode === "walk" ? "Walk" : "Ride";
  return `${base}${o.routeNumber ? ` ${o.routeNumber}` : ""}`;
}

function nearDestination(opt: HopOption, to: TripPlanPlace): boolean {
  if (opt.reachesNearDestination || opt.isFinalWalk) return true;
  const stop = opt.destinationStop;
  if (!stop || typeof stop.lat !== "number" || typeof stop.lng !== "number") return false;
  const d = haversineKm(stop, { lat: to.lat, lng: to.lng });
  return d <= 0.55;
}

export default function TripHopBlock({
  day, from, to, groupSize, budget, departAt,
}: {
  day: number;
  from: TripPlanPlace;
  to: TripPlanPlace;
  groupSize: number;
  budget: number;
  /** itinerary clock time to plan the leg for; defaults to now when absent */
  departAt?: string;
}) {
  const { tripTransports, setTripTransport, setTripHopRoute } = useApp();
  const key = hopKey(day, from, to);
  const chosen = tripTransports[key];

  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<HopTab>("transit");

  // quick suggestion (top transit route)
  const [quick, setQuick] = useState<QuickPlanLike | null>(null);
  const [quickLoading, setQuickLoading] = useState(false);

  // hop-by-hop picker
  const [hopStarted, setHopStarted] = useState(false);
  const [levels, setLevels] = useState<Segment[]>([]);
  const [journey, setJourney] = useState<unknown>({});
  const [confirmed, setConfirmed] = useState<HopOption[]>([]);
  const [complete, setComplete] = useState(false);
  const [hopLoading, setHopLoading] = useState(false);
  const [hopError, setHopError] = useState<string | null>(null);

  // private
  const [rides, setRides] = useState<RidePrice[] | null>(null);
  const [rideLoading, setRideLoading] = useState(false);
  const [rideError, setRideError] = useState<string | null>(null);
  const [busyProvider, setBusyProvider] = useState<string | null>(null);

  const straightKm = useMemo(() => haversineKm(from, to), [from, to]);
  const walk = useMemo(() => walkEstimateKm(from, to), [from, to]);

  useEffect(() => {
    // A new plan/hop key should not keep another hop's open state.
    setOpen(false);
    setTab("transit");
    setQuick(null);
    setHopStarted(false);
    setLevels([]);
    setConfirmed([]);
    setComplete(false);
    setRides(null);
  }, [key]);

  const src = useMemo(() => ({ lat: from.lat, lng: from.lng, name: from.name }), [from]);
  const dst = useMemo(() => ({ lat: to.lat, lng: to.lng, name: to.name }), [to]);

  const loadQuick = useCallback(async () => {
    if (quick || quickLoading) return;
    setQuickLoading(true);
    try {
      const res = await api.quickSuggestion(src, dst, groupSize, budget, departAt ?? null);
      const plan = (res.plans?.[0] ?? null) as QuickPlanLike | null;
      setQuick(plan);
      if (plan) {
        setTripHopRoute({ key, mode: "transit", geometry: joinGeometry(plan.legs) });
      }
    } catch {
      setQuick(null);
    } finally {
      setQuickLoading(false);
    }
  }, [quick, quickLoading, src, dst, groupSize, budget, departAt, key, setTripHopRoute]);

  const loadRides = useCallback(async () => {
    if (rides || rideLoading) return;
    setRideLoading(true);
    setRideError(null);
    try {
      setRides(await api.ridePrices(src, dst, groupSize));
    } catch (e) {
      setRides([]);
      setRideError(e instanceof Error ? e.message : "Ride prices unavailable right now.");
    } finally {
      setRideLoading(false);
    }
  }, [rides, rideLoading, src, dst, groupSize]);

  const openTab = (next: HopTab) => {
    setTab(next);
    if (next === "transit") loadQuick();
    if (next === "private") loadRides();
    if (next === "walk") {
      setTripHopRoute({ key, mode: "walk", geometry: [[from.lat, from.lng], [to.lat, to.lng]] });
    }
  };

  const expand = () => {
    const next = !open;
    setOpen(next);
    if (next) openTab(tab);
  };

  const choose = (choice: TripTransportChoice) => {
    setTripTransport(key, choice);
    setTripHopRoute(null);
  };

  // ------------------------------------------------------------ hop-by-hop
  const startHopByHop = async () => {
    if (hopStarted) return;
    setHopStarted(true);
    setHopLoading(true);
    setHopError(null);
    try {
      const res = await api.routeSegments(src, dst, groupSize, budget, departAt ?? null);
      setLevels(res.segments ?? []);
      setJourney(res.journey ?? {});
      setComplete(!!res.journeyComplete);
    } catch (e) {
      setHopError(e instanceof Error ? e.message : "Hop-by-hop routes unavailable.");
    } finally {
      setHopLoading(false);
    }
  };

  const selectHop = async (opt: HopOption) => {
    const next = [...confirmed, opt];
    setConfirmed(next);
    if (opt.geometry?.length) {
      setTripHopRoute({ key, mode: opt.mode, geometry: joinGeometry([opt]) });
    }
    if (nearDestination(opt, to)) {
      setComplete(true);
      return;
    }
    const nextIdx = next.length;
    if (optionsForLevel(levels[nextIdx], next, nextIdx).length > 0) return;
    setHopLoading(true);
    try {
      const res = await api.segmentNext(journey, next, groupSize, budget);
      setLevels((prev) => [...prev.slice(0, nextIdx), ...(res.segments ?? [])]);
      setJourney(res.journey ?? journey);
      setComplete(!!res.journeyComplete);
    } catch (e) {
      setHopError(e instanceof Error ? e.message : "Could not fetch the next hop.");
    } finally {
      setHopLoading(false);
    }
  };

  const undoHop = () => {
    const next = confirmed.slice(0, -1);
    setConfirmed(next);
    setComplete(false);
  };

  const saveHopRoute = () => {
    if (!confirmed.length) return;
    const geometry = joinGeometry(confirmed);
    const fare = confirmed.reduce((s, o) => s + (o.fare ?? 0), 0);
    const perPerson = confirmed.reduce((s, o) => s + (o.perPersonFare ?? o.fare ?? 0), 0);
    const minutes = confirmed.reduce((s, o) => s + (o.durationMin ?? 0) + (o.waitMin ?? 0), 0);
    const modes = Array.from(new Set(confirmed.filter((o) => o.mode !== "walk").map((o) => o.mode)));
    const label = modes.length
      ? modes.map((m) => (m === "bus" ? "Bus" : m === "metro" ? "Metro" : m === "train" ? "Train" : "Ride")).join(" + ")
      : "Walk";
    choose({
      key, mode: "transit", label,
      detail: `${confirmed.length} hop${confirmed.length > 1 ? "s" : ""}${perPerson ? ` · ₹${Math.round(perPerson)}/person` : ""}`,
      fare: perPerson || fare,
      duration_min: minutes || null,
      geometry,
      source: "planned",
      note: "Interactive hop-by-hop route. Times follow the schedule.",
    });
  };

  // --------------------------------------------------------------- private
  const pickProvider = async (p: RidePrice) => {
    setBusyProvider(`${p.provider}-${p.mode}`);
    let geometry: Coord[] = [[from.lat, from.lng], [to.lat, to.lng]];
    let minutes: number | null = null;
    try {
      const route = await api.driveRoute(src, dst);
      if (route.geometry?.length > 1) geometry = route.geometry;
      minutes = route.duration_s ? Math.round(route.duration_s / 60) : null;
    } catch {
      // straight-line fallback is still useful and labelled as estimated
    }
    choose({
      key, mode: "private", label: `${p.provider} ${p.mode}`,
      detail: `${p.per_person ? `₹${Math.round(p.total)} total · ` : ""}${p.source === "live" ? "live price" : "estimated fare"}`,
      fare: p.total,
      duration_min: p.eta_min ?? minutes,
      geometry,
      source: p.source === "live" ? "live" : "estimated",
      note: p.note,
    });
    setBusyProvider(null);
  };

  const pickDrive = async () => {
    setBusyProvider("drive");
    let geometry: Coord[] = [[from.lat, from.lng], [to.lat, to.lng]];
    let km = 0;
    let minutes: number | null = null;
    let fuel: number | null = null;
    try {
      const route = await api.driveRoute(src, dst);
      if (route.geometry?.length > 1) geometry = route.geometry;
      km = (route.distance_m ?? 0) / 1000;
      minutes = route.duration_s ? Math.round(route.duration_s / 60) : null;
      const perLiter = route.fuel_price_per_liter ?? 110;
      fuel = Math.round(((perLiter * km) / 15) * 100) / 100;
    } catch {
      km = straightKm;
    }
    choose({
      key, mode: "private", label: "Drive yourself",
      detail: `${km.toFixed(1)} km${fuel != null ? ` · ~₹${Math.round(fuel)} fuel` : ""}`,
      fare: fuel,
      duration_min: minutes,
      geometry,
      source: "estimated",
      note: "Fuel at the configured per-litre price and 15 km/L — a cost estimate, not a fare.",
    });
    setBusyProvider(null);
  };

  const levelsOptions = hopStarted
    ? optionsForLevel(levels[confirmed.length], confirmed, confirmed.length)
    : [];

  return (
    <div className={`hop-block ${chosen ? "planned" : ""}`}>
      <button className="hop-summary" onClick={expand} aria-expanded={open}>
        <span className="hop-line" />
        <span className="hop-icon material-symbols-outlined">
          {chosen?.mode === "walk" ? "directions_walk" : chosen?.mode === "private" ? "local_taxi" : "route"}
        </span>
        <span className="hop-text">
          <span className="hop-title">
            {chosen ? chosen.label : "Travel to " + to.name}
          </span>
          <span className="muted tiny">
            {chosen
              ? [chosen.detail, chosen.duration_min ? fmtMins(chosen.duration_min) : null, chosen.fare != null ? `₹${Math.round(chosen.fare)}` : null].filter(Boolean).join(" · ")
              : `${straightKm.toFixed(1)} km straight-line · plan this leg`}
          </span>
        </span>
        <span className={`hop-chevron material-symbols-outlined ${open ? "open" : ""}`}>expand_more</span>
      </button>

      {open && (
        <div className="hop-panel anim-in">
          <div className="hop-tabs row">
            {(["transit", "private", "walk"] as HopTab[]).map((t) => (
              <button key={t} className={`chip ${tab === t ? "active" : ""}`} onClick={() => openTab(t)}>
                <span className="material-symbols-outlined">
                  {t === "transit" ? "directions_bus" : t === "private" ? "local_taxi" : "directions_walk"}
                </span>
                {t === "transit" ? "Hop-by-hop" : t === "private" ? "Private" : "Walk"}
              </button>
            ))}
          </div>

          {tab === "transit" && (
            <div className="hop-tab-body">
              {quickLoading && <div className="hop-loading"><span className="spinner inline" /> Finding the best transit route…</div>}

              {quick && (
                <div className="hop-quick glass">
                  <div className="spread">
                    <span className="row"><span className="material-symbols-outlined">bolt</span><b className="small">Top suggestion</b></span>
                    <span className="badge est">AUTO</span>
                  </div>
                  <div className="hop-quick-legs">
                    {quick.legs.map((l, i) => (
                      <span key={i} className="hop-leg">
                        <span className="material-symbols-outlined">{MODE_ICON[l.mode] ?? "directions_bus"}</span>
                        <span className="small">{l.route_number ?? l.mode}</span>
                      </span>
                    ))}
                  </div>
                  <div className="spread mt6">
                    <span className="small"><b>{fmtMins(quick.total_duration_min)}</b> · ₹{Math.round(quick.per_person_fare)}/person</span>
                    <span className="muted tiny">{quick.transfers} transfer{quick.transfers === 1 ? "" : "s"}</span>
                  </div>
                  <button
                    className="btn small mt8 full"
                    onClick={() => choose({
                      key, mode: "transit", label: "Transit (top suggestion)",
                      detail: quick.summary,
                      fare: quick.per_person_fare,
                      duration_min: quick.total_duration_min,
                      geometry: joinGeometry(quick.legs),
                      source: "planned",
                      note: "Auto-computed transit route for this leg.",
                    })}
                  >
                    Use this route
                  </button>
                </div>
              )}

              <div className="hop-divider"><span>or build it hop by hop</span></div>

              {!hopStarted && (
                <button className="btn ghost full small" onClick={startHopByHop}>
                  <span className="material-symbols-outlined">account_tree</span> Show hop options
                </button>
              )}

              {hopStarted && (
                <div className="hop-picker">
                  {confirmed.length > 0 && (
                    <div className="hop-chosen">
                      {confirmed.map((o, i) => (
                        <div key={i} className="hop-chosen-row small">
                          <span className="material-symbols-outlined">{MODE_ICON[o.mode] ?? "directions_bus"}</span>
                          <span><b>{modeLabel(o)}</b> → {o.destinationStop?.name ?? "next stop"}</span>
                          {o.fare != null && <span className="muted tiny">₹{Math.round(o.fare)}</span>}
                        </div>
                      ))}
                      <div className="row gap mt6">
                        <button className="link-btn" onClick={undoHop}>Undo last hop</button>
                        {confirmed.length > 0 && <button className="btn small" onClick={saveHopRoute}>Save this route</button>}
                      </div>
                    </div>
                  )}

                  {hopLoading && <div className="hop-loading"><span className="spinner inline" /> Loading the next hops…</div>}
                  {hopError && <div className="small muted">{hopError}</div>}

                  {!hopLoading && !complete && levelsOptions.length > 0 && (
                    <div className="hop-options">
                      <div className="muted tiny">
                        Step {confirmed.length + 1} · from {confirmed.length ? confirmed[confirmed.length - 1].destinationStop?.name : from.name}
                      </div>
                      {levelsOptions.slice(0, 8).map((o) => (
                        <button key={o.optionId} className="hop-option" onClick={() => selectHop(o)}>
                          <span className="material-symbols-outlined" style={{ color: o.mode === "walk" ? "#95a5a6" : undefined }}>
                            {MODE_ICON[o.mode] ?? "directions_bus"}
                          </span>
                          <span className="hop-option-main">
                            <span className="small"><b>{modeLabel(o)}</b>{o.isTopRecommended ? " · recommended" : ""}</span>
                            <span className="muted tiny">
                              {o.fromStop?.name ? `${o.fromStop.name} → ` : ""}{o.destinationStop?.name}
                              {o.departureTime ? ` · ${o.departureTime}` : ""}
                              {o.waitMin != null ? ` · wait ${o.waitMin}m` : ""}
                            </span>
                          </span>
                          <span className="hop-option-meta small">
                            {o.durationMin ? `${o.durationMin}m` : ""}
                            {o.fare != null ? ` · ₹${Math.round(o.fare)}` : ""}
                          </span>
                        </button>
                      ))}
                    </div>
                  )}

                  {complete && (
                    <div className="hop-done small">
                      <span className="material-symbols-outlined">check_circle</span> You've reached {to.name}. Save this route to put it on the map.
                    </div>
                  )}

                  {!hopLoading && !complete && levelsOptions.length === 0 && confirmed.length === 0 && (
                    <div className="small muted">No transit hop found for this leg. Try a cab or walk.</div>
                  )}
                </div>
              )}
            </div>
          )}

          {tab === "private" && (
            <div className="hop-tab-body">
              {rideLoading && <div className="hop-loading"><span className="spinner inline" /> Fetching cab and auto prices…</div>}
              {rideError && <div className="small muted">{rideError}</div>}
              {(rides ?? []).map((p, i) => (
                <button key={`${p.provider}-${i}`} className="ride-row" onClick={() => pickProvider(p)} disabled={busyProvider != null}>
                  <span className="material-symbols-outlined">{p.mode.toLowerCase().includes("bike") ? "two_wheeler" : p.mode.toLowerCase().includes("auto") ? "electric_rickshaw" : "local_taxi"}</span>
                  <span className="ride-row-main">
                    <span className="small"><b>{p.provider}</b> <span className="muted">{p.mode}</span></span>
                    <span className="muted tiny">{p.note || (p.eta_min ? `pickup ~${p.eta_min} min` : "fare estimate")}</span>
                  </span>
                  <span className="ride-row-price">
                    <b>₹{Math.round(p.total)}</b>
                    <span className={`badge ${p.source === "live" ? "live" : "est"}`}>{p.source === "live" ? "LIVE" : "EST"}</span>
                  </span>
                </button>
              ))}
              <button className="ride-row" onClick={pickDrive} disabled={busyProvider != null}>
                <span className="material-symbols-outlined">directions_car</span>
                <span className="ride-row-main">
                  <span className="small"><b>Drive yourself</b></span>
                  <span className="muted tiny">Petrol + distance via the routing engine</span>
                </span>
                <span className="ride-row-price"><b>{busyProvider === "drive" ? "…" : "fuel"}</b></span>
              </button>
              {!rideLoading && !rides?.length && !rideError && (
                <div className="small muted">Private fares come from live pricing when available, otherwise Karnataka rate estimates.</div>
              )}
            </div>
          )}

          {tab === "walk" && (
            <div className="hop-tab-body">
              <div className="hop-walk">
                <div className="row"><span className="material-symbols-outlined">directions_walk</span>
                  <b>{walk.km.toFixed(1)} km</b><span className="muted small">· about {fmtMins(walk.minutes)}</span></div>
                <div className="muted tiny mt6">
                  Straight-line walking estimate. The dashed line on the map is not a turn-by-turn path.
                </div>
                <button
                  className="btn small full mt8"
                  onClick={() => choose({
                    key, mode: "walk", label: "Walk",
                    detail: `${walk.km.toFixed(1)} km straight-line`,
                    fare: 0,
                    duration_min: walk.minutes,
                    geometry: [[from.lat, from.lng], [to.lat, to.lng]],
                    source: "estimated",
                    note: "Straight-line walking estimate.",
                  })}
                >
                  Choose walking
                </button>
              </div>
            </div>
          )}

          {chosen && (
            <div className="hop-current row spread">
              <span className="small">Saved: <b>{chosen.label}</b></span>
              <button className="link-btn" onClick={() => { setTripTransport(key, null); setTripHopRoute(null); }}>
                Remove
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
