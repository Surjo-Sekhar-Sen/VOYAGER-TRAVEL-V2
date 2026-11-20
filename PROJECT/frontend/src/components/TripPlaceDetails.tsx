import { useEffect, useMemo, useState } from "react";
import { useApp } from "../context/AppContext";
import { api } from "../services/api";
import type { PlaceDetails } from "../types";
import { RatingBreakdown, photoUrl as imageUrl } from "./PlaceBits";
import { categoryMeta, fmtFee, fmtMins, SLOT_LABEL } from "./tripTheme";
import "./TripPlaceDetails.css";

// Session cache: opening the same place again should not spend another
// SerpAPI quota unit. The backend also caches for 24h.
const detailCache = new Map<string, PlaceDetails>();

function fact(icon: string, label: string, value: string, muted = false) {
  return { icon, label, value, muted };
}

export default function TripPlaceDetails() {
  const { tripSelectedPlace, setTripSelectedPlace, setFlyTo, setTripDay, tripPlan } = useApp();
  const place = tripSelectedPlace;
  const [details, setDetails] = useState<PlaceDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAllReviews, setShowAllReviews] = useState(false);

  const dayNumber = useMemo(() => {
    if (!place || !tripPlan) return null;
    const day = tripPlan.days.find((d) => d.places.some((p) => p.id === place.id));
    return day?.day ?? null;
  }, [place, tripPlan]);

  useEffect(() => {
    setShowAllReviews(false);
    if (!place) return;
    setDetails(null);
    setError(null);
    if (!place.place_id) return;
    const cached = detailCache.get(place.place_id);
    if (cached) { setDetails(cached); return; }

    let cancelled = false;
    setLoading(true);
    api.enrichPlace({
      place_id: place.place_id,
      name: place.name,
      address: place.address ?? "",
      lat: place.lat,
      lng: place.lng,
      rating: place.rating_known ? place.rating : null,
      user_rating_count: place.review_count || null,
      photo_name: place.photo_url || null,
    })
      .then((res) => {
        if (cancelled) return;
        if ((res as unknown as { error?: string }).error) {
          setError((res as unknown as { error?: string }).error ?? "Live details unavailable.");
          return;
        }
        detailCache.set(place.place_id!, res);
        setDetails(res);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Live details unavailable."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [place]);

  if (!place) return null;

  const meta = categoryMeta(place.category);
  const hero = imageUrl({ photo_url: details?.photo_name || place.photo_url });
  const rating = details?.rating ?? (place.rating_known ? place.rating : null);
  const reviewCount = details?.user_rating_count ?? place.review_count;
  const reviews = details?.reviews ?? [];
  const shownReviews = showAllReviews ? reviews : reviews.slice(0, 3);

  const facts = [
    fact("schedule", "Suggested time", place.suggested_start
      ? `${place.suggested_start} – ${place.suggested_end}${place.slot ? ` · ${SLOT_LABEL[place.slot] ?? place.slot}` : ""}`
      : "Flexible"),
    fact("hourglass", "Visit length", fmtMins(place.duration_min)),
    fact("confirmation_number", "Entry", fmtFee(place)),
    fact("schedule", "Timings", place.opening_hours || details?.weekday_hours?.[0] || "Not listed"),
    fact("family_restroom", "Good for", place.family_friendly ? "Families" : "Adults"),
    fact("accessible", "Accessibility", place.accessibility_notes || "Not listed"),
  ];

  return (
    <aside className="trip-details glass-strong anim-scale" role="dialog" aria-label={place.name}>
      <button className="trip-details-close" onClick={() => setTripSelectedPlace(null)} title="Close">×</button>

      <div className={`td-hero ${hero ? "" : "fallback"}`}>
        {hero ? (
          <img src={hero} alt={place.name} onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
        ) : (
          <span className="material-symbols-outlined">{meta.icon}</span>
        )}
        <span className="td-hero-grad" />
        <div className="td-hero-text">
          {dayNumber != null && <span className="td-day">Day {dayNumber}</span>}
          <h2>{place.name}</h2>
          <div className="row wrap td-hero-meta">
            <span className="td-cat" style={{ background: meta.color }}>{meta.label}</span>
            {rating ? <span>★ <b>{rating.toFixed(1)}</b> {reviewCount ? `(${reviewCount.toLocaleString()})` : ""}</span> : <span className="muted">Rating not listed</span>}
            {place.verified && <span className="badge live">GOOGLE</span>}
          </div>
        </div>
      </div>

      <div className="td-body">
        <p className="td-desc">{place.description}</p>
        {place.address && (
          <div className="td-address small muted">
            <span className="material-symbols-outlined">location_on</span> {details?.address || place.address}
          </div>
        )}

        <div className="td-facts">
          {facts.map((f) => (
            <div className="td-fact" key={f.label}>
              <span className="material-symbols-outlined">{f.icon}</span>
              <span>
                <span className="muted tiny">{f.label}</span>
                <span className={f.muted ? "muted small" : "small"}>{f.value}</span>
              </span>
            </div>
          ))}
        </div>

        {place.why && (
          <div className="td-why">
            <span className="material-symbols-outlined">auto_awesome</span>
            <span className="small">{place.why}</span>
          </div>
        )}

        <div className="td-source small">
          <span className={`badge ${place.verified ? "live" : "est"}`}>{place.verified ? "LIVE GOOGLE" : "CURATED"}</span>
          <span className="muted">
            {place.data_is_estimated
              ? "Durations, fees and crowd levels are curated estimates — verify before visiting."
              : "Live venue data."}
          </span>
        </div>

        {loading && (
          <div className="td-live-loading">
            <span className="spinner inline" /> Fetching live Google reviews & timings…
          </div>
        )}

        {!loading && error && (
          <div className="td-live-error small muted">
            <span className="material-symbols-outlined">cloud_off</span> {error} Curated details above are still available.
          </div>
        )}

        {!loading && details && (
          <div className="td-live">
            <div className="td-live-head row spread">
              <b className="small">Live Google details</b>
              <span className="badge live">LIVE</span>
            </div>
            {details.weekday_hours && details.weekday_hours.length > 0 && (
              <details className="td-hours">
                <summary className="small">Opening hours</summary>
                {details.weekday_hours.map((h) => <div key={h} className="muted small">{h}</div>)}
              </details>
            )}
            <RatingBreakdown dist={details.rating_distribution} />

            {(details.summary || details.verdict) && (
              <div className="td-review-summary">
                <b className="small">{details.summary_source === "llm" ? "AI review analysis" : "Review analysis"}</b>
                {details.summary && <p className="small">{details.summary}</p>}
                {details.verdict && <div className="small"><b>Verdict:</b> {details.verdict}</div>}
                {details.positives && details.positives.length > 0 && (
                  <div className="small td-pos">+ {details.positives.slice(0, 3).join(" · ")}</div>
                )}
              </div>
            )}

            {reviews.length > 0 && (
              <div className="td-reviews">
                <div className="row spread">
                  <b className="small">Recent reviews</b>
                  {reviews.length > 3 && (
                    <button className="link-btn" onClick={() => setShowAllReviews((v) => !v)}>
                      {showAllReviews ? "Show less" : `All ${reviews.length}`}
                    </button>
                  )}
                </div>
                {shownReviews.map((r, i) => (
                  <div className="td-review" key={i}>
                    <div className="row spread">
                      <span className="row">
                        {r.avatar
                          ? <img className="review-avatar" src={r.avatar} alt="" loading="lazy" />
                          : <span className="review-avatar fallback">{r.author_name?.[0] ?? "?"}</span>}
                        <b className="small">{r.author_name || "Google user"}</b>
                      </span>
                      <span className="small">{r.rating ? `★ ${r.rating}` : ""}</span>
                    </div>
                    {r.text && <p className="small">{r.text}</p>}
                    {r.date && <div className="muted tiny">{r.date}</div>}
                  </div>
                ))}
                <div className="muted tiny">Google reviews via SerpAPI · {details.reviews_sampled ?? reviews.length} sampled</div>
              </div>
            )}
          </div>
        )}

        <div className="row gap td-actions">
          <button className="btn small" onClick={() => setFlyTo({ lat: place.lat, lng: place.lng, name: place.name })}>
            <span className="material-symbols-outlined">my_location</span> Show on map
          </button>
          {dayNumber != null && (
            <button className="btn ghost small" onClick={() => { setTripDay(dayNumber); setTripSelectedPlace(null); }}>
              <span className="material-symbols-outlined">calendar_view_day</span> Focus day {dayNumber}
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
