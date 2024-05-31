import ee
import geemap.foliumap as geemap
import streamlit as st
import os
import zipfile
import random
from streamlit_folium import st_folium
import folium
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import transforms, models
from PIL import Image
import numpy as np
import matplotlib.pyplot as plt

# Initialize the Earth Engine module
try:
    ee.Initialize()
except Exception as e:
    ee.Authenticate()
    ee.Initialize()

class SentinelTimelapse:
    def __init__(self, roi=None):
        self.Map = geemap.Map()
        self.roi = roi

        if roi is not None:
            self.Map.addLayer(self.roi, {}, "ROI")
            self.Map.centerObject(self.roi)

    def set_roi(self, roi):
        self.roi = roi
        self.Map.addLayer(self.roi, {}, "ROI")
        self.Map.centerObject(self.roi)

    def mask_s2_clouds(self, image):
        qa = image.select('QA60')
        cloud_bit_mask = 1 << 10
        cirrus_bit_mask = 1 << 11
        mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(qa.bitwiseAnd(cirrus_bit_mask).eq(0))
        return image.updateMask(mask).divide(10000)

    def calculate_indices(self, image):
        ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
        ndwi = image.normalizedDifference(['B8', 'B11']).rename('NDWI')
        evi = image.expression(
            '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))', {
                'NIR': image.select('B8'),
                'RED': image.select('B4'),
                'BLUE': image.select('B2')
            }).rename('EVI')
        savi = image.expression(
            '(1 + L) * (NIR - RED) / (NIR + RED + L)', {
                'NIR': image.select('B8'),
                'RED': image.select('B4'),
                'L': 0.5
            }).rename('SAVI')
        ndmi = image.normalizedDifference(['B8', 'B11']).rename('NDMI')
        nbr = image.normalizedDifference(['B8', 'B12']).rename('NBR')
        
        return image.addBands([ndvi, ndwi, evi, savi, ndmi, nbr])

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

    def is_land(self, lat, lon):
        try:
            landcover = ee.Image('MODIS/006/MCD12Q1/2019_01_01').select('LC_Type1')
            point = ee.Geometry.Point([lon, lat])
            land_mask = landcover.reduceRegion(ee.Reducer.first(), point, 30).get('LC_Type1').getInfo()
            return land_mask != 0  # Returns True if it's land
        except Exception as e:
            st.error(f"Error checking if point is land: {e}")
            return False

    def random_location_map(self):
        try:
            is_land_point = False
            while not is_land_point:
                lat = random.uniform(-90, 90)
                lon = random.uniform(-180, 180)
                is_land_point = self.is_land(lat, lon)

            dataset = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                       .filterDate('2022-01-01', '2022-01-31')
                       .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
                       .map(self.mask_s2_clouds)
                       .map(self.calculate_indices))

            self.Map.set_center(lon, lat, 12)  # Set the center to the random coordinates

            ndvi_vis = {'min': 0, 'max': 1, 'palette': ['red', 'yellow', 'green']}
            ndwi_vis = {'min': 0, 'max': 1, 'palette': ['brown', 'blue']}
            evi_vis = {'min': 0, 'max': 1, 'palette': ['yellow', 'darkgreen']}
            savi_vis = {'min': 0, 'max': 1, 'palette': ['brown', 'green']}
            ndmi_vis = {'min': -1, 'max': 1, 'palette': ['yellow', 'blue']}
            nbr_vis = {'min': -1, 'max': 1, 'palette': ['green', 'black']}
            rgb_vis = {'min': 0.0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}

            self.Map.add_layer(dataset.median().select('NDVI'), ndvi_vis, 'NDVI')
            self.Map.add_layer(dataset.median().select('NDWI'), ndwi_vis, 'NDWI')
            self.Map.add_layer(dataset.median().select('EVI'), evi_vis, 'EVI')
            self.Map.add_layer(dataset.median().select('SAVI'), savi_vis, 'SAVI')
            self.Map.add_layer(dataset.median().select('NDMI'), ndmi_vis, 'NDMI')
            self.Map.add_layer(dataset.median().select('NBR'), nbr_vis, 'NBR')
            self.Map.add_layer(dataset.median(), rgb_vis, 'RGB')

            self.Map.add_layer_control()  # This automatically places it at the top right

            return lat, lon
        except Exception as e:
            st.error(f"Error creating random location map: {e}")
            return None, None

# Function to read shapefile
def read_shapefile(uploaded_file):
    with zipfile.ZipFile(uploaded_file, 'r') as z:
        z.extractall("shapefile")
    shapefile_path = "shapefile"
    return geemap.shp_to_ee(os.path.join(shapefile_path, os.listdir(shapefile_path)[0]))

# Streamlit App
st.title("Sentinel Timelapse and Spectral Analysis")

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

timelapse_creator = SentinelTimelapse(roi)

s2_gif_path = "sentinel2.gif"
start_year = st.slider("Select start year", 2015, 2024, 2023)
end_year = st.slider("Select end year", 2015, 2024, 2023)
frequency = st.selectbox("Select frequency", ('day', 'month', 'year'))
fps = st.slider("Select frames per second", 1, 30, 10)

if st.button('Create Sentinel-2 Timelapse'):
    if roi is not None:
        timelapse_creator.create_timelapse_s2(s2_gif_path, start_year=start_year, end_year=end_year, frequency=frequency, fps=fps)
        if os.path.exists(s2_gif_path):
            st.image(s2_gif_path)
        else:
            st.error(f"Failed to create the timelapse GIF at {s2_gif_path}")
    else:
        st.error("Please upload a shapefile or enter bounding box coordinates.")

# Display random location map and handle click to show spectra
if st.button('Show random location map and click for spectra'):
    lat, lon = timelapse_creator.random_location_map()
    if lat and lon:
        st.session_state['lat'] = lat
        st.session_state['lon'] = lon
        st.session_state['map'] = timelapse_creator.Map  # Store the map in the session state
        st.session_state['map_click_spectra'] = True

if 'map_click_spectra' in st.session_state and st.session_state['map_click_spectra']:
    st.write("Click on the map to select a pixel and display its spectra")

    # Retrieve the map from session state
    timelapse_map = st.session_state['map']

    # Display the map
    map_display = st_folium(timelapse_map, width=700, height=500)

    # Handle map click
    if map_display is not None and 'last_clicked' in map_display and map_display['last_clicked'] is not None:
        lat, lon = map_display['last_clicked']['lat'], map_display['last_clicked']['lng']
        point = ee.Geometry.Point([lon, lat])
        image = ee.ImageCollection('COPERNICUS/S2_HARMONIZED').filterBounds(point).first()
        spectra = image.reduceRegion(reducer=ee.Reducer.mean(), geometry=point, scale=10).getInfo()
        st.write(f"Spectra at point ({lat}, {lon}): {spectra}")

        # Plot spectra
        import matplotlib.pyplot as plt
        bands = list(spectra.keys())
        values = list(spectra.values())
        plt.figure(figsize=(10, 5))
        plt.plot(bands, values, marker='o')
        plt.title(f'Spectra at ({lat}, {lon})')
        plt.xlabel('Bands')
        plt.ylabel('Reflectance')
        st.pyplot(plt)





 