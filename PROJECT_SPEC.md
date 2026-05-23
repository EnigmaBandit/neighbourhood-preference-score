# Neighbourhood Preference Score — Project Spec

## What it does

A single-page web app that lets you score every neighbourhood in NYC based on the amenities you care about. You pick categories (cafés, gyms, subway stations, etc.), assign each one an importance weight from 1–10, hit Generate Map, and the city lights up as a hex grid — bright where your priorities cluster, dark where they don't.

Useful for: deciding where to move, scouting a business location, or just exploring how different parts of the city are served.

---

## Data sources

### OpenStreetMap (OSM)
All point-of-interest (POI) data comes from OSM — the free, community-maintained map of the world. We query it for 20 amenity categories (restaurants, cafés, gyms, schools, subway stations, etc.) using their tag system, e.g. `{"amenity": "cafe"}` or `{"railway": "station"}`.

OSM data is fetched via the **Overpass API** (a read-only query API over OSM's database). No account or API key needed.

### CARTO Basemap Tiles
The dark background map is served by CARTO's free basemap service (`dark-matter` style). These are vector tiles — pre-rendered map tiles that cover the whole world. No API key required.

---

## Tools and libraries

| Library | Role |
|---|---|
| **Streamlit** | Web UI framework. Turns a Python script into an interactive browser app — handles the sidebar, buttons, sliders, spinners, and state management. |
| **OSMnx** | Fetches POI data from OpenStreetMap's Overpass API. Returns a GeoDataFrame with geometries (points, polygons, ways). We reduce polygons to their centroid to get a single lat/lon per feature. |
| **H3** (Uber) | Hexagonal spatial indexing. Divides the Earth into a hierarchy of hexagon grids. We use **resolution 8** (~0.74 km² per cell), which gives neighbourhood-level granularity. Each POI is snapped to the hex cell it falls inside using `h3.latlng_to_cell()`. |
| **pandas** | All score computation happens in DataFrames — groupby, merge, weighted sum. |
| **pydeck** | Python wrapper for deck.gl, a WebGL map rendering library. Renders the `H3HexagonLayer` — fills each hex cell with a colour proportional to its score. Version 0.9+ uses MapLibre GL JS as the map backend. |
| **MapLibre GL JS** | Open-source fork of Mapbox GL JS (used internally by pydeck 0.9+). Renders the basemap vector tiles. Completely free, no token. |
| **pyarrow** | Reads and writes Parquet files for the local POI cache. |

---

## Architecture and data flow

```
User selects amenities + weights
        │
        ▼
load_pois_for_category()
  ├── Check .cache/pois_<category>.parquet  ← local disk cache
  │       hit → read from Parquet (instant)
  │       miss → fetch from OSM via OSMnx (~10-20s)
  │              → reduce polygons to centroids
  │              → save to Parquet for next time
  └── Returns DataFrame[lon, lat]
        │
        ▼
compute_scores()
  ├── For each POI: snap to H3 cell (h3.latlng_to_cell, res=8)
  ├── Filter to cells inside NYC bounding box
  ├── Weighted sum per cell: score = Σ (POI_count × weight) per category
  └── Returns DataFrame[hex, preference_score, <category_counts>]
        │
        ▼
render_map()
  ├── Normalise scores → RGBA colour (dark transparent → bright yellow-green)
  ├── pydeck H3HexagonLayer: one hex per cell, filled with colour
  ├── CARTO dark-matter tiles as basemap (MapLibre GL JS)
  └── Hover tooltip showing per-category counts + total score
```

### Bounding box
NYC is defined as `(west=-74.26, south=40.49, east=-73.70, north=40.92)`. H3 fills this rectangle with ~2,500 hex cells at resolution 8.

### Scoring formula
For each hex cell:

```
preference_score = Σ over selected categories of (count_of_POIs_in_cell × weight)
```

So if you weight cafés at 8 and gyms at 3, a cell with 5 cafés and 2 gyms scores `5×8 + 2×3 = 46`.

### Colour scale
Score 0 → fully transparent (cell not drawn). Max score → `rgb(52, 168, 83)` (bright green). Linear interpolation between them. Alpha also scales from 20 → 230 so low-scoring cells fade out rather than hard-cut.

---

## Caching strategy

Two layers of caching keep the app fast after the first fetch:

1. **Disk cache** (`.cache/pois_<category>.parquet`) — persists POI data across restarts. Checked first; OSM is only hit on a cache miss. TTL is effectively forever (files are manually cleared or expire via `st.cache_data` after 7 days).

2. **Streamlit memory cache** (`@st.cache_data`) — holds the loaded DataFrames in RAM for the lifetime of the server process. Avoids re-reading Parquet on every interaction.

---

## File structure

```
preference_count.py   — entire application (single file)
requirements.txt      — Python dependencies
.cache/               — disk-cached POI Parquet files (gitignored)
venv/                 — Python virtual environment
```

---

## Running it

```bash
source venv/bin/activate
streamlit run preference_count.py
```

No environment variables or secrets needed. No paid API keys anywhere.
