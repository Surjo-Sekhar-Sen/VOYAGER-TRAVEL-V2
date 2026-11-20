import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Coord, LatLng, NewsItem, PlaceDetails, PlaceResult, QuickPlan, RidePrice, SegmentResponse, TripHopRoute, TripInputData, TripPlan, TripPlanPlace, TripTransportChoice, WeatherNow } from "../types";

export const TRIP_STORAGE_KEY = "voyager.trip.v1";

interface PersistedTrip {
  input: TripInputData | null;
  plan: TripPlan | null;
  planKey: string | null;
  transports: Record<string, TripTransportChoice>;
  day: number | null;
}

function readPersistedTrip(): PersistedTrip {
  try {
    const raw = localStorage.getItem(TRIP_STORAGE_KEY);
    if (!raw) throw new Error("empty");
    const parsed = JSON.parse(raw) as Partial<PersistedTrip>;
    return {
      input: parsed.input ?? null,
      plan: parsed.plan ?? null,
      planKey: parsed.planKey ?? null,
      transports: parsed.transports ?? {},
      day: parsed.day ?? null,
    };
  } catch {
    return { input: null, plan: null, planKey: null, transports: {}, day: null };
  }
}

export type Mode = "search" | "atob" | "trip";

export interface NearbyBase {
  lat: number;
  lng: number;
  radiusM: number;
}

/** Where the last search was centred — lets "Nearby" search around the place
 *  the user just looked up instead of throwing that away and falling back to
 *  their GPS position. */
export interface SearchAnchor {
  name: string;
  lat: number;
  lng: number;
}

export interface JourneyState {
  segments: SegmentResponse | null;
  chosenLegs: unknown[];
  active: boolean;
  position: LatLng | null;
}

interface AppState {
  mode: Mode;
  setMode: (m: Mode) => void;
  dark: boolean;
  toggleDark: () => void;

  userLoc: LatLng | null;
  setUserLoc: (p: LatLng | null) => void;

  source: LatLng | null;
  dest: LatLng | null;
  setSource: (p: LatLng | null) => void;
  setDest: (p: LatLng | null) => void;
  swap: () => void;

  weather: WeatherNow | null;
  setWeather: (w: WeatherNow | null) => void;

  places: PlaceResult[];
  setPlaces: (p: PlaceResult[]) => void;
  searchResults: PlaceResult[];
  setSearchResults: (p: PlaceResult[]) => void;
  hoveredPlaceId: string | null;
  setHoveredPlaceId: (id: string | null) => void;
  pinned: PlaceResult | null;
  setPinned: (p: PlaceResult | null) => void;
  searching: boolean;
  setSearching: (v: boolean) => void;
  searched: boolean;
  setSearched: (v: boolean) => void;
  radiusKm: number;
  setRadiusKm: (v: number) => void;
  selected: PlaceDetails | null;
  setSelected: (p: PlaceDetails | null) => void;
  showDiscovery: boolean;
  setShowDiscovery: (v: boolean) => void;

  prices: RidePrice[];
  setPrices: (p: RidePrice[]) => void;
  fuel: number | null;
  setFuel: (f: number | null) => void;
  quickPlans: QuickPlan[];
  setQuickPlans: (p: QuickPlan[]) => void;
  nearbyBase: NearbyBase | null;
  setNearbyBase: (b: NearbyBase | null) => void;
  searchAnchor: SearchAnchor | null;
  setSearchAnchor: (a: SearchAnchor | null) => void;
  clearTransient: () => void;

  ridePath: Coord[] | null;
  setRidePath: (p: Coord[] | null) => void;

  flowOpen: boolean;
  setFlowOpen: (v: boolean) => void;
  flowParams: { groupSize: number; budget: number };
  setFlowParams: (p: { groupSize: number; budget: number }) => void;

  news: NewsItem[];
  setNews: (n: NewsItem[]) => void;

  journey: JourneyState;
  setJourney: (j: Partial<JourneyState>) => void;

  trip: TripInputData | null;
  setTrip: (t: TripInputData | null) => void;
  tripPlan: TripPlan | null;
  setTripPlan: (p: TripPlan | null) => void;
  /** identity of the request the stored plan was generated for */
  tripPlanKey: string | null;
  setTripPlanKey: (k: string | null) => void;
  /** null = full trip */
  tripDay: number | null;
  setTripDay: (d: number | null) => void;
  tripTransports: Record<string, TripTransportChoice>;
  setTripTransport: (key: string, choice: TripTransportChoice | null) => void;
  clearTripTransports: () => void;
  tripSelectedPlace: TripPlanPlace | null;
  setTripSelectedPlace: (p: TripPlanPlace | null) => void;
  /** geometry of the hop currently being previewed, drawn on the map */
  tripHopRoute: TripHopRoute | null;
  setTripHopRoute: (r: TripHopRoute | null) => void;

  flyTo: LatLng | null;
  setFlyTo: (p: LatLng | null) => void;
}

const Ctx = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>("search");
  const [dark, setDark] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  const [userLoc, setUserLoc] = useState<LatLng | null>(null);
  const [source, setSource] = useState<LatLng | null>(null);
  const [dest, setDest] = useState<LatLng | null>(null);
  const [weather, setWeather] = useState<WeatherNow | null>(null);
  const [places, setPlaces] = useState<PlaceResult[]>([]);
  const [searchResults, setSearchResults] = useState<PlaceResult[]>([]);
  const [hoveredPlaceId, setHoveredPlaceId] = useState<string | null>(null);
  const [pinned, setPinned] = useState<PlaceResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [radiusKm, setRadiusKm] = useState(2);
  const [selected, setSelected] = useState<PlaceDetails | null>(null);
  const [showDiscovery, setShowDiscovery] = useState(false);
  const [prices, setPrices] = useState<RidePrice[]>([]);
  const [fuel, setFuel] = useState<number | null>(null);
  const [quickPlans, setQuickPlans] = useState<QuickPlan[]>([]);
  const [nearbyBase, setNearbyBase] = useState<NearbyBase | null>(null);
  const [searchAnchor, setSearchAnchor] = useState<SearchAnchor | null>(null);
  const [ridePath, setRidePath] = useState<Coord[] | null>(null);
  const [flowOpen, setFlowOpen] = useState(false);
  const [flowParams, setFlowParams] = useState({ groupSize: 1, budget: 500 });
  const [news, setNews] = useState<NewsItem[]>([]);
  const [journey, setJourneyState] = useState<JourneyState>({
    segments: null,
    chosenLegs: [],
    active: false,
    position: null,
  });
  const persisted = useState(readPersistedTrip)[0];
  const [trip, setTrip] = useState<TripInputData | null>(persisted.input);
  const [tripPlan, setTripPlan] = useState<TripPlan | null>(persisted.plan);
  const [tripPlanKey, setTripPlanKey] = useState<string | null>(persisted.planKey);
  const [tripDay, setTripDay] = useState<number | null>(persisted.day);
  const [tripTransports, setTripTransports] = useState<Record<string, TripTransportChoice>>(persisted.transports);
  const [tripSelectedPlace, setTripSelectedPlace] = useState<TripPlanPlace | null>(null);
  const [tripHopRoute, setTripHopRoute] = useState<TripHopRoute | null>(null);
  const [flyTo, setFlyTo] = useState<LatLng | null>(null);

  // Persist the last plan + per-hop choices so a reload restores the trip.
  useEffect(() => {
    try {
      const payload: PersistedTrip = { input: trip, plan: tripPlan, planKey: tripPlanKey, transports: tripTransports, day: tripDay };
      localStorage.setItem(TRIP_STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // storage disabled/full — the in-memory trip still works
    }
  }, [trip, tripPlan, tripPlanKey, tripTransports, tripDay]);

  const setTripTransport = (key: string, choice: TripTransportChoice | null) => {
    setTripTransports((prev) => {
      const next = { ...prev };
      if (choice) next[key] = choice;
      else delete next[key];
      return next;
    });
  };

  const clearTripTransports = () => setTripTransports({});

  const toggleDark = () => {
    setDark((d) => {
      const next = !d;
      document.documentElement.classList.toggle("dark", next);
      return next;
    });
  };

  const setJourney = (patch: Partial<JourneyState>) => setJourneyState((j) => ({ ...j, ...patch }));

  const swap = () => {
    setSource(dest);
    setDest(source);
  };

  // NOTE: `searchAnchor` deliberately survives clearTransient() so switching to
  // the Nearby tab keeps searching around the place the user just looked up.
  // clearQuery() (the search box's X) clears it explicitly.
  const clearTransient = () => {
    setPlaces([]);
    setSearchResults([]);
    setSearched(false);
    setPinned(null);
    setSelected(null);
    setShowDiscovery(false);
    setPrices([]);
    setFuel(null);
    setQuickPlans([]);
    setNearbyBase(null);
    setRidePath(null);
    setFlyTo(null);
    setFlowOpen(false);
    setJourney({ segments: null, chosenLegs: [], active: false, position: null });
  };

  // NOTE: this object is rebuilt on every provider render on purpose. It used to
  // be wrapped in useMemo with an incomplete dependency list, so `radiusKm`,
  // `hoveredPlaceId`, `searching` and `searched` went stale for every consumer
  // (hover-sync between the result cards and the map pins silently stopped
  // working, and the nearby radius slider froze until another dep changed).
  const value: AppState = {
    mode, setMode, dark, toggleDark,
    userLoc, setUserLoc,
    source, dest, setSource, setDest, swap,
    weather, setWeather,
    places, setPlaces,
    searchResults, setSearchResults, hoveredPlaceId, setHoveredPlaceId,
    pinned, setPinned,
    searching, setSearching, searched, setSearched, radiusKm, setRadiusKm,
    selected, setSelected, showDiscovery, setShowDiscovery,
    prices, setPrices, fuel, setFuel, quickPlans, setQuickPlans,
    nearbyBase, setNearbyBase, searchAnchor, setSearchAnchor, clearTransient,
    ridePath, setRidePath,
    flowOpen, setFlowOpen, flowParams, setFlowParams,
    news, setNews,
    journey, setJourney,
    trip, setTrip,
    tripPlan, setTripPlan, tripPlanKey, setTripPlanKey,
    tripDay, setTripDay, tripTransports, setTripTransport, clearTripTransports,
    tripSelectedPlace, setTripSelectedPlace,
    tripHopRoute, setTripHopRoute,
    flyTo, setFlyTo,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}

export function scoreClass(score: number | null | undefined): "green" | "yellow" | "orange" | "red" {
  if (score == null) return "yellow";
  if (score >= 70) return "green";
  if (score >= 50) return "yellow";
  if (score >= 30) return "orange";
  return "red";
}
