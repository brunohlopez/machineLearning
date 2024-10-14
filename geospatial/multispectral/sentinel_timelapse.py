import geemap.foliumap as geemap
import ee
import random
import streamlit as st
from utils import is_land

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

class SentinelTimelapse:
    def __init__(self, roi=None):
        self.roi = roi
        self.lat = None
        self.lon = None

    def set_roi(self, roi):
        self.roi = roi

    def mask_s2_clouds(self, image):
        qa = image.select('QA60')
        cloud_bit_mask = 1 << 10
        cirrus_bit_mask = 1 << 11
        mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(
            qa.bitwiseAnd(cirrus_bit_mask).eq(0)
        )
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

    def create_timelapse_s2(
        self,
        out_gif,
        start_year=2023,
        end_year=2023,
        bands=['Red', 'Green', 'Blue'],
        frequency='day',
        fps=1
    ):
        # Map band names to Sentinel-2 bands
        band_mapping = {
            'Blue': 'B2',
            'Green': 'B3',
            'Red': 'B4',
            'NIR': 'B8',
            'SWIR1': 'B11',
            'SWIR2': 'B12'
        }
        bands = [band_mapping.get(band, band) for band in bands]

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
            title=f"Sentinel-2 Timelapse {start_year}-{end_year}"
        )

    def random_location_map(self):
        try:
            is_land_point = False
            attempts = 0
            max_attempts = 100  # Prevent infinite loop
            while not is_land_point and attempts < max_attempts:
                lat = random.uniform(-90, 90)
                lon = random.uniform(-180, 180)
                is_land_point = is_land(lat, lon)
                attempts += 1

            if not is_land_point:
                st.error("Could not find a random land location.")
                return None

            self.lat = lat  # Save coordinates for later use
            self.lon = lon

            dataset = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                       .filterDate('2022-01-01', '2022-01-31')
                       .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
                       .map(self.mask_s2_clouds)
                       .map(self.calculate_indices))

            m = geemap.Map()
            m.set_center(lon, lat, 12)  # Set the center to the random coordinates

            ndvi_vis = {'min': 0, 'max': 1, 'palette': ['red', 'yellow', 'green']}
            ndwi_vis = {'min': 0, 'max': 1, 'palette': ['brown', 'blue']}
            evi_vis = {'min': 0, 'max': 1, 'palette': ['yellow', 'darkgreen']}
            savi_vis = {'min': 0, 'max': 1, 'palette': ['brown', 'green']}
            ndmi_vis = {'min': -1, 'max': 1, 'palette': ['yellow', 'blue']}
            nbr_vis = {'min': -1, 'max': 1, 'palette': ['green', 'black']}
            rgb_vis = {'min': 0.0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}

            m.add_layer(dataset.median().select('NDVI'), ndvi_vis, 'NDVI')
            m.add_layer(dataset.median().select('NDWI'), ndwi_vis, 'NDWI')
            m.add_layer(dataset.median().select('EVI'), evi_vis, 'EVI')
            m.add_layer(dataset.median().select('SAVI'), savi_vis, 'SAVI')
            m.add_layer(dataset.median().select('NDMI'), ndmi_vis, 'NDMI')
            m.add_layer(dataset.median().select('NBR'), nbr_vis, 'NBR')
            m.add_layer(dataset.median(), rgb_vis, 'RGB')

            m.add_layer_control()  # This automatically places it at the top right

            return m
        except Exception as e:
            st.error(f"Error creating random location map: {e}")
            return None
