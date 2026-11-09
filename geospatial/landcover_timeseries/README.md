# Land Cover Time Series

A Streamlit app for looking at how an area has changed through time with Google Earth Engine.

1. Draw a rectangle or polygon on the map.
2. Browse the individual Sentinel-2 (`COPERNICUS/S2_SR_HARMONIZED`) or Landsat 8/9 (Collection 2, Level 2) scenes that cover it, filtered by date range and maximum cloud cover.
3. Pick a scene to view it as RGB imagery tiles.
4. Optionally compute [Dynamic World](https://dynamicworld.app/) land-cover class areas (km² and % of your area) for each scene date and chart them over time.
5. Download the scene metadata and land-cover stats as a CSV.

## Setup

```bash
pip install -r requirements.txt

# A Google Cloud project with Earth Engine enabled
export EE_PROJECT="your-gcp-project-id"

streamlit run time_series_analysis.py
```

The first run opens an Earth Engine sign-in (`ee.Authenticate()`) if you haven't authenticated on this machine before.

## Notes

- Dynamic World is a 10 m product, so stats are computed at 10 m inside the drawn area. Large areas and long date ranges mean many Earth Engine calls, so start small.
