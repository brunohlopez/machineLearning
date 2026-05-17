# AVIRIS-NG Hyperspectral

Two things live here:

1. **Hyperspectral analysis app** (`hsi_app.py`) — interactive spectral
   exploration and dNBR burn-scar mapping on AVIRIS-NG L2 reflectance cubes.
   The high-resolution counterpart to the Sentinel-2 app in
   `../multispectral/app.py`.
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
| `aviris_analyzer.py` | `AVIRISAnalyzer` — ENVI reader mirroring `SentinelAnalyzer` |
| `aviris_processor.py` | KML/shapefile + DWR crop-label utilities |
| `AVIRIS-NG Flight Lines.xlsx` | NASA flight line index with KML links |
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

`AVIRISAnalyzer` mirrors `SentinelAnalyzer`'s interface (`render_rgb`,
`render_index`, `get_spectra`, `burn_scar_analysis`) so the two apps share a
mental model. Bands are looked up by nearest wavelength instead of by name;
RGB stretch and normalized-difference indices are product-agnostic.

## Data Prep Utilities

### AVIRISProcessor
Batch-download KML flight line outlines and convert to ESRI Shapefiles.

```python
from aviris_processor import AVIRISProcessor

proc = AVIRISProcessor(
    excel_file="AVIRIS-NG Flight Lines - AVIRIS-NG Flight Lines.xlsx",
    kml_dir="kml/",
    shapefile_dir="shapefiles/",
)
proc.download_kml_files()
proc.convert_to_shapefiles()
```

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
