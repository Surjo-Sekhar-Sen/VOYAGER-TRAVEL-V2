import { useApp, scoreClass } from "../context/AppContext";
import { RatingBreakdown, photoUrl } from "./PlaceBits";
import "./DiscoveryPanel.css";

export default function DiscoveryPanel() {
  const { selected, showDiscovery, setShowDiscovery, setFlyTo, setDest, setMode } = useApp();
  if (!showDiscovery) return null;

  const cls = selected?.pin_class ?? scoreClass(selected?.rating ? selected.rating * 20 : null);
  const loading = !selected;

  const navigate = () => {
    if (!selected) return;
    setDest({ lat: selected.lat, lng: selected.lng, name: selected.name });
    setMode("atob");
  };

  return (
    <aside className="discovery glass-strong anim-scale">
      <div className="spread">
        <h3>{selected ? "Details" : "Loading…"}</h3>
        <button className="close" onClick={() => setShowDiscovery(false)}>×</button>
      </div>

      {loading ? (
        <div className="disc-skeleton">
          <div className="skeleton hero-skel" />
          <div className="skeleton line-skel" />
          <div className="skeleton line-skel short" />
        </div>
      ) : (
        <div className="disc-body">
          {photoUrl(selected) ? (
            <img className="hero" src={photoUrl(selected)} alt={selected.name} onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
          ) : (
            <div className="hero fallback"><span className="material-symbols-outlined">image</span></div>
          )}
          <div className="spread mt8">
            <h2>{selected.name}</h2>
            <span className={`score-pill ${cls}`}>{selected.reliability_score ?? "—"}%</span>
          </div>
          <div className="muted small">{selected.primary_type ?? selected.types?.[0] ?? ""}</div>
          <div className="muted small mt8">{selected.address}</div>

          <div className="row mt8">
            {selected.rating != null && <span>★ <b>{selected.rating}</b> ({selected.user_rating_count ?? 0})</span>}
            {selected.distance_km != null && <span className="muted">{selected.distance_km.toFixed(1)} km</span>}
          </div>

          {selected.business_status && (
            <div className="mt8">
              <span className={`score-pill ${selected.business_status === "OPERATIONAL" ? "green" : "red"}`}>
                {selected.business_status === "OPERATIONAL" ? "Open" : selected.business_status}
              </span>
            </div>
          )}

          {selected.weekday_hours && selected.weekday_hours.length > 0 && (
            <details className="hours mt8">
              <summary>Opening hours</summary>
              {selected.weekday_hours.map((h) => <div key={h} className="muted small">{h}</div>)}
            </details>
          )}

          <RatingBreakdown dist={selected.rating_distribution} />

          {(selected.summary || selected.verdict) && (
            <div className={`ai-summary ${cls} mt12`}>
              <b>
                {selected.summary_source === "llm" ? "AI review analysis" : "Review analysis"}
              </b>
              {selected.summary && <p className="small">{selected.summary}</p>}
              {selected.verdict && (
                <div className="verdict small"><b>Verdict:</b> {selected.verdict}</div>
              )}
              {selected.positives && selected.positives.length > 0 && (
                <div className="concerns">
                  <b>Praised</b>
                  {selected.positives.map((c, i) => <div key={i} className="small">+ {c}</div>)}
                </div>
              )}
              {selected.concerns && selected.concerns.length > 0 && (
                <div className="concerns">
                  <b>Concerns</b>
                  {selected.concerns.map((c, i) => <div key={i} className="small">• {c}</div>)}
                </div>
              )}
              {selected.reviews_sampled ? (
                <div className="muted small">
                  based on {selected.reviews_sampled} review{selected.reviews_sampled > 1 ? "s" : ""} Google returned
                  {selected.user_rating_count ? ` · ${selected.user_rating_count.toLocaleString()} ratings in total` : ""}
                </div>
              ) : null}
            </div>
          )}

          {selected.reviews && selected.reviews.length > 0 && (
            <div className="reviews mt12">
              <b>Reviews</b>
              {selected.reviews.slice(0, 5).map((r, i) => (
                <div key={i} className="review glass">
                  <div className="spread">
                    <span className="row">
                      {r.avatar ? (
                        <img className="review-avatar" src={r.avatar} alt="" loading="lazy" />
                      ) : (
                        <span className="review-avatar fallback">{r.author_name?.[0] ?? "?"}</span>
                      )}
                      <span className="small"><b>{r.author_name || "Anonymous"}</b></span>
                    </span>
                    <span className="small">{r.rating ? "★ " + r.rating : ""}</span>
                  </div>
                  {r.text && <p className="small">{r.text}</p>}
                  {r.date && <div className="muted small">{r.date}</div>}
                </div>
              ))}
            </div>
          )}

          <div className="row mt12">
            <button className="btn small" onClick={() => setFlyTo({ lat: selected.lat, lng: selected.lng })}>
              Show on map
            </button>
            <button className="btn ghost small" onClick={navigate}>Navigate here</button>
          </div>
        </div>
      )}
    </aside>
  );
}
