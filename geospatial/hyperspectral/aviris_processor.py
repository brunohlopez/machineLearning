"""
aviris_processor.py
-------------------
Utilities for working with NASA/JPL AVIRIS-NG hyperspectral data and the
California DWR i15 land-use shapefiles that are commonly paired with it.

Classes
-------
AVIRISProcessor      – Batch-download KML flight lines and convert to shapefiles
ShapefileProcessor   – Label i15 crop shapefiles with human-readable DWR crop codes
ShapefileSplitter    – Split a labeled shapefile into one file per crop subclass
ShapefileToMap       – Render any shapefile as an interactive Folium map
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import folium
import geopandas as gpd
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# California DWR i15 crop code → description mapping
# Source: California Department of Water Resources Land Use Survey
# ---------------------------------------------------------------------------
DWR_CROP_CODES: dict = {
    'G':  {'description': 'Grain and hay crops',
           'subclasses': {'1': 'Barley', '2': 'Wheat', '3': 'Oats',
                          '6': 'Miscellaneous grain and hay', '7': 'Mixed grain and hay'}},
    'R':  {'description': 'Rice',
           'subclasses': {'1': 'Rice', '2': 'Wild Rice'}},
    'F':  {'description': 'Field crops',
           'subclasses': {'1': 'Cotton', '2': 'Safflower', '3': 'Flax', '4': 'Hops',
                          '5': 'Sugar beets', '6': 'Corn (field & sweet)',
                          '7': 'Grain sorghum', '8': 'Sudan', '10': 'Beans (dry)',
                          '12': 'Sunflowers', '15': 'Sugar cane'}},
    'P':  {'description': 'Pasture',
           'subclasses': {'1': 'Alfalfa & alfalfa mixtures', '2': 'Clover',
                          '3': 'Mixed pasture', '4': 'Native pasture',
                          '6': 'Miscellaneous grasses', '7': 'Turf farms',
                          '8': 'Bermuda grass', '9': 'Rye grass'}},
    'T':  {'description': 'Truck, nursery & berry crops',
           'subclasses': {'1': 'Artichokes', '2': 'Asparagus', '3': 'Beans (green)',
                          '6': 'Carrots', '7': 'Celery', '8': 'Lettuce',
                          '9': 'Melons, squash & cucumbers', '10': 'Onions & garlic',
                          '11': 'Peas', '12': 'Potatoes', '15': 'Tomatoes (processing)',
                          '16': 'Flowers & nursery', '19': 'Bush berries',
                          '20': 'Strawberries', '21': 'Peppers', '22': 'Broccoli',
                          '23': 'Cabbage', '24': 'Cauliflower', '26': 'Tomatoes (market)',
                          '27': 'Greenhouse', '28': 'Blueberries'}},
    'D':  {'description': 'Deciduous fruits and nuts',
           'subclasses': {'1': 'Apples', '2': 'Apricots', '3': 'Cherries',
                          '5': 'Peaches and nectarines', '6': 'Pears', '7': 'Plums',
                          '8': 'Prunes', '9': 'Figs', '10': 'Miscellaneous deciduous',
                          '12': 'Almonds', '13': 'Walnuts', '14': 'Pistachios',
                          '15': 'Pomegranates'}},
    'C':  {'description': 'Citrus and subtropical',
           'subclasses': {'1': 'Grapefruit', '2': 'Lemons', '3': 'Oranges',
                          '4': 'Dates', '5': 'Avocados', '6': 'Olives',
                          '8': 'Kiwis', '10': 'Eucalyptus'}},
    'V':  {'description': 'Vineyards',
           'subclasses': {'1': 'Table grapes', '2': 'Wine grapes', '3': 'Raisin grapes'}},
    'I':  {'description': 'Idle',
           'subclasses': {'1': 'Idle (cropped within past 3 years)',
                          '2': 'New lands being prepared',
                          '4': 'Long-term idle (4+ years)'}},
    'NV': {'description': 'Native vegetation',
           'subclasses': {'1': 'Grassland', '2': 'Light brush', '3': 'Medium brush',
                          '4': 'Heavy brush', '5': 'Brush and timber',
                          '6': 'Forest', '7': 'Oak woodland'}},
    'NR': {'description': 'Riparian vegetation',
           'subclasses': {'1': 'Marsh lands & tules', '2': 'Natural meadow',
                          '3': 'Streamside trees & shrubs',
                          '4': 'Seasonal duck marsh', '5': 'Permanent duck marsh'}},
    'NW': {'description': 'Water surface',
           'subclasses': {'1': 'River or stream', '2': 'Irrigation canal/ditch',
                          '3': 'Drainage canal/ditch',
                          '4': 'Freshwater lake, reservoir or pond',
                          '5': 'Brackish or saline water',
                          '6': 'Wastewater pond'}},
    'NB': {'description': 'Barren and wasteland',
           'subclasses': {'1': 'Dry stream channels', '2': 'Mine tailings',
                          '3': 'Barren land', '4': 'Salt flats', '5': 'Sand dunes'}},
    'U':  {'description': 'Urban (generic)', 'subclasses': {}},
    'UR': {'description': 'Urban residential', 'subclasses': {}},
    'UC': {'description': 'Commercial', 'subclasses': {}},
    'UI': {'description': 'Industrial', 'subclasses': {}},
    'UL': {'description': 'Urban landscape', 'subclasses': {}},
    'UV': {'description': 'Urban vacant', 'subclasses': {}},
    'S':  {'description': 'Semi-agricultural', 'subclasses': {}},
    'X':  {'description': 'Not cropped / unclassified', 'subclasses': {}},
    'E':  {'description': 'Entry denied', 'subclasses': {}},
    'Z':  {'description': 'Outside study area', 'subclasses': {}},
}


# ---------------------------------------------------------------------------

class AVIRISProcessor:
    """
    Batch-download AVIRIS-NG KML flight line outlines from the NASA/JPL data
    portal and convert them to ESRI Shapefiles via GDAL.

    Parameters
    ----------
    excel_file : str
        Path to the AVIRIS-NG Flight Lines Excel spreadsheet.
    kml_dir : str
        Directory to save downloaded KML files.
    shapefile_dir : str
        Directory to save converted shapefiles.

    Example
    -------
    >>> proc = AVIRISProcessor("AVIRIS-NG Flight Lines.xlsx", "kml/", "shapefiles/")
    >>> proc.download_kml_files()
    >>> proc.convert_to_shapefiles()
    """

    def __init__(self, excel_file: str, kml_dir: str, shapefile_dir: str):
        self.kml_dir       = Path(kml_dir)
        self.shapefile_dir = Path(shapefile_dir)
        self.flight_lines  = pd.read_excel(excel_file)

    def download_kml_files(self, workers: int = 5) -> None:
        """Parallel KML download. Skips rows with missing links."""
        self.kml_dir.mkdir(parents=True, exist_ok=True)

        def _download(row) -> None:
            kml_link = row.link_kml_outline
            if pd.isna(kml_link):
                print(f"Skipping {row.Name} — missing KML link")
                return
            out_path = self.kml_dir / f"{row.Name}.kml"
            try:
                resp = requests.get(kml_link, timeout=30)
                resp.raise_for_status()
                out_path.write_bytes(resp.content)
                print(f"Downloaded: {out_path.name}")
            except requests.exceptions.RequestException as e:
                print(f"Failed {row.Name}: {e}")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            pool.map(_download, self.flight_lines.itertuples(index=False))

    def convert_to_shapefiles(self) -> None:
        """Convert all KMLs in kml_dir to ESRI Shapefiles using ogr2ogr."""
        self.shapefile_dir.mkdir(parents=True, exist_ok=True)
        kml_files = list(self.kml_dir.glob("*.kml"))
        print(f"Converting {len(kml_files)} KML files...")

        for kml_file in kml_files:
            out_path = self.shapefile_dir / f"{kml_file.stem}.shp"
            try:
                subprocess.run(
                    ["ogr2ogr", "-f", "ESRI Shapefile", str(out_path), str(kml_file)],
                    check=True,
                )
                print(f"Saved: {out_path.name}")
            except subprocess.CalledProcessError as e:
                print(f"Failed to convert {kml_file.name}: {e}")


# ---------------------------------------------------------------------------

class ShapefileProcessor:
    """
    Label a California DWR i15 land-use shapefile with human-readable crop
    type descriptions using DWR_CROP_CODES.

    Filters to single-use parcels (MULTIUSE == 'S'), parses the MAIN_CROP
    field into class and subclass codes, and saves a new shapefile with
    CLASS_DESCRIPTION and SUBCLASS_DESCRIPTION columns appended.

    Example
    -------
    >>> sp = ShapefileProcessor("i15_crop.shp", "i15_labeled.shp")
    >>> df = sp.process()
    """

    def __init__(self, shapefile_path: str, output_path: str):
        self.shapefile_path = Path(shapefile_path)
        self.output_path    = Path(output_path)
        self.gdf            = gpd.read_file(self.shapefile_path)

    def _describe(self, class_code: str, subclass_code: Optional[str]):
        info       = DWR_CROP_CODES.get(str(class_code).strip(), {})
        class_desc = info.get('description', 'Unknown')
        sub_desc   = info.get('subclasses', {}).get(str(subclass_code).strip(), 'Unknown')
        return class_desc, sub_desc

    def process(self) -> gpd.GeoDataFrame:
        """Filter, label, and save. Returns the labeled GeoDataFrame."""
        df = self.gdf[self.gdf['MULTIUSE'] == 'S'].copy()
        df['CLASS_CODE']    = df['MAIN_CROP'].str.extract(r'([A-Za-z]+)')
        df['SUBCLASS_CODE'] = df['MAIN_CROP'].str.extract(r'(\d+)')

        labels = df.apply(
            lambda r: self._describe(r['CLASS_CODE'], r['SUBCLASS_CODE']), axis=1
        )
        df['CLASS_DESCRIPTION']    = [lbl[0] for lbl in labels]
        df['SUBCLASS_DESCRIPTION'] = [lbl[1] for lbl in labels]

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_file(self.output_path, driver='ESRI Shapefile')
        print(f"Saved labeled shapefile → {self.output_path}")
        return df


# ---------------------------------------------------------------------------

class ShapefileSplitter:
    """
    Split a labeled i15 shapefile into one shapefile per crop subclass.

    Expects a SUBCLASS_D column produced by ShapefileProcessor.

    Example
    -------
    >>> ss = ShapefileSplitter("i15_labeled.shp", "split/")
    >>> ss.split()
    """

    def __init__(self, shapefile_path: str, output_dir: str):
        self.gdf        = gpd.read_file(shapefile_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def split(self) -> None:
        """Write one shapefile per unique SUBCLASS_D value."""
        for subclass in self.gdf['SUBCLASS_D'].unique():
            subset   = self.gdf[self.gdf['SUBCLASS_D'] == subclass]
            out_name = f"{str(subclass).replace(' ', '_')}.shp"
            subset.to_file(self.output_dir / out_name)
            print(f"Saved: {out_name}  ({len(subset)} features)")


# ---------------------------------------------------------------------------

class ShapefileToMap:
    """
    Load any shapefile and render it as an interactive Folium map.

    Example
    -------
    >>> m = ShapefileToMap("shapefiles/Almonds.shp").load().build_map()
    >>> m.save("almonds_map.html")
    """

    def __init__(self, shapefile_path: str):
        self.shapefile_path = Path(shapefile_path)
        self.gdf: Optional[gpd.GeoDataFrame] = None
        self.map: Optional[folium.Map]        = None

    def load(self) -> 'ShapefileToMap':
        if not self.shapefile_path.exists():
            raise FileNotFoundError(self.shapefile_path)
        self.gdf = gpd.read_file(self.shapefile_path)
        return self

    def build_map(self, zoom_start: int = 10) -> 'ShapefileToMap':
        """Build the Folium map centred on the data extent."""
        if self.gdf is None:
            raise ValueError("Call .load() first.")
        cx = self.gdf.geometry.centroid.x.mean()
        cy = self.gdf.geometry.centroid.y.mean()
        self.map = folium.Map(
            location=[cy, cx],
            zoom_start=zoom_start,
            tiles='CartoDB dark_matter',
        )
        folium.GeoJson(
            self.gdf,
            tooltip=folium.GeoJsonTooltip(fields=list(self.gdf.columns[:3])),
        ).add_to(self.map)
        return self

    def save(self, output_path: str) -> None:
        """Save the map to an HTML file."""
        if self.map is None:
            raise ValueError("Call .build_map() first.")
        self.map.save(output_path)
        print(f"Map saved → {output_path}")

    def show(self) -> folium.Map:
        """Return the map object (renders inline in Jupyter)."""
        if self.map is None:
            raise ValueError("Call .build_map() first.")
        return self.map
