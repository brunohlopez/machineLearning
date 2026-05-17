"""
aviris_analyzer.py
------------------
AVIRIS-NG hyperspectral analysis — the hyperspectral counterpart to
geospatial/multispectral/sentinel_analysis.py.

Mirrors SentinelAnalyzer's interface (render_rgb / render_index / get_spectra /
burn_scar_analysis) but is backed by a local ENVI cube read with the
`spectral` library instead of streaming COGs from S3.

Supports both AVIRIS-NG product levels:
  * L2 surface reflectance (`*corr_v*_img` / `*rfl*`) — atmospherically
    corrected, 32-bit float, native ~0–1.
  * L1B at-sensor radiance (`*rdn_v*_img` / `*rad*`) — uncorrected, values in
    µW·cm⁻²·nm⁻¹·sr⁻¹ (tens–hundreds). Atmospheric absorption features are
    visible. Indices/dNBR on radiance are uncalibrated proxies (see UI caveat).

Product type is auto-detected from the filename, confirmed by the data range.
Common to both: orthorectified `corr`/`rdn` products carry georeferencing in
the .hdr (read via rasterio); negative values are fill/edge → masked as NaN;
~422 bands, 380–2500 nm at 5 nm; rendering uses percentile stretch and
normalized-difference indices, so it is product-agnostic by construction.
"""

import base64
import io
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import rasterio
import spectral.io.envi as envi
from PIL import Image
from rasterio.warp import transform_bounds

# Natural-colour band targets (nm) — AVIRIS-NG true colour
RGB_WAVELENGTHS = {'red': 650.0, 'green': 550.0, 'blue': 470.0}

# Same index names/colormaps as SentinelAnalyzer, for cross-app parity
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

# Index → (wavelength_a nm, wavelength_b nm) for normalized-difference indices
_ND_BANDS = {
    'NDVI':  (842, 665),
    'NDWI':  (842, 1610),
    'NBR':   (842, 2190),
    'NDMI':  (842, 1610),
    'GNDVI': (842, 560),
    'NDRE':  (842, 705),
    'NDSI':  (560, 1610),
    'NDBI':  (1610, 842),
    'MNDWI': (560, 1610),
    'NBR2':  (2190, 842),
}

# USGS dNBR burn severity thresholds (Key & Benson 2006) — kept local so the
# hyperspectral app stays self-contained from the multispectral package.
SEVERITY_CLASSES: List[Tuple] = [
    ("Enhanced Regrowth (High)", (-2.000, -0.501), "#3A7D44"),
    ("Enhanced Regrowth (Low)",  (-0.500, -0.251), "#86C166"),
    ("Unburned",                 (-0.250,  0.099), "#D4D4AA"),
    ("Low Severity",             ( 0.100,  0.269), "#F5E642"),
    ("Moderate-Low Severity",    ( 0.270,  0.439), "#F0A500"),
    ("Moderate-High Severity",   ( 0.440,  0.659), "#E8530A"),
    ("High Severity",            ( 0.660,  2.000), "#7A0000"),
]


_RADIANCE_UNITS = "µW·cm⁻²·nm⁻¹·sr⁻¹"


class AVIRISAnalyzer:
    """
    Lazy, memory-mapped reader/analyzer for one AVIRIS-NG cube (reflectance or
    radiance — auto-detected).

    Parameters
    ----------
    img_path : str
        Path to the `*corr_v*_img` (reflectance) or `*rdn_v*_img` (radiance)
        ENVI binary. The matching `.hdr` is auto-detected.

    Example
    -------
    >>> a = AVIRISAnalyzer("ang20210815t181024_corr_v2z1_img").load()
    >>> a.product_type            # 'reflectance' or 'radiance'
    >>> rgba, bounds = a.render_rgb()
    >>> spectrum = a.get_spectra_latlon(38.9, -120.8)
    """

    def __init__(self, img_path: str):
        self.img_path = Path(img_path)
        hdr = Path(str(self.img_path) + ".hdr")
        if not hdr.exists():
            hdr = self.img_path.with_suffix(".hdr")
        self.hdr_path = hdr

        self._spy = None
        self._wl: Optional[np.ndarray] = None
        self._bounds: Optional[List[float]] = None   # [south, west, north, east]
        self._crs = None
        self._transform = None
        self._product_type: Optional[str] = None     # 'reflectance' | 'radiance'

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(self) -> "AVIRISAnalyzer":
        if not self.hdr_path.exists():
            raise FileNotFoundError(f"ENVI header not found: {self.hdr_path}")

        self._spy = envi.open(str(self.hdr_path), str(self.img_path))
        centers = self._spy.bands.centers
        if centers is None:
            raise ValueError("Header has no `wavelength` field — not a spectral cube.")
        self._wl = np.asarray(centers, dtype=float)

        # Orthorectified product → georeferencing lives in the .hdr
        with rasterio.open(str(self.img_path)) as src:
            self._crs = src.crs
            self._transform = src.transform
            w, s, e, n = src.bounds
            if src.crs and not src.crs.is_geographic:
                w, s, e, n = transform_bounds(src.crs, "EPSG:4326", w, s, e, n)
            self._bounds = [s, w, n, e]

        self._product_type = self._detect_product_type()
        return self

    def _detect_product_type(self) -> str:
        """
        Filename first (authoritative for standard AVIRIS-NG naming), then a
        data-range sanity check on a mid-spectrum band.
        """
        name = self.img_path.name.lower()
        if any(k in name for k in ("corr", "rfl", "refl")):
            return "reflectance"
        if any(k in name for k in ("rdn", "_rad", "radiance")):
            return "radiance"

        # Heuristic fallback: 98th pct of a ~800 nm band.
        # Reflectance is bounded ~0–1.3; radiance is tens–hundreds.
        try:
            sample = self._read_band(800.0, downsample=8)
            valid = sample[np.isfinite(sample)]
            if valid.size and np.nanpercentile(valid, 98) <= 1.5:
                return "reflectance"
        except Exception:
            pass
        return "radiance"

    @property
    def product_type(self) -> str:
        """'reflectance' or 'radiance'."""
        return self._product_type or "unknown"

    @property
    def is_reflectance(self) -> bool:
        return self._product_type == "reflectance"

    @property
    def value_label(self) -> str:
        """Y-axis label for spectra / colour bars."""
        return ("Reflectance" if self.is_reflectance
                else f"Radiance ({_RADIANCE_UNITS})")

    @property
    def wavelengths(self) -> np.ndarray:
        return self._wl

    @property
    def n_bands(self) -> int:
        return 0 if self._wl is None else len(self._wl)

    @property
    def shape(self) -> Tuple[int, int]:
        return int(self._spy.nrows), int(self._spy.ncols)

    # ── Band access ───────────────────────────────────────────────────────────

    def _nearest_band(self, wavelength_nm: float) -> int:
        """0-based index of the band closest to the requested wavelength."""
        return int(np.argmin(np.abs(self._wl - wavelength_nm)))

    def _read_band(self, wavelength_nm: float, downsample: int = 1) -> np.ndarray:
        """Read one band as float32 with negatives (fill values) → NaN."""
        idx = self._nearest_band(wavelength_nm)
        arr = self._spy.read_band(idx).astype(np.float32)
        if downsample > 1:
            arr = arr[::downsample, ::downsample]
        arr[arr < 0] = np.nan          # AVIRIS-NG fills bad/edge pixels negative
        return arr

    # ── Rendering ─────────────────────────────────────────────────────────────

    def render_rgb(self, p_low: float = 2, p_high: float = 98,
                    downsample: int = 4) -> Tuple[np.ndarray, List[float]]:
        r = self._read_band(RGB_WAVELENGTHS['red'],   downsample)
        g = self._read_band(RGB_WAVELENGTHS['green'], downsample)
        b = self._read_band(RGB_WAVELENGTHS['blue'],  downsample)
        rgb = np.stack([r, g, b], axis=-1)            # already 0–1 reflectance

        valid = rgb[~np.isnan(rgb)]
        lo, hi = np.nanpercentile(valid, (p_low, p_high))
        rgb = np.clip((rgb - lo) / (hi - lo + 1e-8), 0, 1)

        alpha = (~np.any(np.isnan(rgb), axis=-1) * 255).astype(np.uint8)
        rgba = np.dstack([(np.nan_to_num(rgb) * 255).astype(np.uint8), alpha])
        return rgba, self._bounds

    def render_index(self, index_name: str,
                     downsample: int = 4) -> Tuple[np.ndarray, List[float]]:
        arr = self._compute_index(index_name, downsample)
        cmap = plt.get_cmap(INDEX_COLORMAPS.get(index_name, 'RdYlGn'))
        norm = mcolors.TwoSlopeNorm(vmin=-1, vcenter=0, vmax=1)
        rgba = (cmap(norm(np.nan_to_num(arr, nan=0.0))) * 255).astype(np.uint8)
        rgba[..., 3] = (~np.isnan(arr) * 255).astype(np.uint8)
        return rgba, self._bounds

    # ── Point queries ─────────────────────────────────────────────────────────

    def get_spectra(self, row: int, col: int) -> Optional[Dict[float, float]]:
        """Full 422-band spectrum at a pixel: {wavelength_nm: reflectance}."""
        if not (0 <= row < self._spy.nrows and 0 <= col < self._spy.ncols):
            return None
        px = np.asarray(self._spy.read_pixel(row, col), dtype=float)
        return {
            float(wl): float(v)
            for wl, v in zip(self._wl, px)
            if v >= 0 and not np.isnan(v)
        } or None

    def latlon_to_pixel(self, lat: float, lon: float) -> Tuple[int, int]:
        """Map a WGS-84 click back to (row, col) using the .hdr georeferencing."""
        x, y = lon, lat
        if self._crs and not self._crs.is_geographic:
            from pyproj import Transformer
            tr = Transformer.from_crs("EPSG:4326", self._crs, always_xy=True)
            x, y = tr.transform(lon, lat)
        col, row = ~self._transform * (x, y)
        return int(row), int(col)

    def get_spectra_latlon(self, lat: float, lon: float) -> Optional[Dict[float, float]]:
        row, col = self.latlon_to_pixel(lat, lon)
        return self.get_spectra(row, col)

    # ── Burn scar ─────────────────────────────────────────────────────────────

    def burn_scar_analysis(self, post: "AVIRISAnalyzer",
                           downsample: int = 4
                           ) -> Tuple[np.ndarray, np.ndarray, List[float]]:
        """
        dNBR = NBR_pre − NBR_post, classified with USGS thresholds.
        `self` is pre-fire, `post` is the post-fire analyzer.
        Returns (dnbr, classified, bounds).
        """
        nbr_pre  = self._compute_index('NBR', downsample)
        nbr_post = post._compute_index('NBR', downsample)

        r = min(nbr_pre.shape[0], nbr_post.shape[0])
        c = min(nbr_pre.shape[1], nbr_post.shape[1])
        dnbr = nbr_pre[:r, :c] - nbr_post[:r, :c]

        return dnbr, self._classify_severity(dnbr), self._bounds

    def render_burn_severity(self, classified: np.ndarray) -> np.ndarray:
        rgba = np.zeros((*classified.shape, 4), dtype=np.uint8)
        for i, (_, _, hex_color) in enumerate(SEVERITY_CLASSES):
            r, g, b, _ = (np.array(mcolors.to_rgba(hex_color)) * 255).astype(np.uint8)
            rgba[classified == i] = [r, g, b, 220]
        return rgba

    def burn_severity_stats(self, classified: np.ndarray,
                            pixel_size_m: float = 4.0) -> List[dict]:
        """Per-class area. AVIRIS-NG GSD is typically 3–5 m."""
        px_km2 = (pixel_size_m / 1000.0) ** 2
        total  = int(np.sum(classified >= 0))
        rows   = []
        for i, (label, _, color) in enumerate(SEVERITY_CLASSES):
            n = int(np.sum(classified == i))
            rows.append({
                'label':    label,
                'color':    color,
                'pixels':   n,
                'area_km2': round(n * px_km2, 3),
                'pct':      round(100 * n / total, 1) if total else 0.0,
            })
        return rows

    # ── Internal ──────────────────────────────────────────────────────────────

    def _compute_index(self, name: str, downsample: int = 1) -> np.ndarray:
        def b(wl):
            return self._read_band(wl, downsample)

        if name in _ND_BANDS:
            wa, wb = _ND_BANDS[name]
            x, y = b(wa), b(wb)
            denom = x + y
            return np.where(denom == 0, np.nan, (x - y) / denom)

        nir, red, blue = b(842), b(665), b(470)
        if name == 'EVI':
            return 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)
        if name == 'SAVI':
            return 1.5 * (nir - red) / (nir + red + 0.5)
        raise ValueError(f"Unknown index: {name}")

    def _classify_severity(self, dnbr: np.ndarray) -> np.ndarray:
        out = np.full(dnbr.shape, -1, dtype=np.int8)
        for i, (_, (lo, hi), _) in enumerate(SEVERITY_CLASSES):
            out = np.where((dnbr >= lo) & (dnbr < hi) & ~np.isnan(dnbr), i, out)
        return out


# ── Encoding helper (mirrors sentinel_analysis.rgba_to_base64) ────────────────

def rgba_to_base64(rgba: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgba, 'RGBA').save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()
