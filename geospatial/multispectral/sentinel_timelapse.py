# sentinel_timelapse.py

import ee
import geemap.foliumap as geemap
import random

class SentinelTimelapse:
    def __init__(self, roi=None):
        self.roi = roi
        self.lat = None
        self.lon = None

    def random_location_map(self, load_initially='RGB'):
        """Displays a map centered at a random land location with Sentinel-2 indices."""
        # Generate a random latitude and longitude over land
        self.lat, self.lon = self.get_random_land_location()
        self.roi = ee.Geometry.Point([self.lon, self.lat]).buffer(5000)  # 5 km buffer

        # Create a map centered at the random location
        Map = geemap.Map()
        Map.setCenter(self.lon, self.lat, 12)

        # Load Sentinel-2 data
        dataset = (ee.ImageCollection('COPERNICUS/S2_HARMONIZED')
                   .filterBounds(self.roi)
                   .filterDate('2021-12-01', '2022-02-28')
                   .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 50))
                   .map(self.mask_s2_clouds)
                   .map(self.calculate_indices))

        if dataset.size().getInfo() == 0:
            print("No images found in this location and date range.")
            return None

        # Visualization parameters
        ndvi_vis = {'min': -1, 'max': 1, 'palette': ['blue', 'white', 'green']}
        rgb_vis = {'min': 0.0, 'max': 0.3, 'bands': ['B4', 'B3', 'B2']}

        # Add layers to the map
        median_image = dataset.median()
        if load_initially == 'RGB':
            Map.addLayer(median_image, rgb_vis, 'RGB')
        else:
            Map.addLayer(median_image.select('NDVI'), ndvi_vis, 'NDVI')

        # Add layer control
        Map.addLayerControl()

        return Map

    def get_random_land_location(self):
        """Generates random latitude and longitude coordinates over land."""
        land = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        max_attempts = 100
        for _ in range(max_attempts):
            lat = random.uniform(-60, 80)  # Limit latitude to avoid poles
            lon = random.uniform(-180, 180)
            point = ee.Geometry.Point([lon, lat])
            # Check if the point is within land
            region = land.filterBounds(point)
            size = region.size().getInfo()
            if size > 0:
                return lat, lon
        # If no land point is found after max_attempts
        print("Could not find a random land location.")
        return 0, 0  # Default to (0, 0)

    def mask_s2_clouds(self, image):
        """Masks clouds and shadows in Sentinel-2 imagery."""
        qa = image.select('QA60')
        # Bits 10 and 11 are clouds and cirrus, respectively
        cloudBitMask = 1 << 10
        cirrusBitMask = 1 << 11
        # Both flags should be set to zero, indicating clear conditions
        mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(
               qa.bitwiseAnd(cirrusBitMask).eq(0))
        return image.updateMask(mask).divide(10000)

    def calculate_indices(self, image):
        """Calculates various indices for a Sentinel-2 image."""
        ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
        ndwi = image.normalizedDifference(['B8', 'B11']).rename('NDWI')
        evi = image.expression(
            '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))', {
                'NIR': image.select('B8'),
                'RED': image.select('B4'),
                'BLUE': image.select('B2')
            }).rename('EVI')
        savi = image.expression(
            '(1 + L) * ((NIR - RED) / (NIR + RED + L))', {
                'NIR': image.select('B8'),
                'RED': image.select('B4'),
                'L': 0.5
            }).rename('SAVI')
        ndmi = image.normalizedDifference(['B8', 'B11']).rename('NDMI')
        nbr = image.normalizedDifference(['B8', 'B12']).rename('NBR')
        gndvi = image.normalizedDifference(['B8', 'B3']).rename('GNDVI')
        msavi = image.expression(
            '((2 * NIR + 1) - sqrt((2 * NIR + 1) ** 2 - 8 * (NIR - RED))) / 2', {
                'NIR': image.select('B8'),
                'RED': image.select('B4')
            }).rename('MSAVI')
        ndre = image.normalizedDifference(['B8', 'B5']).rename('NDRE')
        ndsi = image.normalizedDifference(['B3', 'B11']).rename('NDSI')
        # New Indices
        ndbi = image.normalizedDifference(['B11', 'B8']).rename('NDBI')
        bsi = image.expression(
            '((SWIR + RED) - (NIR + BLUE)) / ((SWIR + RED) + (NIR + BLUE))', {
                'SWIR': image.select('B11'),
                'RED': image.select('B4'),
                'NIR': image.select('B8'),
                'BLUE': image.select('B2')
            }).rename('BSI')
        mndwi = image.normalizedDifference(['B3', 'B11']).rename('MNDWI')
        nbr2 = image.normalizedDifference(['B12', 'B8']).rename('NBR2')
        afri = image.expression(
            '(NIR - (SWIR / 2)) / (NIR + (SWIR / 2))', {
                'NIR': image.select('B8'),
                'SWIR': image.select('B12')
            }).rename('AFRI')

        return image.addBands([ndvi, ndwi, evi, savi, ndmi, nbr, gndvi, msavi, ndre, ndsi,
                               ndbi, bsi, mndwi, nbr2, afri])
