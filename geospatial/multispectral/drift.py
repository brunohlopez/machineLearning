"""Drift mode — cinematic flythrough engine for the Sentinel-2 app.

Pure-Python module (no Streamlit imports) so it can be unit-tested standalone.

The engine models the world as a grid of tiles (side = 2 * radius_deg). Tiles
are fetched in background threads through the existing SentinelAnalyzer
pipeline (search -> load_stack -> cloud_mask -> median_composite -> render_rgb),
kept in a small LRU cache, and stitched into a mosaic. The client (a canvas +
requestAnimationFrame component in app.py) pans smoothly across that mosaic;
Python only pushes a new mosaic when a prefetched tile lands.
"""

import base64
import io
import math
import random
import socket
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

BG_RGB = (13, 17, 23)          # matches the app's #0d1117 background
VIEW_SPAN_FRAC = 0.6           # viewport width as a fraction of tile size
FRAME_CAP = 300                # rolling frame buffer cap (GIF export)
MAX_RECORD_SECONDS = 90        # MP4 recording duration cap
EDGE_FRAC = 0.20               # prefetch when within 20% of tile edge
FETCH_TIMEOUT_S = 45           # give up waiting on a stalled fetch after this
HTTP_TIMEOUT_S = 20            # per-request timeout passed to requests/pystac

# Instant basemap source: EOX "Sentinel-2 cloudless" global mosaic, served as
# pre-rendered Web-Mercator tiles (free for non-commercial use, © EOX,
# https://s2maps.eu). Loads in ~1-2 s per drift tile vs 10-30 s for live S3 —
# the immersive "Google-Earth-like" option. No auth required.
EOX_LAYERS = [           # tried in order; first that responds is kept
    "s2cloudless-2020_3857",
    "s2cloudless-2019_3857",
    "s2cloudless_3857",
]
EOX_TILE_URL = ("https://tiles.maps.eox.at/wmts/1.0.0/"
                "{layer}/default/g/{z}/{y}/{x}.jpg")
EOX_MAX_ZOOM = 14
EMPTY_ALPHA_FRAC = 0.25        # tile counts as empty below this valid-pixel frac

# ~30 visually rich Sentinel-2 targets across continents and biomes.
DRIFT_SITES: List[Tuple[str, float, float]] = [
    # Reefs / shallow seas
    ("Great Barrier Reef, Australia",   -18.60,  147.60),
    ("Belize Barrier Reef",              17.30,  -87.80),
    ("Maldives Atolls",                   3.90,   73.40),
    ("Bahama Banks",                     24.20,  -77.60),
    ("Red Sea Reefs, Egypt",             27.20,   33.90),
    # River deltas / wetlands
    ("Ganges-Brahmaputra Delta",         22.10,   89.60),
    ("Nile Delta, Egypt",                31.10,   31.10),
    ("Mississippi Delta, USA",           29.30,  -89.40),
    ("Okavango Delta, Botswana",        -19.30,   22.90),
    ("Lena Delta, Russia",               72.90,  126.00),
    # Deserts / dunes
    ("Sossusvlei Dunes, Namibia",       -24.80,   15.40),
    ("Rub' al Khali, Saudi Arabia",      20.50,   51.00),
    ("White Sands, USA",                 32.80, -106.30),
    ("Atacama Desert, Chile",           -24.50,  -69.30),
    ("Richat Structure, Mauritania",     21.12,  -11.40),
    # Ice / glaciers
    ("Vatnajokull Glacier, Iceland",     64.40,  -16.80),
    ("Southern Patagonia Icefield",     -49.50,  -73.30),
    ("Baltoro Glacier, Pakistan",        35.70,   76.40),
    # Volcanic
    ("Mount Etna, Italy",                37.75,   15.00),
    ("Kilauea, Hawaii",                  19.40, -155.28),
    ("Ngorongoro Crater, Tanzania",      -3.17,   35.58),
    # Urban
    ("Tokyo Bay, Japan",                 35.60,  139.85),
    ("New York City, USA",               40.70,  -74.00),
    ("Cairo and the Nile, Egypt",        30.05,   31.25),
    ("Palm Jumeirah, Dubai",             25.10,   55.15),
    # Agriculture
    ("Center-Pivot Fields, Kansas",      37.70, -100.80),
    ("Dutch Polders, Netherlands",       52.55,    5.40),
    ("Mato Grosso Farmland, Brazil",    -12.50,  -55.80),
    # Mountains / canyons / salt flats
    ("Grand Canyon, USA",                36.10, -112.10),
    ("Everest Region, Himalaya",         27.99,   86.92),
    ("Salar de Uyuni, Bolivia",         -20.25,  -67.55),
    ("Amazon Meanders, Peru",            -4.50,  -73.80),
]


def site_by_name(name: str) -> Optional[Tuple[str, float, float]]:
    for s in DRIFT_SITES:
        if s[0] == name:
            return s
    return None


class DriftEngine:
    """Holds drift position, tile cache, prefetch threads and frame buffers.

    Stored in st.session_state so it survives Streamlit reruns; background
    fetches keep running between reruns.
    """

    MAX_TILES = 4  # LRU: current + neighbors + a jump target in flight

    def __init__(self, radius_deg: float, resolution: float,
                 start: str, end: str, cloud: int,
                 site: Optional[Tuple[str, float, float]] = None,
                 analyzer=None, source: str = 'live',
                 gap_fill: bool = True):
        self.source = source  # 'live' (dated S2 from S3) | 'instant' (EOX mosaic)
        self.gap_fill = gap_fill
        if analyzer is None and source == 'live':
            # Own instance -> background threads never share the UI's client.
            from sentinel_analysis import SentinelAnalyzer
            analyzer = SentinelAnalyzer()
        self.analyzer = analyzer
        self.radius_deg = float(radius_deg)
        self.resolution = float(resolution)
        self.start, self.end, self.cloud = start, end, int(cloud)
        self.tile_size = 2.0 * self.radius_deg
        # One stretch for the whole session -> consistent brightness across
        # tiles (per-tile percentile stretch made mosaics flicker/speckle).
        self.stretch: Optional[Tuple[float, float]] = None

        self.tiles: "OrderedDict[Tuple[int, int], Dict]" = OrderedDict()
        self.futures: Dict[Tuple[int, int], object] = {}
        self.fetch_started: Dict[Tuple[int, int], float] = {}
        self.lock = threading.Lock()
        # Background prefetch (neighbor tiles) and the actively-awaited
        # current/retry tile use separate pools. Otherwise, on a degraded
        # connection, several slow prefetches can saturate one shared pool
        # and starve the tile the user is actually staring at — it would sit
        # queued behind them and get marked "timed out" before a worker ever
        # picks it up.
        self.executor = ThreadPoolExecutor(max_workers=2)
        # 2 workers: if the in-flight current-tile fetch is genuinely stuck
        # (not just slow), a user-triggered retry() still gets its own free
        # worker immediately instead of queuing behind the stuck one forever.
        self.urgent_executor = ThreadPoolExecutor(max_workers=2)

        if site is None:
            site = random.choice(DRIFT_SITES)
        self.site_name, self.lat, self.lon = site
        self.heading = random.uniform(0.0, 360.0)
        self.tiles_crossed = 0
        self.visited = {self.site_name}
        self.jump_target: Optional[Tuple[str, float, float]] = None

        # Rolling JPEG frame buffer (GIF export) + separate recording buffer.
        self.frames: List[bytes] = []
        self.frame_cap_hit = False
        self.recording = False
        self.record_frames: List[bytes] = []

        self.max_tiles = self.MAX_TILES
        self.waiting = False            # True while holding at a tile edge
        self.preload_keys: List[Tuple[int, int]] = []
        self._mosaic_cache = None  # (token, canvas, bounds, b64)

        self.ensure(self.key_for(self.lat, self.lon), urgent=True)
        self._prefetch_ahead(self.key_for(self.lat, self.lon), force=True)

    # ── Tile grid ────────────────────────────────────────────────────────────

    def key_for(self, lat: float, lon: float) -> Tuple[int, int]:
        return (math.floor(lon / self.tile_size),
                math.floor(lat / self.tile_size))

    def bbox_for(self, key: Tuple[int, int]) -> List[float]:
        tx, ty = key
        return [tx * self.tile_size, ty * self.tile_size,
                (tx + 1) * self.tile_size, (ty + 1) * self.tile_size]

    # ── Fetching ─────────────────────────────────────────────────────────────

    def _fetch(self, key: Tuple[int, int]) -> Dict:
        try:
            if self.source == 'instant':
                return self._fetch_instant(key)
            return self._fetch_live(key)
        except Exception as e:
            return {'empty': True, 'error': f'{type(e).__name__}: {e}'}

    def _fetch_live(self, key: Tuple[int, int]) -> Dict:
        bbox = self.bbox_for(key)
        result = self.analyzer.drift_tile(
            bbox, self.start, self.end, self.cloud,
            resolution=self.resolution,
            gap_fill=self.gap_fill,
            stretch=self.stretch,
        )
        if result is None:
            return {'empty': True,
                    'error': 'No scenes match the date/cloud filter here'}
        if self.stretch is None:
            self.stretch = result['stretch']  # lock brightness to first tile
        rgba, bounds = result['rgba'], result['bounds']
        coverage = float((rgba[..., 3] > 0).mean())
        if coverage < EMPTY_ALPHA_FRAC:
            return {'empty': True,
                    'error': 'Mostly nodata / ocean / clouds here'}
        return {'empty': False, 'rgba': rgba, 'bounds': bounds}

    # ── Instant source (EOX Sentinel-2 cloudless mosaic) ─────────────────────

    @staticmethod
    def _merc_frac(lat: float, lon: float, z: int) -> Tuple[float, float]:
        """(x, y) fractional Web-Mercator tile coords at zoom z."""
        n = 2.0 ** z
        x = (lon + 180.0) / 360.0 * n
        lat_r = math.radians(max(-85.05, min(85.05, lat)))
        y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
        return x, y

    def _fetch_instant(self, key: Tuple[int, int]) -> Dict:
        """Stitch pre-rendered EOX s2cloudless XYZ tiles covering this drift
        tile's bbox. Whole-tile latency is ~1-2 s instead of 10-30 s."""
        import requests

        w, s, e, n = self.bbox_for(key)
        # Zoom whose ground resolution best matches self.resolution
        z = int(round(math.log2(360.0 / (256.0 * self.resolution))))
        z = max(3, min(EOX_MAX_ZOOM, z))

        x0f, y0f = self._merc_frac(n, w, z)   # top-left
        x1f, y1f = self._merc_frac(s, e, z)   # bottom-right
        xt0, yt0 = int(x0f), int(y0f)
        xt1, yt1 = int(x1f), int(y1f)

        n_tiles = 2 ** z
        stitched = Image.new(
            'RGB', ((xt1 - xt0 + 1) * 256, (yt1 - yt0 + 1) * 256), BG_RGB
        )
        session = requests.Session()
        # Stick with whichever layer name worked first for this engine.
        layers = ([self._eox_layer] if getattr(self, '_eox_layer', None)
                  else list(EOX_LAYERS))
        got_any = False
        for xt in range(xt0, xt1 + 1):
            for yt in range(yt0, yt1 + 1):
                if not (0 <= yt < n_tiles):
                    continue
                for layer in layers:
                    url = EOX_TILE_URL.format(
                        layer=layer, z=z, y=yt, x=xt % n_tiles
                    )
                    try:
                        r = session.get(url, timeout=10)
                        r.raise_for_status()
                        tile_img = Image.open(io.BytesIO(r.content)).convert('RGB')
                        stitched.paste(tile_img,
                                       ((xt - xt0) * 256, (yt - yt0) * 256))
                        got_any = True
                        self._eox_layer = layer
                        layers = [layer]
                        break
                    except Exception:
                        continue  # try next layer name / leave background
        if not got_any:
            return {'empty': True,
                    'error': 'Basemap tiles unreachable — check your network '
                             '(tiles.maps.eox.at)'}

        # Crop the stitched mercator image to the exact bbox. Over a <1° tile
        # the mercator-vs-linear latitude difference is sub-pixel, so a
        # linear crop between the projected corners is visually exact.
        px0 = (x0f - xt0) * 256.0
        py0 = (y0f - yt0) * 256.0
        px1 = (x1f - xt0) * 256.0
        py1 = (y1f - yt0) * 256.0
        cropped = stitched.crop((int(px0), int(py0),
                                 max(int(px0) + 1, int(px1)),
                                 max(int(py0) + 1, int(py1))))
        out_w = max(2, int(round((e - w) / self.resolution)))
        out_h = max(2, int(round((n - s) / self.resolution)))
        cropped = cropped.resize((out_w, out_h), Image.LANCZOS)

        rgba = np.dstack([
            np.asarray(cropped, dtype=np.uint8),
            np.full((out_h, out_w), 255, dtype=np.uint8),
        ])
        return {'empty': False, 'rgba': rgba, 'bounds': [s, w, n, e]}

    def ensure(self, key: Tuple[int, int], urgent: bool = False) -> None:
        """Request a tile fetch if not cached and not already in flight.
        urgent=True (current-position / retry tiles) uses a dedicated pool so
        it's never queued behind background neighbor prefetches."""
        with self.lock:
            if key in self.tiles or key in self.futures:
                return
            pool = self.urgent_executor if urgent else self.executor
            self.futures[key] = pool.submit(self._fetch, key)
            self.fetch_started[key] = time.time()

    def retry(self, key: Tuple[int, int]) -> None:
        """Clear a failed/timed-out tile and re-fetch it from scratch."""
        with self.lock:
            self.tiles.pop(key, None)
            self.futures.pop(key, None)
            self.fetch_started.pop(key, None)
        self._mosaic_cache = None
        self.ensure(key, urgent=True)

    def poll(self) -> bool:
        """Collect finished fetches into the LRU cache, and give up on any
        fetch that's been stalled past FETCH_TIMEOUT_S (marks it failed so
        the UI can show an error + retry instead of waiting forever — the
        orphaned thread, if truly hung on a dead socket, is abandoned).
        Returns True if new tile state arrived."""
        arrived = False
        now = time.time()
        with self.lock:
            done = [k for k, f in self.futures.items() if f.done()]
            for k in done:
                f = self.futures.pop(k)
                self.fetch_started.pop(k, None)
                try:
                    self.tiles[k] = f.result()
                except Exception as e:
                    self.tiles[k] = {'empty': True, 'error': f'{type(e).__name__}: {e}'}
                self.tiles.move_to_end(k)
                arrived = True
            stalled = [
                k for k, f in self.futures.items()
                if not f.done() and now - self.fetch_started.get(k, now) > FETCH_TIMEOUT_S
            ]
            for k in stalled:
                self.futures.pop(k)  # stop waiting; thread may linger, harmless
                self.fetch_started.pop(k, None)
                self.tiles[k] = {
                    'empty': True,
                    'error': f'Timed out after {FETCH_TIMEOUT_S}s — check your '
                             'network connection',
                }
                self.tiles.move_to_end(k)
                arrived = True
            # Keep current + jump-target tiles hot so LRU never evicts them.
            for hot in (self.key_for(self.lat, self.lon),
                        self.key_for(*self.jump_target[1:])
                        if self.jump_target else None):
                if hot in self.tiles:
                    self.tiles.move_to_end(hot)
            while len(self.tiles) > self.max_tiles:
                self.tiles.popitem(last=False)
        if arrived:
            self._mosaic_cache = None
        return arrived

    def fetching(self) -> int:
        with self.lock:
            return len(self.futures)

    def current_key(self) -> Tuple[int, int]:
        return self.key_for(self.lat, self.lon)

    def current_tile_empty(self) -> bool:
        tile = self.tiles.get(self.key_for(self.lat, self.lon))
        return tile is not None and tile.get('empty', False)

    def current_tile_error(self) -> Optional[str]:
        tile = self.tiles.get(self.key_for(self.lat, self.lon))
        return tile.get('error') if tile and tile.get('empty') else None

    def current_wait_seconds(self) -> Optional[float]:
        started = self.fetch_started.get(self.key_for(self.lat, self.lon))
        return (time.time() - started) if started else None

    # ── Motion ───────────────────────────────────────────────────────────────

    def _advance(self, dt: float, speed: float, random_walk: bool) -> None:
        if random_walk:
            # Gentle jitter: sigma ~12 deg of heading change per second.
            self.heading = (self.heading + random.gauss(0.0, 12.0) * dt) % 360.0
        rad = math.radians(self.heading)
        self.lat += speed * dt * math.cos(rad)
        self.lon += speed * dt * math.sin(rad)
        if self.lat > 80.0:
            self.lat, self.heading = 80.0, (180.0 - self.heading) % 360.0
        elif self.lat < -80.0:
            self.lat, self.heading = -80.0, (180.0 - self.heading) % 360.0
        if self.lon > 180.0:
            self.lon -= 360.0
        elif self.lon < -180.0:
            self.lon += 360.0

    def bounce(self) -> None:
        """Reverse out of an empty tile (used when random-jump is disabled)."""
        self.heading = (self.heading + 180.0) % 360.0
        rad = math.radians(self.heading)
        step = self.tile_size * 0.10
        self.lat += step * math.cos(rad)
        self.lon += step * math.sin(rad)

    def _blocked_ahead(self, sub: float, speed: float) -> bool:
        """True if continuing would carry the viewport edge into a tile that
        is still being fetched. (Empty/failed tiles don't block — the
        jump/bounce logic deals with those.)"""
        rad = math.radians(self.heading)
        look = self.tile_size * VIEW_SPAN_FRAC / 2.0 + speed * sub
        la = self.lat + look * math.cos(rad)
        lo = self.lon + look * math.sin(rad)
        lk = self.key_for(la, lo)
        if lk in self.tiles:
            return False
        self.ensure(lk, urgent=True)
        return True

    def tick(self, dt: float, speed: float, random_walk: bool,
             capture_fps: int, capture: bool,
             viewport_px: Tuple[int, int] = (512, 384)) -> None:
        """Advance the drift by dt seconds, capturing viewport frames along
        the way so exports stay smooth regardless of the rerun cadence.
        Holds position (waiting=True) instead of panning into a not-yet-
        loaded tile, so the view never slides off into black."""
        n_sub = max(1, int(round(dt * capture_fps))) if capture else 1
        sub = dt / n_sub
        prev_key = self.key_for(self.lat, self.lon)
        self.waiting = False
        for _ in range(n_sub):
            if self._blocked_ahead(sub, speed):
                self.waiting = True
                break
            self._advance(sub, speed, random_walk)
            if capture:
                jpg = self.viewport_jpeg(viewport_px)
                if jpg is not None:
                    self.frames.append(jpg)
                    if len(self.frames) > FRAME_CAP:
                        self.frames.pop(0)
                        self.frame_cap_hit = True
                    if self.recording:
                        self.record_frames.append(jpg)
        key = self.key_for(self.lat, self.lon)
        if key != prev_key:
            self.tiles_crossed += 1
        self.ensure(key, urgent=True)
        self._prefetch_ahead(key)

    # ── Preload (immersive corridors) ────────────────────────────────────────

    def preload(self) -> None:
        """Prefetch the full 3x3 neighborhood around the current tile so a
        long stretch of drifting needs no network at all."""
        self.max_tiles = max(self.max_tiles, 12)
        tx, ty = self.current_key()
        self.preload_keys = [
            (tx + dx, ty + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
        ]
        for k in self.preload_keys:
            self.ensure(k)

    def preload_progress(self) -> Optional[float]:
        """0..1 while a preload is pending, None when idle/done."""
        if not self.preload_keys:
            return None
        done = sum(1 for k in self.preload_keys if k in self.tiles)
        if done == len(self.preload_keys):
            self.preload_keys = []
            return None
        return done / len(self.preload_keys)

    def _prefetch_ahead(self, key: Tuple[int, int], force: bool = False) -> None:
        """Kick off fetches for the neighbor tile(s) we're heading toward."""
        tx, ty = key
        w, s = tx * self.tile_size, ty * self.tile_size
        fx = (self.lon - w) / self.tile_size
        fy = (self.lat - s) / self.tile_size
        rad = math.radians(self.heading)
        dx, dy = math.sin(rad), math.cos(rad)
        if dx > 0 and (force or fx > 1 - EDGE_FRAC):
            self.ensure((tx + 1, ty))
        if dx < 0 and (force or fx < EDGE_FRAC):
            self.ensure((tx - 1, ty))
        if dy > 0 and (force or fy > 1 - EDGE_FRAC):
            self.ensure((tx, ty + 1))
        if dy < 0 and (force or fy < EDGE_FRAC):
            self.ensure((tx, ty - 1))

    # ── Random jump ──────────────────────────────────────────────────────────

    def need_jump(self, corridor_tiles: int) -> bool:
        return self.tiles_crossed >= corridor_tiles or self.current_tile_empty()

    def start_jump(self) -> None:
        """Pick the next site and prefetch its tile; the cut happens only once
        that tile is ready (no blank frames)."""
        if self.jump_target is not None:
            return
        pool = [s for s in DRIFT_SITES if s[0] not in self.visited]
        if not pool:
            self.visited = {self.site_name}
            pool = [s for s in DRIFT_SITES if s[0] not in self.visited]
        site = random.choice(pool)
        self.jump_target = site
        self.ensure(self.key_for(site[1], site[2]))

    def try_complete_jump(self) -> bool:
        """Teleport once the target tile has landed. Returns True on cut."""
        if self.jump_target is None:
            return False
        name, lat, lon = self.jump_target
        key = self.key_for(lat, lon)
        tile = self.tiles.get(key)
        if tile is None:
            self.ensure(key)
            return False
        if tile.get('empty'):
            # Bad luck (clouds/nodata) — mark visited, try another site.
            self.visited.add(name)
            self.jump_target = None
            self.start_jump()
            return False
        self.site_name, self.lat, self.lon = name, lat, lon
        self.visited.add(name)
        self.heading = random.uniform(0.0, 360.0)
        self.tiles_crossed = 0
        self.jump_target = None
        self._mosaic_cache = None
        self._prefetch_ahead(key, force=True)
        return True

    # ── Mosaic / viewport ────────────────────────────────────────────────────

    def _mosaic_data(self):
        """Stitch cached tiles adjacent to the current tile into one RGBA
        canvas. Cached until the tile set changes."""
        key = self.key_for(self.lat, self.lon)
        tx, ty = key
        with self.lock:
            sel = [(k, t) for k, t in self.tiles.items()
                   if abs(k[0] - tx) <= 1 and abs(k[1] - ty) <= 1
                   and not t.get('empty')]
        if not sel:
            return None
        token = tuple(sorted(k for k, _ in sel))
        if self._mosaic_cache is not None and self._mosaic_cache[0] == token:
            return self._mosaic_cache

        res = self.resolution
        S = min(t['bounds'][0] for _, t in sel)
        W = min(t['bounds'][1] for _, t in sel)
        N = max(t['bounds'][2] for _, t in sel)
        E = max(t['bounds'][3] for _, t in sel)
        Wpx = int(round((E - W) / res)) + 1
        Hpx = int(round((N - S) / res)) + 1
        canvas = np.zeros((Hpx, Wpx, 4), dtype=np.uint8)
        for _, t in sel:
            _, tw, tn, _ = t['bounds']
            arr = t['rgba']
            row0 = max(0, int(round((N - tn) / res)))
            col0 = max(0, int(round((tw - W) / res)))
            r1 = min(row0 + arr.shape[0], Hpx)
            c1 = min(col0 + arr.shape[1], Wpx)
            if r1 > row0 and c1 > col0:
                canvas[row0:r1, col0:c1] = arr[:r1 - row0, :c1 - col0]

        buf = io.BytesIO()
        Image.fromarray(canvas, 'RGBA').save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        self._mosaic_cache = (token, canvas, (S, W, N, E), b64)
        return self._mosaic_cache

    def mosaic_b64(self) -> Optional[Tuple[str, Tuple[float, float, float, float]]]:
        data = self._mosaic_data()
        if data is None:
            return None
        _, _, bounds, b64 = data
        return b64, bounds

    def viewport_jpeg(self, size: Tuple[int, int] = (512, 384)) -> Optional[bytes]:
        """Crop the current viewport out of the mosaic -> JPEG bytes."""
        data = self._mosaic_data()
        if data is None:
            return None
        _, canvas, (S, W, N, E), _ = data
        res = self.resolution
        span_x = self.tile_size * VIEW_SPAN_FRAC
        span_y = span_x * size[1] / size[0]
        cx = (self.lon - W) / res
        cy = (N - self.lat) / res
        hx, hy = span_x / res / 2.0, span_y / res / 2.0
        # Clamp the crop inside the mosaic (mirrors the client-side clamp)
        if canvas.shape[1] > 2 * hx:
            cx = min(max(cx, hx), canvas.shape[1] - hx)
        else:
            cx = canvas.shape[1] / 2.0
        if canvas.shape[0] > 2 * hy:
            cy = min(max(cy, hy), canvas.shape[0] - hy)
        else:
            cy = canvas.shape[0] / 2.0
        x0, x1 = int(cx - hx), int(cx + hx)
        y0, y1 = int(cy - hy), int(cy + hy)
        if x1 <= x0 or y1 <= y0:
            return None
        out = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
        sx0, sy0 = max(x0, 0), max(y0, 0)
        sx1 = min(x1, canvas.shape[1])
        sy1 = min(y1, canvas.shape[0])
        if sx1 > sx0 and sy1 > sy0:
            out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = canvas[sy0:sy1, sx0:sx1]
        img = Image.fromarray(out, 'RGBA')
        bg = Image.new('RGB', img.size, BG_RGB)
        bg.paste(img, mask=img.split()[3])
        bg = bg.resize(size, Image.LANCZOS)
        buf = io.BytesIO()
        bg.save(buf, format='JPEG', quality=85)
        return buf.getvalue()

    def shutdown(self) -> None:
        for pool in (self.executor, self.urgent_executor):
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:  # Python < 3.9
                pool.shutdown(wait=False)


# ── Export helpers ────────────────────────────────────────────────────────────

def build_gif_bytes(jpeg_frames: List[bytes], fps: int) -> bytes:
    """Assemble buffered JPEG frames into an animated GIF (PIL), mirroring the
    Timelapse tab's save call."""
    imgs = [Image.open(io.BytesIO(j)).convert('RGB') for j in jpeg_frames]
    buf = io.BytesIO()
    imgs[0].save(
        buf,
        format='GIF',
        save_all=True,
        append_images=imgs[1:],
        duration=int(1000 / max(1, fps)),
        loop=0,
    )
    return buf.getvalue()


def build_mp4_bytes(jpeg_frames: List[bytes], fps: int) -> bytes:
    """Encode buffered JPEG frames into an H.264 MP4 via imageio-ffmpeg."""
    import imageio.v2 as imageio
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        path = tmp.name
    writer = imageio.get_writer(
        path, fps=max(1, fps), codec='libx264',
        quality=7, macro_block_size=None,
    )
    try:
        for j in jpeg_frames:
            writer.append_data(np.asarray(Image.open(io.BytesIO(j)).convert('RGB')))
    finally:
        writer.close()
    with open(path, 'rb') as f:
        return f.read()


# ── Client-side pan component ────────────────────────────────────────────────

def drift_component_html(mosaic_b64: str,
                         bounds: Tuple[float, float, float, float],
                         lat: float, lon: float,
                         speed: float, heading: float,
                         tile_size: float, running: bool,
                         site_name: str,
                         width: int = 680, height: int = 460) -> str:
    """Canvas + requestAnimationFrame pan across the mosaic. The client
    extrapolates the same motion model Python uses, so panning stays smooth
    between Streamlit reruns; each rerun rebases it on the authoritative
    position (and a fresh mosaic when a prefetched tile has landed)."""
    S, W, N, E = bounds
    span_x = tile_size * VIEW_SPAN_FRAC
    speed_js = speed if running else 0.0
    return f"""
<div style="position:relative;background:#0d1117;border-radius:8px;overflow:hidden;
            font-family:sans-serif">
  <canvas id="driftcv" width="{width}" height="{height}"
          style="width:100%;display:block"></canvas>
  <div style="position:absolute;left:10px;bottom:8px;color:#94a3b8;
              font-size:12px;background:rgba(13,17,23,.65);padding:2px 8px;
              border-radius:4px">
    <span style="color:#38bdf8">{site_name}</span>
    &nbsp;·&nbsp;<span id="driftpos"></span>
  </div>
</div>
<script>
(function() {{
  const cv = document.getElementById('driftcv');
  const ctx = cv.getContext('2d');
  const img = new Image();
  const Wd = {W}, Nd = {N}, Ed = {E}, Sd = {S};
  const lat0 = {lat}, lon0 = {lon};
  const speed = {speed_js};
  const rad = {heading} * Math.PI / 180.0;
  const vLat = speed * Math.cos(rad), vLon = speed * Math.sin(rad);
  const spanX = {span_x};
  const spanY = spanX * cv.height / cv.width;
  const posEl = document.getElementById('driftpos');
  let t0 = null;

  function draw(ts) {{
    if (t0 === null) t0 = ts;
    const t = (ts - t0) / 1000.0;
    const lat = lat0 + vLat * t, lon = lon0 + vLon * t;
    const ppdX = img.width / (Ed - Wd);
    const ppdY = img.height / (Nd - Sd);
    let cx = (lon - Wd) * ppdX, cy = (Nd - lat) * ppdY;
    const sw = spanX * ppdX, sh = spanY * ppdY;
    // Clamp: never pan the viewport past the mosaic edge (no black bands).
    // If the mosaic is smaller than the viewport in an axis, center on it.
    cx = (img.width  > sw) ? Math.min(Math.max(cx, sw / 2), img.width  - sw / 2)
                           : img.width  / 2;
    cy = (img.height > sh) ? Math.min(Math.max(cy, sh / 2), img.height - sh / 2)
                           : img.height / 2;
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(img, cx - sw / 2, cy - sh / 2, sw, sh,
                  0, 0, cv.width, cv.height);
    posEl.textContent = lat.toFixed(3) + '\\u00b0, ' + lon.toFixed(3) + '\\u00b0';
    if (speed > 0) requestAnimationFrame(draw);
  }}
  img.onload = function() {{ requestAnimationFrame(draw); }};
  img.src = 'data:image/png;base64,{mosaic_b64}';
}})();
</script>
"""
