import streamlit as st
import os
import ee

from sentinel_timelapse import SentinelTimelapse
from utils import read_shapefile

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

st.title("Sentinel-2 Timelapse Creator")

# Sidebar for input options
st.sidebar.title("Input Options")

# Option to upload shapefile
uploaded_file = st.sidebar.file_uploader("Upload a Shapefile (zip)", type=["zip"])

# Option to enter bounding box coordinates
use_bbox = st.sidebar.checkbox("Enter Bounding Box Coordinates")

roi = None
if uploaded_file is not None:
    roi = read_shapefile(uploaded_file)
elif use_bbox:
    min_lon = st.sidebar.number_input("Min Longitude", value=117.1132)
    min_lat = st.sidebar.number_input("Min Latitude", value=3.5227)
    max_lon = st.sidebar.number_input("Max Longitude", value=117.2214)
    max_lat = st.sidebar.number_input("Max Latitude", value=3.5843)
    roi = ee.Geometry.BBox(min_lon, min_lat, max_lon, max_lat)

if roi is not None:
    timelapse_creator = SentinelTimelapse(roi)
else:
    timelapse_creator = None

# Timelapse parameters
st.header("Create Sentinel-2 Timelapse")
s2_gif_path = "sentinel2_timelapse.gif"
start_year = st.slider("Select start year", 2015, 2024, 2023)
end_year = st.slider("Select end year", 2015, 2024, 2023)
frequency = st.selectbox("Select frequency", ('day', 'month', 'year'))
fps = st.slider("Select frames per second", 1, 30, 10)
bands = st.multiselect("Select bands", ['Red', 'Green', 'Blue', 'NIR', 'SWIR1', 'SWIR2'], default=['Red', 'Green', 'Blue'])

if st.button('Create Sentinel-2 Timelapse'):
    if roi is not None and timelapse_creator is not None:
        with st.spinner("Creating timelapse..."):
            try:
                timelapse_creator.create_timelapse_s2(
                    out_gif=s2_gif_path,
                    start_year=start_year,
                    end_year=end_year,
                    bands=bands,
                    frequency=frequency,
                    fps=fps
                )
                if os.path.exists(s2_gif_path):
                    st.image(s2_gif_path)
                    # Option to download the GIF
                    with open(s2_gif_path, "rb") as file:
                        btn = st.download_button(
                            label="Download Timelapse GIF",
                            data=file,
                            file_name=s2_gif_path,
                            mime="image/gif"
                        )
                else:
                    st.error(f"Failed to create the timelapse GIF at {s2_gif_path}")
            except Exception as e:
                st.error(f"An error occurred: {e}")
    else:
        st.error("Please upload a shapefile or enter bounding box coordinates.")
