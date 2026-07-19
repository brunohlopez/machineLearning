import datetime
import io
import os
import time

import folium
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
from streamlit_folium import st_folium

from drift import (
    DRIFT_SITES,
    FETCH_TIMEOUT_S,
    FRAME_CAP,
    MAX_RECORD_SECONDS,
    DriftEngine,
    build_gif_bytes,
    build_mp4_bytes,
    drift_component_html,
    site_by_name,
)
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
    ('drift_engine', None),
    ('drift_cfg', None),
    ('drift_running', False),
    ('drift_last', None),
    ('drift_gif', None),
    ('drift_mp4', None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

tab_spectral, tab_timelapse, tab_wildfire, tab_drift = st.tabs(
    ["Spectral Analysis", "Timelapse", "Wildfire / Burn Scar", "Drift"]
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

# ── Tab 4: Drift ──────────────────────────────────────────────────────────────
with tab_drift:
    st.markdown(
        "Cinematic flythrough over Sentinel-2 imagery. Tiles stream in ahead "
        "of the pan; when a corridor runs out (or hits open ocean), the view "
        "can cut to a new location around the world. Export what you watched "
        "as a GIF or record an MP4."
    )

    d_ctl, d_view = st.columns([1, 3])

    with d_ctl:
        st.subheader("Flythrough")
        d_source = st.radio(
            "Imagery source",
            ["Instant basemap", "Live Sentinel-2"],
            key='d_source', horizontal=True,
            help="**Instant basemap**: EOX Sentinel-2 cloudless mosaic — "
                 "pre-rendered tiles, loads in ~1-2 s, fully seamless "
                 "(© EOX, s2maps.eu, free for non-commercial use). "
                 "**Live Sentinel-2**: real dated scenes streamed from S3 "
                 "with your sidebar date/cloud filters — slower (~10-30 s "
                 "per tile) but it's the actual data.",
        )
        d_site = st.selectbox(
            "Start location",
            ["Random"] + [s[0] for s in DRIFT_SITES] + ["Custom coordinates…"],
            key='d_site',
        )
        d_custom_lat = d_custom_lon = None
        if d_site == "Custom coordinates…":
            cc1, cc2 = st.columns(2)
            d_custom_lat = cc1.number_input(
                "Lat", -80.0, 80.0, 37.7700, format="%.4f", key='d_custom_lat'
            )
            d_custom_lon = cc2.number_input(
                "Lon", -180.0, 180.0, -122.4200, format="%.4f", key='d_custom_lon'
            )
        d_speed = st.slider(
            "Speed (deg/s)", 0.002, 0.050, 0.012, step=0.002,
            format="%.3f", key='d_speed',
        )
        d_heading_mode = st.radio(
            "Heading", ["Random walk", "Fixed"], horizontal=True,
            key='d_heading_mode',
        )
        d_heading = None
        if d_heading_mode == "Fixed":
            d_heading = st.slider("Compass (°)", 0, 359, 90, key='d_heading')

        d_radius = st.slider(
            "Tile radius (deg)", 0.05, 0.30, 0.15, step=0.05, key='d_radius',
        )
        d_res = st.select_slider(
            "Resolution (deg/px)", options=[0.0005, 0.001, 0.002],
            value=0.001, key='d_res',
            help="Finer = sharper but slower tile fetches",
        )
        d_gap_fill = st.toggle(
            "Fill data gaps", value=True, key='d_gap_fill',
            disabled=(d_source == "Instant basemap"),
            help="Live source only: fill cloud-masked holes with pixels from "
                 "the unmasked median so the view isn't speckled with black. "
                 "Turn off to see exactly which pixels the cloud mask removed.",
        )
        d_jump = st.toggle(
            "Random jump at corridor end", value=True, key='d_jump',
            help="Cut to a new world location when the corridor runs out "
                 "or imagery goes empty (ocean / no clear-sky)",
        )
        d_corridor = st.slider(
            "Tiles per corridor", 2, 12, 5, key='d_corridor',
            disabled=not d_jump,
        )
        d_fps = st.slider("Export fps", 4, 24, 10, key='d_fps')

        st.caption(
            "While drifting, the app refreshes continuously — pause before "
            "working in the other tabs. Changing tile radius, resolution, or "
            "the sidebar date/cloud filters restarts the drift."
        )

    # ── Engine lifecycle: rebuild only when fetch-relevant params change ─────
    d_cfg = {
        'radius': d_radius, 'res': d_res, 'site': d_site,
        'custom': (d_custom_lat, d_custom_lon),
        'source': d_source, 'gap_fill': d_gap_fill,
        'start': start_str, 'end': end_str, 'cloud': cloud_cover,
    }
    eng = st.session_state['drift_engine']
    if eng is None or st.session_state['drift_cfg'] != d_cfg:
        if eng is not None:
            eng.shutdown()
        if d_site == "Random":
            site = None
        elif d_site == "Custom coordinates…":
            site = (f"Custom ({d_custom_lat:.3f}°, {d_custom_lon:.3f}°)",
                    d_custom_lat, d_custom_lon)
        else:
            site = site_by_name(d_site)
        eng = DriftEngine(
            radius_deg=d_radius, resolution=d_res,
            start=start_str, end=end_str, cloud=cloud_cover, site=site,
            source=('instant' if d_source == "Instant basemap" else 'live'),
            gap_fill=d_gap_fill,
        )
        st.session_state['drift_engine'] = eng
        st.session_state['drift_cfg'] = d_cfg
        st.session_state['drift_running'] = False
        st.session_state['drift_last'] = None

    with d_ctl:
        b_run, b_reset, b_rec = st.columns(3)
        run_label = "⏸ Pause" if st.session_state['drift_running'] else "▶ Start"
        if b_run.button(run_label, key='d_run_btn', type="primary"):
            st.session_state['drift_running'] = not st.session_state['drift_running']
            st.session_state['drift_last'] = None
            st.rerun()
        if b_reset.button("⟲ Reset", key='d_reset_btn'):
            eng.shutdown()
            st.session_state['drift_engine'] = None
            st.session_state['drift_running'] = False
            st.session_state['drift_gif'] = None
            st.session_state['drift_mp4'] = None
            st.rerun()
        rec_label = "⏹ Stop" if eng.recording else "⏺ Record"
        if b_rec.button(rec_label, key='d_rec_btn'):
            if eng.recording:
                eng.recording = False
                if eng.record_frames:
                    with st.spinner("Encoding MP4…"):
                        st.session_state['drift_mp4'] = build_mp4_bytes(
                            eng.record_frames, d_fps
                        )
            else:
                eng.record_frames = []
                eng.recording = True
                st.session_state['drift_mp4'] = None
            st.rerun()

        if st.button("Preload 3×3 region", key='d_preload_btn',
                     help="Fetch the full surrounding grid of tiles up front "
                          "for a long, uninterrupted immersive corridor"):
            eng.preload()

        if st.button("Export GIF", key='d_gif_btn', disabled=not eng.frames):
            with st.spinner("Assembling GIF…"):
                st.session_state['drift_gif'] = build_gif_bytes(eng.frames, d_fps)

        if st.session_state['drift_gif']:
            st.download_button(
                "Download GIF", st.session_state['drift_gif'],
                "drift.gif", "image/gif", key='d_gif_dl',
            )
        if st.session_state['drift_mp4']:
            st.download_button(
                "Download MP4", st.session_state['drift_mp4'],
                "drift.mp4", "video/mp4", key='d_mp4_dl',
            )

    with d_view:
        eng.poll()
        running = st.session_state['drift_running']

        if running and eng.mosaic_b64() is not None:
            now = time.time()
            last = st.session_state['drift_last']
            dt = min(now - last, 5.0) if last else 0.0
            st.session_state['drift_last'] = now
            if d_heading is not None:
                eng.heading = float(d_heading)
            if dt > 0:
                eng.tick(
                    dt, d_speed,
                    random_walk=(d_heading_mode == "Random walk"),
                    capture_fps=d_fps, capture=True,
                )
            # Jump / bounce logic
            if d_jump:
                if eng.jump_target is not None:
                    eng.try_complete_jump()
                elif eng.need_jump(d_corridor):
                    eng.start_jump()
            elif eng.current_tile_empty():
                eng.bounce()
        elif running:
            # First tile still in flight — hold position, keep polling.
            st.session_state['drift_last'] = time.time()

        mos = eng.mosaic_b64()
        if mos is None:
            err = eng.current_tile_error()
            wait = eng.current_wait_seconds()
            if err:
                st.error(f"Couldn't load imagery for **{eng.site_name}**: {err}")
                e_retry, e_other = st.columns(2)
                if e_retry.button("Retry this location", key='d_retry_btn'):
                    eng.retry(eng.current_key())
                    st.rerun()
                if e_other.button("Try a different site", key='d_other_btn'):
                    eng.shutdown()
                    st.session_state['drift_engine'] = None
                    st.rerun()
            else:
                waited = f" — {int(wait)}s elapsed" if wait else ""
                st.info(
                    f"Fetching first tile over **{eng.site_name}** from S3…"
                    f"{waited} (typically 10–30 s, gives up at "
                    f"{FETCH_TIMEOUT_S}s). The pan starts automatically once "
                    "imagery lands."
                )
                if wait and wait > 8:
                    st.caption(
                        "Taking longer than usual — this streams live imagery "
                        "from S3, so it depends on your connection. If it errors "
                        "out you'll get a Retry button here."
                    )
                if running:
                    def _cancel_wait(engine=eng):
                        engine.shutdown()
                        st.session_state['drift_engine'] = None
                        st.session_state['drift_running'] = False
                    st.button(
                        "Cancel and pick another site", key='d_cancel_wait_btn',
                        on_click=_cancel_wait,
                    )
        else:
            b64, bounds = mos
            # If holding at an edge waiting on imagery, freeze the client pan
            # too — otherwise it would extrapolate into the void.
            client_speed = 0.0 if eng.waiting else d_speed
            components.html(
                drift_component_html(
                    b64, bounds, eng.lat, eng.lon,
                    speed=client_speed, heading=eng.heading,
                    tile_size=eng.tile_size, running=running,
                    site_name=eng.site_name,
                ),
                height=480,
            )

        prog = eng.preload_progress()
        if prog is not None:
            st.progress(prog, text=f"Preloading region… {int(prog * 100)} %")
        if eng.waiting:
            st.caption("⏳ Holding at tile edge — imagery ahead is still loading…")

        # ── Status row ───────────────────────────────────────────────────────
        s1, s2, s3, s4 = st.columns(4)
        s1.caption(f"**Site:** {eng.site_name}")
        s2.caption(f"**Pos:** {eng.lat:.3f}°, {eng.lon:.3f}°")
        s3.caption(
            f"**Tiles:** {len(eng.tiles)} cached · {eng.fetching()} fetching"
        )
        s4.caption(
            f"**Corridor:** {eng.tiles_crossed} crossed · "
            f"{len(eng.frames)} frames buffered"
        )

        if eng.jump_target is not None:
            st.caption(f"✈ Prefetching next stop: **{eng.jump_target[0]}**…")
        if eng.frame_cap_hit:
            st.warning(
                f"Frame buffer hit its {FRAME_CAP}-frame cap — GIF export "
                "keeps only the most recent frames."
            )
        if eng.recording:
            rec_s = len(eng.record_frames) / max(1, d_fps)
            st.markdown(
                f"<span style='color:#ef4444'>●</span> Recording — "
                f"{int(rec_s // 60):02d}:{int(rec_s % 60):02d}",
                unsafe_allow_html=True,
            )
            if rec_s >= MAX_RECORD_SECONDS:
                eng.recording = False
                with st.spinner("Recording cap reached — encoding MP4…"):
                    st.session_state['drift_mp4'] = build_mp4_bytes(
                        eng.record_frames, d_fps
                    )
                st.warning(f"Recording auto-stopped at {MAX_RECORD_SECONDS} s.")
            elif rec_s >= 0.8 * MAX_RECORD_SECONDS:
                st.warning(
                    f"Approaching the {MAX_RECORD_SECONDS} s recording cap."
                )

        if running:
            time.sleep(1.2)
            st.rerun()
        elif eng.preload_progress() is not None or (mos is None and eng.fetching()):
            # Not running, but tiles are inbound (preload / first fetch):
            # keep polling so progress and the first frame appear on their own.
            time.sleep(1.0)
            st.rerun()
