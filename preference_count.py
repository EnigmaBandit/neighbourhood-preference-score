import warnings
from pathlib import Path

import h3
import osmnx as ox
import pandas as pd
import pydeck as pdk
import streamlit as st

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
# osmnx 2.x bbox: (west, south, east, north)
BBOX       = (-74.26, 40.49, -73.70, 40.92)
RESOLUTION = 8   # ~0.74 km² per cell — good neighbourhood granularity

CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True)

CATEGORY_TAGS: dict[str, dict] = {
    "restaurant":     {"amenity": "restaurant"},
    "cafe":           {"amenity": "cafe"},
    "bar":            {"amenity": "bar"},
    "fast food":      {"amenity": "fast_food"},
    "gym":            {"leisure": "fitness_centre"},
    "park":           {"leisure": "park"},
    "playground":     {"leisure": "playground"},
    "supermarket":    {"shop": "supermarket"},
    "grocery":        {"shop": "grocery"},
    "pharmacy":       {"amenity": "pharmacy"},
    "hospital":       {"amenity": "hospital"},
    "dentist":        {"amenity": "dentist"},
    "school":         {"amenity": "school"},
    "university":     {"amenity": "university"},
    "library":        {"amenity": "library"},
    "cinema":         {"amenity": "cinema"},
    "museum":         {"tourism": "museum"},
    "hotel":          {"tourism": "hotel"},
    "bank":           {"amenity": "bank"},
    "subway station": {"railway": "station"},
}

NYC_BBOX_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[
        [BBOX[0], BBOX[1]], [BBOX[2], BBOX[1]],
        [BBOX[2], BBOX[3]], [BBOX[0], BBOX[3]],
        [BBOX[0], BBOX[1]],
    ]],
}

# ── Data ──────────────────────────────────────────────────────────────────────

@st.cache_data
def get_all_cells() -> frozenset[str]:
    return frozenset(h3.geo_to_cells(NYC_BBOX_GEOJSON, RESOLUTION))


@st.cache_data(ttl=604800, show_spinner="Fetching POI data…")
def load_pois_for_category(category: str) -> pd.DataFrame:
    cache = CACHE_DIR / f"pois_{category.replace(' ', '_')}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    try:
        gdf = ox.features_from_bbox(bbox=BBOX, tags=CATEGORY_TAGS[category])
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.apply(
            lambda g: g.centroid if g.geom_type != "Point" else g
        )
        df = pd.DataFrame({"lon": gdf.geometry.x, "lat": gdf.geometry.y})
    except Exception as e:
        st.warning(f"Could not fetch '{category}': {e}")
        df = pd.DataFrame(columns=["lon", "lat"])
    df.to_parquet(cache)
    return df


# ── Scoring ───────────────────────────────────────────────────────────────────

def _col(category: str) -> str:
    """Safe DataFrame column name for a category (spaces → underscores)."""
    return category.replace(" ", "_")


def compute_scores(
    selections: list[dict],
    all_cells: frozenset[str],
) -> pd.DataFrame:
    """Return a DataFrame with one row per hex cell containing the total
    preference_score and a per-category count column for each selection."""
    all_pois: list[pd.DataFrame] = []
    for item in selections:
        df = load_pois_for_category(item["category"])
        if not df.empty:
            df = df.copy()
            df["weight"]   = item["weight"]
            df["category"] = item["category"]
            all_pois.append(df)

    cat_cols = [_col(s["category"]) for s in selections]
    base = pd.DataFrame({"hex": list(all_cells)})

    if not all_pois:
        base["preference_score"] = 0.0
        for col in cat_cols:
            base[col] = 0
        return base

    combined = pd.concat(all_pois, ignore_index=True)
    combined["hex"] = [
        h3.latlng_to_cell(lat, lon, RESOLUTION)
        for lat, lon in zip(combined["lat"], combined["lon"])
    ]
    combined = combined[combined["hex"].isin(all_cells)]

    # Total weighted score
    weighted = combined.groupby("hex")["weight"].sum().reset_index()
    weighted.columns = ["hex", "preference_score"]

    # Per-category counts
    for item in selections:
        col = _col(item["category"])
        counts = (
            combined[combined["category"] == item["category"]]
            .groupby("hex")
            .size()
            .reset_index(name=col)
        )
        weighted = weighted.merge(counts, on="hex", how="left")
        weighted[col] = weighted[col].fillna(0).astype(int)

    result = base.merge(weighted, on="hex", how="left")
    result["preference_score"] = result["preference_score"].fillna(0.0)
    for col in cat_cols:
        result[col] = result[col].fillna(0).astype(int)
    return result


# ── Map ───────────────────────────────────────────────────────────────────────

def _score_to_color(score: float, max_score: float) -> list[int]:
    t = min(score / max_score, 1.0) if max_score > 0 else 0.0
    return [
        int(232 + t * (52  - 232)),
        int(246 + t * (168 - 246)),
        int(240 + t * (83  - 240)),
        int(20  + t * 210),
    ]


def _build_tooltip(selections: list[dict]) -> dict:
    rows = "".join(
        f"<tr><td style='padding-right:10px'>{s['category'].title()}</td>"
        f"<td><b>{{{_col(s['category'])}}}</b></td></tr>"
        for s in selections
    )
    html = (
        f"<table style='border-collapse:collapse'>{rows}"
        "<tr><td colspan='2'><hr style='margin:4px 0;border-color:#555'></td></tr>"
        "<tr><td style='padding-right:10px'>Score</td>"
        "<td><b>{preference_score}</b></td></tr></table>"
    )
    return {
        "html": html,
        "style": {
            "color": "white",
            "backgroundColor": "#1a1a2e",
            "padding": "8px 12px",
            "borderRadius": "4px",
            "fontSize": "13px",
        },
    }


def render_map(scores_df: pd.DataFrame, selections: list[dict]) -> None:
    max_score = float(scores_df["preference_score"].max())
    scored = scores_df[scores_df["preference_score"] > 0].copy()
    scored["preference_score"] = scored["preference_score"].round(1)
    scored["fill_color"] = [
        _score_to_color(s, max_score) for s in scored["preference_score"]
    ]

    layer = pdk.Layer(
        "H3HexagonLayer",
        data=scored,
        get_hexagon="hex",
        get_fill_color="fill_color",
        get_line_color=[255, 255, 255, 30],
        pickable=True,
        filled=True,
        stroked=True,
        line_width_min_pixels=1,
        extruded=False,
    )

    mapbox_token = st.secrets.get("mapbox_token", "")

    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=pdk.ViewState(
                latitude=40.73,
                longitude=-73.99,
                zoom=10.5,
                pitch=0,
            ),
            map_style="mapbox://styles/mapbox/dark-v10",
            mapbox_key=mapbox_token,
            tooltip=_build_tooltip(selections),
        ),
        use_container_width=True,
        height=600,
    )


# ── UI ────────────────────────────────────────────────────────────────────────

st.title("Neighbourhood Preference Score")
st.markdown("""
Identify areas that match your priorities — planning a move, opening a business, or exploring a new area.

**How it works:** select amenities, assign importance weights, and see NYC coloured by how well each
area matches your priorities.

Data: [OpenStreetMap](https://www.openstreetmap.org) · Hexagons: [H3](https://h3geo.org)
""")

for key, default in [
    ("selections", []),
    ("expander_state", False),
    ("show_map", False),
    ("modified_categories", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

available_categories = sorted(CATEGORY_TAGS.keys())


def update_weight(index):
    st.session_state.selections[index]["weight"] = st.session_state[f"weight_{index}"]
    if st.session_state.show_map:
        st.session_state.modified_categories = True


def delete_selection(index):
    del st.session_state.selections[index]
    if not st.session_state.selections:
        st.session_state.show_map = False
    if st.session_state.show_map:
        st.session_state.modified_categories = True


def on_generate_map():
    st.session_state.expander_state = False
    st.session_state.show_map = True
    st.session_state.modified_categories = False


def clear_all():
    st.session_state.selections = []
    st.session_state.show_map = False
    st.session_state.modified_categories = False


@st.fragment()
def category_fragment():
    with st.expander("Add Preferred Amenity", expanded=st.session_state.expander_state):
        available = [
            c for c in available_categories
            if not any(s["category"] == c for s in st.session_state.selections)
        ]
        selected = st.selectbox(
            "Select amenity:",
            available if available else ["All amenities added"],
            index=None,
        )
        weight = st.slider("Importance (1–10):", min_value=1, max_value=10, value=5)

        def add_category(cat):
            st.session_state.expander_state = True
            if cat and cat != "All amenities added":
                st.session_state.selections.append({"category": cat, "weight": weight})
                if st.session_state.show_map:
                    st.session_state.modified_categories = True
            else:
                st.warning("All amenities have been added.")

        st.button("Add", on_click=add_category, args=(selected,))

    if st.session_state.selections:
        st.subheader("Your Priorities")
        for i, item in enumerate(st.session_state.selections):
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1:
                st.write(item["category"])
            with c2:
                st.number_input(
                    f"w_{i}", min_value=1, max_value=10,
                    value=item["weight"], key=f"weight_{i}",
                    label_visibility="collapsed",
                    on_change=update_weight, args=(i,),
                )
            with c3:
                st.button("✕", key=f"del_{i}", on_click=delete_selection, args=(i,))

        if st.button("Generate Map", type="primary", on_click=on_generate_map):
            st.rerun(scope="app")

        if st.session_state.modified_categories:
            st.warning("Priorities changed — click Generate Map to refresh.")


category_fragment()

if st.session_state.show_map:
    all_cells = get_all_cells()
    st.info("First fetch per category takes ~15s. Results are cached for future runs.")
    with st.spinner("Computing scores…"):
        scores = compute_scores(st.session_state.selections, all_cells)

    render_map(scores, st.session_state.selections)

st.button("Clear All", on_click=clear_all)
