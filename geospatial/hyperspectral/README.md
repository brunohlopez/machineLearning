# AVIRIS-NG Hyperspectral

Two things live here:

1. **Hyperspectral analysis app** (`hsi_app.py`) — interactive spectral
   exploration and dNBR burn-scar mapping on AVIRIS-NG L2 reflectance cubes.
   A 3-5 m hyperspectral complement to the 10 m Sentinel-2 work in
   [`../multispectral`](../multispectral).
2. **Data prep utilities** (`aviris_processor.py`) — batch KML/shapefile tooling
   and California DWR i15 crop labeling.

## What is AVIRIS-NG?

AVIRIS-NG (Airborne Visible/Infrared Imaging Spectrometer - Next Generation) is
a NASA/JPL hyperspectral sensor capturing ~422 contiguous channels from
380-2500 nm at 5 nm spacing, 3-5 m ground resolution. Used for wildfire burn
severity, methane plume detection, mineralogy, and agricultural studies.

## Contents

| File | Description |
|------|-------------|
| `hsi_app.py` | Streamlit hyperspectral app (spectral + burn scar tabs) |
| `aviris_analyzer.py` | `AVIRISAnalyzer` — ENVI reader (RGB, indices, spectra, dNBR) |
| `aviris_processor.py` | KML/shapefile + DWR crop-label utilities; loads the live NASA/JPL flight-line catalog |
| `requirements.txt` | Python dependencies |

## Hyperspectral App

```bash
streamlit run hsi_app.py
```

Three tabs:

**Browse Catalog** — loads the live JPL flight-line catalog (public Google
Sheet, no auth) and draws every matching footprint on a map. Filter by year,
site name, pixel size, or comment keyword; pick a scene to see its metadata
+ RGB quicklook and a link to the [data portal](https://avirisng.jpl.nasa.gov/dataportal/)
to download it (Earthdata login required).

**Spectral Explorer** — point it at a local AVIRIS-NG `*_img` path, pick RGB
or any of 12 indices, click the map to pull the full ~422-band spectrum at
that pixel. Atmospheric water-vapor windows are shaded.

**Wildfire / Burn Scar** — give it a pre-fire and post-fire scene of the same
area; computes dNBR, classifies with USGS severity thresholds, reports burned
area at 3-5 m.

### Reflectance vs radiance

Both AVIRIS-NG product levels are supported, auto-detected by filename
(confirmed by data range):

| Product | Files | Values | Notes |
|---------|-------|--------|-------|
| L2 surface reflectance | `*corr*` / `*rfl*` | float ~0–1 | atmospherically corrected — use for quantitative work |
| L1B at-sensor radiance | `*rdn*` / `*rad*` | tens–hundreds (µW·cm⁻²·nm⁻¹·sr⁻¹) | atmosphere **not** removed; indices/dNBR are uncalibrated proxies (UI warns) |

Radiance is genuinely interesting in the Spectral Explorer — the uncorrected
atmospheric absorption (O₂ at 760 nm, water vapor at 940/1140/1400/1900 nm)
is visible in the raw spectrum.

`AVIRISAnalyzer` exposes `render_rgb`, `render_index`, `get_spectra` and
`burn_scar_analysis`. Bands are looked up by nearest wavelength instead of by
name; RGB stretch and normalized-difference indices are product-agnostic.

## Data Prep Utilities

### AVIRISProcessor
Batch-download KML flight line outlines and convert to ESRI Shapefiles. The
flight-line list is pulled straight from the live NASA/JPL catalog (the same
public Google Sheet the app uses), so there is no spreadsheet to download or
keep up to date.

```python
from aviris_processor import AVIRISProcessor

proc = AVIRISProcessor.from_live_catalog(
    kml_dir="kml/",
    shapefile_dir="shapefiles/",
)
proc.flight_lines          # the catalog as a pandas DataFrame (filter it first if you like)
proc.download_kml_files()
proc.convert_to_shapefiles()
```

To work from a saved copy instead, pass a path to a `.xlsx`/`.csv` export (or a
DataFrame) as the first argument: `AVIRISProcessor("catalog.csv", "kml/", "shapefiles/")`.
The catalog needs `Name` and `link_kml_outline` columns.

### ShapefileProcessor / ShapefileSplitter / ShapefileToMap
Label California DWR i15 land-use shapefiles with crop types, split per
subclass, and render on a Folium map.

```python
from aviris_processor import ShapefileProcessor, ShapefileSplitter, ShapefileToMap

ShapefileProcessor("i15_SacValley.shp", "i15_labeled.shp").process()
ShapefileSplitter("i15_labeled.shp", "split/").split()
ShapefileToMap("split/Almonds.shp").load().build_map().save("almonds.html")
```

## Setup

```bash
pip install -r requirements.txt

# GDAL must be available on your PATH (for ogr2ogr in aviris_processor)
conda install -c conda-forge gdal
```
