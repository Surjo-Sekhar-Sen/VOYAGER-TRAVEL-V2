"""Drive the real app in headless Chrome and assert the requested behaviours.

Flows checked (the ones the user reported):
  1. header "current location" button exists and is visible
  2. search a place -> Details shows a real photo + rating breakdown + verdict
  3. search a place -> switch to Nearby -> Nearby is centred on the SEARCHED place
  4. A->B -> build the hop tree -> the transfers show the WALK between stops
No console errors throughout.
"""
import json
import subprocess
import sys
import time

sys.path.insert(0, ".")
from scripts.browser_check import WS, click_js, evaluate, page_ws, wait_for  # noqa: E402

PORT = 9333
APP = "http://localhost:3000/"


def set_input(ws, selector, value):
    """Type into a React-controlled input (native setter + input event)."""
    return evaluate(ws, f"""
    (() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return 'NOT_FOUND';
      const proto = el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, {json.dumps(value)});
      el.dispatchEvent(new Event('input', {{ bubbles: true }}));
      return el.value;
    }})()
    """)


def click(ws, selector, index=0):
    return evaluate(ws, click_js(selector, index))


def main():
    ws = WS(page_ws(PORT, "localhost:3000"))
    ws.call("Runtime.enable")
    ws.call("Log.enable")
    ws.call("Page.enable")
    console_errors = []

    # start from a clean load so each run is independent of the previous one
    ws.call("Page.navigate", {"url": APP})
    time.sleep(3)

    # ---- 1. app mounted + header current-location button -------------------
    wait_for(ws, "!!document.querySelector('.app-shell')", timeout=60, label="app shell")
    wait_for(ws, "!!document.querySelector('.loc-btn')", label="location button")
    loc = evaluate(ws, """
    (() => {
      const b = document.querySelector('.loc-btn');
      const r = b.getBoundingClientRect();
      return { label: b.querySelector('.loc-btn-label')?.textContent,
               tip: b.parentElement.getAttribute('data-tip'),
               visible: r.width > 0 && r.height > 0 && getComputedStyle(b).display !== 'none',
               width: Math.round(r.width), height: Math.round(r.height) };
    })()
    """)
    print("1. current-location button:", json.dumps(loc))
    assert loc["visible"] and loc["width"] > 60, "location button not a visible labelled button"

    # ---- 2. search -> details (photo + breakdown + verdict) ---------------
    set_input(ws, ".search-panel .text-input", "Toit Brewery Indiranagar")
    # the round button doubles as "clear" once text is present, so a search is
    # started the way a user does it: pick a suggestion (or press Enter)
    wait_for(ws, "document.querySelectorAll('.search-panel .suggest-item').length > 0",
             timeout=90, label="suggestions")
    click(ws, ".search-panel .suggest-item", 0)
    wait_for(ws, "document.querySelectorAll('.place-card').length > 0", timeout=120, label="results")
    n = evaluate(ws, "document.querySelectorAll('.place-card').length")
    print(f"2. search returned {n} cards")
    click(ws, ".place-card .btn", 0)  # Details
    wait_for(ws, "!!document.querySelector('.discovery .disc-body')", timeout=120, label="details panel")
    detail = wait_for(ws, """
    (() => {
      const img = document.querySelector('.discovery .hero');
      const pick = document.querySelector('.discovery .hero.fallback') ? null : img;
      const bars = document.querySelectorAll('.rating-breakdown .rb-row').length;
      const verdict = document.querySelector('.ai-summary .verdict')?.textContent || '';
      const summary = document.querySelector('.ai-summary p')?.textContent || '';
      // wait for the hero image to actually finish loading (naturalWidth > 0)
      const loaded = pick ? (pick.complete && pick.naturalWidth > 0) : false;
      if (!summary || !bars || !pick || !loaded) return null;
      return { photo_src: pick.getAttribute('src').slice(0, 60),
               photo_loaded: true,
               bars, verdict: verdict.slice(0, 90), summary: summary.slice(0, 110),
               avatars: document.querySelectorAll('.review-avatar').length };
    })()
    """, timeout=120, label="details content + hero image")
    print("   details:", json.dumps(detail, indent=None)[:400])
    assert detail["photo_loaded"], "hero photo did not load"
    assert detail["bars"] == 5, "rating breakdown missing"
    assert detail["verdict"], "verdict missing"

    # ---- 3. search -> Nearby uses the searched place ----------------------
    click(ws, ".discovery .close")
    time.sleep(0.4)
    click(ws, ".seg-btn", 1)  # Nearby tab
    time.sleep(0.8)
    banner = wait_for(ws, """
    (() => {
      const b = document.querySelector('.pinned-banner');
      if (!b) return null;
      const t = b.textContent || '';
      return t.includes('Near your search') ? t : null;
    })()
    """, timeout=20, label="nearby banner centred on the searched place")
    print("3. nearby banner:", json.dumps(banner))
    assert "Toit" in banner, f"Nearby did not use the searched place: {banner}"

    # the "My location" button must re-base Nearby on GPS (headless Chrome denies
    # geolocation, so the app falls back to the honest Bengaluru centre) and the
    # banner must stop pointing at the searched place
    click(ws, ".pinned-banner .btn", 0)
    rebased = wait_for(ws, """
    (() => {
      const t = document.querySelector('.pinned-banner')?.textContent || '';
      return (t.includes('Near your location') || t.includes('Bengaluru centre')) ? t : null;
    })()
    """, timeout=20, label="nearby re-based on my location")
    print("   after My location:", json.dumps(rebased))
    assert "Toit" not in rebased, f"Nearby still anchored to the search: {rebased}"

    # the header pill must survive a click (re-detect) without breaking the app
    click(ws, ".loc-btn")
    time.sleep(1.5)
    assert evaluate(ws, "!!document.querySelector('.loc-btn-label')"), "location pill label vanished"
    print("   location pill label after click:",
          json.dumps(evaluate(ws, "document.querySelector('.loc-btn-label').textContent")))

    # ---- 4. A->B hop tree shows the walk between hops --------------------
    click(ws, ".bottom-nav .tab", 1)  # A->B
    wait_for(ws, "!!document.querySelector('.atob-panel')", label="a-to-b panel")
    inputs = "document.querySelectorAll('.atob-panel .auto .text-input')"
    wait_for(ws, f"{inputs}.length === 2", label="two autocomplete inputs")
    def type_place(idx, text):
        return evaluate(ws, f"""
        (() => {{
          const el = {inputs}[{idx}];
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          setter.call(el, {json.dumps(text)});
          el.dispatchEvent(new Event('input', {{ bubbles: true }}));
          return el.value;
        }})()
        """)

    def present(sel: str) -> str:
        return f"!!document.querySelector({json.dumps(sel)})"

    src_box = ".atob-panel .auto:nth-of-type(1)"
    dst_box = ".atob-panel .auto:nth-of-type(3)"

    type_place(0, "Rajajinagar")
    wait_for(ws, present(f"{src_box} .suggest-item"), timeout=90, label="source suggestions")
    click(ws, f"{src_box} .suggest-item", 1)  # skip the "Current location" row
    wait_for(ws, f"{inputs}[0].value.length > 0", timeout=20, label="source set")
    print("4. source set to:", evaluate(ws, f"{inputs}[0].value"))

    type_place(1, "MG Road Bengaluru")
    wait_for(ws, present(f"{dst_box} .suggest-item"), timeout=90, label="destination suggestions")
    click(ws, f"{dst_box} .suggest-item", 0)
    wait_for(ws, f"{inputs}[1].value.length > 0", timeout=20, label="destination set")
    print("   destination set to:", evaluate(ws, f"{inputs}[1].value"))

    # Find routes is disabled until both ends are set
    enabled = wait_for(ws, f"!{inputs}[0].closest('.atob-panel').querySelector('.btn.full').disabled",
                       timeout=20, label="find-routes enabled")
    print("   find-routes enabled:", enabled)
    click(ws, ".atob-panel .btn.full", 0)  # Find routes
    wait_for(ws, "!!document.querySelector('.flow-sheet .tree-svg')", timeout=150, label="hop tree")
    tree = wait_for(ws, """
    (() => {
      const svg = document.querySelector('.flow-sheet .tree-svg');
      if (!svg) return null;
      const nodes = [...svg.querySelectorAll('text.node-label')].map(t => t.textContent);
      const edges = [...svg.querySelectorAll('text.edge-label')].map(t => t.textContent);
      if (nodes.length < 2) return null;
      return { nodes, edges, walkEdges: edges.filter(e => e.startsWith('walk')) };
    })()
    """, timeout=150, label="hop tree content")
    print("   tree nodes:", json.dumps(tree["nodes"])[:220])
    print("   tree edges:", json.dumps(tree["edges"])[:220])
    print("   walk edges:", json.dumps(tree["walkEdges"]))

    # choose the recommended hop, then confirm a TRANSFER WALK appears in the tree
    click(ws, ".flow-sheet .tree-node.clickable", 0)
    time.sleep(0.8)
    click(ws, ".flow-sheet .hop-detail .btn.full", 0)  # "Take this hop"
    time.sleep(1.5)
    after = wait_for(ws, """
    (() => {
      const svg = document.querySelector('.flow-sheet .tree-svg');
      if (!svg) return null;
      const edges = [...svg.querySelectorAll('text.edge-label')].map(t => t.textContent);
      const crumbs = [...document.querySelectorAll('.breadcrumb .crumb')].map(c => c.textContent);
      if (!edges.some(e => e.startsWith('walk'))) return null;
      return { edges, crumbs: crumbs.slice(0, 12) };
    })()
    """, timeout=150, label="tree after choosing a hop")
    print("   edges after hop:", json.dumps(after["edges"])[:260])
    print("   breadcrumb:", json.dumps(after["crumbs"])[:260])

    # ---- console errors --------------------------------------------------
    for ev in ws.events:
        m = ev.get("method")
        if m == "Log.entryAdded" and ev["params"]["entry"].get("level") == "error":
            console_errors.append(ev["params"]["entry"].get("text", "")[:200])
        if m == "Runtime.exceptionThrown":
            console_errors.append(str(ev["params"].get("exceptionDetails", {}).get("text"))[:200])
    print("\nconsole errors:", len(console_errors))
    for e in console_errors[:8]:
        print("   -", e)
    assert not console_errors, "console errors present"
    print("\nBROWSER CHECKS PASSED")


if __name__ == "__main__":
    main()
