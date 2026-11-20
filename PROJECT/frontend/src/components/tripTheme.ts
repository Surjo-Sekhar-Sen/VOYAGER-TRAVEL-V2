import type { Coord, TripPlanPlace } from "../types";

/** Distinct, map-friendly colours per itinerary day. */
export const DAY_COLORS = [
  "#6c5ce7", "#00b894", "#e17055", "#0984e3", "#e84393",
  "#f39c12", "#00cec9", "#8e44ad", "#d63031", "#2d3436",
];

export const dayColor = (day: number): string =>
  DAY_COLORS[(Math.max(1, day) - 1) % DAY_COLORS.length];

export const CATEGORY_META: Record<string, { label: string; icon: string; color: string }> = {
  nature: { label: "Nature", icon: "park", color: "#27ae60" },
  heritage: { label: "Heritage", icon: "account_balance", color: "#b7791f" },
  food: { label: "Food", icon: "restaurant", color: "#e17055" },
  adventure: { label: "Adventure", icon: "attractions", color: "#d63031" },
  shopping: { label: "Shopping", icon: "shopping_bag", color: "#8e44ad" },
  nightlife: { label: "Nightlife", icon: "local_bar", color: "#e84393" },
  wellness: { label: "Wellness", icon: "spa", color: "#00b894" },
  religious: { label: "Temple", icon: "temple_hindu", color: "#f39c12" },
  museum: { label: "Museum", icon: "museum", color: "#0984e3" },
  photo: { label: "Photo spot", icon: "photo_camera", color: "#6c5ce7" },
  offbeat: { label: "Offbeat", icon: "explore", color: "#636e72" },
};

export const categoryMeta = (category: string) =>
  CATEGORY_META[category] ?? { label: category, icon: "place", color: "#6c5ce7" };

export const SLOT_LABEL: Record<string, string> = {
  early_morning: "Sunrise",
  morning: "Morning",
  afternoon: "Afternoon",
  evening: "Evening",
  night: "Night",
};

export function fmtMins(m: number | null | undefined): string {
  if (!m || m <= 0) return "—";
  if (m < 60) return `${Math.round(m)} min`;
  const h = Math.floor(m / 60);
  const r = Math.round(m % 60);
  return r ? `${h}h ${r}m` : `${h}h`;
}

export function fmtFee(place: TripPlanPlace): string {
  if (!place.entry_fee_known) return "Entry not listed";
  if (!place.entry_fee) return "Free entry";
  return `₹${Math.round(place.entry_fee)} entry`;
}

export function hopKey(day: number, from: TripPlanPlace, to: TripPlanPlace): string {
  return `${day}:${from.id}->${to.id}`;
}

export function haversineKm(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const r = 6371;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLng = ((b.lng - a.lng) * Math.PI) / 180;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
  return 2 * r * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
}

/** Concatenate leg geometries ([lat, lng] arrays) into one Leaflet path. */
export function joinGeometry(legs: { geometry?: number[][] | Coord[] | null }[]): Coord[] {
  const out: Coord[] = [];
  for (const leg of legs) {
    for (const pt of leg.geometry ?? []) {
      const lat = Number(pt[0]);
      const lng = Number(pt[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue;
      const last = out[out.length - 1];
      if (last && last[0] === lat && last[1] === lng) continue;
      out.push([lat, lng]);
    }
  }
  return out;
}

export function walkEstimateKm(from: { lat: number; lng: number }, to: { lat: number; lng: number }) {
  const km = haversineKm(from, to);
  // 4.8 km/h is a normal city walking pace; clearly labelled as an estimate.
  return { km, minutes: Math.max(2, Math.round((km / 4.8) * 60)) };
}
