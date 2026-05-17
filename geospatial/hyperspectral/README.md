# AVIRIS-NG Hyperspectral Processing

Tools for batch downloading and processing AVIRIS-NG flight line data from the NASA/JPL data portal.

## What is AVIRIS-NG?

AVIRIS-NG (Airborne Visible/Infrared Imaging Spectrometer — Next Generation) is a NASA/JPL hyperspectral sensor capturing 432 contiguous spectral channels from 380–2510 nm at 1–8 m resolution. It is widely used for wildfire burn severity mapping, methane plume detection, and urban heat island studies across California and beyond.

## Features

- Batch download KML flight line outlines from the AVIRIS-NG data portal
- Parallel downloads with configurable worker count
- KML → ESRI Shapefile conversion via GDAL `ogr2ogr`

## Setup

```bash
pip install -r requirements.txt

# GDAL must be available on your PATH (provides ogr2ogr)
conda install -c conda-forge gdal
```

## Usage

```python
from aviris_processor import AVIRISProcessor

processor = AVIRISProcessor(
    excel_file="AVIRIS-NG Flight Lines.xlsx",
    kml_dir="kml/",
    shapefile_dir="shapefiles/",
)

# Download KML outlines in parallel (5 workers by default)
processor.download_kml_files()

# Convert all KMLs to shapefiles
processor.convert_to_shapefiles()
```

See `notebooks/` for end-to-end examples including spectral analysis of downloaded scenes.
