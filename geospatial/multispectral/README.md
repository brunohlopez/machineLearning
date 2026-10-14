# Sentinel Flyover

A continuous, full-screen flyover of **live Sentinel-2 L2A imagery**, streamed straight from the open archive into the browser. No server, no API keys, no build step: open `index.html` and it starts flying.

Each leg of the flight picks a recent, low-cloud scene, reads the raw band GeoTIFFs on the fly, composites them client-side, and pans across the ground at a steady speed. When a strip runs out it either continues into the neighbouring tile or jumps to somewhere new on Earth, cross-fading between scenes so the motion never stops.

## Features

- **Live L2A data** — scenes come from the [Element84 Earth Search](https://earth-search.aws.element84.com/v1) STAC catalog; pixels are read directly from the cloud-optimized GeoTIFFs on AWS with [geotiff.js](https://geotiffjs.github.io/).
- **Band composites** — true color, color infrared, shortwave IR, agriculture, geology, urban and land/water.
- **Spectral indices** — NDVI, EVI, SAVI, NDRE, NDWI, NDCI, NDMI, NBR, NDBI, BSI and NDSI, each with its own color ramp and formula shown on screen.
- **L2A products** — the Sen2Cor scene classification, aerosol optical thickness (haze) and water vapour layers.
- **~90 curated places** — volcanoes, deltas, glaciers, mines, solar parks, cities — each opening in the view that suits it best, mixed with random land.
- **Pick anywhere** — search for a place (Photon geocoder) or click on a street, satellite or terrain map to fly there, or type `lat, lon`.
- **Flight-plan locator** — a minimap shows where you are and the strip being flown.
- **Clip recording** — record MP4 (WebCodecs) or GIF clips, or run a batch of clips across many places. Each clip gets a `.txt` caption with the Copernicus credit, and a batch also writes a CSV index.
- **Adaptive loading** — the next scene is searched and pre-fetched well before the current one ends; the map is rendered past the screen edge so tiles arrive before they are seen.

## Running it

It is a static page. Serve the folder with any web server:

```bash
cd geospatial/multispectral
python -m http.server 8000
# open http://localhost:8000
```

Or publish it with **GitHub Pages** and open `.../geospatial/multispectral/`.

Start at a specific spot by adding coordinates to the URL hash, e.g. `index.html#36.1,-112.1`.

## Controls

| Key | Action |
|---|---|
| `Space` | Pause / resume |
| `←` `→` | Ground speed |
| `↑` `↓` | Detail (metres per screen pixel; 10 m is the sensor's native resolution) |
| `1`–`7` | Band composites |
| `P` | Pick a place on a map |
| `N` | Next scene |
| `R` | Record panel |
| `H` | Hide overlays |
| `F` | Full screen |

Settings (speed, detail, brightness, view) are remembered in the browser between visits.

## How it works

```
multispectral/
├── index.html   # page structure: stage, HUD, record panel, place picker
├── styles.css   # layout and theme
└── app.js       # everything else
```

`app.js` is organized in sections:

1. **Configuration** — STAC endpoint, speeds, resolutions, band table, view and index definitions, curated places.
2. **Reading the rasters** — every band is its own COG with internal overviews. For each map tile the matching window is read from each band at the best overview and stacked; decoding runs in a small worker pool.
3. **Finding scenes** — STAC searches over the last ~18 months, relaxing the cloud-cover threshold in steps (12 → 35 → 75%) until a usable scene is found.
4. **The flight plan** — chooses a heading and a strip across the scene footprint sized to the current speed and detail.
5. **The two projectors** — two OpenLayers maps, one showing the current leg while the other loads the next, cross-faded on the switch. Composites and indices are computed on the GPU with OpenLayers WebGL tile-layer style expressions, with an automatic brightness stretch.
6. **Recording** — frames are captured from the stage canvas and encoded to H.264 MP4 (`mp4-muxer`) or GIF (`gifenc`); a folder can be chosen once with the File System Access API so batch clips save together.
7. **Picking a place** — an OpenLayers picker with EOX street (OpenStreetMap), Sentinel-2 cloudless 2025 and terrain basemaps, plus Photon search.

### Browser support

Best in a current Chromium browser (Chrome, Edge). MP4 recording needs WebCodecs and choosing a save folder needs the File System Access API; other browsers can still fly and record GIFs, with files downloading one at a time.

## Data and credits

- Contains modified **Copernicus Sentinel** data, processed by ESA. Recorded clips carry this credit in their captions.
- Scene search: Element84 Earth Search; imagery hosted on the AWS Open Data registry.
- Minimap and picker basemaps: EOX Sentinel-2 cloudless, street and terrain maps (© EOX IT Services GmbH, [s2maps.eu](https://s2maps.eu)); OpenStreetMap contributors.
- Libraries: [OpenLayers](https://openlayers.org/), [geotiff.js](https://geotiffjs.github.io/), [mp4-muxer](https://github.com/Vanilagy/mp4-muxer), [gifenc](https://github.com/mattdesl/gifenc), [world-atlas](https://github.com/topojson/world-atlas).
- Geocoding: [Photon](https://photon.komoot.io) by Komoot, OpenStreetMap data.

The previous Python/Streamlit version of this project (spectral analysis, wildfire dNBR and drift mode) is in this folder's git history.
