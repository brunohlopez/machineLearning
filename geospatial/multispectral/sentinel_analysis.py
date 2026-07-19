import io
import os
import base64
from typing import Dict, List, Optional, Tuple

# GDAL/rasterio (used under the hood by stackstac for the actual S3 reads
# during .compute()) has no timeout by default — a stalled/black-holed
# connection can hang a read forever with zero feedback. Set process-wide
# bounds *before* rasterio/GDAL is touched anywhere, so every read — plain
# usage and the Drift tab's background-thread prefetches alike — fails fast
# and raises instead of hanging. Real env vars take precedence if already set.
os.environ.setdefault('GDAL_HTTP_TIMEOUT', '20')
os.environ.setdefault('GDAL_HTTP_CONNECTTIMEOUT', '10')
os.environ.setdefault('GDAL_HTTP_MAX_RETRY', '2')
os.environ.setdefault('GDAL_HTTP_RETRY_DELAY', '1')

import numpy as np
import requests
import xarray as xr
import pystac_client
import stackstac
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from PIL import Image

STAC_URL  = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"

# STAC common-name → center wavelength (nm)
SPECTRAL_ASSETS: Dict[str, int] = {
    'coastal':  443,
    'blue':     490,
    'green':    560,
    'red':      665,
    'rededge1': 705,
    'rededge2': 740,
    'rededge3': 783,
    'nir':      842,
    'nir08':    865,
    'nir09':    940,
    'swir16':   1610,
    'swir22':   2190,
}

# USGS dNBR burn severity thresholds (Key et al. 2006)
SEVERITY_CLASSES: List[Tuple] = [
    ("Enhanced Regrowth (High)", (-2.000, -0.501), "#3A7D44"),
    ("Enhanced Regrowth (Low)",  (-0.500, -0.251), "#86C166"),
    ("Unburned",                 (-0.250,  0.099), "#D4D4AA"),
    ("Low Severity",             ( 0.100,  0.269), "#F5E642"),
    ("Moderate-Low Severity",    ( 0.270,  0.439), "#F0A500"),
    ("Moderate-High Severity",   ( 0.440,  0.659), "#E8530A"),
    ("High Severity",            ( 0.660,  2.000), "#7A0000"),
]

INDEX_COLORMAPS: Dict[str, str] = {
    'NDVI':  'RdYlGn',
    'NDWI':  'RdBu',
    'EVI':   'Greens',
    'SAVI':  'YlGn',
    'NBR':   'RdYlGn',
    'NDMI':  'RdBu',
    'GNDVI': 'Greens',
    'NDRE':  'RdYlGn',
    'NDSI':  'Blues',
    'NDBI':  'Greys',
    'MNDWI': 'Blues',
    'NBR2':  'RdYlGn',
}


class SentinelAnalyzer:
    def __init__(self):
        # timeout guards against a silently stalled connection hanging forever
        # (connect timeout, read timeout) in seconds.
        self.catalog = pystac_client.Client.open(STAC_URL, timeout=(10, 20))

    def search(self, bbox: List[float], start_date: str,
               end_date: str, cloud_pct: int = 30) -> list:
        """bbox = [west, south, east, north]"""
        return list(
            self.catalog.search(
                collections=[COLLECTION],
                bbox=bbox,
                datetime=f"{start_date}/{end_date}",
                query={"eo:cloud_cover": {"lt": cloud_pct}},
            ).items()
        )

    def load_stack(self, items: list, bbox: List[float],
                   resolution: float = 0.001,
                   assets: Optional[List[str]] = None) -> xr.DataArray:
        """
        Lazy-load bands into an xarray stack in WGS-84.
        resolution in degrees — 0.001° ≈ 111 m at equator.
        assets: subset of bands to load (default: all 12 spectral + scl).
        Loading only what you need is a large speedup — e.g. RGB tiles for
        the Drift tab load 4 assets instead of 13.
        """
        if assets is None:
            assets = list(SPECTRAL_ASSETS.keys()) + ['scl']
        return stackstac.stack(
            items,
            assets=assets,
            bounds_latlon=bbox,
            epsg=4326,
            resolution=resolution,
            dtype='float64',
            rescale=False,
        )

    def cloud_mask(self, stack: xr.DataArray) -> xr.DataArray:
        """Mask clouds / shadows using SCL band (values 3, 8, 9, 10).
        Works with any subset of spectral bands (as long as scl is loaded)."""
        scl  = stack.sel(band='scl')
        bad  = (scl == 3) | (scl == 8) | (scl == 9) | (scl == 10)
        spec_bands = [b for b in stack.band.values.tolist() if b != 'scl']
        spec = stack.sel(band=spec_bands)
        return spec.where(~bad)

    def median_composite(self, stack: xr.DataArray) -> xr.DataArray:
        """Time-median composite. Triggers S3 download via Dask."""
        return stack.median(dim='time', skipna=True).compute()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def render_rgb(self, composite: xr.DataArray,
                   p_low: float = 2, p_high: float = 98
                   ) -> Tuple[np.ndarray, List[float]]:
        rgb = np.stack([
            composite.sel(band='red').values,
            composite.sel(band='green').values,
            composite.sel(band='blue').values,
        ], axis=-1) / 10000.0

        valid_px = rgb[~np.isnan(rgb)]
        lo, hi = np.nanpercentile(valid_px, (p_low, p_high))
        rgb = np.clip((rgb - lo) / (hi - lo + 1e-8), 0, 1)

        alpha = (~np.any(np.isnan(rgb), axis=-1) * 255).astype(np.uint8)
        rgba  = np.dstack([(rgb * 255).astype(np.uint8), alpha])
        return rgba, self._bounds(composite)

    def render_index(self, composite: xr.DataArray,
                     index_name: str) -> Tuple[np.ndarray, List[float]]:
        arr  = self._compute_index(composite, index_name)
        cmap = plt.get_cmap(INDEX_COLORMAPS.get(index_name, 'RdYlGn'))
        norm = mcolors.TwoSlopeNorm(vmin=-1, vcenter=0, vmax=1)
        rgba = (cmap(norm(np.nan_to_num(arr, nan=0.0))) * 255).astype(np.uint8)
        rgba[..., 3] = (~np.isnan(arr) * 255).astype(np.uint8)
        return rgba, self._bounds(composite)

    def render_timelapse(self, items: list, bbox: List[float],
                         resolution: float = 0.002) -> List[Image.Image]:
        """Render one RGB frame per calendar month. Returns PIL images."""
        stack   = self.load_stack(items, bbox, resolution=resolution)
        masked  = self.cloud_mask(stack)
        monthly = masked.resample(time='1MS').median(skipna=True).compute()

        frames = []
        for t in monthly.time.values:
            comp = monthly.sel(time=t)
            if np.all(np.isnan(comp.values)):
                continue
            rgba, _ = self.render_rgb(comp)
            frames.append(Image.fromarray(rgba, 'RGBA').convert('RGB'))
        return frames

    def drift_tile(
        self,
        bbox: List[float],
        start: str,
        end: str,
        cloud: int,
        resolution: float = 0.001,
        gap_fill: bool = True,
        stretch: Optional[Tuple[float, float]] = None,
        max_scenes: int = 4,
    ) -> Optional[Dict]:
        """
        Fast RGB tile for Drift mode. Compared to the full pipeline this:
        - loads only red/green/blue + scl (4 assets instead of 13),
        - keeps just the `max_scenes` least-cloudy scenes,
        - optionally fills cloud-mask holes from the unmasked median
          (gap_fill), so the flythrough doesn't show black speckle,
        - accepts a fixed (lo, hi) stretch so brightness stays consistent
          across tiles instead of re-normalizing per tile.
        Returns {'rgba', 'bounds', 'stretch'} or None if no scenes.
        """
        items = self.search(bbox, start, end, cloud)
        if not items:
            return None
        items = sorted(
            items, key=lambda it: it.properties.get('eo:cloud_cover', 100.0)
        )[:max_scenes]

        stack  = self.load_stack(items, bbox, resolution,
                                 assets=['red', 'green', 'blue', 'scl'])
        masked = self.cloud_mask(stack)
        comp   = masked.median(dim='time', skipna=True)
        if gap_fill:
            raw  = stack.sel(band=['red', 'green', 'blue']).median(
                dim='time', skipna=True
            )
            comp = comp.fillna(raw)
        comp = comp.compute()

        rgb = np.stack(
            [comp.sel(band=b).values for b in ('red', 'green', 'blue')],
            axis=-1,
        )
        valid = ~np.any(np.isnan(rgb), axis=-1)
        if not valid.any():
            return None
        if stretch is None:
            lo, hi = np.nanpercentile(rgb[valid], (2, 98))
        else:
            lo, hi = stretch
        norm  = np.clip((rgb - lo) / (hi - lo + 1e-8), 0, 1)
        alpha = (valid * 255).astype(np.uint8)
        rgba  = np.dstack([(norm * 255).astype(np.uint8), alpha])
        return {
            'rgba':    rgba,
            'bounds':  self._bounds(comp),
            'stretch': (float(lo), float(hi)),
        }

    # ── Point queries ─────────────────────────────────────────────────────────

    def get_spectra(self, lat: float, lon: float,
                    composite: xr.DataArray) -> Optional[Dict[str, float]]:
        pt = composite.sel(y=lat, x=lon, method='nearest')
        return {
            band: float(pt.sel(band=band).values) / 10000.0
            for band in SPECTRAL_ASSETS
            if band in pt.band.values
            and not np.isnan(float(pt.sel(band=band).values))
        } or None

    # ── Burn Scar Analysis ────────────────────────────────────────────────────

    def burn_scar_analysis(
        self,
        items_pre: list,
        items_post: list,
        bbox: List[float],
        resolution: float = 0.001,
    ) -> Tuple[np.ndarray, np.ndarray, List[float]]:
        """
        Compute dNBR burn severity from pre/post fire STAC items.
        Returns (dnbr, classified, bounds).
        dNBR = NBR_pre − NBR_post  (positive values indicate burn).
        """
        def _nbr_composite(items):
            stack    = self.load_stack(items, bbox, resolution)
            masked   = self.cloud_mask(stack)
            comp     = self.median_composite(masked)
            return comp, self._compute_index(comp, 'NBR')

        comp_pre,  nbr_pre  = _nbr_composite(items_pre)
        _,         nbr_post = _nbr_composite(items_post)

        dnbr       = nbr_pre - nbr_post
        classified = self._classify_severity(dnbr)
        return dnbr, classified, self._bounds(comp_pre)

    def render_burn_severity(self, classified: np.ndarray) -> np.ndarray:
        """RGBA image of USGS severity classes."""
        rgba = np.zeros((*classified.shape, 4), dtype=np.uint8)
        for i, (_, _, hex_color) in enumerate(SEVERITY_CLASSES):
            r, g, b, a = (np.array(mcolors.to_rgba(hex_color)) * 255).astype(np.uint8)
            mask = classified == i
            rgba[mask] = [r, g, b, 220]
        return rgba

    def burn_severity_stats(
        self, classified: np.ndarray, resolution: float = 0.001
    ) -> List[dict]:
        """Area stats per severity class. resolution in degrees."""
        px_km2 = (resolution * 111.0) ** 2
        total  = int(np.sum(classified >= 0))
        rows   = []
        for i, (label, _, color) in enumerate(SEVERITY_CLASSES):
            n = int(np.sum(classified == i))
            rows.append({
                'label':   label,
                'color':   color,
                'pixels':  n,
                'area_km2': round(n * px_km2, 2),
                'pct':     round(100 * n / total, 1) if total else 0.0,
            })
        return rows

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _compute_index(self, composite: xr.DataArray, name: str) -> np.ndarray:
        def b(band):
            return composite.sel(band=band).values.astype(float)

        def nd(a, bnd):
            x, y = b(a), b(bnd)
            return np.where((x + y) == 0, np.nan, (x - y) / (x + y))

        nir, red, green = b('nir'), b('red'), b('green')
        blue, s16, s22  = b('blue'), b('swir16'), b('swir22')

        if   name == 'NDVI':  return nd('nir', 'red')
        elif name == 'NDWI':  return nd('green', 'nir')      # McFeeters 1996
        elif name == 'EVI':   return 2.5 * (nir - red) / (nir + 6*red - 7.5*blue + 1)
        elif name == 'SAVI':  return 1.5 * (nir - red) / (nir + red + 0.5)
        elif name == 'NBR':   return nd('nir', 'swir22')
        elif name == 'NDMI':  return nd('nir', 'swir16')
        elif name == 'GNDVI': return nd('nir', 'green')
        elif name == 'NDRE':  return nd('nir', 'rededge1')
        elif name == 'NDSI':  return nd('green', 'swir16')
        elif name == 'NDBI':  return nd('swir16', 'nir')
        elif name == 'MNDWI': return nd('green', 'swir16')
        elif name == 'NBR2':  return nd('swir16', 'swir22')  # USGS NBR2
        else: raise ValueError(f"Unknown index: {name}")

    def _classify_severity(self, dnbr: np.ndarray) -> np.ndarray:
        out = np.full(dnbr.shape, -1, dtype=np.int8)
        for i, (_, (lo, hi), _) in enumerate(SEVERITY_CLASSES):
            out = np.where((dnbr >= lo) & (dnbr < hi) & ~np.isnan(dnbr), i, out)
        return out

    def _bounds(self, composite: xr.DataArray) -> List[float]:
        """Returns [south, west, north, east]."""
        return [
            float(composite.y.min()), float(composite.x.min()),
            float(composite.y.max()), float(composite.x.max()),
        ]


# ── Open-Meteo ────────────────────────────────────────────────────────────────

def fetch_weather(lat: float, lon: float) -> Optional[Dict]:
    """
    Current weather at a point from Open-Meteo (no API key required).
    Swap the base URL with archive-api.open-meteo.com/v1/archive and add
    `start_date`/`end_date` params to pull historical weather aligned to
    the imagery date range.
    """
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,precipitation,cloud_cover,wind_speed_10m"
        f"&forecast_days=1"
    )
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return resp.json().get('current', {})
    except Exception:
        return None


# ── Encoding helper ───────────────────────────────────────────────────────────

def rgba_to_base64(rgba: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgba, 'RGBA').save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()
