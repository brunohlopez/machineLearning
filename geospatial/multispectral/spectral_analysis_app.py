import streamlit as st
import ee

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

st.title("Spectral Analysis Tool")

# Display random location map
if st.button('Show random location map with indices'):
    timelapse_creator = SentinelTimelapse()
    random_map = timelapse_creator.random_location_map()
    if random_map is not None:
        st.session_state['timelapse_creator'] = timelapse_creator
        st.session_state['random_map'] = random_map
        st.session_state['lat'] = timelapse_creator.lat
        st.session_state['lon'] = timelapse_creator.lon

if 'random_map' in st.session_state:
    st_folium(st.session_state['random_map'], width=700, height=500, key='random_map_widget')

# Adding functionality to click a pixel and show the spectra
if st.button('Click a pixel to show spectra'):
    st.session_state['map_click_spectra'] = True

if 'map_click_spectra' in st.session_state and st.session_state['map_click_spectra']:
    st.write("Click on the map to select a pixel and display its spectra")

    # Create a new Map object to avoid duplicate widget IDs
    click_map = geemap.Map()

    # Center the map on the same location as the random map
    if 'lat' in st.session_state and 'lon' in st.session_state:
        click_map.set_center(st.session_state['lon'], st.session_state['lat'], 12)
    else:
        click_map.set_center(0, 0, 2)  # Default to (0,0) if no location is set

    # Optionally add the same layers as in the random map
    timelapse_creator = SentinelTimelapse()
    dataset = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
               .filterDate('2022-01-01', '2022-01-31')
               .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
               .map(timelapse_creator.mask_s2_clouds)
               .map(timelapse_creator.calculate_indices))

    ndvi_vis = {'min': 0, 'max': 1, 'palette': ['red', 'yellow', 'green']}
    ndwi_vis = {'min': 0, 'max': 1, 'palette': ['brown', 'blue']}
    evi_vis = {'min': 0, 'max': 1, 'palette': ['yellow', 'darkgreen']}
    savi_vis = {'min': 0, 'max': 1, 'palette': ['brown', 'green']}
    ndmi_vis = {'min': -1, 'max': 1, 'palette': ['yellow', 'blue']}
    nbr_vis = {'min': -1, 'max': 1, 'palette': ['green', 'black']}
    rgb_vis = {'min': 0.0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}

    click_map.add_layer(dataset.median().select('NDVI'), ndvi_vis, 'NDVI')
    click_map.add_layer(dataset.median().select('NDWI'), ndwi_vis, 'NDWI')
    click_map.add_layer(dataset.median().select('EVI'), evi_vis, 'EVI')
    click_map.add_layer(dataset.median().select('SAVI'), savi_vis, 'SAVI')
    click_map.add_layer(dataset.median().select('NDMI'), ndmi_vis, 'NDMI')
    click_map.add_layer(dataset.median().select('NBR'), nbr_vis, 'NBR')
    click_map.add_layer(dataset.median(), rgb_vis, 'RGB')

    click_map.add_layer_control()

    # Display the new map with a unique key
    map_display = st_folium(click_map, width=700, height=500, key='click_map_widget')

    # Handle map click
    if map_display is not None and 'last_clicked' in map_display and map_display['last_clicked'] is not None:
        lat, lon = map_display['last_clicked']['lat'], map_display['last_clicked']['lng']
        point = ee.Geometry.Point([lon, lat])
        image = ee.ImageCollection('COPERNICUS/S2_SR')\
            .filterBounds(point)\
            .filterDate('2022-01-01', '2022-01-31')\
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))\
            .first()
        if image:
            spectra = image.reduceRegion(reducer=ee.Reducer.mean(), geometry=point, scale=10).getInfo()
            st.write(f"Spectra at point ({lat}, {lon}): {spectra}")

            # Plot spectra if data is valid
            if spectra:
                bands = list(spectra.keys())
                values = list(spectra.values())

                # Filter out None values
                valid_data = [(band, value) for band, value in zip(bands, values) if value is not None]
                if valid_data:
                    bands, values = zip(*valid_data)
                    plt.figure(figsize=(10, 5))
                    plt.plot(bands, values, marker='o')
                    plt.title(f'Spectra at ({lat}, {lon})')
                    plt.xlabel('Bands')
                    plt.ylabel('Reflectance')
                    st.pyplot(plt)
                else:
                    st.error("No valid spectral data available at this point.")
            else:
                st.error("No spectral data available at this point.")
        else:
            st.error("No image data available at this point.")
