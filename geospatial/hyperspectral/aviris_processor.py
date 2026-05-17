import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests


class AVIRISProcessor:
    def __init__(self, excel_file: str, kml_dir: str, shapefile_dir: str):
        self.kml_dir = Path(kml_dir)
        self.shapefile_dir = Path(shapefile_dir)
        self.flight_lines = pd.read_excel(excel_file)

    def download_kml_files(self, workers: int = 5) -> None:
        self.kml_dir.mkdir(parents=True, exist_ok=True)

        def _download(row) -> None:
            kml_link = row.link_kml_outline
            if pd.isna(kml_link):
                print(f"Skipping {row.Name} — missing KML link")
                return
            out_path = self.kml_dir / f"{row.Name}.kml"
            try:
                response = requests.get(kml_link, timeout=30)
                response.raise_for_status()
                out_path.write_bytes(response.content)
                print(f"Downloaded: {out_path.name}")
            except requests.exceptions.RequestException as e:
                print(f"Failed {row.Name}: {e}")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            pool.map(_download, self.flight_lines.itertuples(index=False))

    def convert_to_shapefiles(self) -> None:
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
