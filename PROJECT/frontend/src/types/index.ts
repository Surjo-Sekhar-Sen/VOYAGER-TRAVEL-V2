export type Coord = [number, number];

// ============================================================ shared
export type ScoreClass = "green" | "yellow" | "orange" | "red";

export interface LatLng {
  lat: number;
  lng: number;
  name?: string;
}

// ============================================================ weather
export interface WeatherHour {
  time: string;
  temp_c: number | null;
  condition: string;
  rain_prob: number | null;
}

export interface WeatherNow {
  temp_c: number | null;
  condition: string;
  weather_code?: number;
  humidity?: number;
  wind_kmh?: number;
  is_day?: boolean;
  rain_next_hour?: boolean;
  hourly?: WeatherHour[];
  source?: string;
}

// ============================================================ news
export interface NewsItem {
  title: string;
  text?: string;
  url?: string;
  source?: string;
  category?: "traffic" | "weather" | "event" | "general";
  geo?: { name?: string; lat: number; lng: number } | null;
  summary?: string;
  ts?: number;
  stale?: boolean;
}

// ============================================================ places
export interface PlaceResult {
  place_id: string;
  name: string;
  address: string;
  lat: number;
  lng: number;
  rating: number | null;
  user_rating_count: number | null;
  price_level?: number | null;
  business_status?: string | null;
  open_now?: boolean | null;
  weekday_hours?: string[];
  types?: string[];
  primary_type?: string | null;
  photo_name?: string | null;
  distance_km?: number | null;
  query?: string;
}

export interface Review {
  author_name: string;
  rating: number;
  text: string;
  date: string;
  source: string;
  avatar?: string;
  author_review_count?: number | null;
}

export interface PlaceDetails extends PlaceResult {
  phone?: string | null;
  website?: string | null;
  reviews: Review[];
  sentiment_avg?: number | null;
  reliability_score?: number | null;
  pin_class?: "green" | "yellow" | "red" | null;
  summary?: string;
  concerns?: string[];
  positives?: string[];
  verdict?: string;
  /** real Google per-star tally over ALL ratings, e.g. {5: 38910, 4: 17833} */
  rating_distribution?: Record<string, number>;
  /** how many review bodies the analysis was actually built from */
  reviews_sampled?: number;
  summary_source?: "llm" | "heuristic" | "none" | string;
}

// ============================================================ rides
export interface RidePrice {
  provider: string;
  mode: string;
  total: number;
  per_person: number;
  eta_min?: number | null;
  source: "live" | "estimated";
  note?: string;
}

export interface DriveRoute {
  geometry: Coord[];
  distance_m: number;
  duration_s: number;
  path_source: string;
  mode: string;
  fuel_price_per_liter: number;
}

// ============================================================ routes / segments
export interface PlaceModel {
  lat: number;
  lng: number;
  name: string;
}

export interface StopRef {
  name: string;
  lat: number;
  lng: number;
}

export interface AccessWalk {
  stopName: string;
  lat: number;
  lng: number;
  durationMin: number;
  distanceKm: number;
  /** true when this walk is between two stops (bus → walk → bus) */
  isTransferWalk?: boolean;
  fromStop?: string;
}

export interface HopOption {
  optionId: string;
  mode: "walk" | "bus" | "metro" | "train" | "ride" | string;
  routeNumber?: string;
  fromStop?: StopRef;
  destinationStop: StopRef;
  departureTime?: string;
  arrivalTime?: string;
  durationMin?: number;
  distanceKm?: number;
  fare?: number | null;
  perPersonFare?: number | null;
  walk_to_board?: number | null;
  geometry?: Coord[] | number[][] | null;
  geometrySource?: string;
  status?: string;
  isTopRecommended?: boolean;
  isMetroTransfer?: boolean;
  transitOptionsFromThisStop?: number;
  connectedFrom?: string | null;
  probeNext?: ProbeOption[];
  exceedsBudget?: boolean;
  accessWalk?: AccessWalk;
  /** walkable last leg straight to the destination */
  isFinalWalk?: boolean;
  /** real wait at the boarding stop before this departure, minutes */
  waitMin?: number | null;
  /** wait longer than the builder's comfort threshold */
  longWait?: boolean | null;
  /** this ride ends within a short walk of the destination */
  reachesNearDestination?: boolean;
  remainingToDestKm?: number;
}

export interface ProbeOption {
  destinationStop: StopRef;
  mode: string;
  routeNumber?: string;
  departureTime?: string;
  arrivalTime?: string;
  fare?: number;
  isProbe?: boolean;
}

export interface Segment {
  from?: StopRef | null;
  to?: StopRef | null;
  title?: string;
  options: HopOption[];
}

export interface SegmentResponse {
  journey: Record<string, unknown>;
  segments: Segment[];
  probes?: ProbeOption[];
  warnings?: string[];
  journeyComplete: boolean;
  timeline?: unknown[];
}

export interface JourneyComplete {
  totalTimeMin?: number;
  totalFare?: number;
  segmentCount?: number;
  timeline?: unknown[];
}

// ============================================================ langgraph live context
export interface LiveContext {
  weather: WeatherNow;
  traffic: { ratio?: number; label?: string; source?: string; alerts?: string[] } | Record<string, never>;
  news: NewsItem[];
  prices: RidePrice[];
  reviews?: PlaceDetails | null;
  factors?: {
    time_of_day?: string;
    rain_next_hour?: boolean;
    traffic_label?: string;
    safety?: string;
  };
  errors?: string[];
}

// assistant (/api/langgraph/ask) — the live context the question is asked about
// and the synthesis the backend returns (it never invents numbers).
export interface AskContext {
  source?: PlaceModel | null;
  destination?: PlaceModel | null;
  place?: PlaceResult | null;
  groupSize?: number;
  budget?: number;
}

export interface AskResponse {
  live_context: LiveContext;
  synthesis?: { answer?: string; factors?: string[] };
}

export interface ScoredRoute {
  legs: unknown[];
  total_fare: number;
  total_duration_min: number;
  total_walk_km: number;
  transfers: number;
  per_person_fare: number;
  scores: Record<string, number | string>;
  cc_score: number;
  rank: number;
  best_match: boolean;
  explanation?: string | null;
}

// ============================================================ quick suggestion (one-shot A*)
export interface QuickLeg {
  mode: string;
  route_number?: string | null;
  from_stop: string;
  to_stop: string;
  from_lat: number;
  from_lng: number;
  to_lat: number;
  to_lng: number;
  line?: string | null;
  depart_time?: string | null;
  arrive_time?: string | null;
  duration_min: number;
  distance_m: number;
  fare: number;
  per_person_fare: number;
  geometry: number[][];
  geometry_source: string;
  status: string;
  alternate_routes: string[];
}

export interface QuickPlan {
  legs: QuickLeg[];
  total_fare: number;
  total_duration_min: number;
  total_walk_km: number;
  transfers: number;
  per_person_fare: number;
  summary: string;
}

export interface QuickSuggestionResponse {
  plans: QuickPlan[];
}

// ============================================================ trip planner
export type GroupType = "solo" | "couple" | "friends" | "family" | "seniors";
export type Pace = "relaxed" | "balanced" | "packed";
export type BudgetShape = "total" | "perPerson";

export interface TripPlacePick {
  name: string;
  lat: number;
  lng: number;
}

export interface BudgetSplits {
  stay: number;
  food: number;
  transport: number;
  attractions: number;
  misc: number;
}

export interface TripInputData {
  destination: TripPlacePick | null;
  suggestDestination: boolean;
  durationMode: "dates" | "days";
  startDate: string | null;
  endDate: string | null;
  days: number;
  groupSize: number;
  groupType: GroupType;
  hasKids: boolean;
  budget: number;
  budgetShape: BudgetShape;
  budgetSplits: BudgetSplits;
  /** Backend category keys (heritage, food, nature, ...). */
  categories: string[];
  /** Legacy label list kept for older saved drafts. */
  interests: string[];
  pace: Pace;
}

// ============================================================ trip plan (generated itinerary)
export interface TripPlanPlace {
  id: string;
  osm_id?: string;
  /** Google place_id — enables live reviews/photos on the details panel. */
  place_id?: string;
  name: string;
  category: string;
  description: string;
  address?: string;
  photo_url?: string;
  duration_min: number;
  duration_known?: boolean;
  entry_fee: number;
  entry_fee_child?: number | null;
  entry_fee_known?: boolean;
  rating: number;
  rating_known?: boolean;
  review_count: number;
  best_times: string[];
  crowd: Record<string, string>;
  opening_hours: string;
  weekly_closures: string[];
  lat: number;
  lng: number;
  tags: string[];
  family_friendly: boolean;
  physically_demanding: boolean;
  accessibility_notes: string;
  suitability?: Record<string, boolean>;
  destination: string;
  data_source: string;
  data_is_estimated: boolean;
  verified?: boolean;
  score: number;
  components: Record<string, number>;
  why: string;
  rank: number;
  // scheduling (added by generate_plan)
  slot?: "early_morning" | "morning" | "afternoon" | "evening" | "night";
  suggested_start?: string;
  suggested_end?: string;
  /** straight-line km from the previous stop on the same day */
  travel_from_prev_km?: number;
}

export interface TripPlanDay {
  day: number;
  theme?: string;
  place_count: number;
  total_activity_min: number;
  total_distance_km?: number;
  start_time?: string;
  end_time?: string;
  places: TripPlanPlace[];
}

export interface TripPlan {
  destination: string;
  group_type: string;
  interests: string[];
  categories?: string[];
  data_source?: string;
  pace: string;
  days: TripPlanDay[];
  total_places: number;
  total_activity_min?: number;
  total_distance_km?: number;
  disclaimer: string;
  relaxed: boolean;
  warning?: string;
}

// A travel option the traveller picked for one hop of the itinerary.
export interface TripTransportChoice {
  key: string;
  mode: "transit" | "private" | "walk";
  label: string;
  detail?: string;
  fare?: number | null;
  duration_min?: number | null;
  geometry: Coord[];
  source?: "live" | "estimated" | "planned";
  note?: string;
}

export interface TripHopRoute {
  key: string;
  mode: string;
  geometry: Coord[];
}
