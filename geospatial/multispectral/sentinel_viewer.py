import ee
import geemap.foliumap as geemap
import streamlit as st
import os

# Initialize the Earth Engine module
try:
    ee.Initialize()
except Exception as e:
    ee.Authenticate()
    ee.Initialize()

class SentinelTimelapse:
    def __init__(self, roi=None):
        self.Map = geemap.Map()
        if roi is None:
            self.roi = ee.Geometry.BBox(117.1132, 3.5227, 117.2214, 3.5843)  # Default location in Turkey
        else:
            self.roi = roi

        self.Map.addLayer(self.roi, {}, "ROI")
        self.Map.centerObject(self.roi)

    def create_timelapse_s2(self, out_gif, start_year=2023, end_year=2023, bands=['Red', 'Green', 'Blue'], frequency='day', fps=1):
        return geemap.sentinel2_timelapse(
            self.roi,
            out_gif=out_gif,
            start_year=start_year,
            bands=bands,
            end_year=end_year,
            start_date="01-01",
            apply_fmask=True,
            end_date="12-31",
            frequency=frequency,
            frames_per_second=fps,
            title=f"Sentinel-2 Timelapse"
        )

# Streamlit App
st.title("Simplified Sentinel Timelapse")

timelapse_creator = SentinelTimelapse()

s2_gif_path = "sentinel2.gif"
start_year = st.slider("Select start year", 2015, 2024, 2023)
end_year = st.slider("Select end year", 2015, 2024, 2023)
frequency = st.selectbox("Select frequency", ('day', 'month', 'year'))
fps = st.slider("Select frames per second", 1, 30, 10)

if st.button('Create Sentinel-2 Timelapse'):
    timelapse_creator.create_timelapse_s2(s2_gif_path, start_year=start_year, end_year=end_year, frequency=frequency, fps=fps)
    if os.path.exists(s2_gif_path):
        st.image(s2_gif_path)
    else:
        st.error(f"Failed to create the timelapse GIF at {s2_gif_path}")
