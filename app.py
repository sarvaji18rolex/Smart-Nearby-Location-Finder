"""
Location Finder / Nearby Places App
Backend: Python Flask
APIs used (all FREE, no key needed):
  - Nominatim (OpenStreetMap) — geocoding & reverse geocoding
  - Overpass API — POI/nearby places search
  - OpenStreetMap tiles — map rendering (via Leaflet.js in frontend)
"""

from flask import Flask, render_template, request, jsonify
import json
import urllib.request
import urllib.parse
import urllib.error

app = Flask(__name__, template_folder="templates")

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
OVERPASS_URL  = "https://overpass-api.de/api/interpreter"
HEADERS       = {"User-Agent": "LocationFinderApp/1.0 (educational project)"}

# ──────────────────────────────────────────────
# Utility: HTTP GET helper
# ──────────────────────────────────────────────
def http_get(url: str, params: dict = None) -> dict | list | None:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[HTTP ERROR] {e}")
        return None


# ──────────────────────────────────────────────
# Overpass query builder
# ──────────────────────────────────────────────
CATEGORY_TAGS = {
    "restaurant":   'amenity~"restaurant|cafe|fast_food|bar|pub"',
    "hospital":     'amenity~"hospital|clinic|pharmacy|doctors"',
    "hotel":        'tourism~"hotel|hostel|motel|guest_house"',
    "school":       'amenity~"school|university|college|library"',
    "shopping":     'shop~"supermarket|mall|convenience|clothes|electronics"',
    "bank":         'amenity~"bank|atm"',
    "fuel":         'amenity="fuel"',
    "park":         'leisure~"park|garden|playground|sports_centre"',
    "transport":    'amenity~"bus_station|taxi|parking"',
    "worship":      'amenity~"place_of_worship"',
    "entertainment":'amenity~"cinema|theatre|nightclub|casino"',
    "all":          'amenity~".+"',
}

def build_overpass_query(lat: float, lon: float, radius: int, category: str) -> str:
    tag = CATEGORY_TAGS.get(category, CATEGORY_TAGS["all"])
    return f"""
[out:json][timeout:25];
(
  node[{tag}](around:{radius},{lat},{lon});
  way[{tag}](around:{radius},{lat},{lon});
  relation[{tag}](around:{radius},{lat},{lon});
);
out center tags 50;
"""


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/geocode")
def geocode():
    """Convert address text → lat/lon (forward geocoding)."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Missing query"}), 400

    data = http_get(NOMINATIM_URL + "/search", {
        "q": q, "format": "json", "limit": 5,
        "addressdetails": 1
    })
    if data is None:
        return jsonify({"error": "Geocoding service unavailable"}), 503

    results = []
    for r in data:
        results.append({
            "display_name": r.get("display_name", ""),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "type": r.get("type", ""),
            "class": r.get("class", ""),
        })
    return jsonify(results)


@app.route("/api/reverse")
def reverse_geocode():
    """Convert lat/lon → address (reverse geocoding)."""
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid coordinates"}), 400

    data = http_get(NOMINATIM_URL + "/reverse", {
        "lat": lat, "lon": lon,
        "format": "json", "addressdetails": 1
    })
    if data is None:
        return jsonify({"error": "Reverse geocoding unavailable"}), 503

    return jsonify({
        "display_name": data.get("display_name", "Unknown location"),
        "address": data.get("address", {}),
        "lat": lat,
        "lon": lon,
    })


@app.route("/api/nearby")
def nearby():
    """Search for nearby places using Overpass API."""
    try:
        lat    = float(request.args.get("lat"))
        lon    = float(request.args.get("lon"))
        radius = int(request.args.get("radius", 1000))
        cat    = request.args.get("category", "all").lower()
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid parameters"}), 400

    radius = min(max(radius, 100), 10000)   # clamp 100m – 10km
    query  = build_overpass_query(lat, lon, radius, cat)

    req = urllib.request.Request(
        OVERPASS_URL,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode())
    except Exception as e:
        return jsonify({"error": f"Overpass API error: {e}"}), 503

    places = []
    for el in raw.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:en") or tags.get("brand")
        if not name:
            continue

        # coordinates (nodes have lat/lon directly; ways/relations use center)
        if el["type"] == "node":
            elat, elon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            elat, elon = center.get("lat"), center.get("lon")

        if elat is None or elon is None:
            continue

        # Distance (Haversine approximation via simple formula)
        import math
        dlat = math.radians(elat - lat)
        dlon = math.radians(elon - lon)
        a = (math.sin(dlat/2)**2 +
             math.cos(math.radians(lat)) *
             math.cos(math.radians(elat)) *
             math.sin(dlon/2)**2)
        dist_m = int(6371000 * 2 * math.asin(math.sqrt(a)))

        # Friendly category label
        amenity  = tags.get("amenity", "")
        tourism  = tags.get("tourism", "")
        shop     = tags.get("shop", "")
        leisure  = tags.get("leisure", "")
        kind     = amenity or tourism or shop or leisure or "place"

        places.append({
            "id":      el.get("id"),
            "name":    name,
            "lat":     elat,
            "lon":     elon,
            "kind":    kind,
            "dist_m":  dist_m,
            "address": tags.get("addr:full") or
                       ", ".join(filter(None, [
                           tags.get("addr:housenumber"),
                           tags.get("addr:street"),
                           tags.get("addr:city"),
                       ])),
            "phone":   tags.get("phone") or tags.get("contact:phone", ""),
            "website": tags.get("website") or tags.get("contact:website", ""),
            "hours":   tags.get("opening_hours", ""),
            "cuisine": tags.get("cuisine", ""),
            "wheelchair": tags.get("wheelchair", ""),
        })

    places.sort(key=lambda p: p["dist_m"])
    return jsonify({"count": len(places), "places": places[:60]})


@app.route("/api/suggest")
def suggest():
    """Autocomplete suggestions for the search bar."""
    q = request.args.get("q", "").strip()
    if len(q) < 3:
        return jsonify([])

    data = http_get(NOMINATIM_URL + "/search", {
        "q": q, "format": "json", "limit": 7,
        "addressdetails": 0, "namedetails": 0,
    })
    if not data:
        return jsonify([])

    return jsonify([{
        "label": r.get("display_name", ""),
        "lat": float(r["lat"]),
        "lon": float(r["lon"]),
    } for r in data])


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)