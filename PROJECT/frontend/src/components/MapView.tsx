import { useEffect, useMemo, useRef } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, CircleMarker, Circle, useMap } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import { useApp, scoreClass } from "../context/AppContext";
import type { Coord, HopOption, LatLng, TripPlanPlace } from "../types";
import { dayColor, hopKey } from "./tripTheme";

const NUM_COLORS = [
  "#6c5ce7", "#00cec9", "#fd79a8", "#f39c12", "#27ae60",
  "#0984e3", "#e17055", "#a29bfe", "#00b894", "#d63031",
];

// sanity guard: never let a rogue coordinate fly the map outside the Bengaluru area
const BLANGARU = { minLat: 12.0, maxLat: 14.0, minLng: 76.5, maxLng: 78.5 };
function inBengaluru(lat: number, lng: number): boolean {
  return Number.isFinite(lat) && Number.isFinite(lng) &&
    lat >= BLANGARU.minLat && lat <= BLANGARU.maxLat &&
    lng >= BLANGARU.minLng && lng <= BLANGARU.maxLng;
}

function userIcon() {
  return L.divIcon({ className: "", html: `<div class="marker-user"></div>`, iconSize: [14, 14] });
}
function pinIcon(cls: string) {
  return L.divIcon({ className: "", html: `<div class="marker-pin ${cls}"><span>${cls[0].toUpperCase()}</span></div>`, iconSize: [30, 30] });
}
function numIcon(n: number, delayMs = 0) {
  const c = NUM_COLORS[n % NUM_COLORS.length];
  return L.divIcon({
    className: "",
    html: `<div class="pin-wrap" style="animation-delay:${delayMs}ms"><div class="marker-num nearby-pin" style="background:${c}">${n + 1}</div></div>`,
    iconSize: [30, 30],
  });
}

// cluster bubble for wide-radius searches: count badge, splits apart on zoom
function clusterIcon(cluster: L.MarkerCluster) {
  const n = cluster.getChildCount();
  const tier = n < 10 ? "small" : n < 50 ? "medium" : "large";
  const size = tier === "small" ? 40 : tier === "medium" ? 48 : 56;
  return L.divIcon({
    className: "cluster-icon",
    html: `<div class="cluster-num ${tier}"><span>${n}</span></div>`,
    iconSize: L.point(size, size),
  });
}
function starIcon() {
  return L.divIcon({ className: "", html: `<div class="marker-star">★</div>`, iconSize: [40, 40] });
}
function newsIcon(cat: string) {
  const color = { traffic: "#e74c3c", weather: "#3498db", event: "#f1c40f", general: "#95a5a6" }[cat] ?? "#95a5a6";
  return L.divIcon({ className: "", html: `<div class="marker-num" style="background:${color}">!</div>`, iconSize: [22, 22] });
}

function FlyController({ target }: { target: LatLng | null }) {
  const map = useMap();
  useEffect(() => {
    if (target && inBengaluru(target.lat, target.lng)) {
      map.flyTo([target.lat, target.lng], Math.max(map.getZoom(), 14), { duration: 0.9 });
    }
  }, [target, map]);
  return null;
}

const MODE_COLOR: Record<string, string> = {
  bus: "#6c5ce7", metro: "#00cec9", train: "#e17055", walk: "#95a5a6",
  ride: "#f39c12", connecting: "#f39c12", dropoff: "#fd79a8",
};

// distinct per-hop colors so the growing journey is traceable as a numbered
// sequence of differently-colored segments (§1.7)
const HOP_COLORS = [
  "#6c5ce7", "#e17055", "#27ae60", "#0984e3", "#00cec9",
  "#e84393", "#f39c12", "#a29bfe", "#d63031", "#00b894",
];

function hopIcon(n: number) {
  const c = HOP_COLORS[(n - 1) % HOP_COLORS.length];
  return L.divIcon({ className: "", html: `<div class="marker-num" style="background:${c}">${n}</div>`, iconSize: [24, 24] });
}

function legGeometry(opt: HopOption): Coord[] {
  const g = opt.geometry;
  if (!g) return [];
  // geometry comes from the API as [lat, lng]; Leaflet wants [lat, lng]
  return g.map((pt: number[]) => [Number(pt[0]), Number(pt[1])]) as Coord[];
}

function NearbyRadius() {
  const { nearbyBase } = useApp();
  if (!nearbyBase || !inBengaluru(nearbyBase.lat, nearbyBase.lng)) return null;
  return (
    <Circle
      center={[nearbyBase.lat, nearbyBase.lng]}
      radius={nearbyBase.radiusM}
      pathOptions={{
        color: "#6c5ce7",
        fillColor: "#6c5ce7",
        fillOpacity: 0.08,
        dashArray: "6 6",
        weight: 2,
      }}
    />
  );
}

function RoutePolylines() {
  const { journey, ridePath } = useApp();
  const chosen: HopOption[] = (journey.chosenLegs as HopOption[]) ?? [];
  const lines: { pts: Coord[]; mode: string; hop: number }[] = [];

  for (const opt of chosen) {
    const pts = legGeometry(opt);
    if (pts.length < 2) continue;
    const hopIdx = chosen.findIndex((c) => c.optionId === opt.optionId) + 1;
    lines.push({ pts, mode: opt.mode ?? "connecting", hop: hopIdx });
  }

  return (
    <>
      {lines.map((ln, i) => (
        <Polyline
          key={i}
          positions={ln.pts}
          color={HOP_COLORS[(ln.hop - 1) % HOP_COLORS.length]}
          weight={6}
          dashArray={ln.mode === "walk" ? "6 6" : undefined}
          opacity={0.95}
        />
      ))}
      {ridePath && ridePath.length > 1 && (
        <Polyline
          positions={ridePath as Coord[]}
          color={MODE_COLOR.ride}
          weight={5}
          opacity={0.95}
        />
      )}
    </>
  );
}

// numbered markers on the confirmed path (order = journey leg index, §1.7)
function ConfirmedLegs() {
  const { journey } = useApp();
  const chosen = journey.chosenLegs as HopOption[] | undefined;
  if (!chosen?.length) return null;
  return (
    <>
      {chosen.map((opt, i) => {
        const g = opt.geometry;
        if (!g?.length) return null;
        const last = g[g.length - 1];
        const [la, ln] = [Number(last[0]), Number(last[1])];
        if (!inBengaluru(la, ln)) return null;
        return <Marker key={opt.optionId || i} position={[la, ln]} icon={hopIcon(i + 1)} />;
      })}
    </>
  );
}

function Pins() {
  const { places, selected, userLoc, source, dest, journey, news, hoveredPlaceId, setHoveredPlaceId } = useApp();
  const numbered = journey.active ? [] : places; // numbered pins hidden during journey
  const pinEls = useRef(new Map<string, HTMLElement | null>());

  // sync card hover → pin highlight by toggling .hot directly on the DOM element
  // (leaflet setIcon would recreate the element and replay the drop animation)
  useEffect(() => {
    for (const [id, el] of pinEls.current) {
      el?.querySelector(".marker-num")?.classList.toggle("hot", id === hoveredPlaceId);
    }
  }, [hoveredPlaceId]);

  return (
    <>
      {userLoc && <Marker position={[userLoc.lat, userLoc.lng]} icon={userIcon()} />}
      {source && (
        <Marker position={[source.lat, source.lng]} icon={pinIcon("green")}>
          <Popup>Source: {source.name}</Popup>
        </Marker>
      )}
      {dest && (
        <Marker position={[dest.lat, dest.lng]} icon={pinIcon("red")}>
          <Popup>Destination: {dest.name}</Popup>
        </Marker>
      )}
      {selected && (
        <Marker position={[selected.lat, selected.lng]} icon={starIcon()}>
          <Popup>{selected.name}</Popup>
        </Marker>
      )}
      <MarkerClusterGroup
        chunkedLoading
        maxClusterRadius={48}
        showCoverageOnHover={false}
        spiderfyDistanceMultiplier={1.6}
        iconCreateFunction={clusterIcon}
      >
        {numbered.map((p, i) => {
          const cls = p.business_status === "CLOSED_PERMANENTLY" ? "red" : scoreClass(p.rating ? p.rating * 20 : null);
          return (
            <Marker
              key={p.place_id || i}
              position={[p.lat, p.lng]}
              icon={numIcon(i, Math.min(i * 60, 540))}
              ref={(m) => { pinEls.current.set(p.place_id || String(i), m?.getElement() ?? null); }}
              eventHandlers={{
                mouseover: () => setHoveredPlaceId(p.place_id),
                mouseout: () => setHoveredPlaceId(null),
              }}
            >
              <Popup>
                <b>{p.name}</b>
                <div className="muted">{p.address}</div>
                {p.rating != null && <div>★ {p.rating} ({p.user_rating_count ?? 0})</div>}
                <div>
                  <span className={`score-pill ${cls}`}>
                    {p.business_status ?? "OPERATIONAL"}
                  </span>
                </div>
              </Popup>
            </Marker>
          );
        })}
      </MarkerClusterGroup>
      {news.map((n, i) =>
        n.geo ? (
          <Marker key={i} position={[n.geo.lat, n.geo.lng]} icon={newsIcon(n.category ?? "general")}>
            <Popup>
              <b>{n.title}</b>
              {n.summary && <div className="muted">{n.summary}</div>}
            </Popup>
          </Marker>
        ) : null,
      )}
    </>
  );
}

// ---------------------------------------------------------------- trip layer
function tripPinIcon(n: number, color: string) {
  return L.divIcon({
    className: "",
    html: `<div class="trip-pin-wrap"><div class="trip-pin" style="--pin-color:${color}"><span>${n}</span></div><div class="trip-pin-tail" style="--pin-color:${color}"></div></div>`,
    iconSize: [32, 40],
    iconAnchor: [16, 38],
    popupAnchor: [0, -34],
  });
}

function TripLayer() {
  const {
    mode, tripPlan, tripDay, tripTransports, tripHopRoute,
    setTripSelectedPlace, setFlyTo,
  } = useApp();
  if (mode !== "trip" || !tripPlan) return null;

  const days = tripDay == null ? tripPlan.days : tripPlan.days.filter((d) => d.day === tripDay);
  const legs: { key: string; day: number; from: TripPlanPlace; to: TripPlanPlace }[] = [];
  for (const d of days) {
    for (let i = 0; i < d.places.length - 1; i += 1) {
      legs.push({ key: hopKey(d.day, d.places[i], d.places[i + 1]), day: d.day, from: d.places[i], to: d.places[i + 1] });
    }
  }

  return (
    <>
      {days.map((d) => d.places.map((p, i) => {
        const color = dayColor(d.day);
        return (
          <Marker
            key={`${d.day}-${p.id}`}
            position={[p.lat, p.lng]}
            icon={tripPinIcon(i + 1, color)}
            eventHandlers={{
              click: () => setFlyTo({ lat: p.lat, lng: p.lng, name: p.name }),
            }}
          >
            <Popup>
              <div className="trip-popup">
                <div className="trip-popup-day" style={{ color }}>Day {d.day} · stop {i + 1}</div>
                <b>{p.name}</b>
                <div className="muted">{p.suggested_start}{p.suggested_end ? ` – ${p.suggested_end}` : ""} · {p.category}</div>
                <button className="trip-popup-btn" onClick={() => setTripSelectedPlace(p)}>View place details</button>
                <button className="trip-popup-btn ghost" onClick={() => setFlyTo({ lat: p.lat, lng: p.lng, name: p.name })}>Centre map</button>
              </div>
            </Popup>
          </Marker>
        );
      }))}

      {legs.map((leg) => {
        const color = dayColor(leg.day);
        const choice = tripTransports[leg.key];
        const straight: Coord[] = [[leg.from.lat, leg.from.lng], [leg.to.lat, leg.to.lng]];
        if (choice && choice.geometry.length > 1) {
          return (
            <Polyline
              key={`chosen-${leg.key}`}
              positions={choice.geometry}
              color={choice.mode === "walk" ? "#95a5a6" : color}
              weight={choice.mode === "transit" ? 6 : 5}
              opacity={0.95}
              dashArray={choice.mode === "walk" ? "7 9" : undefined}
            />
          );
        }
        return (
          <Polyline
            key={`dash-${leg.key}`}
            positions={straight}
            color={color}
            weight={2.5}
            opacity={0.5}
            dashArray="2 8"
          />
        );
      })}

      {tripHopRoute && tripHopRoute.geometry.length > 1 && (
        <Polyline
          positions={tripHopRoute.geometry}
          color="#f39c12"
          weight={6}
          opacity={0.95}
          dashArray={tripHopRoute.mode === "walk" ? "6 8" : undefined}
        />
      )}
    </>
  );
}

function TripFit() {
  const map = useMap();
  const { mode, tripPlan, tripDay } = useApp();
  useEffect(() => {
    if (mode !== "trip" || !tripPlan) return;
    const days = tripDay == null ? tripPlan.days : tripPlan.days.filter((d) => d.day === tripDay);
    const pts: [number, number][] = [];
    for (const d of days) for (const p of d.places) {
      if (inBengaluru(p.lat, p.lng)) pts.push([p.lat, p.lng]);
    }
    if (pts.length > 1) {
      map.fitBounds(L.latLngBounds(pts), { padding: [56, 56], maxZoom: 14 });
    } else if (pts.length === 1) {
      map.setView(pts[0], 14);
    }
  }, [mode, tripPlan, tripDay, map]);
  return null;
}

function JourneyPosition() {
  const { journey } = useApp();
  if (!journey.active || !journey.position) return null;
  return (
    <CircleMarker center={[journey.position.lat, journey.position.lng]} radius={7} pathOptions={{ color: "#2196f3", fillColor: "#2196f3", fillOpacity: 1 }} />
  );
}

export default function MapView() {
  const { flyTo, userLoc, journey, ridePath, source, dest } = useApp();
  const center: [number, number] = useMemo(() => {
    if (userLoc) return [userLoc.lat, userLoc.lng];
    return [12.9716, 77.5946];
  }, [userLoc]);

  const mapRef = useRef<L.Map | null>(null);
  useEffect(() => {
    if (!mapRef.current) return;
    const pts: [number, number][] = [];
    const chosen = journey.chosenLegs as HopOption[] | undefined;
    for (const opt of chosen ?? []) {
      for (const pt of legGeometry(opt)) {
        const [la, ln] = pt;
        if (inBengaluru(la, ln)) pts.push(pt as [number, number]);
      }
    }
    if (ridePath && ridePath.length > 1) {
      for (const pt of ridePath) {
        const [la, ln] = pt;
        if (inBengaluru(la, ln)) pts.push(pt as [number, number]);
      }
    }
    if (!pts.length) {
      const anchors = [source, dest].filter(Boolean) as LatLng[];
      for (const a of anchors) if (inBengaluru(a.lat, a.lng)) pts.push([a.lat, a.lng]);
    }
    if (pts.length) mapRef.current.fitBounds(L.latLngBounds(pts), { padding: [40, 40] });
  }, [journey.chosenLegs, source, dest, ridePath]);

  return (
    <MapContainer
      center={center}
      zoom={13}
      className="full-map"
      ref={(m) => { mapRef.current = m ?? null; }}
      zoomControl={true}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FlyController target={flyTo} />
      <Pins />
      <NearbyRadius />
      <RoutePolylines />
      <ConfirmedLegs />
      <JourneyPosition />
      <TripLayer />
      <TripFit />
    </MapContainer>
  );
}
