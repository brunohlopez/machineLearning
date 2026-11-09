import datetime as dt
import os
import pandas as pd
import streamlit as st
import altair as alt
import folium
import folium.plugins
from streamlit_folium import st_folium
import ee

# -----------------------------
# Earth Engine init
# Set EE_PROJECT to your Google Cloud project ID that has Earth Engine enabled.
# -----------------------------
EE_PROJECT = os.environ.get("EE_PROJECT")

@st.cache_resource
def init_ee():
    try:
        ee.Initialize(project=EE_PROJECT)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=EE_PROJECT)

# -----------------------------
# Helpers
# -----------------------------
def geojson_to_ee_geometry(feature: dict) -> ee.Geometry:
    """Accepts a GeoJSON feature from st_folium draw plugin output."""
    geom = feature["geometry"]
    gtype = geom["type"]
    coords = geom["coordinates"]

    if gtype == "Polygon":
        return ee.Geometry.Polygon(coords)
    if gtype == "MultiPolygon":
        return ee.Geometry.MultiPolygon(coords)
    if gtype == "Point":
        return ee.Geometry.Point(coords)
    if gtype == "LineString":
        return ee.Geometry.LineString(coords)
    raise ValueError(f"Unsupported geometry type: {gtype}")

def ee_tile_layer(img: ee.Image, vis: dict, name: str = "EE Layer", opacity: float = 1.0):
    """Create a Folium TileLayer from an ee.Image using getMapId."""
    map_id = img.getMapId(vis)
    tiles = map_id["tile_fetcher"].url_format
    return folium.raster_layers.TileLayer(
        tiles=tiles,
        attr="Google Earth Engine",
        name=name,
        overlay=True,
        control=True,
        opacity=opacity,
    )

def sentinel2_collection(roi: ee.Geometry, start: str, end: str, cloud_max: float):
    # Harmonized SR is a good default
    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
           .filterBounds(roi)
           .filterDate(start, end)
           .filter(ee.Filter.lte("CLOUDY_PIXEL_PERCENTAGE", cloud_max)))
    return col

def landsat_collection(roi: ee.Geometry, start: str, end: str, cloud_max: float):
    # Landsat 8/9 L2 Collection 2
    col = (ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
           .merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
           .filterBounds(roi)
           .filterDate(start, end)
           .filter(ee.Filter.lte("CLOUD_COVER", cloud_max)))
    return col

def scene_table_from_collection(col: ee.ImageCollection, sensor: str, limit: int = 250) -> pd.DataFrame:
    # Pull lightweight metadata only (avoid heavy server calls)
    def feat(img):
        props = ee.Dictionary({
            "system_index": img.get("system:index"),
            "system_time_start": img.get("system:time_start"),
        })
        if sensor == "Sentinel-2":
            props = props.combine(ee.Dictionary({
                "cloud": img.get("CLOUDY_PIXEL_PERCENTAGE"),
                "platform": img.get("SPACECRAFT_NAME"),
            }))
        else:
            props = props.combine(ee.Dictionary({
                "cloud": img.get("CLOUD_COVER"),
                "platform": img.get("SPACECRAFT_ID"),
            }))
        return ee.Feature(None, props)

    fc = ee.FeatureCollection(col.limit(limit).map(feat))
    rows = fc.getInfo()["features"]

    data = []
    for r in rows:
        p = r["properties"]
        ts = p.get("system_time_start")
        date = dt.datetime.utcfromtimestamp(ts / 1000).date() if ts else None
        data.append({
            "date": str(date) if date else None,
            "system_index": p.get("system_index"),
            "cloud_%": float(p.get("cloud")) if p.get("cloud") is not None else None,
            "platform": p.get("platform"),
        })

    df = pd.DataFrame(data).dropna(subset=["system_index", "date"]).sort_values("date", ascending=False)
    return df

# -----------------------------
# Dynamic World land cover stats
# -----------------------------
DW_CLASSES = [
    (0, "Water"),
    (1, "Trees"),
    (2, "Grass"),
    (3, "Flooded vegetation"),
    (4, "Crops"),
    (5, "Shrub & scrub"),
    (6, "Built area"),
    (7, "Bare ground"),
    (8, "Snow & ice"),
]

def dw_for_date(roi: ee.Geometry, date_str: str):
    """
    Get Dynamic World label image near that date.
    We'll use a 1-day window [date, date+1).
    """
    d0 = ee.Date(date_str)
    d1 = d0.advance(1, "day")
    dw = (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
          .filterBounds(roi)
          .filterDate(d0, d1)
          .select("label")
          .first())
    return dw

def area_by_class_km2(label_img: ee.Image, roi: ee.Geometry, scale_m: int = 10):
    """
    Returns dict class_name -> area_km2 inside ROI using pixelArea.
    """
    pix_area = ee.Image.pixelArea()  # m^2
    results = {}

    for cls_id, cls_name in DW_CLASSES:
        mask = label_img.eq(cls_id)
        area_m2 = pix_area.updateMask(mask).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=roi,
            scale=scale_m,
            maxPixels=1e13,
            bestEffort=True,
        ).get("area")

        # area may be null if that class not present
        area_km2 = ee.Number(area_m2).divide(1e6)
        results[cls_name] = area_km2

    # Turn EE numbers into client dict
    out = {}
    for k, v in results.items():
        out[k] = float(ee.Number(v).getInfo()) if v is not None else 0.0
    return out

# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="GEE Scene Browser + Land Cover", layout="wide")
st.title("🌍 GEE Scene Browser (Individual Images) + Dynamic World Land Cover Time Series")

init_ee()

with st.sidebar:
    st.header("Data selection")
    sensor = st.selectbox("Satellite", ["Sentinel-2", "Landsat 8/9"])
    start_date = st.date_input("Start date", value=dt.date(2020, 1, 1))
    end_date = st.date_input("End date", value=dt.date.today())
    cloud_max = st.slider("Max cloud (%)", 0, 100, 30)

    st.header("Land cover stats")
    compute_dw = st.toggle("Compute Dynamic World class proportions per scene date", value=True)
    st.caption("Dynamic World is 10m. Stats computed inside your drawn ROI.")
    limit = st.slider("Max scenes to list", 10, 250, 80)

# Map
st.subheader("1) Draw an ROI (bbox or polygon)")
m = folium.Map(location=[37.8, -122.3], zoom_start=8, control_scale=True)

draw = folium.plugins.Draw(
    export=False,
    draw_options={
        "polyline": False,
        "circle": False,
        "circlemarker": False,
        "marker": False,
        "rectangle": True,
        "polygon": True,
    },
    edit_options={"edit": True, "remove": True},
)
draw.add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

out = st_folium(m, height=520, width=None, returned_objects=["all_drawings"])

if not out or not out.get("all_drawings"):
    st.info("Draw a rectangle or polygon to load scenes.")
    st.stop()

roi_feature = out["all_drawings"][-1]
roi = geojson_to_ee_geometry(roi_feature)

# Collections & table
st.subheader("2) Browse individual scenes (metadata list)")
start_str = str(start_date)
end_str = str(end_date)

if sensor == "Sentinel-2":
    col = sentinel2_collection(roi, start_str, end_str, cloud_max)
else:
    col = landsat_collection(roi, start_str, end_str, cloud_max)

df = scene_table_from_collection(col, sensor=sensor, limit=limit)

if df.empty:
    st.warning("No scenes found for that ROI/date/cloud filter. Try expanding the date range or increasing cloud max.")
    st.stop()

st.dataframe(df, use_container_width=True, height=260)

selected_index = st.selectbox(
    "Pick a scene (system:index)",
    options=df["system_index"].tolist(),
    index=0
)

selected_row = df[df["system_index"] == selected_index].iloc[0]
selected_date = selected_row["date"]

# Fetch selected image safely (DON'T ee.Image(index))
img = col.filter(ee.Filter.eq("system:index", selected_index)).first()

# Visualization
st.subheader("3) View selected scene (as imagery tiles)")

vis_map = folium.Map(location=[37.8, -122.3], zoom_start=8, control_scale=True)

# Style per sensor
if sensor == "Sentinel-2":
    rgb = img.select(["B4", "B3", "B2"])  # 10m
    rgb_vis = {"min": 0, "max": 3000}
    scale_for_stats = 10
else:
    # Landsat L2 reflectance needs scaling
    # SR scale/offset per docs: scale=0.0000275, offset=-0.2 for optical SR bands
    sr = img.select(["SR_B4", "SR_B3", "SR_B2"]).multiply(0.0000275).add(-0.2)
    rgb = sr
    rgb_vis = {"min": 0.0, "max": 0.3}
    scale_for_stats = 30

# Add RGB + ROI outline
ee_tile_layer(rgb.clip(roi), rgb_vis, name="Selected RGB", opacity=1.0).add_to(vis_map)

# Outline ROI
folium.GeoJson(roi_feature, name="ROI").add_to(vis_map)
folium.LayerControl(collapsed=False).add_to(vis_map)

st_folium(vis_map, height=520, width=None)

st.caption(f"Selected date: **{selected_date}** | system:index: **{selected_index}**")

# Land cover stats per scene date (Dynamic World)
stats_rows = []
if compute_dw:
    st.subheader("4) Land cover proportions over time (Dynamic World)")

    # Compute stats for each scene date (unique dates)
    unique_dates = list(pd.unique(df["date"]))  # strings
    unique_dates = sorted(unique_dates)  # ascending for time series

    prog = st.progress(0)
    for i, d in enumerate(unique_dates):
        label = dw_for_date(roi, d)
        if label is None:
            # no DW that day (can happen)
            row = {"date": d}
            for _, name in DW_CLASSES:
                row[name] = 0.0
            stats_rows.append(row)
            prog.progress((i + 1) / len(unique_dates))
            continue

        areas = area_by_class_km2(label, roi, scale_m=10)
        row = {"date": d, **areas}
        stats_rows.append(row)
        prog.progress((i + 1) / len(unique_dates))

    prog.empty()

    stats_df = pd.DataFrame(stats_rows)
    # Add totals + percentages
    class_cols = [name for _, name in DW_CLASSES]
    stats_df["total_km2"] = stats_df[class_cols].sum(axis=1)
    for c in class_cols:
        stats_df[c + "_pct"] = stats_df.apply(lambda r: (r[c] / r["total_km2"] * 100.0) if r["total_km2"] > 0 else 0.0, axis=1)

    st.dataframe(stats_df, use_container_width=True, height=260)

    # Chart: pick a few classes to plot
    default_plot = ["Trees_pct", "Crops_pct", "Built area_pct", "Water_pct"]
    plot_cols = st.multiselect("Plot classes (percent of ROI)", options=[c + "_pct" for c in class_cols], default=[c for c in default_plot if c in [x + "_pct" for x in class_cols]])
    if plot_cols:
        chart_df = stats_df[["date"] + plot_cols].copy()
        chart_df["date"] = pd.to_datetime(chart_df["date"])

        melted = chart_df.melt("date", var_name="class", value_name="percent")
        ch = alt.Chart(melted).mark_line().encode(
            x="date:T",
            y="percent:Q",
            color="class:N",
            tooltip=["date:T", "class:N", "percent:Q"]
        ).properties(height=320)
        st.altair_chart(ch, use_container_width=True)

    # Merge scene metadata + stats (on date)
    merged = df.merge(stats_df, on="date", how="left")
else:
    merged = df.copy()

st.subheader("5) Export CSV (scene metadata + land cover stats)")
csv_bytes = merged.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download CSV",
    data=csv_bytes,
    file_name=f"scenes_landcover_{sensor.replace('/','_')}_{start_str}_to_{end_str}.csv",
    mime="text/csv"
)

st.success("Done. Draw a new ROI or change filters to refresh.")
