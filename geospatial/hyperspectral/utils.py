import requests
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
import os
import subprocess

class AVIRISDataProcessor:
    def __init__(self, excel_file, kml_dir, shapefile_dir):
        self.excel_file = excel_file
        self.kml_dir = kml_dir
        self.shapefile_dir = shapefile_dir
        self.aviris_path_df = pd.read_excel(self.excel_file)

    def download_aviris_kml(self):
        # Ensure the output directory exists
        Path(self.kml_dir).mkdir(parents=True, exist_ok=True)

        def download_kml(row):
            kml_link = row.link_kml_outline
            if pd.isna(kml_link):
                print(f"Skipping {row.Name} due to missing KML link")
                return  # Skip if the link is NaN
            output_name = f"{row.Name}.kml"
            final_output_path = Path(self.kml_dir) / output_name

            try:
                response = requests.get(kml_link)
                response.raise_for_status()  # Will raise an exception for HTTP errors
                with open(final_output_path, 'wb') as file:
                    file.write(response.content)
                print(f"Downloaded: {output_name}")
            except requests.exceptions.HTTPError as err:
                print(f"HTTP Error for {kml_link}: {err}")
            except requests.exceptions.RequestException as err:
                print(f"Request Exception for {kml_link}: {err}")

        # Using ThreadPoolExecutor to download files in parallel
        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(download_kml, self.aviris_path_df.itertuples(index=False))

    def convert_kml_to_shapefiles_gdal(self):
        # Ensure the output directory exists
        Path(self.shapefile_dir).mkdir(parents=True, exist_ok=True)

        # List all KML files in the specified directory
        kml_files = list(Path(self.kml_dir).glob('*.kml'))
        print(f"Found {len(kml_files)} KML files.")

        for kml_file in kml_files:
            print(f"Converting {kml_file} to Shapefile...")
            try:
                shapefile_name = kml_file.stem + '.shp'
                shapefile_path = Path(self.shapefile_dir) / shapefile_name

                # Construct the ogr2ogr command
                cmd = [
                    "ogr2ogr",
                    "-f", "ESRI Shapefile",
                    str(shapefile_path),
                    str(kml_file)
                ]

                # Run the command
                subprocess.run(cmd, check=True)
                print(f"Saved Shapefile: {shapefile_path}")
            except subprocess.CalledProcessError as e:
                print(f"Failed to convert {kml_file}: {e}")