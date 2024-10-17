import ee
import os
import zipfile
import geemap
import random
import streamlit as st

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

def mask_s2_clouds(image):
    """Masks clouds in a Sentinel-2 image using the QA band."""
    qa = image.select('QA60')
    cloud_bit_mask = 1 << 10
    cirrus_bit_mask = 1 << 11
    mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(qa.bitwiseAnd(cirrus_bit_mask).eq(0))
    return image.updateMask(mask).divide(10000)

def calculate_ndvi(image):
    """Calculates NDVI for a Sentinel-2 image."""
    ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
    return image.addBands(ndvi)

def calculate_ndwi(image):
    """Calculates the Normalized Difference Water Index (NDWI) for a Sentinel-2 image."""
    ndwi = image.normalizedDifference(['B8', 'B11']).rename('NDWI')
    return image.addBands(ndwi)

def calculate_evi(image):
    """Calculates the Enhanced Vegetation Index (EVI) for a Sentinel-2 image."""
    evi = image.expression(
        '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))', {
            'NIR': image.select('B8'),
            'RED': image.select('B4'),
            'BLUE': image.select('B2')
        }).rename('EVI')
    return image.addBands(evi)

def calculate_savi(image):
    """Calculates the Soil Adjusted Vegetation Index (SAVI) for a Sentinel-2 image."""
    savi = image.expression(
        '(1 + L) * (NIR - RED) / (NIR + RED + L)', {
            'NIR': image.select('B8'),
            'RED': image.select('B4'),
            'L': 0.5
        }).rename('SAVI')
    return image.addBands(savi)

def calculate_ndmi(image):
    """Calculates the Normalized Difference Moisture Index (NDMI) for a Sentinel-2 image."""
    ndmi = image.normalizedDifference(['B8', 'B11']).rename('NDMI')
    return image.addBands(ndmi)

def calculate_nbr(image):
    """Calculates the Normalized Burn Ratio (NBR) for a Sentinel-2 image."""
    nbr = image.normalizedDifference(['B8', 'B12']).rename('NBR')
    return image.addBands(nbr)

def calculate_gndvi(image):
    """Calculates the Green Normalized Difference Vegetation Index (GNDVI)."""
    gndvi = image.normalizedDifference(['B8', 'B3']).rename('GNDVI')
    return image.addBands(gndvi)

def calculate_msavi(image):
    """Calculates the Modified Soil Adjusted Vegetation Index (MSAVI)."""
    msavi = image.expression(
        '((2 * NIR + 1) - sqrt((2 * NIR + 1) ** 2 - 8 * (NIR - RED))) / 2', {
            'NIR': image.select('B8'),
            'RED': image.select('B4')
        }).rename('MSAVI')
    return image.addBands(msavi)

def calculate_ndre(image):
    """Calculates the Normalized Difference Red Edge Index (NDRE)."""
    ndre = image.normalizedDifference(['B8', 'B5']).rename('NDRE')
    return image.addBands(ndre)

def calculate_ndsi(image):
    """Calculates the Normalized Difference Snow Index (NDSI)."""
    ndsi = image.normalizedDifference(['B3', 'B11']).rename('NDSI')
    return image.addBands(ndsi)

def is_land(lat, lon):
    """Check if the coordinates are over land using MODIS land cover data."""
    try:
        landcover = ee.Image('MODIS/006/MCD12Q1/2019_01_01').select('LC_Type1')
        point = ee.Geometry.Point([lon, lat])
        land_mask = landcover.reduceRegion(
            reducer=ee.Reducer.first(), geometry=point, scale=30
        ).get('LC_Type1')
        land_mask_info = land_mask.getInfo()
        return land_mask_info != 0 and land_mask_info is not None  # Returns True if it's land
    except Exception as e:
        st.error(f"Error checking if point is land: {e}")
        return False

def random_location_map():
    """Generates a random location over land and creates a map with various indices."""
    # Generate random latitude and longitude until it's over land.
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

    # Prepare the dataset with cloud masking and index calculations.
    dataset = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
               .filterDate('2022-01-01', '2022-01-31')
               .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
               .map(mask_s2_clouds)
               .map(calculate_ndvi)
               .map(calculate_ndwi)
               .map(calculate_evi)
               .map(calculate_savi)
               .map(calculate_ndmi)
               .map(calculate_nbr))

    # Create a geemap object.
    m = geemap.Map()
    m.set_center(lon, lat, 12)  # Set the center to the random coordinates

    # Visualization parameters for each index
    ndvi_vis = {'min': 0, 'max': 1, 'palette': ['red', 'yellow', 'green']}
    ndwi_vis = {'min': 0, 'max': 1, 'palette': ['brown', 'blue']}
    evi_vis = {'min': 0, 'max': 1, 'palette': ['yellow', 'darkgreen']}
    savi_vis = {'min': 0, 'max': 1, 'palette': ['brown', 'green']}
    ndmi_vis = {'min': -1, 'max': 1, 'palette': ['yellow', 'blue']}
    nbr_vis = {'min': -1, 'max': 1, 'palette': ['green', 'black']}
    # RGB visualization parameters
    rgb_vis = {'min': 0.0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}

    # Add index layers to the map
    m.add_layer(dataset.median().select('NDVI'), ndvi_vis, 'NDVI')
    m.add_layer(dataset.median().select('NDWI'), ndwi_vis, 'NDWI')
    m.add_layer(dataset.median().select('EVI'), evi_vis, 'EVI')
    m.add_layer(dataset.median().select('SAVI'), savi_vis, 'SAVI')
    m.add_layer(dataset.median().select('NDMI'), ndmi_vis, 'NDMI')
    m.add_layer(dataset.median().select('NBR'), nbr_vis, 'NBR')
    m.add_layer(dataset.median(), rgb_vis, 'RGB')

    # Add layer control
    m.add_layer_control()  # This automatically places it at the top right

    return m

def read_shapefile(uploaded_file):
    with zipfile.ZipFile(uploaded_file, 'r') as z:
        z.extractall("shapefile")
    shapefile_path = "shapefile"
    # Find the .shp file
    shp_files = [f for f in os.listdir(shapefile_path) if f.endswith('.shp')]
    if shp_files:
        shp_path = os.path.join(shapefile_path, shp_files[0])
        return geemap.shp_to_ee(shp_path)
    else:
        st.error("No .shp file found in the uploaded zip file.")
        return None
