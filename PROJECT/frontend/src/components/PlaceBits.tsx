/** Shared place-detail pieces used by both DiscoveryPanel and TripPlaceDetails. */

/** Real Google per-star tally ({5: n, 4: n, ...}) -> bars with true percentages. */
export function RatingBreakdown({ dist }: { dist?: Record<string, number> }) {
  if (!dist) return null;
  const total = [1, 2, 3, 4, 5].reduce((a, s) => a + (dist[String(s)] ?? 0), 0);
  if (total <= 0) return null;
  return (
    <div className="rating-breakdown mt8">
      {[5, 4, 3, 2, 1].map((s) => {
        const n = dist[String(s)] ?? 0;
        const pct = (n / total) * 100;
        return (
          <div className="rb-row" key={s}>
            <span className="rb-star">{s}★</span>
            <span className="rb-track">
              <span className="rb-fill" style={{ width: `${pct.toFixed(1)}%` }} />
            </span>
            <span className="rb-pct">{pct >= 10 || pct === 0 ? Math.round(pct) : pct.toFixed(1)}%</span>
          </div>
        );
      })}
      <div className="muted small">from all {total.toLocaleString()} Google ratings</div>
    </div>
  );
}

/** The SerpAPI thumbnail is a 1000x1000 Google image (~280 KB) for a small
 *  card. Google's own sizing suffix either already sets the size or can be
 *  appended to fetch a smaller crop; anything unexpected is left untouched. */
export function rightSized(url: string): string {
  if (!url.includes("googleusercontent.com") && !url.includes("googleapis.com")) return url;
  if (/=w\d+-h\d+/.test(url)) return url.replace(/=w\d+-h\d+[^\s]*$/, "=w640-h420-c-n");
  return `${url}=w640-h420-c-n`;
}

export function photoUrl(place: { photo_name?: string | null; photo_url?: string | null }): string | undefined {
  if (place.photo_url) return rightSized(place.photo_url);
  if (!place.photo_name) return undefined;
  // SerpAPI hits come with a ready-to-fetch image URL; Google photo refs are
  // proxied server-side to keep the key out of the browser.
  if (place.photo_name.startsWith("http")) return rightSized(place.photo_name);
  return `/api/photo?name=${encodeURIComponent(place.photo_name)}`;
}
