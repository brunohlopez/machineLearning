# Sentinel-2 Multispectral Analysis

Interactive Streamlit app for Sentinel-2 imagery analysis. **No Google Earth Engine account required** — data streams directly from AWS S3 via the Element84 Earth Search STAC catalog.

## Features

- **Spectral Analysis** — Click any land pixel to plot the full reflectance curve across 12 bands (443–2190 nm)
- **12 Spectral Indices** — Toggle between NDVI, NDWI, EVI, SAVI, NBR, NDMI, GNDVI, NDRE, NDSI, NDBI, MNDWI, NBR2
- **Live Weather** — Current conditions via [Open-Meteo](https://open-meteo.com/) for any clicked point (no API key)
- **Monthly Timelapse** — Animated GIF from monthly median composites for any region
- **Drift Mode** — Cinematic flythrough that pans continuously across imagery, prefetching tiles ahead of the motion; jumps to a new world location when a corridor runs out (or hits ocean). Export the run as a GIF or record an MP4
- **Cloud Masking** — SCL-band masking (cloud shadow, medium/high cloud, cirrus)

## Stack

| Layer | Library |
|---|---|
| Satellite data | Sentinel-2 L2A via [Element84 Earth Search](https://earth-search.aws.element84.com/v1) |
| Lazy S3 loading | `stackstac` |
| STAC search | `pystac-client` |
| Array ops | `xarray` + `numpy` |
| Weather | Open-Meteo REST API |
| UI | `streamlit` + `streamlit-folium` + `folium` |
| Viz | `matplotlib` + `pillow` |

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

No authentication needed.

## Architecture

```
multispectral/
├── sentinel_analysis.py   # SentinelAnalyzer class + fetch_weather() + rgba_to_base64()
├── app.py                 # Streamlit app — Spectral / Timelapse / Wildfire / Drift tabs
├── drift.py               # Drift engine — tile grid, LRU cache, threaded prefetch,
│                          #   random-jump sites, GIF/MP4 export, canvas pan component
└── requirements.txt
```

### Drift mode

The world is modeled as a grid of tiles (side = 2 × tile radius). Each tile is a
cloud-masked median RGB composite fetched through the same STAC/stackstac
pipeline as the other tabs. An LRU cache holds the last few tiles; when the pan
position gets within ~20% of a tile edge in the direction of travel, the next
tile is fetched on a background thread so motion never stalls on S3. Panning is
animated client-side (canvas + `requestAnimationFrame` over a stitched mosaic),
so it stays smooth between Streamlit reruns. When the corridor runs out — N
tiles crossed, or a tile comes back empty (ocean / no clear-sky) — the view
prefetches one of ~30 curated global sites and cuts over without a blank frame.
Viewport frames are buffered continuously (capped at 300) for GIF export, and a
Record toggle writes an MP4 via `imageio-ffmpeg` (capped at 90 s).

Two imagery sources:

- **Instant basemap** (default) — the EOX "Sentinel-2 cloudless" global mosaic
  served as pre-rendered tiles (© EOX, [s2maps.eu](https://s2maps.eu), free for
  non-commercial use). Tiles land in ~1-2 s, fully seamless — the immersive
  option.
- **Live Sentinel-2** — real dated scenes from S3 honoring the sidebar
  date/cloud filters. Optimized for drift: only RGB+SCL assets, capped at the
  4 least-cloudy scenes, one brightness stretch reused across tiles, and a
  "Fill data gaps" option that patches cloud-mask holes from the unmasked
  median.

The pan holds at a tile edge (with an indicator) rather than sliding into
unloaded imagery, and the viewport is clamped to the mosaic on both the client
and in exported frames. Start anywhere via "Custom coordinates", and
"Preload 3×3 region" fetches the whole neighborhood up front for long
uninterrupted corridors.

### How it works

1. `pystac_client` queries the free Element84 STAC catalog for Sentinel-2 L2A scenes matching the bbox, date range, and cloud cover threshold.
2. `stackstac` lazily stacks the matching scenes into an `xarray.DataArray` in WGS-84, reading only the spatial subset needed from S3.
3. Clouds are masked using the SCL band. A time-median composite is computed via Dask.
4. The composite is rendered as a PNG image overlay on a Folium map. Clicking any pixel extracts the spectrum directly from the in-memory xarray — no additional S3 request.
5. Open-Meteo provides live weather for the clicked point. Swap the base URL to `archive-api.open-meteo.com/v1/archive` and add `start_date`/`end_date` to align weather with the imagery dates.
