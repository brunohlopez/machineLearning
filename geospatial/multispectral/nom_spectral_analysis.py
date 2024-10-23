# spectral_analysis_app.py

import streamlit as st
import ee
import os
import datetime
import folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

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
from folium.plugins import MiniMap

def geocode_nominatim(lat, lon):
    """Reverse geocoding using Nominatim."""
    geolocator = Nominatim(user_agent="spectral_analysis_app")
    try:
        location = geolocator.reverse((lat, lon), timeout=10)
        if location and 'address' in location.raw:
            address = location.raw['address']
            city_name = address.get('city', address.get('town', address.get('village', 'Unknown')))
            country_name = address.get('country', 'Unknown')
            return city_name, country_name
        else:
            return 'Unknown', 'Unknown'
    except GeocoderTimedOut:
        return 'Unknown', 'Unknown'

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

# Sidebar for date range and cloud cover input
st.sidebar.header("Data Filters")
start_date = st.sidebar.date_input("Start Date", value=datetime.date(2021, 12, 1))
end_date = st.sidebar.date_input("End Date", value=datetime.date(2022, 2, 28))
cloud_cover = st.sidebar.slider("Max Cloud Cover (%)", min_value=0, max_value=100, value=50)

# Display random location map with indices and enable spectral analysis
if st.button('Show Random Location Map and Enable Spectral Analysis'):
    timelapse_creator = st.session_state['timelapse_creator']
    random_map = timelapse_creator.random_location_map(load_initially='RGB')
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

    # Convert dates to strings
    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')

    # Get the dataset (global)
    @st.cache_data(show_spinner=False)
    def get_dataset(start_date_str, end_date_str, cloud_cover):
        dataset = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                   .filterDate(start_date_str, end_date_str)
                   .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_cover))
                   .map(timelapse_creator.mask_s2_clouds)
                   .map(timelapse_creator.calculate_indices))
        return dataset

    dataset = get_dataset(start_date_str, end_date_str, cloud_cover)

    # Check if the dataset is empty
    if dataset.size().getInfo() == 0:
        st.error("No images found for the selected date range and cloud cover threshold.")
    else:
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
            .filterDate(start_date_str, end_date_str) \
            .select('avg_rad') \
            .median()

        viirs_vis = {'min': 0, 'max': 60, 'palette': ['black', 'blue', 'purple', 'cyan', 'green', 'yellow', 'red']}
        click_map.add_layer(viirs, viirs_vis, 'Nighttime Lights', shown=False)

        # Add basemaps
        basemaps = ['CartoDB.Positron',  'CartoDB.DarkMatter']

        # Add basemaps to the map
        for basemap in basemaps:
            if basemap in geemap.basemaps:
                click_map.add_basemap(basemap)
            else:
                st.warning(f"Basemap '{basemap}' is not available.")

        # Add the MiniMap plugin
        minimap = MiniMap(toggle_display=True, position='bottomright')
        click_map.add_child(minimap)

        click_map.add_layer_control()

        # Display the map
        map_display = st_folium(click_map, width=700, height=500, key='click_map_widget')

        # Handle map click
        if map_display is not None and 'last_clicked' in map_display and map_display['last_clicked'] is not None:
            lat_click, lon_click = map_display['last_clicked']['lat'], map_display['last_clicked']['lng']

            # Reverse Geocoding using Nominatim
            city_name, country_name = geocode_nominatim(lat_click, lon_click)
            st.write(f"Nearest City: {city_name}, Country: {country_name}")

            point = ee.Geometry.Point([lon_click, lat_click])
            # Update dataset to use the harmonized collection
            image = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                     .filterDate(start_date_str, end_date_str)
                     .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_cover))
                     .filterBounds(point)
                     .first())
            if image and image.getInfo():  # Check if image exists
                spectra = image.reduceRegion(reducer=ee.Reducer.mean(), geometry=point, scale=10).getInfo()
                if spectra:
                    st.write(f"Spectral Reflectance at ({lat_click:.6f}, {lon_click:.6f}):")
                    # st.json(spectra)  # Uncomment to display raw spectra data

                    # Plot spectra if data is valid
                    main_bands = ['B1', 'B2', 'B3', 'B4', 'B5', 'B6',
                                  'B7', 'B8', 'B8A', 'B9', 'B11', 'B12']  # Excluding B10
                    bands = [band for band in main_bands if band in spectra]
                    values = [spectra[band] for band in bands]

                    # Filter out None values
                    valid_data = [(band, value) for band, value in zip(bands, values) if value is not None]
                    if valid_data:
                        bands, values = zip(*valid_data)
                        # Map bands to wavelengths
                        band_wavelengths = {
                            'B1': 443, 'B2': 490, 'B3': 560, 'B4': 665,
                            'B5': 705, 'B6': 740, 'B7': 783, 'B8': 842,
                            'B8A': 865, 'B9': 940, 'B11': 1610, 'B12': 2190
                        }
                        wavelengths = [band_wavelengths[band] for band in bands]

                        plt.figure(figsize=(10, 5))
                        plt.plot(wavelengths, values, marker='o')
                        plt.title(f'Spectral Reflectance at ({lat_click:.6f}, {lon_click:.6f})')
                        plt.xlabel('Wavelength (nm)')
                        plt.ylabel('Reflectance')
                        plt.grid(True)
                        st.pyplot(plt)
                    else:
                        st.error("No valid spectral data available at this point.")
                else:
                    st.error("No spectral data available at this point.")
            else:
                st.error("No image data available at this point.")
