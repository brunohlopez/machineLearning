"""
hsi_app.py
----------
AVIRIS-NG hyperspectral Streamlit app — the high-resolution counterpart to
geospatial/multispectral/app.py.

Kept separate from app.py on purpose: HSI cubes are multi-GB and the rendering
is heavier, so it shouldn't slow the Sentinel-2 app. Same UX patterns and the
same index / dNBR logic, driven by a local AVIRIS-NG ENVI cube.

Tabs
----
Browse Catalog   – live JPL flight-line catalog, map + filters + portal links
Spectral Explorer – render RGB / indices, click for the full ~422-band spectrum
Wildfire / Burn Scar – pre/post dNBR severity at 3–5 m

Supports both AVIRIS-NG reflectance (`*corr*`) and radiance (`*rdn*`) cubes;
the analyzer auto-detects which and the UI adjusts labels / caveats.
"""

import folium
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from aviris_analyzer import (
    INDEX_COLORMAPS,
    SEVERITY_CLASSES,
    AVIRISAnalyzer,
    rgba_to_base64,
)

CATALOG_CSV = (
    "https://docs.google.com/spreadsheets/d/"
    "1g_yWgr4kwGPwVCDiFgCX3tZ9tAkxk-oDXxfqv5U4LeU/export?format=csv"
)
DATA_PORTAL = "https://avirisng.jpl.nasa.gov/dataportal/"

st.set_page_config(page_title="AVIRIS-NG Hyperspectral", layout="wide")
st.title("AVIRIS-NG Hyperspectral Analysis")
st.caption(
    "NASA/JPL AVIRIS-NG — ~422 bands, 380–2500 nm, 3–5 m GSD. "
    "Browse the live flight-line catalog, then analyze a local reflectance "
    "(`*corr*`) or radiance (`*rdn*`) cube."
)


@st.cache_resource(show_spinner=False)
def get_analyzer(img_path: str) -> AVIRISAnalyzer:
    """Cached per file path — memory-mapped, so cheap to keep around."""
    return AVIRISAnalyzer(img_path).load()


@st.cache_data(ttl=86_400, show_spinner="Loading AVIRIS-NG catalog…")
def load_catalog() -> pd.DataFrame:
    """Live JPL flight-line catalog (public Google Sheet, no auth)."""
    df = pd.read_csv(CATALOG_CSV, low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    for c in ("Year", "Month", "Day", "Pixel Size", "File Size (GB)",
              "Lon1", "Lon2", "Lon3", "Lon4", "Lat1", "Lat2", "Lat3", "Lat4"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["_date"] = pd.to_datetime(
        dict(year=df.get("Year"), month=df.get("Month"), day=df.get("Day")),
        errors="coerce",
    )
    # Footprint validity = all four corners numeric
    corner_cols = ["Lon1", "Lon2", "Lon3", "Lon4",
                   "Lat1", "Lat2", "Lat3", "Lat4"]
    df["_has_geom"] = df[corner_cols].notna().all(axis=1) if all(
        c in df.columns for c in corner_cols) else False
    return df


def _footprint(row) -> list:
    """(lat, lon) ring from the four catalog corners."""
    return [
        (row["Lat1"], row["Lon1"]),
        (row["Lat2"], row["Lon2"]),
        (row["Lat3"], row["Lon3"]),
        (row["Lat4"], row["Lon4"]),
        (row["Lat1"], row["Lon1"]),
    ]


for key, default in [
    ('hsi_click', None),
    ('hsi_path_loaded', None),
    ('wf_result', None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

tab_browse, tab_spectral, tab_wildfire = st.tabs(
    ["Browse Catalog", "Spectral Explorer", "Wildfire / Burn Scar"]
)

# ── Tab 1: Browse Catalog ─────────────────────────────────────────────────────
with tab_browse:
    try:
        cat = load_catalog()
    except Exception as e:
        st.error(f"Could not load the JPL catalog: {e}")
        cat = None

    if cat is not None:
        f_col, m_col = st.columns([1, 3])

        with f_col:
            st.subheader("Filters")

            yrs = cat["_date"].dt.year.dropna()
            y_lo, y_hi = int(yrs.min()), int(yrs.max())
            yr_range = st.slider("Year", y_lo, y_hi, (max(y_lo, y_hi - 3), y_hi))

            site_q = st.text_input("Site name contains", placeholder="e.g. Caldor, CA")

            px_vals = cat["Pixel Size"].dropna()
            px_max = float(np.nanpercentile(px_vals, 99)) if len(px_vals) else 10.0
            px_cap = st.slider("Max pixel size (m)", 0.5, round(px_max, 1),
                               round(px_max, 1), step=0.5)

            kw = st.text_input("Comment keyword", placeholder="e.g. clear, fire")

            sub = cat[
                cat["_date"].dt.year.between(*yr_range)
                & cat["Pixel Size"].fillna(1e9).le(px_cap)
            ]
            if site_q:
                sub = sub[sub["Site Name"].astype(str)
                          .str.contains(site_q, case=False, na=False)]
            if kw:
                sub = sub[sub["Comments"].astype(str)
                          .str.contains(kw, case=False, na=False)]

            st.metric("Matching scenes", f"{len(sub):,}")
            geom = sub[sub["_has_geom"]]
            MAX_DRAW = 400
            if len(geom) > MAX_DRAW:
                st.caption(f"Showing first {MAX_DRAW} footprints on the map.")
                geom = geom.head(MAX_DRAW)

            names = sub["Name"].astype(str).tolist()
            picked = st.selectbox(
                "Select a scene",
                options=["—"] + names,
                help="Pick a flight line to inspect and get the portal link.",
            )

        with m_col:
            if geom.empty:
                st.info("No footprints match — widen the filters.")
            else:
                clat = geom[["Lat1", "Lat2", "Lat3", "Lat4"]].mean().mean()
                clon = geom[["Lon1", "Lon2", "Lon3", "Lon4"]].mean().mean()
                m = folium.Map(location=[clat, clon], zoom_start=5,
                               tiles="CartoDB dark_matter")

                for _, r in geom.iterrows():
                    is_sel = str(r["Name"]) == picked
                    folium.Polygon(
                        _footprint(r),
                        color="#38bdf8" if not is_sel else "#f59e0b",
                        weight=1 if not is_sel else 3,
                        fill=True,
                        fill_opacity=0.08 if not is_sel else 0.35,
                        tooltip=f"{r['Name']} — {r.get('Site Name', '')}",
                    ).add_to(m)

                st_folium(m, width='100%', height=460, key='cat_map')

            if picked and picked != "—":
                row = sub[sub["Name"].astype(str) == picked].iloc[0]
                st.divider()
                meta_l, meta_r = st.columns([2, 1])

                with meta_l:
                    st.markdown(f"### {row['Name']}")
                    st.write(f"**Site:** {row.get('Site Name', '—')}")
                    d = row["_date"]
                    st.write(f"**Date:** {d.date() if pd.notna(d) else '—'}")
                    st.write(f"**Pixel size:** {row.get('Pixel Size', '—')} m")
                    st.write(
                        f"**Scene size:** {row.get('Number of Lines', '—')} × "
                        f"{row.get('Number of Samples', '—')} px · "
                        f"{row.get('File Size (GB)', '—')} GB"
                    )
                    cm = str(row.get("Comments", "")).replace("<br>", " · ")
                    if cm and cm != "nan":
                        st.caption(cm)

                with meta_r:
                    thumb = row.get("link_rgb_small")
                    if isinstance(thumb, str) and thumb.startswith("http"):
                        st.image(thumb, caption="RGB quicklook")

                st.markdown(
                    f"**Download:** open the [AVIRIS-NG data portal]({DATA_PORTAL}) "
                    f"(Earthdata login required) and search for `{row['Name']}`. "
                    f"Grab the **reflectance** (`{row['Name']}_*corr*`) product, "
                    f"extract `{row.get('Download Name', row['Name'] + '.tar.gz')}`, "
                    "then paste the `*_img` path into the **Spectral Explorer** tab."
                )
                full_rgb = row.get("link_rgb")
                if isinstance(full_rgb, str) and full_rgb.startswith("http"):
                    st.markdown(f"[Full-resolution RGB quicklook]({full_rgb})")

# ── Tab 2: Spectral Explorer ──────────────────────────────────────────────────
with tab_spectral:
    col_ctrl, col_map = st.columns([1, 3])

    with col_ctrl:
        st.subheader("Scene")
        img_path = st.text_input(
            "Cube file path (reflectance or radiance)",
            placeholder=r"C:\data\ang20210815t181024_corr_v2z1_img",
            help="ENVI `*corr*` (reflectance) or `*rdn*` (radiance) file. "
                 "Its .hdr must sit beside it.",
        )
        downsample = st.select_slider(
            "Render downsample", options=[1, 2, 4, 8, 16], value=4,
            help="HSI cubes are huge — higher = faster, coarser overlay.",
        )

        st.subheader("Display Layer")
        layer = st.selectbox("Layer", ['RGB'] + list(INDEX_COLORMAPS.keys()))
        load_btn = st.button("Load Scene", type="primary")

    analyzer = None
    if img_path:
        try:
            analyzer = get_analyzer(img_path)
            if load_btn or st.session_state['hsi_path_loaded'] != img_path:
                st.session_state['hsi_click'] = None
                st.session_state['hsi_path_loaded'] = img_path
        except Exception as e:
            with col_map:
                st.error(f"Could not open scene: {e}")

    with col_ctrl:
        if analyzer is not None:
            rows, cols = analyzer.shape
            badge = ("🟢 Reflectance" if analyzer.is_reflectance
                     else "🟠 Radiance")
            st.success(
                f"{badge} · {analyzer.n_bands} bands · {rows:,}×{cols:,} px · "
                f"{analyzer.wavelengths.min():.0f}–{analyzer.wavelengths.max():.0f} nm"
            )
            if not analyzer.is_reflectance and layer != 'RGB':
                st.warning(
                    f"**{layer} on radiance** is an uncalibrated proxy — "
                    "atmospheric path radiance is *not* removed. Fine for "
                    "visualization, not quantitative work."
                )

    with col_map:
        if analyzer is None:
            st.info("Enter a local AVIRIS-NG cube path and click **Load Scene**, "
                    "or find one in the **Browse Catalog** tab.")
        else:
            with st.spinner(f"Rendering {layer}…"):
                if layer == 'RGB':
                    rgba, bounds = analyzer.render_rgb(downsample=downsample)
                else:
                    rgba, bounds = analyzer.render_index(layer, downsample=downsample)

            south, west, north, east = bounds
            b64 = rgba_to_base64(rgba)
            m = folium.Map(
                location=[(south + north) / 2, (west + east) / 2],
                zoom_start=13,
                tiles='CartoDB dark_matter',
            )
            folium.raster_layers.ImageOverlay(
                image=f"data:image/png;base64,{b64}",
                bounds=[[south, west], [north, east]],
                opacity=0.9,
                name=layer,
            ).add_to(m)
            if st.session_state['hsi_click']:
                folium.CircleMarker(
                    st.session_state['hsi_click'], radius=5,
                    color='#38bdf8', fill=True, fill_opacity=0.9,
                ).add_to(m)
            folium.LayerControl().add_to(m)
            md = st_folium(m, width='100%', height=480, key='hsi_map')

            if md and md.get('last_clicked'):
                st.session_state['hsi_click'] = (
                    md['last_clicked']['lat'], md['last_clicked']['lng']
                )

            if st.session_state['hsi_click']:
                clat, clon = st.session_state['hsi_click']
                with st.spinner("Extracting spectrum…"):
                    spec = analyzer.get_spectra_latlon(clat, clon)

                if not spec:
                    st.warning("No valid spectrum at that pixel (edge / fill value).")
                else:
                    wl  = np.array(list(spec.keys()))
                    val = np.array(list(spec.values()))
                    order = np.argsort(wl)
                    wl, val = wl[order], val[order]

                    fig, ax = plt.subplots(figsize=(9, 3.2))
                    fig.patch.set_facecolor('#0d1117')
                    ax.set_facecolor('#0d1117')
                    ax.plot(wl, val, color='#38bdf8', linewidth=1.3)
                    ax.fill_between(wl, val, alpha=0.12, color='#38bdf8')
                    for lo, hi in [(1340, 1460), (1790, 1960)]:
                        ax.axvspan(lo, hi, color='#475569', alpha=0.25)
                    ax.set_xlabel('Wavelength (nm)', color='#94a3b8')
                    ax.set_ylabel(analyzer.value_label, color='#94a3b8')
                    ax.set_title(
                        f'{"Reflectance" if analyzer.is_reflectance else "Radiance"} '
                        f'spectrum — {len(wl)} bands @ '
                        f'({clat:.5f}°, {clon:.5f}°)',
                        color='white', fontsize=11,
                    )
                    ax.tick_params(colors='#94a3b8')
                    ax.grid(True, alpha=0.12, color='#94a3b8')
                    for sp in ax.spines.values():
                        sp.set_edgecolor('#1e293b')
                    st.pyplot(fig)
                    plt.close(fig)

                    if analyzer.is_reflectance:
                        st.caption(
                            "Shaded bands = atmospheric water-vapor windows "
                            "(1.4, 1.9 µm), removed by L2 correction. Note the "
                            "vegetation red edge near 700 nm and the NIR plateau."
                        )
                    else:
                        st.caption(
                            "Raw at-sensor radiance — the shaded bands show "
                            "**uncorrected** atmospheric water-vapor absorption, "
                            "plus the O₂ notch near 760 nm. This is exactly the "
                            "atmospheric signal L2 reflectance processing removes."
                        )

# ── Tab 3: Wildfire / Burn Scar ───────────────────────────────────────────────
with tab_wildfire:
    st.markdown(
        "dNBR burn severity from **two** AVIRIS-NG scenes of the same area — a "
        "pre-fire and a post-fire acquisition. USGS severity thresholds, at "
        "3–5 m instead of Sentinel-2's 20 m."
    )

    wf_l, wf_r = st.columns([1, 2])

    with wf_l:
        pre_path  = st.text_input("Pre-fire file path",  key='wf_pre',
                                  placeholder=r"C:\data\ang_prefire_corr_v2z1_img")
        post_path = st.text_input("Post-fire file path", key='wf_post',
                                  placeholder=r"C:\data\ang_postfire_corr_v2z1_img")
        wf_ds = st.select_slider("Downsample", options=[1, 2, 4, 8, 16], value=4,
                                 key='wf_ds')
        wf_px = st.number_input("Pixel size (m)", value=4.0, step=0.5, key='wf_px',
                                help="AVIRIS-NG GSD for area stats (scene-dependent).")
        analyze_btn = st.button("Analyze Burn Scar", type="primary")

    with wf_r:
        if analyze_btn and pre_path and post_path:
            try:
                with st.spinner("Loading pre/post scenes and computing dNBR…"):
                    pre  = get_analyzer(pre_path)
                    post = get_analyzer(post_path)
                    dnbr, classified, bounds = pre.burn_scar_analysis(
                        post, downsample=wf_ds
                    )
                    stats = pre.burn_severity_stats(classified, pixel_size_m=wf_px)
                    sev_rgba = pre.render_burn_severity(classified)
                st.session_state['wf_result'] = (
                    dnbr, sev_rgba, bounds, stats,
                    pre.is_reflectance and post.is_reflectance,
                )
            except Exception as e:
                st.error(f"Analysis failed: {e}")

        if st.session_state['wf_result'] is None:
            st.info(
                "Provide a pre-fire and post-fire AVIRIS-NG scene of the same "
                "area, then click **Analyze Burn Scar**."
            )
        else:
            dnbr, sev_rgba, bounds, stats, both_refl = st.session_state['wf_result']
            south, west, north, east = bounds

            if not both_refl:
                st.warning(
                    "One or both scenes are **radiance**. dNBR here is "
                    "**qualitative only** — differing atmosphere/illumination "
                    "between dates is not corrected. Use for visualization, "
                    "not calibrated severity reporting."
                )

            b64 = rgba_to_base64(sev_rgba)
            m = folium.Map(
                location=[(south + north) / 2, (west + east) / 2],
                zoom_start=13, tiles='CartoDB dark_matter',
            )
            folium.raster_layers.ImageOverlay(
                image=f"data:image/png;base64,{b64}",
                bounds=[[south, west], [north, east]],
                opacity=0.85, name="Burn Severity (dNBR)",
            ).add_to(m)
            folium.LayerControl().add_to(m)
            st_folium(m, width='100%', height=440, key='wf_map')

            legend = "<div style='display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:6px'>"
            for label, _, color in SEVERITY_CLASSES:
                legend += (
                    f"<span style='display:inline-flex;align-items:center;gap:4px'>"
                    f"<span style='width:14px;height:14px;border-radius:2px;"
                    f"background:{color};display:inline-block'></span>"
                    f"<span style='font-size:12px'>{label}</span></span>"
                )
            legend += "</div>"
            st.markdown(legend, unsafe_allow_html=True)
            st.divider()

            h_col, s_col = st.columns(2)
            with h_col:
                st.markdown("**dNBR Distribution**")
                valid = dnbr[~np.isnan(dnbr)].ravel()
                fig, ax = plt.subplots(figsize=(5, 3))
                fig.patch.set_facecolor('#0d1117')
                ax.set_facecolor('#0d1117')
                ax.hist(valid, bins=80, color='#E8530A', alpha=0.85)
                for thr, c in [(0.10, '#F5E642'), (0.27, '#F0A500'),
                               (0.44, '#E8530A'), (0.66, '#7A0000')]:
                    ax.axvline(thr, color=c, lw=1, linestyle='--')
                ax.set_xlabel('dNBR', color='#94a3b8')
                ax.set_ylabel('Pixels', color='#94a3b8')
                ax.tick_params(colors='#94a3b8')
                ax.grid(True, alpha=0.12, color='#94a3b8')
                for sp in ax.spines.values():
                    sp.set_edgecolor('#1e293b')
                st.pyplot(fig)
                plt.close(fig)

            with s_col:
                st.markdown("**Burn Severity Breakdown**")
                disp = [
                    {'Severity Class': r['label'],
                     'Area (km²)': r['area_km2'],
                     '% of Scene': f"{r['pct']} %"}
                    for r in stats if r['pixels'] > 0
                ]
                if disp:
                    st.dataframe(disp, use_container_width=True, hide_index=True)
                burned = sum(
                    r['area_km2'] for r in stats
                    if r['label'] not in (
                        "Enhanced Regrowth (High)", "Enhanced Regrowth (Low)", "Unburned"
                    )
                )
                high = next(
                    (r['area_km2'] for r in stats if r['label'] == "High Severity"), 0
                )
                st.metric("Total Burned Area",  f"{burned:,.2f} km²")
                st.metric("High Severity Area", f"{high:,.2f} km²")
