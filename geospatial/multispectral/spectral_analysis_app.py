# spectral_analysis_app.py

import streamlit as st
import ee
import geopandas as gpd
from shapely.geometry import Point
from rtree import index
import os

# Initialize Earth Engine
try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

from sentinel_timelapse import SentinelTimelapse
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
import geemap.foliumap as geemap

# Load cities and build spatial index
@st.cache_resource  # Updated caching decorator
def load_cities():
    shapefile_path = r'C:\Users\brunolopez\machineLearning\geospatial\multispectral\cities\ne_10m_populated_places.shp'  # Adjust path if necessary

    if not os.path.exists(shapefile_path):
        st.error(f"Shapefile not found at {shapefile_path}. Please ensure the file is in the correct directory.")
        st.stop()

    cities = gpd.read_file(shapefile_path)
    cities = cities.to_crs(epsg=4326)
    idx = index.Index()
    for pos, city in enumerate(cities.geometry):
        idx.insert(pos, city.bounds)
    return cities, idx

cities, idx = load_cities()

def find_nearest_city(lat, lon):
    point = Point(lon, lat)
    nearest_pos = list(idx.nearest((point.x, point.y, point.x, point.y), 1))[0]
    nearest_city = cities.iloc[nearest_pos]
    city_name = nearest_city['NAME']        # City name field
    country_name = nearest_city['ADM0NAME']  # Country name field
    return city_name, country_name

st.title("Spectral Analysis Tool")

# Initialize timelapse_creator if not in session state
if 'timelapse_creator' not in st.session_state:
    st.session_state['timelapse_creator'] = SentinelTimelapse()

# Sidebar for user input
st.sidebar.header("Go to Location")
lat = st.session_state.get('lat', 0)
lon = st.session_state.get('lon', 0)
input_lat = st.sidebar.text_input("Latitude", value=str(lat))
input_lon = st.sidebar.text_input("Longitude", value=str(lon))

# Display random location map with indices and enable spectral analysis
if st.button('Show Random Location Map and Enable Spectral Analysis'):
    timelapse_creator = st.session_state['timelapse_creator']
    random_map = timelapse_creator.random_location_map(load_initially='RGB')  # Load only RGB initially
    if random_map is not None:
        st.session_state['random_map'] = random_map
        st.session_state['lat'] = timelapse_creator.lat
        st.session_state['lon'] = timelapse_creator.lon
        st.session_state['map_click_spectra'] = True

if 'random_map' in st.session_state:
    st.write("Click on the map to select a pixel and display its spectra along with the nearest city and country.")

    # Create a new Map object to avoid duplicate widget IDs
    click_map = geemap.Map()

    if st.sidebar.button("Go"):
        try:
            new_lat = float(input_lat)
            new_lon = float(input_lon)
            # Update map center
            click_map.set_center(new_lon, new_lat, 12)
            st.session_state['lat'] = new_lat
            st.session_state['lon'] = new_lon
            lat = new_lat
            lon = new_lon
        except ValueError:
            st.sidebar.error("Please enter valid numeric values for latitude and longitude.")
    else:
        # Use existing lat and lon
        lat = st.session_state.get('lat', 0)
        lon = st.session_state.get('lon', 0)
        click_map.set_center(lon, lat, 12)

    timelapse_creator = st.session_state['timelapse_creator']

    # Adjusted date range and cloud cover threshold
    start_date = '2021-12-01'
    end_date = '2022-02-28'
    cloud_cover = 50  # Increased cloud cover threshold

    dataset = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
               .filterDate(start_date, end_date)
               .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_cover))
               .filterBounds(ee.Geometry.Point([lon, lat]))
               .map(timelapse_creator.mask_s2_clouds)
               .map(timelapse_creator.calculate_indices))

    # Check if the dataset is empty
    if dataset.size().getInfo() == 0:
        st.error("No images found for the selected location and date range. Please adjust the date range or cloud cover threshold.")
    else:
        # Proceed with getting the first image
        first_image = dataset.first()
        band_names = first_image.bandNames().getInfo()
        #print("Available bands:", band_names)
        # st.write("Available bands:", band_names)  # Uncomment to display in the app

        # Visualization parameters
        ndvi_vis = {'min': -1, 'max': 1, 'palette': ['blue', 'white', 'green']}
        ndwi_vis = {'min': -1, 'max': 1, 'palette': ['brown', 'white', 'blue']}
        evi_vis = {'min': -1, 'max': 1, 'palette': ['white', 'green']}
        savi_vis = {'min': -1, 'max': 1, 'palette': ['brown', 'white', 'green']}
        ndmi_vis = {'min': -1, 'max': 1, 'palette': ['yellow', 'white', 'blue']}
        nbr_vis = {'min': -1, 'max': 1, 'palette': ['green', 'white', 'black']}
        gndvi_vis = {'min': -1, 'max': 1, 'palette': ['pink', 'white', 'purple']}
        msavi_vis = {'min': -1, 'max': 1, 'palette': ['orange', 'white', 'blue']}
        ndre_vis = {'min': -1, 'max': 1, 'palette': ['blue', 'white', 'red']}
        ndsi_vis = {'min': -1, 'max': 1, 'palette': ['white', 'black']}

        # Visualization parameters for new indices
        ndbi_vis = {'min': -1, 'max': 1, 'palette': ['white', 'grey', 'black']}
        bsi_vis = {'min': -1, 'max': 1, 'palette': ['white', 'yellow', 'brown']}
        mndwi_vis = {'min': -1, 'max': 1, 'palette': ['purple', 'blue', 'cyan']}
        nbr2_vis = {'min': -1, 'max': 1, 'palette': ['green', 'yellow', 'red']}
        afri_vis = {'min': -1, 'max': 1, 'palette': ['pink', 'white', 'green']}

        # RGB visualization parameters
        rgb_vis = {'min': 0.0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}

        # Add RGB layer initially
        click_map.add_layer(dataset.median(), rgb_vis, 'RGB')

        # Add other layers but don't display them initially
        click_map.add_layer(dataset.median().select('NDVI'), ndvi_vis, 'NDVI', shown=False)
        click_map.add_layer(dataset.median().select('NDWI'), ndwi_vis, 'NDWI', shown=False)
        click_map.add_layer(dataset.median().select('EVI'), evi_vis, 'EVI', shown=False)
        click_map.add_layer(dataset.median().select('SAVI'), savi_vis, 'SAVI', shown=False)
        click_map.add_layer(dataset.median().select('NDMI'), ndmi_vis, 'NDMI', shown=False)
        click_map.add_layer(dataset.median().select('NBR'), nbr_vis, 'NBR', shown=False)
        click_map.add_layer(dataset.median().select('GNDVI'), gndvi_vis, 'GNDVI', shown=False)
        click_map.add_layer(dataset.median().select('MSAVI'), msavi_vis, 'MSAVI', shown=False)
        click_map.add_layer(dataset.median().select('NDRE'), ndre_vis, 'NDRE', shown=False)
        click_map.add_layer(dataset.median().select('NDSI'), ndsi_vis, 'NDSI', shown=False)
        # Add new indices
        click_map.add_layer(dataset.median().select('NDBI'), ndbi_vis, 'NDBI', shown=False)
        click_map.add_layer(dataset.median().select('BSI'), bsi_vis, 'BSI', shown=False)
        click_map.add_layer(dataset.median().select('MNDWI'), mndwi_vis, 'MNDWI', shown=False)
        click_map.add_layer(dataset.median().select('NBR2'), nbr2_vis, 'NBR2', shown=False)
        click_map.add_layer(dataset.median().select('AFRI'), afri_vis, 'AFRI', shown=False)

        # Add light pollution layer (VIIRS Nighttime Lights)
        viirs = ee.ImageCollection('NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG') \
            .filterDate(start_date, end_date) \
            .select('avg_rad') \
            .median()

        viirs_vis = {'min': 0, 'max': 60, 'palette': ['black', 'blue', 'purple', 'cyan', 'green', 'yellow', 'red']}
        click_map.add_layer(viirs, viirs_vis, 'Nighttime Lights', shown=False)

        # # Add basemaps
        # basemaps = ['CartoDB Positron', 'Stamen Toner', 'Stamen Watercolor',
        #             'CartoDB DarkMatter', 'Esri WorldImagery']

        # for basemap in basemaps:
        #     if basemap in geemap.basemaps:
        #         click_map.add_basemap(basemap)
        #     else:
        #         st.warning(f"Basemap '{basemap}' is not available.")

        click_map.add_layer_control()

        # Display the map
        map_display = st_folium(click_map, width=700, height=500, key='click_map_widget')

        # Handle map click
        if map_display is not None and 'last_clicked' in map_display and map_display['last_clicked'] is not None:
            lat_click, lon_click = map_display['last_clicked']['lat'], map_display['last_clicked']['lng']

            # Find the nearest city using local data
            city_name, country_name = find_nearest_city(lat_click, lon_click)
            st.write(f"Nearest City: {city_name}, Country: {country_name}")

            point = ee.Geometry.Point([lon_click, lat_click])
            # Update dataset to use the harmonized collection
            image = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                     .filterBounds(point)
                     .filterDate(start_date, end_date)
                     .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_cover))
                     .first())
            if image and image.getInfo():  # Check if image exists
                spectra = image.reduceRegion(reducer=ee.Reducer.mean(), geometry=point, scale=10).getInfo()
                if spectra:
                    st.write(f"Spectra at point ({lat_click}, {lon_click}): {spectra}")

                    # Plot spectra if data is valid
                    bands = list(spectra.keys())
                    values = list(spectra.values())

                    # Filter out None values
                    valid_data = [(band, value) for band, value in zip(bands, values) if value is not None]
                    if valid_data:
                        bands, values = zip(*valid_data)
                        plt.figure(figsize=(10, 5))
                        plt.plot(bands, values, marker='o')
                        plt.title(f'Spectra at ({lat_click:.6f}, {lon_click:.6f})')
                        plt.xlabel('Bands')
                        plt.ylabel('Reflectance')
                        st.pyplot(plt)
                    else:
                        st.error("No valid spectral data available at this point.")
                else:
                    st.error("No spectral data available at this point.")
            else:
                st.error("No image data available at this point.")
