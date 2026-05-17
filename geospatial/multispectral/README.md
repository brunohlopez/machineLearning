# Sentinel-2 Multispectral Analysis

Interactive Streamlit app for Sentinel-2 imagery analysis. **No Google Earth Engine account required** — data streams directly from AWS S3 via the Element84 Earth Search STAC catalog.

## Features

- **Spectral Analysis** — Click any land pixel to plot the full reflectance curve across 12 bands (443–2190 nm)
- **12 Spectral Indices** — Toggle between NDVI, NDWI, EVI, SAVI, NBR, NDMI, GNDVI, NDRE, NDSI, NDBI, MNDWI, NBR2
- **Live Weather** — Current conditions via [Open-Meteo](https://open-meteo.com/) for any clicked point (no API key)
- **Monthly Timelapse** — Animated GIF from monthly median composites for any region
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
├── app.py                 # Streamlit app — Spectral Analysis tab + Timelapse tab
└── requirements.txt
```

### How it works

1. `pystac_client` queries the free Element84 STAC catalog for Sentinel-2 L2A scenes matching the bbox, date range, and cloud cover threshold.
2. `stackstac` lazily stacks the matching scenes into an `xarray.DataArray` in WGS-84, reading only the spatial subset needed from S3.
3. Clouds are masked using the SCL band. A time-median composite is computed via Dask.
4. The composite is rendered as a PNG image overlay on a Folium map. Clicking any pixel extracts the spectrum directly from the in-memory xarray — no additional S3 request.
5. Open-Meteo provides live weather for the clicked point. Swap the base URL to `archive-api.open-meteo.com/v1/archive` and add `start_date`/`end_date` to align weather with the imagery dates.
