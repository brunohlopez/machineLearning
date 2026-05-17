import datetime
import io
import os

import folium
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st
from PIL import Image
from streamlit_folium import st_folium

from sentinel_analysis import (
    INDEX_COLORMAPS,
    SEVERITY_CLASSES,
    SPECTRAL_ASSETS,
    SentinelAnalyzer,
    fetch_weather,
    rgba_to_base64,
)

st.set_page_config(page_title="Sentinel-2 Analysis", layout="wide")
st.title("Sentinel-2 Analysis")

@st.cache_resource
def get_analyzer():
    return SentinelAnalyzer()

analyzer = get_analyzer()

# ── Session state defaults ────────────────────────────────────────────────────
for key, default in [
    ('composite', None),
    ('load_params', {}),
    ('last_click', None),
    ('center_lat', 37.77),
    ('center_lon', -122.42),
    ('wf_dnbr', None),
    ('wf_classified', None),
    ('wf_bounds', None),
    ('wf_stats', None),
    ('wf_params', {}),
]:
    if key not in st.session_state:
        st.session_state[key] = default

tab_spectral, tab_timelapse, tab_wildfire = st.tabs(
    ["Spectral Analysis", "Timelapse", "Wildfire / Burn Scar"]
)

# ── Shared sidebar controls ───────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    start_date  = st.date_input("Start Date", value=datetime.date(2023, 6, 1))
    end_date    = st.date_input("End Date",   value=datetime.date(2023, 9, 30))
    cloud_cover = st.slider("Max Cloud Cover (%)", 0, 100, 25)
    start_str   = start_date.strftime('%Y-%m-%d')
    end_str     = end_date.strftime('%Y-%m-%d')

    st.divider()
    st.caption("Data: Sentinel-2 L2A via AWS Earth Search (no account required)")

# ── Tab 1: Spectral Analysis ──────────────────────────────────────────────────
with tab_spectral:
    col_controls, col_map = st.columns([1, 3])

    with col_controls:
        st.subheader("Area of Interest")
        center_lat = st.number_input("Center Latitude",  value=st.session_state['center_lat'], format="%.4f")
        center_lon = st.number_input("Center Longitude", value=st.session_state['center_lon'], format="%.4f")
        radius_deg = st.slider("Radius (degrees)", 0.05, 0.5, 0.15, step=0.05,
                               help="0.1° ≈ 11 km")

        st.subheader("Display Layer")
        layer_options = ['RGB'] + list(INDEX_COLORMAPS.keys())
        selected_layer = st.selectbox("Layer", layer_options)

        load_btn = st.button("Load Data", type="primary")

    # Compute bbox from center + radius
    bbox = [
        center_lon - radius_deg, center_lat - radius_deg,
        center_lon + radius_deg, center_lat + radius_deg,
    ]
    current_params = {
        'bbox': bbox, 'start': start_str,
        'end': end_str, 'cloud': cloud_cover,
    }

    # Reload only when parameters change or button pressed
    if load_btn or (st.session_state['composite'] is None
                    and st.session_state['load_params'] != current_params):
        with st.spinner("Searching STAC catalog..."):
            items = analyzer.search(bbox, start_str, end_str, cloud_cover)

        if not items:
            st.warning("No scenes found — try widening the date range or cloud cover.")
        else:
            with st.spinner(f"Loading {len(items)} scenes from S3 and computing composite..."):
                stack     = analyzer.load_stack(items, bbox)
                masked    = analyzer.cloud_mask(stack)
                composite = analyzer.median_composite(masked)

            st.session_state['composite']   = composite
            st.session_state['load_params'] = current_params
            st.session_state['last_click']  = None
            st.session_state['center_lat']  = center_lat
            st.session_state['center_lon']  = center_lon

    composite = st.session_state['composite']

    with col_map:
        if composite is None:
            st.info("Set a location and click **Load Data** to fetch imagery.")
        else:
            # Render chosen layer
            if selected_layer == 'RGB':
                rgba, bounds = analyzer.render_rgb(composite)
            else:
                rgba, bounds = analyzer.render_index(composite, selected_layer)

            south, west, north, east = bounds
            b64 = rgba_to_base64(rgba)

            m = folium.Map(
                location=[center_lat, center_lon],
                zoom_start=11,
                tiles='CartoDB dark_matter',
            )
            folium.raster_layers.ImageOverlay(
                image=f"data:image/png;base64,{b64}",
                bounds=[[south, west], [north, east]],
                opacity=0.85,
                name=selected_layer,
            ).add_to(m)

            # Show last-click marker
            if st.session_state['last_click']:
                clat, clon = st.session_state['last_click']
                folium.CircleMarker(
                    [clat, clon], radius=5,
                    color='#38bdf8', fill=True, fill_opacity=0.9,
                ).add_to(m)

            folium.LayerControl().add_to(m)
            map_data = st_folium(m, width='100%', height=500, key='spectral_map')

            # Handle click
            if map_data and map_data.get('last_clicked'):
                clat = map_data['last_clicked']['lat']
                clon = map_data['last_clicked']['lng']
                st.session_state['last_click'] = (clat, clon)

        # Show spectra + weather below the map
        if st.session_state['last_click'] and composite is not None:
            clat, clon = st.session_state['last_click']
            spectra = analyzer.get_spectra(clat, clon, composite)

            spec_col, wx_col = st.columns([2, 1])

            with spec_col:
                if spectra:
                    wavelengths = [SPECTRAL_ASSETS[b] for b in spectra]
                    values      = list(spectra.values())

                    fig, ax = plt.subplots(figsize=(7, 3))
                    fig.patch.set_facecolor('#0d1117')
                    ax.set_facecolor('#0d1117')
                    ax.plot(wavelengths, values, marker='o', color='#38bdf8', linewidth=2)
                    ax.fill_between(wavelengths, values, alpha=0.15, color='#38bdf8')
                    ax.set_xlabel('Wavelength (nm)', color='#94a3b8')
                    ax.set_ylabel('Reflectance', color='#94a3b8')
                    ax.set_title(
                        f'Spectrum — ({clat:.4f}°, {clon:.4f}°)',
                        color='white', fontsize=11,
                    )
                    ax.tick_params(colors='#94a3b8')
                    ax.grid(True, alpha=0.15, color='#94a3b8')
                    for spine in ax.spines.values():
                        spine.set_edgecolor('#1e293b')
                    st.pyplot(fig)
                    plt.close(fig)
                else:
                    st.warning("No clear-sky data at this pixel — try another location.")

            with wx_col:
                with st.spinner("Fetching weather..."):
                    wx = fetch_weather(clat, clon)
                if wx:
                    st.markdown("**Current conditions**")
                    st.metric("Temp (°C)",   wx.get('temperature_2m', '—'))
                    st.metric("Cloud Cover", f"{wx.get('cloud_cover', '—')} %")
                    st.metric("Precip (mm)", wx.get('precipitation', '—'))
                    st.metric("Wind (km/h)", wx.get('wind_speed_10m', '—'))
                    st.caption(
                        "Current weather via Open-Meteo. "
                        "Historical weather aligned to imagery date range coming soon."
                    )

# ── Tab 2: Timelapse ──────────────────────────────────────────────────────────
with tab_timelapse:
    col_tl_left, col_tl_right = st.columns([1, 2])

    with col_tl_left:
        st.subheader("Region of Interest")
        tl_lat = st.number_input("Center Latitude",  value=37.77, format="%.4f", key='tl_lat')
        tl_lon = st.number_input("Center Longitude", value=-122.42, format="%.4f", key='tl_lon')
        tl_radius = st.slider("Radius (degrees)", 0.05, 0.3, 0.10, step=0.05, key='tl_radius',
                              help="Keep small for faster rendering")
        tl_fps = st.slider("Frame rate (fps)", 1, 10, 3)

        tl_bbox = [
            tl_lon - tl_radius, tl_lat - tl_radius,
            tl_lon + tl_radius, tl_lat + tl_radius,
        ]

        create_btn = st.button("Create Timelapse", type="primary")

    with col_tl_right:
        if create_btn:
            with st.spinner("Searching for scenes..."):
                items = analyzer.search(tl_bbox, start_str, end_str, cloud_cover)

            if not items:
                st.warning("No scenes found — try widening the date range or cloud cover.")
            else:
                with st.spinner(f"Rendering monthly composites from {len(items)} scenes..."):
                    frames = analyzer.render_timelapse(items, tl_bbox, resolution=0.002)

                if not frames:
                    st.warning("All months were fully cloudy — no frames to render.")
                else:
                    out_path = "_timelapse.gif"
                    frames[0].save(
                        out_path,
                        save_all=True,
                        append_images=frames[1:],
                        duration=int(1000 / tl_fps),
                        loop=0,
                    )
                    st.image(out_path, caption=f"{len(frames)} monthly frames")
                    with open(out_path, 'rb') as f:
                        st.download_button("Download GIF", f, "timelapse.gif", "image/gif")
        else:
            st.info("Configure a region and click **Create Timelapse**.")

# ── Tab 3: Wildfire / Burn Scar ───────────────────────────────────────────────
with tab_wildfire:
    st.markdown(
        "Map burn severity using **dNBR** (Normalized Burn Ratio difference). "
        "Select a pre-fire and post-fire window; the app fetches Sentinel-2 L2A "
        "composites from AWS and applies USGS severity thresholds."
    )

    wf_col_left, wf_col_right = st.columns([1, 2])

    with wf_col_left:
        st.subheader("Area of Interest")
        wf_lat    = st.number_input("Center Latitude",  value=38.90, format="%.4f", key='wf_lat')
        wf_lon    = st.number_input("Center Longitude", value=-120.80, format="%.4f", key='wf_lon')
        wf_radius = st.slider("Radius (degrees)", 0.05, 0.5, 0.15, step=0.05, key='wf_radius',
                              help="0.15° ≈ 17 km")

        st.subheader("Pre-Fire Window")
        wf_pre_start = st.date_input("Start", value=datetime.date(2021, 6, 1),  key='wf_pre_start')
        wf_pre_end   = st.date_input("End",   value=datetime.date(2021, 8, 31), key='wf_pre_end')

        st.subheader("Post-Fire Window")
        wf_post_start = st.date_input("Start", value=datetime.date(2021, 9, 1),  key='wf_post_start')
        wf_post_end   = st.date_input("End",   value=datetime.date(2021, 11, 30), key='wf_post_end')

        wf_cloud = st.slider("Max Cloud Cover (%)", 0, 100, 20, key='wf_cloud')

        analyze_btn = st.button("Analyze Burn Scar", type="primary")

        st.divider()
        st.markdown(
            "**Default AOI:** Caldor Fire, El Dorado County, CA (Aug 2021).  \n"
            "Adjust coordinates for any fire of interest."
        )

    wf_bbox = [
        wf_lon - wf_radius, wf_lat - wf_radius,
        wf_lon + wf_radius, wf_lat + wf_radius,
    ]
    wf_current_params = {
        'bbox':       wf_bbox,
        'pre_start':  wf_pre_start.isoformat(),
        'pre_end':    wf_pre_end.isoformat(),
        'post_start': wf_post_start.isoformat(),
        'post_end':   wf_post_end.isoformat(),
        'cloud':      wf_cloud,
    }

    with wf_col_right:
        if analyze_btn or (
            st.session_state['wf_dnbr'] is None
            and st.session_state['wf_params'] != wf_current_params
        ):
            pre_str  = f"{wf_pre_start}/{wf_pre_end}"
            post_str = f"{wf_post_start}/{wf_post_end}"

            with st.spinner("Searching pre-fire scenes…"):
                items_pre = analyzer.search(
                    wf_bbox, wf_pre_start.isoformat(), wf_pre_end.isoformat(), wf_cloud
                )
            with st.spinner("Searching post-fire scenes…"):
                items_post = analyzer.search(
                    wf_bbox, wf_post_start.isoformat(), wf_post_end.isoformat(), wf_cloud
                )

            if not items_pre:
                st.warning("No pre-fire scenes found — widen the date range or cloud cover.")
            elif not items_post:
                st.warning("No post-fire scenes found — widen the date range or cloud cover.")
            else:
                with st.spinner(
                    f"Loading {len(items_pre)} pre-fire and {len(items_post)} post-fire "
                    "scenes and computing dNBR…"
                ):
                    dnbr, classified, bounds = analyzer.burn_scar_analysis(
                        items_pre, items_post, wf_bbox
                    )
                    stats = analyzer.burn_severity_stats(classified)

                st.session_state['wf_dnbr']       = dnbr
                st.session_state['wf_classified']  = classified
                st.session_state['wf_bounds']      = bounds
                st.session_state['wf_stats']       = stats
                st.session_state['wf_params']      = wf_current_params

        dnbr       = st.session_state['wf_dnbr']
        classified = st.session_state['wf_classified']
        bounds     = st.session_state['wf_bounds']
        stats      = st.session_state['wf_stats']

        if dnbr is None:
            st.info(
                "Set a pre-fire and post-fire date range, then click **Analyze Burn Scar**.\n\n"
                "The default coordinates cover the **2021 Caldor Fire** in the Sierra Nevada — "
                "one of California's most destructive fires at ~221,000 acres."
            )
        else:
            south, west, north, east = bounds

            # ── Severity map ─────────────────────────────────────────────────
            severity_rgba = analyzer.render_burn_severity(classified)
            sev_b64       = rgba_to_base64(severity_rgba)

            m_wf = folium.Map(
                location=[wf_lat, wf_lon],
                zoom_start=11,
                tiles='CartoDB dark_matter',
            )
            folium.raster_layers.ImageOverlay(
                image=f"data:image/png;base64,{sev_b64}",
                bounds=[[south, west], [north, east]],
                opacity=0.85,
                name="Burn Severity (dNBR)",
            ).add_to(m_wf)
            folium.LayerControl().add_to(m_wf)
            st_folium(m_wf, width='100%', height=460, key='wf_map')

            # ── Legend ───────────────────────────────────────────────────────
            legend_html = (
                "<div style='display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:6px'>"
            )
            for label, _, color in SEVERITY_CLASSES:
                legend_html += (
                    f"<span style='display:inline-flex;align-items:center;gap:4px'>"
                    f"<span style='width:14px;height:14px;border-radius:2px;"
                    f"background:{color};display:inline-block'></span>"
                    f"<span style='font-size:12px'>{label}</span></span>"
                )
            legend_html += "</div>"
            st.markdown(legend_html, unsafe_allow_html=True)

            st.divider()

            # ── dNBR histogram + severity stats side by side ─────────────────
            hist_col, stats_col = st.columns([1, 1])

            with hist_col:
                st.markdown("**dNBR Distribution**")
                valid = dnbr[~np.isnan(dnbr)].ravel()
                fig, ax = plt.subplots(figsize=(5, 3))
                fig.patch.set_facecolor('#0d1117')
                ax.set_facecolor('#0d1117')
                ax.hist(valid, bins=80, color='#E8530A', alpha=0.85, edgecolor='none')
                ax.axvline(0.10,  color='#F5E642', lw=1, linestyle='--', label='Low (0.10)')
                ax.axvline(0.27,  color='#F0A500', lw=1, linestyle='--', label='Mod-Low (0.27)')
                ax.axvline(0.44,  color='#E8530A', lw=1, linestyle='--', label='Mod-High (0.44)')
                ax.axvline(0.66,  color='#7A0000', lw=1, linestyle='--', label='High (0.66)')
                ax.set_xlabel('dNBR', color='#94a3b8')
                ax.set_ylabel('Pixels', color='#94a3b8')
                ax.tick_params(colors='#94a3b8')
                ax.grid(True, alpha=0.12, color='#94a3b8')
                for sp in ax.spines.values():
                    sp.set_edgecolor('#1e293b')
                ax.legend(fontsize=7, facecolor='#0d1117', labelcolor='#94a3b8',
                          framealpha=0.6, edgecolor='#1e293b')
                st.pyplot(fig)
                plt.close(fig)

            with stats_col:
                st.markdown("**Burn Severity Breakdown**")
                rows_display = [
                    {
                        'Severity Class': r['label'],
                        'Area (km²)':     r['area_km2'],
                        '% of AOI':       f"{r['pct']} %",
                    }
                    for r in stats
                    if r['pixels'] > 0
                ]
                if rows_display:
                    st.dataframe(
                        rows_display,
                        use_container_width=True,
                        hide_index=True,
                    )
                burned_km2 = sum(
                    r['area_km2'] for r in stats
                    if r['label'] not in (
                        "Enhanced Regrowth (High)", "Enhanced Regrowth (Low)", "Unburned"
                    )
                )
                high_km2 = next(
                    (r['area_km2'] for r in stats if r['label'] == "High Severity"), 0
                )
                st.metric("Total Burned Area",  f"{burned_km2:,.1f} km²")
                st.metric("High Severity Area", f"{high_km2:,.1f} km²")
