(() => {
'use strict';

/* ================= configuration ================= */
const CFG = {
  stac: 'https://earth-search.aws.element84.com/v1/search',
  collection: 'sentinel-2-l2a',
  lookbackDays: 540,
  cloudSteps: [12, 35, 75],
  miniTiles: 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg',
  countries: 'https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json',
  basePx: 70,                          // screen pixels per second at 1x speed
  speeds: [0.25, 0.5, 1, 2, 4],
  resolutions: [40, 20, 10, 5],        // metres per screen pixel; 10 is the sensor's native detail
  overlap: 0.08,                       // how much neighbouring strips share
  marginX: 192, marginY: 384,          // must match --mx / --my in the stylesheet
  fadeMs: 1600,
  introMs: 4200, introZoom: 5,         // each new scene opens this many times further out, then pushes in
  preloadSeconds: 40,                  // start loading what comes next this long before the current leg ends
  muxerLib: 'https://cdn.jsdelivr.net/npm/mp4-muxer@5.2.2/build/mp4-muxer.mjs',
  gifLib: 'https://cdn.jsdelivr.net/npm/gifenc@1.0.3/dist/gifenc.esm.js',
  photon: 'https://photon.komoot.io',
};

/* The twelve MSI bands delivered in L2A (B10, the cirrus band, is dropped by Sen2Cor). */
const BANDS = [
  { id: 'B01', key: 'coastal',  nm: 443,  w: 20,  res: 60, name: 'Coastal aerosol', c: '#7a5cff' },
  { id: 'B02', key: 'blue',     nm: 492,  w: 65,  res: 10, name: 'Blue',            c: '#3f7dff' },
  { id: 'B03', key: 'green',    nm: 560,  w: 35,  res: 10, name: 'Green',           c: '#3fcf74' },
  { id: 'B04', key: 'red',      nm: 665,  w: 30,  res: 10, name: 'Red',             c: '#ff5148' },
  { id: 'B05', key: 'rededge1', nm: 704,  w: 15,  res: 20, name: 'Red edge 1',      c: '#e23f58' },
  { id: 'B06', key: 'rededge2', nm: 740,  w: 15,  res: 20, name: 'Red edge 2',      c: '#c43a69' },
  { id: 'B07', key: 'rededge3', nm: 783,  w: 20,  res: 20, name: 'Red edge 3',      c: '#a53a78' },
  { id: 'B08', key: 'nir',      nm: 833,  w: 115, res: 10, name: 'Near infrared',   c: '#8a4288' },
  { id: 'B8A', key: 'nir08',    nm: 865,  w: 20,  res: 20, name: 'Narrow NIR',      c: '#774993' },
  { id: 'B09', key: 'nir09',    nm: 945,  w: 20,  res: 60, name: 'Water vapour',    c: '#66519b' },
  { id: 'B11', key: 'swir16',   nm: 1614, w: 90,  res: 20, name: 'Shortwave IR 1',  c: '#9a9183' },
  { id: 'B12', key: 'swir22',   nm: 2202, w: 180, res: 20, name: 'Shortwave IR 2',  c: '#b8ad9a' },
];
const BAND = Object.fromEntries(BANDS.map(b => [b.id, b]));

/* Normalized difference of two bands: the shape most indices take. */
function ND(a, b) {
  return {
    bands: [a, b],
    value: R => ['/', ['-', R(a), R(b)], ['+', R(a), R(b)]],
    calc: r => (r[a] + r[b] ? (r[a] - r[b]) / (r[a] + r[b]) : 0),
    formula: '(' + a + ' \u2212 ' + b + ') / (' + a + ' + ' + b + ')',
    roles: { [a]: '+', [b]: '\u2212' },
  };
}
/* Other L2A layers that are not reflectance bands. Values are DN times scale. */
const EXTRA = {
  AOT: { key: 'aot', res: 20, scale: 0.001 },
  WVP: { key: 'wvp', res: 20, scale: 0.001 },
};
const resOf = id => (BAND[id] || EXTRA[id] || { res: 20 }).res;
const assetFor = (item, id) => item.assets[id === 'SCL' ? 'scl' : (BAND[id] || EXTRA[id]).key];
const MODES = [
  { id: 'true',  kind: 'rgb', name: 'True color',     bands: ['B04', 'B03', 'B02'], note: 'Red, green and blue as your eye would see them from orbit.' },
  { id: 'cir',   kind: 'rgb', name: 'Color infrared', bands: ['B08', 'B04', 'B03'], note: 'Healthy plants reflect near infrared strongly, so vegetation shows red. Water goes almost black.' },
  { id: 'swir',  kind: 'rgb', name: 'Shortwave IR',   bands: ['B12', 'B8A', 'B04'], note: 'Sees through haze and thin smoke. Burn scars and hot lava glow, snow and ice turn blue.' },
  { id: 'agri',  kind: 'rgb', name: 'Agriculture',    bands: ['B11', 'B08', 'B02'], note: 'Growing crops in bright green, bare or harvested fields in magenta and brown.' },
  { id: 'geo',   kind: 'rgb', name: 'Geology',        bands: ['B12', 'B11', 'B02'], note: 'Rock and soil types separate by how they reflect shortwave infrared.' },
  { id: 'urban', kind: 'rgb', name: 'Urban',          bands: ['B12', 'B11', 'B04'], note: 'Built surfaces in white, grey and purple against green vegetation.' },
  { id: 'lw',    kind: 'rgb', name: 'Land / water',   bands: ['B8A', 'B11', 'B04'], note: 'Water absorbs infrared and drops to deep blue and black, land stays orange and green.' },
  { id: 'ndvi', kind: 'index', group: 'idx', name: 'NDVI', ...ND('B08', 'B04'), note: 'Vegetation greenness. Dense healthy canopy sits above 0.6, bare ground near 0.1, water below 0.',
    ramp: [[-0.3, '#1b3a5c'], [0, '#c9b891'], [0.15, '#e8dba0'], [0.3, '#b7d168'], [0.5, '#5dab3a'], [0.7, '#237a2c'], [0.9, '#0b4a1e']] },
  { id: 'evi', kind: 'index', group: 'idx', name: 'EVI', bands: ['B08', 'B04', 'B02'],
    value: R => ['*', 2.5, ['/', ['-', R('B08'), R('B04')], ['+', ['-', ['+', R('B08'), ['*', 6, R('B04')]], ['*', 7.5, R('B02')]], 1]]],
    calc: r => 2.5 * (r.B08 - r.B04) / (r.B08 + 6 * r.B04 - 7.5 * r.B02 + 1),
    formula: '2.5 (B08 \u2212 B04) / (B08 + 6 B04 \u2212 7.5 B02 + 1)',
    note: 'Enhanced vegetation index. Like NDVI, but it keeps separating dense forest and crops where NDVI flattens out, and corrects for haze using the blue band.',
    ramp: [[-0.2, '#1b3a5c'], [0, '#c9b891'], [0.15, '#e8dba0'], [0.3, '#b7d168'], [0.45, '#5dab3a'], [0.6, '#237a2c'], [0.8, '#0b4a1e']] },
  { id: 'savi', kind: 'index', group: 'idx', name: 'SAVI', bands: ['B08', 'B04'],
    value: R => ['*', 1.5, ['/', ['-', R('B08'), R('B04')], ['+', ['+', R('B08'), R('B04')], 0.5]]],
    calc: r => 1.5 * (r.B08 - r.B04) / (r.B08 + r.B04 + 0.5),
    formula: '1.5 (B08 \u2212 B04) / (B08 + B04 + 0.5)',
    note: 'Soil-adjusted vegetation index. Better than NDVI where plants are sparse and bright soil shows between them: deserts, rangeland, young crops.',
    ramp: [[-0.2, '#1b3a5c'], [0, '#c9b891'], [0.1, '#e8dba0'], [0.2, '#b7d168'], [0.35, '#5dab3a'], [0.5, '#237a2c'], [0.7, '#0b4a1e']] },
  { id: 'ndre', kind: 'index', group: 'idx', name: 'NDRE', ...ND('B8A', 'B05'), note: 'Red-edge index, using a band Landsat does not have. Tracks leaf chlorophyll and stays sensitive in dense canopy where NDVI saturates, so crop stress shows earlier.',
    ramp: [[-0.1, '#5b3a1e'], [0.1, '#d8c38a'], [0.25, '#b9d36a'], [0.4, '#4f9e3a'], [0.6, '#0f5a2a']] },
  { id: 'ndwi', kind: 'index', group: 'idx', name: 'NDWI', ...ND('B03', 'B08'), note: 'Open water. Values above 0 are water surfaces, land is negative.',
    ramp: [[-0.6, '#3b2a14'], [-0.3, '#a08a5c'], [0, '#efe9d6'], [0.2, '#7cc7e8'], [0.5, '#1f78c8'], [0.8, '#0a2f7a']] },
  { id: 'ndci', kind: 'index', group: 'idx', name: 'NDCI', ...ND('B05', 'B04'), note: 'Chlorophyll in water. Only meaningful over lakes, rivers and coasts, where algae blooms and green water light up yellow. Ignore it on land.',
    ramp: [[-0.2, '#0b2a5c'], [0, '#1f6fb5'], [0.1, '#3bb39a'], [0.2, '#9ad84a'], [0.4, '#e8e23a']] },
  { id: 'ndmi', kind: 'index', group: 'idx', name: 'NDMI', ...ND('B8A', 'B11'), note: 'Moisture held in vegetation and soil. Dry ground is negative, wet canopy is positive.',
    ramp: [[-0.5, '#8a3b12'], [-0.2, '#d99a4e'], [0, '#f3e9c6'], [0.2, '#8fd0c4'], [0.4, '#2a8fa8'], [0.7, '#0b3d73']] },
  { id: 'nbr',  kind: 'index', group: 'idx', name: 'NBR', ...ND('B8A', 'B12'), note: 'Normalized burn ratio. Recently burned ground falls well below 0, unburned forest sits high.',
    ramp: [[-0.5, '#6e0f5e'], [-0.25, '#d1495b'], [-0.1, '#f2a65a'], [0.1, '#f6e8b1'], [0.3, '#9bcf8a'], [0.6, '#2e8b57'], [0.8, '#0b4a2e']] },
  { id: 'ndbi', kind: 'index', group: 'idx', name: 'NDBI', ...ND('B11', 'B08'), note: 'Built-up and bare surfaces are positive, vegetation and water are negative.',
    ramp: [[-0.5, '#0e4d3a'], [-0.2, '#6aa58c'], [0, '#eee8d8'], [0.15, '#e39a5b'], [0.3, '#c2452d'], [0.5, '#6b0f1a']] },
  { id: 'bsi', kind: 'index', group: 'idx', name: 'BSI', bands: ['B11', 'B04', 'B08', 'B02'],
    value: R => { const p = ['+', R('B11'), R('B04')], q = ['+', R('B08'), R('B02')]; return ['/', ['-', p, q], ['+', p, q]]; },
    calc: r => { const p = r.B11 + r.B04, q = r.B08 + r.B02; return p + q ? (p - q) / (p + q) : 0; },
    formula: '((B11 + B04) \u2212 (B08 + B02)) / ((B11 + B04) + (B08 + B02))',
    note: 'Bare soil index. Exposed soil, cleared land and dry fields are positive, vegetation is negative. Good for spotting new clearing and construction.',
    ramp: [[-0.4, '#1f6b3a'], [-0.1, '#9cc28a'], [0, '#efe7d2'], [0.1, '#e0b07a'], [0.25, '#b8643a'], [0.4, '#6b2a14']] },
  { id: 'ndsi', kind: 'index', group: 'idx', name: 'NDSI', ...ND('B03', 'B11'), note: 'Snow and ice. Snow is above 0.4, which separates it from cloud that looks the same in true color. The same formula is used as MNDWI for water, so lakes read high too.',
    ramp: [[-0.4, '#3a2d1e'], [0, '#8d8d86'], [0.2, '#c3ced4'], [0.4, '#6fb6ff'], [0.7, '#cfe9ff'], [1, '#ffffff']] },
  { id: 'scl',  kind: 'scl', group: 'more', name: 'Scene classes', note: 'The Sen2Cor classification shipped with every L2A scene at 20 m. It is what the cloud percentage is computed from.' },
  { id: 'aot', kind: 'index', group: 'more', name: 'Haze (AOT)', bands: ['AOT'], value: R => R('AOT'), calc: r => r.AOT, product: true,
    formula: 'Aerosol optical thickness at 550 nm, from Sen2Cor at 20 m',
    note: 'How much haze, smoke and dust the atmospheric correction had to remove. Above about 0.4 the air was thick, so surface colours there are less reliable.',
    ramp: [[0, '#14305a'], [0.1, '#2f7fbf'], [0.2, '#8fc7c9'], [0.35, '#f0d58a'], [0.5, '#d98a3a'], [0.8, '#8a2f1a']] },
  { id: 'wvp', kind: 'index', group: 'more', name: 'Water vapour', bands: ['WVP'], value: R => R('WVP'), calc: r => r.WVP, product: true, unit: 'cm',
    formula: 'Water vapour in the air column, from Sen2Cor at 20 m',
    note: 'Moisture in the air above each pixel, as centimetres of precipitable water. Humid coasts and tropics read high, deserts and high mountains low.',
    ramp: [[0, '#7a5a2e'], [1, '#c9b27a'], [2, '#e9e4cf'], [3, '#8fc8d8'], [4, '#2f7fbf'], [6, '#143a7a']] },
];
const MODE = Object.fromEntries(MODES.map(m => [m.id, m]));

/* Sen2Cor scene classification values and the colors ESA uses for them. */
const SCL = [
  ['No data', 'rgba(0,0,0,0)'], ['Saturated or defective', '#ff0000'], ['Dark area', '#2f2f2f'], ['Cloud shadow', '#643200'],
  ['Vegetation', '#00a000'], ['Not vegetated', '#ffe65a'], ['Water', '#0000ff'], ['Unclassified', '#808080'],
  ['Cloud, medium probability', '#c0c0c0'], ['Cloud, high probability', '#ffffff'], ['Thin cirrus', '#64c8ff'], ['Snow or ice', '#ff96ff'],
];

/* name, region, lat, lon, opening resolution (m per pixel), suggested view, what to look for */
const PLACES = [
  ['Richat Structure', 'Mauritania', 21.124, -11.402, 45, 'geo', 'A 40 km eroded dome in the Sahara. Geology view pulls its concentric rock rings apart.'],
  ['Okavango Delta', 'Botswana', -19.35, 22.9, 40, 'cir', 'A river that ends in the Kalahari sand instead of the sea. Color infrared shows how far the flood reaches.'],
  ['Sundarbans', 'Bangladesh and India', 21.95, 89.2, 30, 'cir', 'The largest mangrove forest on Earth, cut into islands by tidal channels.'],
  ['Palm Jumeirah', 'Dubai, United Arab Emirates', 25.118, 55.134, 12, 'true', 'An artificial archipelago built from dredged sand, with the breakwater crescent around it.'],
  ['Venice Lagoon', 'Italy', 45.43, 12.33, 15, 'true', 'The city sits mid-lagoon. Look for dredged shipping channels and the barrier islands.'],
  ['Salar de Uyuni', 'Bolivia', -20.2, -67.6, 60, 'swir', 'The largest salt flat on Earth. In shortwave infrared, wet salt turns dark.'],
  ['Salar de Atacama', 'Chile', -23.52, -68.35, 20, 'true', 'Lithium brine evaporation ponds in turquoise and yellow, laid out on the salt crust.'],
  ['Grand Canyon', 'Arizona, United States', 36.1, -112.1, 25, 'geo', 'A mile of layered rock exposed by the Colorado River. Geology view separates the strata.'],
  ['Garden City pivots', 'Kansas, United States', 37.87, -100.95, 20, 'agri', 'Each circle is one field watered by a rotating center-pivot arm.'],
  ['Wadi As-Sirhan', 'Saudi Arabia', 30.09, 38.27, 30, 'cir', 'Desert farms running on fossil groundwater. Crops glow red in color infrared.'],
  ['Almería greenhouses', 'Spain', 36.76, -2.76, 20, 'true', 'Tens of thousands of hectares of plastic greenhouses, white from orbit.'],
  ['Mount Etna', 'Sicily, Italy', 37.751, 14.994, 20, 'swir', 'Europe’s most active volcano. Shortwave infrared picks out fresh lava flows and any hot vents.'],
  ['Kīlauea', 'Hawai‘i, United States', 19.41, -155.28, 20, 'swir', 'A shield volcano with a summit caldera. Young lava is dark and bare, older flows are greening.'],
  ['Cairo and the Nile Delta', 'Egypt', 30.05, 31.23, 30, 'ndvi', 'Irrigated green stops dead at the desert edge. NDVI makes the line exact.'],
  ['Rondônia', 'Brazil', -10.0, -63.0, 30, 'cir', 'Fishbone clearings spread from roads into the Amazon forest.'],
  ['Meeting of Waters', 'Manaus, Brazil', -3.14, -59.9, 20, 'true', 'The dark Rio Negro and the sandy Solimões run side by side before they mix.'],
  ['Betsiboka Estuary', 'Madagascar', -15.85, 46.33, 25, 'true', 'Red sediment from eroded hillsides pours into Bombetoka Bay.'],
  ['Hardy Reef', 'Great Barrier Reef, Australia', -19.76, 149.22, 20, 'true', 'Coral platforms and the channels between them, seen through clear shallow water.'],
  ['Exuma Cays', 'Bahamas', 24.2, -76.47, 25, 'true', 'Tidal currents sculpt white sand into ripples under turquoise water.'],
  ['Aral Sea', 'Kazakhstan and Uzbekistan', 45.0, 58.6, 60, 'ndwi', 'The remains of what was the fourth-largest lake. NDWI outlines the water that is left.'],
  ['Dead Sea salt works', 'Israel and Jordan', 31.15, 35.45, 30, 'true', 'The southern basin is divided into evaporation pans for potash.'],
  ['Sossusvlei', 'Namibia', -24.73, 15.3, 25, 'true', 'Red star dunes more than 300 m tall around a white clay pan.'],
  ['Liwa and the Empty Quarter', 'United Arab Emirates', 22.95, 53.9, 30, 'geo', 'Giant dunes separated by flat salt pans at the edge of the Rub’ al Khali.'],
  ['Lut Desert yardangs', 'Iran', 30.75, 58.0, 30, 'geo', 'Wind has carved the desert floor into long parallel ridges.'],
  ['Zagros fold belt', 'Iran', 27.9, 53.7, 40, 'geo', 'Rock layers folded into long whaleback ridges by the collision of two plates.'],
  ['Emi Koussi', 'Chad', 19.79, 18.55, 40, 'geo', 'A shield volcano in the Tibesti with nested calderas at its summit.'],
  ['Ngorongoro Crater', 'Tanzania', -3.17, 35.58, 25, 'cir', 'A collapsed volcano 19 km wide with a soda lake on its floor.'],
  ['Mount Fuji', 'Japan', 35.36, 138.73, 25, 'true', 'A near-symmetric cone ringed by forest, lakes and towns.'],
  ['Tokyo Bay', 'Japan', 35.55, 139.85, 30, 'urban', 'One of the densest urban areas anywhere, with reclaimed land along the shore.'],
  ['South Bay salt ponds', 'California, United States', 37.5, -122.07, 20, 'true', 'Salt ponds colored by the microbes that live at each salinity.'],
  ['Manhattan', 'New York, United States', 40.76, -73.98, 12, 'true', 'The street grid, Central Park and the piers along both rivers.'],
  ['Paris', 'France', 48.86, 2.34, 15, 'urban', 'Boulevards radiating from the Seine, with two large woods at the east and west edges.'],
  ['Brasília', 'Brazil', -15.79, -47.88, 20, 'true', 'A planned capital whose core is shaped like an aircraft, beside an artificial lake.'],
  ['Flevoland', 'Netherlands', 52.55, 5.6, 20, 'agri', 'Farmland on reclaimed seabed, divided into ruler-straight plots.'],
  ['Mekong Delta', 'Vietnam', 9.9, 105.9, 30, 'ndwi', 'Rice paddies, canals and aquaculture ponds on one of the flattest places on Earth.'],
  ['Selenga Delta', 'Lake Baikal, Russia', 52.27, 106.5, 30, 'cir', 'A river delta pushing into the deepest lake in the world.'],
  ['Bird’s Foot Delta', 'Louisiana, United States', 29.15, -89.25, 30, 'true', 'The Mississippi carries its sediment far out into the Gulf between thin levees.'],
  ['Lena Delta', 'Siberia, Russia', 72.8, 126.5, 60, 'cir', 'One of the largest Arctic deltas, a maze of channels across the tundra.'],
  ['Vatnajökull outlets', 'Iceland', 64.1, -16.4, 40, 'swir', 'Glacier tongues flow from the ice cap to the coast. Ice turns blue in shortwave infrared, cloud stays white.'],
  ['Perito Moreno Glacier', 'Argentina', -50.49, -73.1, 25, 'true', 'A glacier that ends in a lake and regularly dams one of its arms.'],
  ['Khumbu and Everest', 'Nepal', 27.98, 86.9, 25, 'swir', 'Debris-covered glaciers drain the highest peaks on Earth.'],
  ['Malaspina Glacier', 'Alaska, United States', 59.92, -140.5, 50, 'true', 'A glacier that spreads onto the coastal plain, folding its moraines like marble.'],
  ['Greenland ice margin', 'Kangerlussuaq, Greenland', 67.1, -50.0, 40, 'true', 'The edge of the ice sheet, with meltwater lakes and rivers in summer.'],
  ['Kati Thanda–Lake Eyre', 'South Australia', -28.6, 137.3, 60, 'true', 'A salt lake that fills only a few times a century.'],
  ['Uluru', 'Northern Territory, Australia', -25.345, 131.036, 15, 'geo', 'A single sandstone monolith in a plain of red sand and spinifex.'],
  ['Shark Bay', 'Western Australia', -25.95, 113.75, 50, 'true', 'Seagrass banks and shallow sills separate very salty lagoons.'],
  ['Mount Whaleback', 'Pilbara, Western Australia', -23.36, 119.67, 20, 'geo', 'One of the largest open-pit iron ore mines on Earth.'],
  ['Chuquicamata', 'Chile', -22.29, -68.9, 20, 'geo', 'A copper pit more than 4 km long with waste dumps the size of towns.'],
  ['Bingham Canyon Mine', 'Utah, United States', 40.523, -112.151, 15, 'true', 'A copper mine that has removed most of a mountain.'],
  ['Great Salt Lake causeway', 'Utah, United States', 41.22, -112.6, 40, 'true', 'A rail causeway splits the lake. The saltier north arm turns pink.'],
  ['Colorado River Delta', 'Mexico', 31.8, -114.8, 40, 'true', 'Tidal channels branch like trees where the river rarely reaches the sea now.'],
  ['Three Gorges Dam', 'Hubei, China', 30.823, 111.003, 20, 'true', 'The largest hydroelectric dam by capacity, with its ship locks beside it.'],
  ['Malé Atoll', 'Maldives', 4.25, 73.5, 30, 'true', 'Ring reefs and lagoons a metre or two above sea level.'],
  ['Bora Bora', 'French Polynesia', -16.5, -151.74, 15, 'true', 'An eroded volcano inside a lagoon and barrier reef.'],
  ['Sierra Negra', 'Galápagos, Ecuador', -0.83, -91.13, 30, 'swir', 'A broad shield volcano with one of the largest calderas in the islands.'],
  ['Dallol and the Danakil', 'Ethiopia', 14.24, 40.3, 25, 'geo', 'Salt flats and acid springs in one of the lowest, hottest places on land.'],
  ['Toshka Lakes', 'Egypt', 23.1, 30.9, 60, 'ndwi', 'Overflow from Lake Nasser pooled in desert basins. Their size changes year to year.'],
  ['Inner Niger Delta', 'Mali', 15.1, -4.2, 60, 'cir', 'A seasonal wetland in the Sahel that swells after the rains.'],
  ['Pantanal', 'Brazil', -17.6, -57.4, 40, 'cir', 'The largest tropical wetland, a mosaic of lakes, forest and flooded grassland.'],
  ['Iguazú Falls', 'Argentina and Brazil', -25.695, -54.437, 12, 'true', 'The river drops over a basalt edge almost 3 km wide.'],
  ['Mount Taranaki', 'New Zealand', -39.296, 174.064, 30, 'ndvi', 'A near-perfect circle. Protected forest inside the park boundary, dairy pasture outside.'],
  ['Klyuchevskaya', 'Kamchatka, Russia', 56.06, 160.64, 40, 'swir', 'The tallest active volcano in Eurasia, surrounded by a cluster of others.'],
  ['Lake Urmia', 'Iran', 37.6, 45.5, 80, 'true', 'A shrinking salt lake crossed by a causeway. Its color shifts with salinity.'],
  ['Manicouagan Crater', 'Quebec, Canada', 51.4, -68.7, 90, 'true', 'An impact crater more than 200 million years old, now a ring-shaped reservoir.'],
  ['Miami and the Everglades', 'Florida, United States', 25.77, -80.4, 30, 'cir', 'The city grid stops at a levee, with the Everglades on the other side.'],
  ['Lake Mead', 'Nevada, United States', 36.1, -114.6, 40, 'true', 'The reservoir behind Hoover Dam. A pale band on the shore marks how far it has dropped.'],
  ['Imperial Valley border', 'United States and Mexico', 32.66, -115.45, 30, 'ndvi', 'The border shows as a change in field size and pattern between Calexico and Mexicali.'],
  ['Rann of Kutch', 'Gujarat, India', 23.9, 70.0, 60, 'true', 'A seasonal salt marsh that floods in the monsoon and dries white.'],
  ['Sognefjord', 'Norway', 61.15, 6.6, 40, 'true', 'The longest fjord in Norway, with glaciers on the plateau above it.'],
  ['Camargue', 'France', 43.5, 4.55, 30, 'true', 'The Rhône delta: rice fields, lagoons and pink salt pans.'],
  ['San Joaquin Valley orchards', 'California, United States', 36.3, -119.7, 20, 'agri', 'A patchwork of orchards, vineyards and row crops on a one-mile grid.'],
  ['Port of Rotterdam', 'Netherlands', 51.95, 4.05, 20, 'urban', 'Europe’s largest port, extended into the North Sea on reclaimed land.'],
  ['Suez Canal', 'Egypt', 30.55, 32.31, 25, 'true', 'The canal and its newer parallel channel, with ships waiting in the lakes.'],
  ['Mount Bromo', 'Java, Indonesia', -7.94, 112.95, 20, 'true', 'Young cones inside a much older caldera filled with a sea of sand.'],
  ['Lake Titicaca', 'Peru and Bolivia', -15.8, -69.5, 60, 'true', 'The highest large lake in the world, at 3,800 m.'],
  ['Torres del Paine', 'Chile', -51.0, -73.1, 30, 'true', 'Granite towers, glaciers and lakes in different shades of meltwater blue.'],
  ['Cape Town', 'South Africa', -33.95, 18.45, 25, 'true', 'A city wrapped around Table Mountain, between two oceans.'],
  ['Etosha Pan', 'Namibia', -18.8, 16.3, 80, 'true', 'A white salt pan 120 km long that briefly holds water after heavy rain.'],
  ['Victoria Falls', 'Zambia and Zimbabwe', -17.925, 25.857, 12, 'true', 'The Zambezi drops into a narrow gorge and zigzags away through older ones.'],
  ['Kilimanjaro', 'Tanzania', -3.07, 37.35, 30, 'cir', 'Rings of farmland, forest, moorland and bare rock climb to a shrinking ice cap.'],
  ['Volga Delta', 'Russia', 45.8, 47.9, 50, 'cir', 'Hundreds of channels fan into the Caspian Sea.'],
  ['Tengger Desert solar park', 'Ningxia, China', 37.55, 105.05, 20, 'true', 'Dark blocks of solar panels laid out across pale desert.'],
  ['Bhadla Solar Park', 'Rajasthan, India', 27.54, 71.92, 20, 'true', 'One of the largest solar parks on Earth, in the Thar Desert.'],
  ['Carrizo Plain solar farms', 'California, United States', 35.38, -120.07, 20, 'true', 'Utility-scale solar arrays on a dry plain beside the San Andreas Fault.'],
  ['Ivanpah', 'California, United States', 35.557, -115.47, 12, 'true', 'Three fields of mirrors, each focused on a central tower.'],
  ['Las Vegas', 'Nevada, United States', 36.12, -115.17, 20, 'urban', 'A city with a hard edge against the desert on every side.'],
  ['Bosphorus', 'Istanbul, Türkiye', 41.05, 29.03, 20, 'true', 'The strait between two continents, bridged three times.'],
  ['Lake Natron', 'Tanzania', -2.42, 36.0, 40, 'true', 'A soda lake stained red by salt-loving microbes.'],
  ['Laguna Colorada', 'Bolivia', -22.2, -67.78, 20, 'true', 'A shallow red lake with white borax islands, high in the Andes.'],
  ['Mount St. Helens', 'Washington, United States', 46.2, -122.18, 25, 'cir', 'The 1980 blast zone still shows north of the crater as thinner vegetation.'],
  ['Athabasca oil sands', 'Alberta, Canada', 57.0, -111.5, 40, 'geo', 'Open-pit mines and tailings ponds cut into boreal forest.'],
  ['Tokachi Plain', 'Hokkaido, Japan', 42.9, 143.2, 20, 'agri', 'Neat field blocks divided by windbreak forest strips.'],
];

/* ================= small helpers ================= */
const $ = (s, r = document) => r.querySelector(s);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'style') e.style.cssText = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}
const hexRgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const fmtLat = v => Math.abs(v).toFixed(4) + '°' + (v >= 0 ? 'N' : 'S');
const fmtLon = v => Math.abs(v).toFixed(4) + '°' + (v >= 0 ? 'E' : 'W');
const fmtLL = ll => fmtLat(ll[1]) + ' ' + fmtLon(ll[0]);
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const fmtDate = d => d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
const wrapLon = v => ((v + 540) % 360) - 180;
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
function toast(msg, ok) {
  const t = h('div', { class: 'toast' + (ok ? ' ok' : ''), role: 'status' }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), ok ? 3800 : 8000);
}
function setStatus(msg) { const s = $('#status'); s.hidden = !msg; s.textContent = msg || ''; }

if (!window.ol || !window.GeoTIFF) {
  $('#name').textContent = 'Map library did not load';
  $('#blurb').textContent = 'This page pulls its map library and all imagery from the internet. Check the connection and reload.';
  return;
}

/* ================= settings ================= */
const P = { playing: true, speedIdx: 2, resIdx: 2, gain: 0, modeId: 'true', cycle: false, after: 'next' };
try { Object.assign(P, JSON.parse(localStorage.getItem('sentinel-flyover') || '{}'), { playing: true }); } catch (e) { /* storage may be unavailable */ }
if (!MODE[P.modeId]) P.modeId = 'true';
P.speedIdx = clamp(P.speedIdx | 0, 0, CFG.speeds.length - 1); P.resIdx = clamp(P.resIdx | 0, 0, CFG.resolutions.length - 1); P.gain = clamp(P.gain | 0, -6, 6);
const save = () => { try { localStorage.setItem('sentinel-flyover', JSON.stringify(P)); } catch (e) { /* fine */ } };
const RES = () => CFG.resolutions[P.resIdx];
const speed = () => CFG.basePx * CFG.speeds[P.speedIdx] * RES();           // metres of ground per second
const gainFactor = () => Math.pow(1.18, P.gain);

/* ================= reading the rasters =================
   Each band is its own cloud-optimized GeoTIFF with internal overviews. The archive is not consistent about block
   sizes or overview counts between bands, so tiles are cut here: for every map tile, read the matching window
   from each band at the best overview and stack them. */
let pool = null, poolProven = false;
try { pool = new GeoTIFF.Pool(Math.max(2, Math.min(4, navigator.hardwareConcurrency || 2))); } catch (e) { pool = null; }
const liveScenes = new Set();

function openBand(scene, href) {
  if (!scene.files.has(href)) {
    scene.files.set(href, (async () => {
      const tiff = await GeoTIFF.fromUrl(href);
      const n = await tiff.getImageCount(), first = await tiff.getImage(0);
      const o = first.getOrigin(), res = first.getResolution()[0], width = first.getWidth();
      const levels = [];
      for (let i = 0; i < n; i++) {
        const img = i ? await tiff.getImage(i) : first;
        if ((img.fileDirectory.NewSubfileType || 0) & 4) continue;                     // skip mask images
        img.tiles = {};                                                                // let geotiff.js keep decoded blocks; trimmed below
        levels.push({ img, res: res * width / img.getWidth() });
      }
      const file = { href, ox: o[0], oy: o[1], res, width, height: first.getHeight(), levels };
      scene.opened.push(file);
      return file;
    })());
    scene.files.get(href).catch(() => scene.files.delete(href));
  }
  return scene.files.get(href);
}
/* Neighbouring map tiles often share one stored block, so decoded blocks are kept. Drop them all once there are many. */
function trimDecoded() {
  let px = 0;
  for (const sc of liveScenes) for (const f of sc.opened) for (const l of f.levels) px += Object.keys(l.img.tiles || {}).length * l.img.getTileWidth() * l.img.getTileHeight();
  if (px > 64e6) for (const sc of liveScenes) for (const f of sc.opened) for (const l of f.levels) l.img.tiles = {};
}
/* Decode in web workers when they work; fall back to the main thread if they never answer. */
async function readWindow(img, opts) {
  if (!pool || poolProven) return img.readRasters(pool ? { ...opts, pool } : opts);
  let timer;
  const guard = new Promise(res => { timer = setTimeout(() => res(null), 15000); });
  const got = await Promise.race([img.readRasters({ ...opts, pool }).then(v => { poolProven = true; return v; }), guard]);
  clearTimeout(timer);
  if (got) return got;
  if (!poolProven) pool = null;
  return img.readRasters(opts);
}
/* A tile source over one or more band files. Output is float DN per band plus a 0/255 valid-data band. */
function bandSource(scene, hrefs, finest, smooth) {
  const mult = finest === 10 ? [16, 8, 4, 2, 1] : finest === 20 ? [8, 4, 2, 1] : [4, 2, 1];
  const resolutions = mult.map(m => m * finest);
  const tileSizes = resolutions.map(() => finest === 60 ? 256 : 512);
  const extent = scene.extent, ox = extent[0], oy = extent[3], n = hrefs.length;
  return new ol.source.DataTile({
    projection: scene.code, wrapX: false, interpolate: smooth, bandCount: n + 1, transition: 250,
    tileGrid: new ol.tilegrid.TileGrid({ extent, origin: [ox, oy], resolutions, tileSizes }),
    loader: async (z, x, y, options) => {
      const R = resolutions[z], T = tileSizes[z], left = x * T * R, top = y * T * R, signal = options && options.signal;
      const rasters = await Promise.all(hrefs.map(async href => {
        const f = await openBand(scene, href);
        let lv = f.levels[0];
        for (const l of f.levels) if (l.res <= R * 1.03) lv = l;                          // coarsest overview that still has the detail
        const k = 1 / lv.res;
        const win = [Math.round(left * k), Math.round(top * k), Math.round((left + T * R) * k), Math.round((top + T * R) * k)];
        const out = await readWindow(lv.img, { window: win, width: T, height: T, samples: [0], fillValue: 0, signal });
        return out[0];
      }));
      const px = T * T, step = n + 1, data = new Float32Array(px * step);
      for (let i = 0, j = 0; i < px; i++, j += step) {
        let any = 0;
        for (let b = 0; b < n; b++) { const v = rasters[b][i]; data[j + b] = v; any |= v; }
        data[j + n] = any ? 255 : 0;                                                     // 0 in every band is the no-data value
      }
      trimDecoded();
      return data;
    },
  });
}

/* ================= finding scenes ================= */
async function stacSearch(lon, lat, cloud) {
  const end = new Date(), start = new Date(end.getTime() - CFG.lookbackDays * 864e5);
  const body = {
    collections: [CFG.collection],
    intersects: { type: 'Point', coordinates: [lon, lat] },
    datetime: start.toISOString() + '/' + end.toISOString(),
    query: { 'eo:cloud_cover': { lt: cloud } },
    sortby: [{ field: 'properties.datetime', direction: 'desc' }],
    limit: 36,
  };
  const r = await fetch(CFG.stac, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error('Earth Search answered ' + r.status);
  const j = await r.json();
  return (j.features || []).filter(f => f.assets && f.assets.red && f.assets.red.href && f.properties && f.properties.datetime);
}
const gridCode = f => f.properties['grid:code'] || f.id.split('_')[1];
/* A point can sit in the overlap of several MGRS tiles. Group by tile and return the passes of the tile where the
   point is most central, newest first. Tiles in `skip` are ignored. */
function pickTile(feats, lon, lat, skip) {
  const tiles = new Map();
  for (const f of feats) {
    const code = gridCode(f);
    if (skip && skip.includes(code)) continue;
    if (!tiles.has(code)) tiles.set(code, []);
    tiles.get(code).push(f);
  }
  let best = [], bestD = Infinity;
  for (const list of tiles.values()) {
    const c = list[0].properties['proj:centroid'], b = list[0].bbox;
    const d = ol.sphere.getDistance([lon, lat], c ? [c.lon, c.lat] : [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]);
    if (d < bestD) { bestD = d; best = list; }
  }
  return best;
}
async function findPasses(lon, lat, skip) {
  for (const cloud of CFG.cloudSteps) {
    const best = pickTile(await stacSearch(lon, lat, cloud), lon, lat, skip);
    if (best.length) return best;
  }
  return [];
}
function sceneExtent(item) {
  const a = item.assets.red, t = a['proj:transform'], sh = a['proj:shape'];
  if (t && sh) return [t[2], t[5] + t[4] * sh[0], t[2] + t[0] * sh[1], t[5]];
  return null;
}
/* DN to reflectance. In this collection the 1000 DN offset of processing baseline 04.00+ is already removed from the
   pixel values when earthsearch:boa_offset_applied is true, whatever raster:bands says. Checked against the data. */
function calibration(item) {
  const rb = (item.assets.red['raster:bands'] || [{}])[0];
  return { scale: rb.scale || 0.0001, offset: item.properties['earthsearch:boa_offset_applied'] === true ? 0 : (rb.offset || 0) };
}
function makeScene(item, place, from) {
  const p = item.properties, code = p['proj:code'] || ('EPSG:' + p['proj:epsg']);
  if (!ol.proj.get(code)) throw new Error('unknown projection ' + code);
  let footprint = null;
  try { footprint = new ol.format.GeoJSON().readGeometry(item.geometry, { dataProjection: 'EPSG:4326', featureProjection: code }); } catch (e) { /* path falls back to the full square */ }
  const extent = sceneExtent(item) || (footprint && footprint.getExtent());
  if (!extent) throw new Error('scene has no usable extent');
  const scene = { item, code, extent, footprint, place, from, cal: calibration(item), files: new Map(), opened: [], stretch: {}, legs: null };
  liveScenes.add(scene);
  return scene;
}
function releaseScene(scene) { scene.files.clear(); scene.opened = []; liveScenes.delete(scene); }

/* ================= the flight plan =================
   A scene is covered in vertical strips one screen wide: down one, U-turn, up the next. The plan starts at the
   point of interest and runs east (leg 1); if that leaves ground uncovered to the west, leg 2 picks up from the
   same start in the other direction. Strips are trimmed to where the satellite actually recorded data. */
function buildLegs(scene, from) {
  const res = RES(), vw = innerWidth * res, vh = innerHeight * res, E = scene.extent, fp = scene.footprint;
  const inside = (x, y) => !fp || fp.intersectsCoordinate([x, y]);
  const x0 = E[0] + vw / 2, x1 = E[2] - vw / 2, xs = [];
  if (x1 <= x0) xs.push((E[0] + E[2]) / 2);
  else { const n = Math.max(1, Math.ceil((x1 - x0) / (vw * (1 - CFG.overlap)))); for (let i = 0; i <= n; i++) xs.push(x0 + (x1 - x0) * i / n); }
  const strips = [];
  for (const x of xs) {
    let top = null, bot = null;
    for (let y = E[3] - 200; y >= E[1] + 200; y -= 400) {
      if (inside(x, y) || inside(x - vw * 0.3, y) || inside(x + vw * 0.3, y)) { if (top === null) top = y; bot = y; }
    }
    if (top === null) continue;
    let a = top - vh / 2, b = bot + vh / 2;                                              // where the screen centre may travel
    if (a < b) a = b = (top + bot) / 2;
    strips.push({ x, top: a, bot: b });
  }
  if (!strips.length) { const m = (E[1] + E[3]) / 2; strips.push({ x: (E[0] + E[2]) / 2, top: Math.max(E[3] - vh / 2, m), bot: Math.min(E[1] + vh / 2, m) }); }
  const n = strips.length;
  let k0 = 0, down = true, y0;
  if (from) {
    for (let i = 1; i < n; i++) if (Math.abs(strips[i].x - from.x) < Math.abs(strips[k0].x - from.x)) k0 = i;
    if (from.edge) { down = from.edge === 'top'; y0 = down ? strips[k0].top : strips[k0].bot; }
    else { y0 = clamp(from.y, strips[k0].bot, strips[k0].top); down = from.down !== false; }
  } else y0 = strips[0].top;
  const run = (order, firstDown) => {
    const raw = []; let d = firstDown;
    order.forEach((k, i) => { const s = strips[k]; raw.push([s.x, i === 0 ? y0 : (d ? s.top : s.bot)], [s.x, d ? s.bot : s.top]); d = !d; });
    return makeLeg(roundCorners(raw, vw * 0.4));
  };
  const east = [], west = [];
  for (let k = k0; k < n; k++) east.push(k);
  for (let k = k0; k >= 0; k--) west.push(k);
  let legs = [run(east, down)];
  const leftBehind = down ? strips[k0].top - y0 : y0 - strips[k0].bot;                  // part of the first strip leg 1 skips
  if (k0 > 0 || leftBehind > vh * 0.75) legs.push(run(west, !down));
  if (legs.length > 1) { const real = legs.filter(l => !l.still); legs = real.length ? real : [legs[0]]; }
  legs.strips = n;
  legs.total = legs.reduce((s, l) => s + l.total, 0);
  return legs;
}
/* Replace each sharp corner with a short curve so turns glide. */
function roundCorners(raw, r) {
  const pts = raw.filter((p, i) => i === 0 || dist(p, raw[i - 1]) > 1);
  if (pts.length < 3) return pts;
  const out = [pts[0]];
  for (let i = 1; i < pts.length - 1; i++) {
    const P0 = pts[i], A = pts[i - 1], B = pts[i + 1], la = dist(P0, A), lb = dist(P0, B);
    const d1 = Math.min(r, la / 2), d2 = Math.min(r, lb / 2);
    const P1 = [P0[0] + (A[0] - P0[0]) / la * d1, P0[1] + (A[1] - P0[1]) / la * d1];
    const P2 = [P0[0] + (B[0] - P0[0]) / lb * d2, P0[1] + (B[1] - P0[1]) / lb * d2];
    for (let j = 0; j <= 14; j++) {
      const t = j / 14, u = 1 - t;
      out.push([u * u * P1[0] + 2 * u * t * P0[0] + t * t * P2[0], u * u * P1[1] + 2 * u * t * P0[1] + t * t * P2[1]]);
    }
  }
  out.push(pts[pts.length - 1]);
  return out;
}
function makeLeg(pts) {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + dist(pts[i], pts[i - 1]));
  const length = cum[cum.length - 1], still = length < 100;
  return { pts, cum, total: still ? 20000 : length, still, i: 0 };                       // nothing to travel: hold here for a while instead
}
function legPos(leg, s) {
  const { pts, cum } = leg, last = pts.length - 1;
  if (last === 0 || s >= cum[last]) return pts[last];
  if (s <= 0) return pts[0];
  let i = Math.min(leg.i, last - 1);
  while (i > 0 && cum[i] > s) i--;
  while (i < last - 1 && cum[i + 1] < s) i++;
  leg.i = i;
  const t = (s - cum[i]) / (cum[i + 1] - cum[i] || 1);
  return [pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t];
}

/* ================= the two projectors =================
   Two maps are stacked. One is on screen; the other loads whatever comes next out of sight, then they crossfade
   and swap jobs. That is what lets a scene in one UTM zone dissolve into a scene in another. */
function makeSlot(id) {
  const el = document.getElementById(id);
  const map = new ol.Map({ target: el.firstElementChild, controls: [], interactions: [], layers: [], view: new ol.View({ center: [0, 0], zoom: 1 }) });
  return { el, map, layer: null, scene: null, legIdx: 0, leg: null, s: 0, modeId: null, st: null, gen: 0, zoom: null, intro: false };
}
const slots = [makeSlot('slot-a'), makeSlot('slot-b')];
let act = 0;
const next = { state: null, token: 0, force: false, retryAt: 0 };          // state: null | 'working' | 'ready' | 'failed'
const recentTiles = [];
let pending = null;                                                         // a place the viewer asked to fly to

const rendered = (map, ms) => new Promise(res => {
  let done = false; const fin = () => { if (!done) { done = true; res(); } };
  map.once('rendercomplete', fin); map.render(); setTimeout(fin, ms);
});
function cacheSize() {
  const cols = Math.ceil((innerWidth + 2 * CFG.marginX) / 512) + 1, rows = Math.ceil((innerHeight + 2 * CFG.marginY) / 512) + 1;
  return clamp(Math.round(cols * rows * 1.7), 48, 240);
}
const specBands = spec => spec.kind === 'scl' ? ['SCL'] : (spec.bands || []);

/* One stretch per scene and band set, measured on the smallest overview, so brightness never flickers mid-flight. */
async function sceneStretch(scene, spec) {
  if (spec.kind !== 'rgb') return null;
  const ids = [...new Set(spec.bands)], key = ids.join('+');
  if (scene.stretch[key]) return scene.stretch[key];
  const vals = [];
  await Promise.all(ids.map(async id => {
    const f = await openBand(scene, assetFor(scene.item, id).href);
    const ras = (await readWindow(f.levels[f.levels.length - 1].img, { samples: [0] }))[0];
    const step = Math.max(1, Math.floor(ras.length / 50000)) | 1;
    for (let i = 0; i < ras.length; i += step) if (ras[i]) vals.push(ras[i] * scene.cal.scale + scene.cal.offset);
  }));
  const st = { lo: 0, hi: 0.3, ga: 1.5 };
  if (vals.length > 300) {
    vals.sort((a, b) => a - b);
    const q = p => vals[Math.floor(p * (vals.length - 1))];
    st.hi = clamp(Math.min(q(0.99) * 1.05, q(0.9) * 1.8), 0.08, 1.3);                    // let salt, cloud and snow clip rather than darken the rest
  }
  return (scene.stretch[key] = st);
}
const styleVars = st => ({ lo: st.lo, hi: Math.max(st.hi / gainFactor(), st.lo + 0.01), ig: 1 / st.ga });

function makeLayer(scene, spec, st) {
  const item = scene.item;
  let source, style;
  if (spec.kind === 'scl') {
    if (!item.assets.scl) throw new Error('this scene has no classification layer');
    source = bandSource(scene, [item.assets.scl.href], 20, false);
    style = { color: ['palette', ['band', 1], SCL.map(c => c[1])] };
  } else {
    const order = [...new Set(specBands(spec))];
    const missing = order.find(id => !assetFor(item, id));
    if (missing) throw new Error('this scene is missing band ' + missing);
    source = bandSource(scene, order.map(id => assetFor(item, id).href), Math.min(...order.map(resOf)), true);
    const pos = id => order.indexOf(id) + 1;
    const refl = id => EXTRA[id] ? ['*', ['band', pos(id)], EXTRA[id].scale] : ['+', ['*', ['band', pos(id)], scene.cal.scale], scene.cal.offset];
    const alpha = ['/', ['band', order.length + 1], 255];
    if (spec.kind === 'rgb') {
      const s = x => ['^', ['clamp', ['/', ['-', x, ['var', 'lo']], ['-', ['var', 'hi'], ['var', 'lo']]], 0, 1], ['var', 'ig']];
      style = { color: ['array', s(refl(spec.bands[0])), s(refl(spec.bands[1])), s(refl(spec.bands[2])), alpha], variables: styleVars(st) };
    } else {
      const ramp = ['interpolate', ['linear'], spec.value(refl)];
      for (const [v, c] of spec.ramp) ramp.push(v, [...hexRgb(c), 1]);
      style = { color: ['case', ['>', ['band', order.length + 1], 0], ramp, [0, 0, 0, 0]] };
    }
  }
  return new ol.layer.WebGLTile({ source, style, cacheSize: cacheSize(), preload: 1 });
}
/* Taking a layer down in steps: the renderer queues a tile-preload step after every frame that still needs the layer
   on the map, so hide it first, remove it once that step has run, and free it after that. */
function dropLayer(slot, layer) {
  if (layer.dropping) return;
  layer.dropping = true;
  layer.setVisible(false);
  setTimeout(() => {
    slot.map.removeLayer(layer);
    setTimeout(() => { const s = layer.getSource(); layer.dispose(); if (s) s.dispose(); }, 1500);
  }, 700);
}

/* Put the slot's scene on its map in the slot's view mode. The old picture stays underneath until the new one has drawn. */
async function dressSlot(slot) {
  const scene = slot.scene, spec = MODE[slot.modeId], gen = ++slot.gen;
  const st = await sceneStretch(scene, spec);
  if (gen !== slot.gen || slot.scene !== scene) return false;
  const layer = makeLayer(scene, spec, st), old = slot.layer;
  slot.layer = layer; slot.st = st;
  slot.map.addLayer(layer);
  await rendered(slot.map, 25000);
  if (old) dropLayer(slot, old);
  return gen === slot.gen;
}
function clearSlot(slot) {
  slot.gen++;
  for (const l of slot.map.getLayers().getArray().slice()) dropLayer(slot, l);
  const scene = slot.scene;
  slot.layer = null; slot.scene = null; slot.leg = null; slot.zoom = null;
  if (scene && !slots.some(s => s.scene === scene)) releaseScene(scene);
}
/* Load a scene leg into the off-screen slot, parked at the leg's first point. */
async function stage(slot, scene, legIdx, modeId, intro) {
  clearSlot(slot);
  Object.assign(slot, { scene, legIdx, leg: scene.legs[legIdx], s: 0, modeId, intro });
  slot.leg.i = 0;
  slot.map.updateSize();
  const view = new ol.View({ projection: scene.code, center: slot.leg.pts[0], resolution: RES(), enableRotation: false, constrainResolution: false });
  slot.map.setView(view);
  const ok = await dressSlot(slot);
  if (ok && intro) { view.setResolution(RES() * CFG.introZoom); await rendered(slot.map, 9000); }   // have the wide opening shot ready too
  return ok;
}

/* ================= what comes next ================= */
function nextPlace() {
  if (!nextPlace.bag || !nextPlace.bag.length) nextPlace.bag = PLACES.map((_, i) => i).sort(() => Math.random() - 0.5);
  const p = PLACES[nextPlace.bag.pop()];
  return { name: p[0], region: p[1], lat: p[2], lon: p[3], res: p[4], mode: p[5], blurb: p[6] };
}
const sceneAt = (item, place, lon, lat) => { const code = item.properties['proj:code'] || ('EPSG:' + item.properties['proj:epsg']); const xy = ol.proj.transform([lon, lat], 'EPSG:4326', code); return makeScene(item, place, { x: xy[0], y: xy[1], down: true }); };

async function sceneForPlace() {
  for (let i = 0; i < 4; i++) {
    const p = nextPlace(), passes = await findPasses(p.lon, p.lat);
    if (passes.length) return sceneAt(passes[0], p, p.lon, p.lat);
  }
  throw new Error('no clear pass found over the last four places tried');
}
/* Carry on into the tile east of where this scene ended, using the pass closest in date so the light matches. */
async function sceneNextDoor(cur, endedAtBottom) {
  const E = cur.extent, y = endedAtBottom ? E[1] + 20000 : E[3] - 20000, when = Date.parse(cur.item.properties.datetime);
  for (const reach of [25000, 80000]) {
    const ll = ol.proj.transform([E[2] + reach, y], cur.code, 'EPSG:4326');
    const lon = wrapLon(ll[0]), lat = ll[1];
    if (!isFinite(lon) || Math.abs(lat) > 82) return null;
    const passes = pickTile(await stacSearch(lon, lat, 20), lon, lat, recentTiles);
    if (!passes.length) continue;
    passes.sort((a, b) => Math.abs(Date.parse(a.properties.datetime) - when) - Math.abs(Date.parse(b.properties.datetime) - when));
    const item = passes[0];
    if ((item.properties['s2:water_percentage'] || 0) > 92) return null;                 // open sea ahead: better to jump
    const scene = makeScene(item, null, null);
    scene.from = { x: scene.extent[0], edge: endedAtBottom ? 'bottom' : 'top' };
    scene.note = 'Continuing east from the last scene.';
    return scene;
  }
  return null;
}
async function chooseNextScene(cur, endedAtBottom) {
  if (pending) {
    const t = pending; pending = null;
    const passes = await findPasses(t.lon, t.lat);
    if (passes.length) { const sc = sceneAt(passes[0], t.place || null, t.lon, t.lat); sc.note = 'Around ' + fmtLL([t.lon, t.lat]) + '.'; return sc; }
    toast('No clear Sentinel-2 pass found at ' + fmtLL([t.lon, t.lat]) + ' in the last 18 months. Flying somewhere else.');
  }
  if (cur && P.after === 'next') {
    try { const sc = await sceneNextDoor(cur, endedAtBottom); if (sc) return sc; } catch (e) { /* fall through to a jump */ }
  }
  return sceneForPlace();
}
function nextComposite(id) {
  const list = MODES.filter(m => m.kind === 'rgb'), i = list.findIndex(m => m.id === id);
  return list[(i + 1) % list.length].id;
}

async function prepareNext(newScene) {
  const tok = ++next.token, a = slots[act], b = slots[1 - act];
  next.state = 'working';
  try {
    let scene = a.scene, legIdx = a.legIdx + 1, intro = false, modeId = P.modeId;
    if (newScene || !scene || legIdx >= scene.legs.length) {
      const end = a.leg ? a.leg.pts[a.leg.pts.length - 1] : null;
      const atBottom = !!(end && scene && end[1] < (scene.extent[1] + scene.extent[3]) / 2);
      const fresh = await chooseNextScene(scene, atBottom);
      if (tok !== next.token) { if (!slots.some(s => s.scene === fresh)) releaseScene(fresh); return; }
      scene = fresh; legIdx = 0; intro = true;
      scene.legs = buildLegs(scene, scene.from);
      if (P.cycle && a.scene) modeId = nextComposite(P.modeId);
    }
    const ok = await stage(b, scene, legIdx, modeId, intro);
    if (tok !== next.token) return;
    if (!ok) throw new Error('the next view did not finish loading');
    next.state = 'ready';
  } catch (err) {
    if (tok !== next.token) return;
    next.state = 'failed'; next.retryAt = performance.now() + 6000;
    if (!slots[act].scene) setStatus('Could not load a scene (' + ((err && err.message) || err) + '). Trying again…');
  }
}
function cancelNext() { next.token++; next.state = null; next.force = false; }

function swap() {
  const a = slots[act], b = slots[1 - act], sceneChanged = a.scene !== b.scene;
  a.el.classList.remove('on'); a.el.classList.add('under'); b.el.classList.add('on');
  act = 1 - act;
  next.state = null; next.force = false;
  if (b.intro) { b.zoom = { from: RES() * CFG.introZoom, to: RES(), t0: performance.now(), ms: CFG.introMs, hold: true }; b.intro = false; }
  else b.map.getView().setResolution(RES());
  setTimeout(() => { a.el.classList.remove('under'); if (slots[1 - act] === a && next.state === null) clearSlot(a); }, CFG.fadeMs + 150);
  if (sceneChanged) {
    const code = gridCode(b.scene.item);
    recentTiles.push(code); if (recentTiles.length > 10) recentTiles.shift();
    if (P.modeId !== b.modeId) { P.modeId = b.modeId; save(); renderControls(); }
    showSceneHud(b.scene);
  }
  setStatus('');
  drawPlan();
}

/* ================= the loop ================= */
let lastT = performance.now(), hudT = 0;
function tick(now) {
  requestAnimationFrame(tick);
  const dt = Math.min(0.1, (now - lastT) / 1000); lastT = now;
  const a = slots[act];
  if (!a.scene) {                                                            // nothing on screen yet
    if (next.state === null) prepareNext(true);
    else if (next.state === 'ready') swap();
    else if (next.state === 'failed' && now > next.retryAt) next.state = null;
    return;
  }
  const view = a.map.getView();
  let holding = false;
  if (a.zoom) {
    const u = clamp((now - a.zoom.t0) / a.zoom.ms, 0, 1), e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
    view.setResolution(a.zoom.from * Math.pow(a.zoom.to / a.zoom.from, e));
    holding = a.zoom.hold && u < 0.85;
    if (u >= 1) a.zoom = null;
  }
  if (P.playing && !holding) a.s = Math.min(a.leg.total, a.s + speed() * dt);
  view.setCenter(legPos(a.leg, a.s));

  const left = a.leg.total - a.s;
  if (next.state === null && (left < speed() * CFG.preloadSeconds || next.force)) prepareNext(next.force);
  else if (next.state === 'failed' && now > next.retryAt) next.state = null;
  else if (next.state === 'ready' && (left <= 0 || next.force)) swap();
  if (left <= 0 && next.state === 'working' && now - hudT > 0) setStatus('Loading the next scene…');

  if (now - hudT > 200) { hudT = now; liveHud(a); }
}

/* ================= overlays ================= */
const landSource = new ol.source.Vector(), markSource = new ol.source.Vector();
const mini = new ol.Map({
  target: 'mini-map', controls: [], interactions: [],
  layers: [
    new ol.layer.Vector({ source: landSource, style: { 'fill-color': '#22303a' } }),
    new ol.layer.Tile({ source: new ol.source.XYZ({ url: CFG.miniTiles, maxZoom: 12, transition: 150 }) }),
    new ol.layer.Vector({ source: landSource, style: { 'stroke-color': 'rgba(231,228,219,.42)', 'stroke-width': 0.7 } }),
    new ol.layer.Vector({
      source: markSource,
      style: [
        { filter: ['==', ['get', 'kind'], 'footprint'], style: { 'stroke-color': 'rgba(231,228,219,.7)', 'stroke-width': 1, 'fill-color': 'rgba(5,8,11,.25)' } },
        { filter: ['==', ['get', 'kind'], 'plan'], style: { 'stroke-color': 'rgba(231,228,219,.5)', 'stroke-width': 1 } },
        { filter: ['==', ['get', 'kind'], 'done'], style: { 'stroke-color': '#ffb547', 'stroke-width': 2 } },
        { filter: ['==', ['get', 'kind'], 'box'], style: { 'stroke-color': '#ffffff', 'stroke-width': 1.5, 'fill-color': 'rgba(255,255,255,.16)' } },
      ],
    }),
  ],
  view: new ol.View({ center: [0, 2500000], zoom: 0.4, enableRotation: false }),
});
let countries = null;
fetch(CFG.countries).then(r => r.json()).then(topo => {
  const feats = new ol.format.TopoJSON({ layers: ['countries'] }).readFeatures(topo);
  countries = feats.map(f => ({ name: f.get('name'), geom: f.getGeometry().clone() }));
  feats.forEach(f => f.getGeometry().transform('EPSG:4326', 'EPSG:3857'));
  landSource.addFeatures(feats);
  const sc = slots[act].scene;
  if (sc && !sc.place) { const name = sceneCountry(sc); if (name) { sc.label = name; $('#name').textContent = name; document.title = name + ' - Sentinel Flyover'; } }
}).catch(() => {});
function countryAt(ll) {
  if (!countries) return null;
  for (const c of countries) if (c.geom.intersectsCoordinate(ll)) return c.name;
  return null;
}
function setMark(kind, geom) {
  for (const f of markSource.getFeatures()) if (f.get('kind') === kind) markSource.removeFeature(f);
  if (geom) markSource.addFeature(new ol.Feature({ geometry: geom, kind }));
}
/* The locator shows the scene outline, the planned route and how much of it has been flown. */
function drawPlan() {
  const a = slots[act]; if (!a.scene) return;
  const sc = a.scene, to = g => g.transform(sc.code, 'EPSG:3857');
  const outline = sc.footprint ? to(sc.footprint.clone()) : to(ol.geom.Polygon.fromExtent(sc.extent));
  setMark('footprint', outline);
  setMark('plan', to(new ol.geom.MultiLineString(sc.legs.map(l => l.pts))));
  mini.updateSize();
  mini.getView().fit(to(ol.geom.Polygon.fromExtent(sc.extent)).getExtent(), { padding: [10, 10, 10, 10] });
}
let slowT = 0, country = null;
function liveHud(a) {
  const sc = a.scene, view = a.map.getView(), c = view.getCenter(), res = view.getResolution();
  const ll = ol.proj.transform(c, sc.code, 'EPSG:4326');
  const hw = innerWidth * res / 2, hh = innerHeight * res / 2;
  setMark('box', ol.geom.Polygon.fromExtent([c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh]).transform(sc.code, 'EPSG:3857'));
  const now = performance.now();
  if (now - slowT > 1500) {
    slowT = now; country = countryAt(ll);
    const flown = [];
    for (let i = 0; i <= a.legIdx; i++) {
      const leg = sc.legs[i], upto = i < a.legIdx ? leg.total : a.s;
      const line = []; for (let k = 0; k < leg.pts.length && leg.cum[k] <= upto; k++) line.push(leg.pts[k]);
      if (i === a.legIdx) line.push(c);
      if (line.length > 1) flown.push(line);
    }
    setMark('done', flown.length ? new ol.geom.MultiLineString(flown).transform(sc.code, 'EPSG:3857') : null);
  }
  let done = a.s; for (let i = 0; i < a.legIdx; i++) done += sc.legs[i].total;
  const frac = clamp(done / sc.legs.total, 0, 1), secs = (sc.legs.total - done) / speed();
  $('#progress i').style.width = (frac * 100).toFixed(1) + '%';
  $('#plan-a').textContent = Math.round(frac * 100) + '% of scene';
  $('#plan-b').textContent = !P.playing ? 'paused' : secs > 90 ? Math.round(secs / 60) + ' min left' : Math.max(0, Math.round(secs)) + ' s left';
  $('#kicker').textContent = (country ? country + '  ·  ' : '') + fmtLL(ll);
  $('#m-speed').textContent = Math.round(speed() * 3.6).toLocaleString('en-US') + ' km/h';
}
const sceneCountry = sc => { const c = sc.item.properties['proj:centroid'], b = sc.item.bbox; return countryAt(c ? [c.lon, c.lat] : b ? [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] : [0, 0]); };
let blurbTimer = 0;
function showSceneHud(sc) {
  const p = sc.item.properties, d = new Date(p.datetime), tile = (p['grid:code'] || '').replace('MGRS-', '');
  sc.label = sc.place ? sc.place.name : (sceneCountry(sc) || 'Tile ' + tile);
  $('#name').textContent = sc.label;
  const blurb = $('#blurb');
  blurb.textContent = sc.place ? sc.place.blurb : (sc.note || '');
  blurb.classList.remove('gone'); clearTimeout(blurbTimer);
  blurbTimer = setTimeout(() => blurb.classList.add('gone'), 30000);
  const sat = (p.platform || '').replace('sentinel-', 'Sentinel-').replace(/[abc]$/, s => s.toUpperCase());
  const meta = $('#meta'); meta.textContent = '';
  const cell = (k, v, id) => meta.append(h('span', { id }, h('b', {}, k), v));
  cell('Acquired', fmtDate(d)); cell('By', sat); cell('Tile', tile);
  cell('Cloud', p['eo:cloud_cover'] != null ? p['eo:cloud_cover'].toFixed(1) + '%' : '?');
  cell('View', MODE[P.modeId].name, 'm-view-cell'); cell('Ground speed', '', 'm-speed-cell');
  $('#m-speed-cell').append(h('i', { id: 'm-speed', style: 'font-style:normal' }));
  $('#m-view-cell').lastChild.textContent = MODE[P.modeId].name;
  document.title = sc.label + ' · Sentinel Flyover';
}

/* ================= controls ================= */
function renderControls() {
  $('#b-play').textContent = P.playing ? 'Pause' : 'Play';
  $('#paused').hidden = P.playing;
  $('#o-speed').textContent = CFG.speeds[P.speedIdx] + '×';
  $('#o-res').textContent = RES() + ' m';
  $('#o-gain').textContent = (P.gain > 0 ? '+' : '') + P.gain;
  $('#sel-mode').value = P.modeId; $('#sel-after').value = P.after; $('#chk-cycle').checked = P.cycle;
  const cell = $('#m-view-cell'); if (cell) cell.lastChild.textContent = MODE[P.modeId].name;
}
function setPlaying(on) { P.playing = on; renderControls(); }
function setSpeed(d) { P.speedIdx = clamp(P.speedIdx + d, 0, CFG.speeds.length - 1); save(); renderControls(); }
function setGain(d) {
  P.gain = clamp(P.gain + d, -6, 6); save(); renderControls();
  for (const s of slots) if (s.layer && s.st) s.layer.updateStyleVariables(styleVars(s.st));
}
/* Changing detail or the window size changes the strip width, so the route is redrawn from where we are. */
function replan(newResIdx) {
  const a = slots[act];
  const from = RES();
  if (newResIdx != null) { P.resIdx = clamp(newResIdx, 0, CFG.resolutions.length - 1); save(); renderControls(); }
  for (const s of slots) s.map.updateSize();
  if (!a.scene) return;
  cancelNext();
  const c = a.map.getView().getCenter(), ahead = legPos(a.leg, Math.min(a.leg.total, a.s + 50));
  a.scene.legs = buildLegs(a.scene, { x: c[0], y: c[1], down: ahead[1] <= c[1] });
  a.legIdx = 0; a.leg = a.scene.legs[0]; a.s = 0;
  if (from !== RES()) a.zoom = { from: a.map.getView().getResolution(), to: RES(), t0: performance.now(), ms: 900, hold: false };
  drawPlan();
}
async function setMode(id) {
  if (!MODE[id] || id === P.modeId) return;
  P.modeId = id; save(); renderControls();
  const a = slots[act];
  if (!a.scene) return;
  cancelNext();                                                              // anything staged was dressed in the old view
  a.modeId = id;
  setStatus('Switching to ' + MODE[id].name + '…');
  try { await dressSlot(a); } catch (err) { toast('Could not show ' + MODE[id].name + ' here (' + err.message + ').'); }
  setStatus('');
}
function skipScene() { cancelNext(); next.force = true; setStatus('Finding the next scene…'); }
function flyTo(lon, lat, place) { pending = { lon, lat, place: place || null }; skipScene(); }
function toggleFull() {
  try { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(() => {}); } catch (e) { /* not available here */ }
}

const sel = $('#sel-mode');
for (const [label, test] of [['Band composites', m => m.kind === 'rgb'], ['Indices', m => m.group === 'idx'], ['Classes and atmosphere', m => m.group === 'more']]) {
  sel.append(h('optgroup', { label }, MODES.filter(test).map(m => h('option', { value: m.id }, m.name))));
}
sel.addEventListener('change', () => setMode(sel.value));
$('#sel-after').addEventListener('change', e => { P.after = e.target.value; save(); if (next.state && slots[1 - act].scene !== slots[act].scene) cancelNext(); });
$('#chk-cycle').addEventListener('change', e => { P.cycle = e.target.checked; save(); });
$('#b-play').addEventListener('click', () => setPlaying(!P.playing));
$('#b-slow').addEventListener('click', () => setSpeed(-1));
$('#b-fast').addEventListener('click', () => setSpeed(1));
$('#b-out').addEventListener('click', () => replan(P.resIdx - 1));
$('#b-in').addEventListener('click', () => replan(P.resIdx + 1));
$('#b-dim').addEventListener('click', () => setGain(-1));
$('#b-bright').addEventListener('click', () => setGain(1));
$('#b-next').addEventListener('click', skipScene);
$('#b-full').addEventListener('click', toggleFull);
$('#goto').addEventListener('submit', e => {
  e.preventDefault();
  const m = $('#goto-input').value.match(/-?\d+(?:\.\d+)?/g), lat = m && +m[0], lon = m && +m[1];
  if (!m || m.length < 2 || Math.abs(lat) > 82 || Math.abs(lon) > 180) { toast('Enter a latitude and a longitude in decimal degrees, for example 37.77, -122.42.'); return; }
  $('#goto-input').blur(); flyTo(lon, lat);
});
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (!$('#pick').hidden) { if (e.key === 'Escape') closePicker(); return; }
  if (e.target.matches('input, select')) return;
  const k = e.key.toLowerCase();
  if (e.code === 'Space') { if (e.target.closest('button')) return; e.preventDefault(); setPlaying(!P.playing); }
  else if (e.key === 'ArrowRight') setSpeed(1);
  else if (e.key === 'ArrowLeft') setSpeed(-1);
  else if (e.key === 'ArrowUp') { e.preventDefault(); replan(P.resIdx + 1); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); replan(P.resIdx - 1); }
  else if (k === 'n') skipScene();
  else if (k === 'r') openRec();
  else if (k === 'p') openPicker();
  else if (k === 'h') document.body.classList.toggle('nohud');
  else if (k === 'f') toggleFull();
  else if (/^[1-7]$/.test(e.key)) setMode(MODES.filter(m => m.kind === 'rgb')[+e.key - 1].id);
});
/* ================= recording clips =================
   Clips are not grabbed from the screen. Each scene is cut into 512 px blocks on its own pixel grid at the chosen
   detail, painted on the CPU from the band files with one fixed stretch, and every frame is a crop of those blocks
   at a whole-pixel offset. The camera moves the same whole number of pixels each frame, so a frame is an exact
   slide of the one before: no resampling, so no shimmer. Frames go straight into the encoder and, when a folder is
   chosen, straight to disk, so length is limited by disk space rather than memory. */
const BLOCK = 512;
const REC_SIZES = {
  portrait: { name: 'Portrait 4:5', mp4: [1080, 1350], gif: [480, 600] },
  square: { name: 'Square', mp4: [1080, 1080], gif: [540, 540] },
  landscape: { name: 'Landscape 16:9', mp4: [1920, 1080], gif: [640, 360] },
};
const GIF_FPS = 20, GIF_MAX_S = 60, GIF_BYTES_PER_PX = 0.55;
const REC = { busy: false, stop: false, dir: null, url: null, rows: [], dry: false };
P.rec = Object.assign({ len: 20, unit: 1, fmt: 'mp4', size: 'portrait', res: 10, step: 2, fps: 30, q: 'std', n: 5, places: 'mix', view: 'place' }, P.rec || {});

/* Hand control back to the page between frames. A message to ourselves is not throttled in background tabs the way
   timers are. One channel is reused: every open port with a listener stays alive until closed. */
const yieldUI = (() => {
  const ch = new MessageChannel(), waiting = [];
  ch.port1.onmessage = () => { const r = waiting.shift(); if (r) r(); };
  return () => new Promise(res => { waiting.push(res); ch.port2.postMessage(0); });
})();
const encoderDrained = enc => new Promise(res => {
  const t = setTimeout(res, 250);
  if ('ondequeue' in enc) enc.addEventListener('dequeue', () => { clearTimeout(t); res(); }, { once: true });
});
const slug = t => String(t).normalize('NFD').replace(/[^\w\s.-]/g, '').trim().replace(/\s+/g, '-').toLowerCase().slice(0, 60) || 'clip';
const fmtDur = s => s >= 60 ? Math.floor(s / 60) + ' min' + (Math.round(s % 60) ? ' ' + Math.round(s % 60) + ' s' : '') : Math.round(s) + ' s';
const fmtMB = b => (b / 1048576 >= 100 ? Math.round(b / 1048576) : (b / 1048576).toFixed(1)) + ' MB';

/* Settings as numbers. */
function recOpts() {
  const r = P.rec, gif = r.fmt === 'gif', [W, H] = REC_SIZES[r.size][gif ? 'gif' : 'mp4'];
  const fps = gif ? GIF_FPS : +r.fps, seconds = clamp(r.len * r.unit, 1, gif ? GIF_MAX_S : 600);
  const fh = Math.round(Math.min(W, H) * 0.215) & ~1;                                       // footer height
  const bitrate = Math.round(W * H * (fps === 60 ? 45 : 30) * (r.q === 'high' ? 0.36 : 0.18));
  return { gif, W, H, fps, seconds, fh, Wm: W, Hm: H - fh, r: +r.res, step: +r.step, bitrate, frames: Math.max(2, Math.round(seconds * fps)) };
}

/* ---- the path: serpentine strips on the scene's own pixel grid ---- */
function planClip(scene, o, at, fit) {
  const E = scene.extent, r = o.r, fp = scene.footprint;
  const GW = Math.round((E[2] - E[0]) / r), GH = Math.round((E[3] - E[1]) / r);
  const toMap = (gx, gy) => [E[0] + gx * r, E[3] - gy * r];
  const inside = (gx, gy) => !fp || fp.intersectsCoordinate(toMap(gx, gy));
  const hw = o.Wm / 2, hh = o.Hm / 2, gap = Math.max(2, Math.round(o.Wm * (1 - CFG.overlap)));
  const gx0 = (at[0] - E[0]) / r, gy0 = (E[3] - at[1]) / r;
  const lo = Math.ceil(hw), hi = Math.floor(GW - hw);
  const sx = hi >= lo ? clamp(Math.round(gx0), lo, hi) : Math.round(GW / 2);
  const xs = [sx];
  for (let x = sx - gap; x >= lo; x -= gap) xs.unshift(x);
  for (let x = sx + gap; x <= hi; x += gap) xs.push(x);
  const strips = xs.map(x => {
    let top = null, bot = null;
    for (let y = 16; y < GH; y += 32) if (inside(x, y) || inside(x - hw * 0.7, y) || inside(x + hw * 0.7, y)) { if (top === null) top = y; bot = y; }
    if (top === null) return null;
    let a = Math.round(top + hh * 0.35), b = Math.round(bot - hh * 0.35);              // a little no-data at the very edge is fine
    a = Math.max(a, Math.ceil(hh)); b = Math.min(b, Math.floor(GH - hh));
    if (a > b) a = b = Math.round((a + b) / 2);
    return { x, top: a, bot: b };
  });
  const L = (o.frames - 1) * o.step;
  const route = (i0, y0, down) => {
    const raw = [];
    let d = down;
    for (let i = i0; i < strips.length; i++) {
      const s = strips[i];
      if (!s) { if (raw.length) break; continue; }
      raw.push([s.x, i === i0 ? clamp(y0, s.top, s.bot) : (d ? s.top : s.bot)], [s.x, d ? s.bot : s.top]);
      d = !d;
    }
    if (!raw.length) return null;
    return makeLeg(roundCorners(raw, gap * 0.4));
  };
  let i0 = xs.indexOf(sx);
  const s0 = strips[i0] || strips.find(Boolean);
  if (!s0) return null;
  if (!strips[i0]) i0 = strips.indexOf(s0);
  let down = at.down !== false;
  let y0 = Math.round(gy0);
  if (fit) y0 = Math.round(gy0 - Math.min(L, s0.bot - s0.top) / 2);                      // keep the place near the middle of short clips
  let leg = route(i0, y0, down);
  if (fit) {                                                                                // not enough ground east of here: start further west
    for (let i = i0; leg && (leg.still ? 0 : leg.cum[leg.cum.length - 1]) < L && i >= 0; i--) {
      if (!strips[i]) continue;
      const t = route(i, strips[i].top, true);
      if (t) { leg = t; down = true; }
    }
  }
  if (!leg) return null;
  const length = leg.still ? 0 : leg.cum[leg.cum.length - 1];
  const frames = Math.max(2, Math.min(o.frames, Math.floor(length / o.step) + 1));
  const pos = new Int32Array(frames * 2);
  leg.i = 0;
  for (let k = 0; k < frames; k++) {
    const p = legPos(leg, k * o.step);
    pos[2 * k] = Math.round(p[0]) - hw; pos[2 * k + 1] = Math.round(p[1]) - hh;
  }
  return { pos, frames, wanted: o.frames, room: length / o.step, GW, GH, pts: leg.pts, hw, hh, r };
}
/* Share of the clip that sits over recorded data, from a sample of frames. */
function clipCoverage(scene, plan) {
  const E = scene.extent, r = plan.r, fp = scene.footprint;
  if (!fp) return 1;
  let hit = 0, n = 0;
  for (let i = 0; i < 24; i++) {
    const k = Math.floor((plan.frames - 1) * i / 23), x0 = plan.pos[2 * k], y0 = plan.pos[2 * k + 1];
    for (const fx of [0.1, 0.5, 0.9]) for (const fy of [0.1, 0.5, 0.9]) { n++; if (fp.intersectsCoordinate([E[0] + (x0 + fx * plan.hw * 2) * r, E[3] - (y0 + fy * plan.hh * 2) * r])) hit++; }
  }
  return hit / n;
}

/* ---- reading and painting blocks ---- */
async function readRaw(img, x0, y0, x1, y1) {
  const w = x1 - x0, h = y1 - y0, iw = img.getWidth(), ih = img.getHeight();
  const a = Math.max(0, x0), b = Math.max(0, y0), c = Math.min(iw, x1), d = Math.min(ih, y1);
  const out = new Float32Array(w * h);
  if (c <= a || d <= b) return out;
  const got = (await readWindow(img, { window: [a, b, c, d], samples: [0] }))[0], gw = c - a;
  for (let y = b; y < d; y++) for (let x = a, j = (y - b) * gw, o = (y - y0) * w + (a - x0); x < c; x++) out[o++] = got[j++];
  return out;
}
/* One band over one block, sampled on the block's pixel centres. Coarser bands are interpolated (classes: nearest). */
async function readBlockBand(scene, id, gx, gy, r) {
  const asset = assetFor(scene.item, id);
  if (!asset) throw new Error('this scene has no ' + id);
  const f = await openBand(scene, asset.href);
  let lv = f.levels[0];
  for (const l of f.levels) if (l.res <= r * 1.03) lv = l;
  const k = r / lv.res, X0 = scene.extent[0] + gx * r, Y0 = scene.extent[3] - gy * r;
  const sx = (X0 - f.ox) / lv.res, sy = (f.oy - Y0) / lv.res, B = BLOCK;
  if (Math.abs(k - 1) < 1e-6) {
    const x = Math.round(sx), y = Math.round(sy);
    return readRaw(lv.img, x, y, x + B, y + B);
  }
  if (k > 1) {                                                                              // finer data than needed: let the library average down
    const iw = lv.img.getWidth(), ih = lv.img.getHeight(), win = [Math.round(sx), Math.round(sy), Math.round(sx + B * k), Math.round(sy + B * k)];
    if (win[0] < 0 || win[1] < 0 || win[2] > iw || win[3] > ih) {
      const raw = await readRaw(lv.img, win[0], win[1], win[2], win[3]), w = win[2] - win[0], out = new Float32Array(B * B);
      for (let j = 0; j < B; j++) for (let i = 0; i < B; i++) out[j * B + i] = raw[Math.min(win[3] - win[1] - 1, Math.floor((j + 0.5) * k)) * w + Math.min(w - 1, Math.floor((i + 0.5) * k))];
      return out;
    }
    return (await readWindow(lv.img, { window: win, width: B, height: B, samples: [0], resampleMethod: id === 'SCL' ? 'nearest' : 'bilinear' }))[0];
  }
  const ux = i => sx + (i + 0.5) * k - 0.5;                                                 // source pixel-centre coordinate of output pixel i
  const x0 = Math.floor(ux(0)) - 1, y0 = Math.floor(sy + 0.5 * k - 0.5) - 1;
  const x1 = Math.ceil(ux(B - 1)) + 2, y1 = Math.ceil(sy + (B - 0.5) * k - 0.5) + 2, w = x1 - x0;
  const raw = await readRaw(lv.img, x0, y0, x1, y1), out = new Float32Array(B * B);
  const near = id === 'SCL';
  for (let j = 0; j < B; j++) {
    const v = sy + (j + 0.5) * k - 0.5 - y0, v0 = Math.floor(v), fv = v - v0;
    for (let i = 0; i < B; i++) {
      const u = ux(i) - x0, u0 = Math.floor(u), fu = u - u0, p = v0 * w + u0;
      if (near) { out[j * B + i] = raw[(fv < 0.5 ? v0 : v0 + 1) * w + (fu < 0.5 ? u0 : u0 + 1)]; continue; }
      const a = raw[p], b = raw[p + 1], c = raw[p + w], d = raw[p + w + 1];
      if (!a || !b || !c || !d) { out[j * B + i] = (fu < 0.5 ? (fv < 0.5 ? a : c) : (fv < 0.5 ? b : d)); continue; }   // keep no-data edges crisp
      out[j * B + i] = (a * (1 - fu) + b * fu) * (1 - fv) + (c * (1 - fu) + d * fu) * fv;
    }
  }
  return out;
}
/* The same colour rules as the on-screen style, in plain JS. */
const VOID_RGB = [5, 8, 11];
function paintPixels(spec, ras, cal, st, rgba) {
  const n = rgba.length / 4;
  for (let i = 0, j = 0; i < n; i++, j += 4) { rgba[j] = VOID_RGB[0]; rgba[j + 1] = VOID_RGB[1]; rgba[j + 2] = VOID_RGB[2]; rgba[j + 3] = 255; }
  if (spec.kind === 'scl') {
    const pal = SCL.map(c => c[1][0] === '#' ? hexRgb(c[1]) : VOID_RGB), a = ras.SCL;
    for (let i = 0, j = 0; i < n; i++, j += 4) { const c = pal[a[i] | 0] || VOID_RGB; rgba[j] = c[0]; rgba[j + 1] = c[1]; rgba[j + 2] = c[2]; }
  } else if (spec.kind === 'index') {
    const rp = spec.ramp, lo = rp[0][0], hi = rp[rp.length - 1][0], lut = new Uint8Array(2048 * 3);
    for (let k = 0; k < 2048; k++) {
      const v = lo + (hi - lo) * k / 2047;
      let s = 0; while (s < rp.length - 2 && v > rp[s + 1][0]) s++;
      const t = clamp((v - rp[s][0]) / (rp[s + 1][0] - rp[s][0]), 0, 1), c0 = hexRgb(rp[s][1]), c1 = hexRgb(rp[s + 1][1]);
      for (let c = 0; c < 3; c++) lut[k * 3 + c] = Math.round(c0[c] + (c1[c] - c0[c]) * t);
    }
    const ids = specBands(spec), arrs = ids.map(id => ras[id]);
    const mul = ids.map(id => EXTRA[id] ? [EXTRA[id].scale, 0] : [cal.scale, cal.offset]), rv = {};
    for (let i = 0, j = 0; i < n; i++, j += 4) {
      let any = 0;
      for (let q = 0; q < ids.length; q++) { const v = arrs[q][i]; if (v) any = 1; rv[ids[q]] = v * mul[q][0] + mul[q][1]; }
      if (!any) continue;
      const val = spec.calc(rv), k = (isFinite(val) ? clamp(Math.round((val - lo) / (hi - lo) * 2047), 0, 2047) : 0) * 3;
      rgba[j] = lut[k]; rgba[j + 1] = lut[k + 1]; rgba[j + 2] = lut[k + 2];
    }
  } else {
    const lut = new Uint8Array(65536), span = Math.max(st.hi, st.lo + 0.01) - st.lo, ig = 1 / st.ga;
    for (let dn = 0; dn < 65536; dn++) lut[dn] = Math.round(255 * Math.pow(clamp((dn * cal.scale + cal.offset - st.lo) / span, 0, 1), ig));
    const R = ras[spec.bands[0]], G = ras[spec.bands[1]], Bl = ras[spec.bands[2]];
    for (let i = 0, j = 0; i < n; i++, j += 4) {
      if (!(R[i] || G[i] || Bl[i])) continue;
      rgba[j] = lut[clamp(Math.round(R[i]), 0, 65535)]; rgba[j + 1] = lut[clamp(Math.round(G[i]), 0, 65535)]; rgba[j + 2] = lut[clamp(Math.round(Bl[i]), 0, 65535)];
    }
  }
}
async function paintBlock(job, bx, by) {
  for (let tries = 0; ; tries++) {                                                        // a dropped connection should not end a long clip
    try { return await paintBlockOnce(job, bx, by); }
    catch (e) { if (tries >= 3 || REC.stop) throw e; await new Promise(res => setTimeout(res, 1500 * (tries + 1))); }
  }
}
async function paintBlockOnce(job, bx, by) {
  const ids = [...new Set(specBands(job.spec))], ras = {};
  await Promise.all(ids.map(async id => { ras[id] = await readBlockBand(job.scene, id, bx * BLOCK, by * BLOCK, job.plan.r); }));
  const img = new ImageData(BLOCK, BLOCK);
  paintPixels(job.spec, ras, job.scene.cal, job.st, img.data);
  trimDecoded();
  return createImageBitmap(img, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
}
/* Every block the clip will touch, in the order it is first needed, and the last frame that needs it. */
function blockSchedule(plan, Wm, Hm) {
  const first = new Map(), last = new Map(), B = BLOCK;
  for (let k = 0; k < plan.frames; k++) {
    const x0 = plan.pos[2 * k], y0 = plan.pos[2 * k + 1];
    for (let by = Math.floor(y0 / B); by <= Math.floor((y0 + Hm - 1) / B); by++) {
      for (let bx = Math.floor(x0 / B); bx <= Math.floor((x0 + Wm - 1) / B); bx++) {
        const key = bx + ',' + by;
        if (!first.has(key)) first.set(key, k);
        last.set(key, k);
      }
    }
  }
  return { order: [...first.entries()].map(([key, k]) => ({ key, first: k, last: last.get(key), bx: +key.split(',')[0], by: +key.split(',')[1] })) };
}
function blockCache(job, sched) {
  const live = new Map(), ahead = Math.max(job.fps * 4, Math.ceil(2 * BLOCK / Math.max(1, job.step)));
  let next = 0, inflight = 0;
  const start = b => {
    if (live.has(b.key)) return live.get(b.key);
    inflight++;
    const e = { b, bmp: null, p: null };
    e.p = paintBlock(job, b.bx, b.by).then(bmp => { e.bmp = bmp; return bmp; }).finally(() => { inflight--; });
    live.set(b.key, e);
    return e;
  };
  return {
    pump(k) { while (next < sched.order.length && inflight < 4 && sched.order[next].first <= k + ahead) start(sched.order[next++]); },
    async get(b) { return start(b).p; },
    evict(k) { for (const [key, e] of live) if (e.b.last < k && e.bmp) { e.bmp.close(); live.delete(key); } },
    clear() { for (const e of live.values()) if (e.bmp) e.bmp.close(); live.clear(); },
    size: () => live.size,
  };
}

/* ---- the footer ---- */
const FONT = { display: '"Barlow Condensed", "Arial Narrow", Arial, sans-serif', body: '"Barlow Semi Condensed", Arial, sans-serif', data: '"JetBrains Mono", Menlo, Consolas, monospace' };
async function loadFonts() {
  try { await Promise.race([Promise.all(['600 40px "Barlow Condensed"', '400 20px "Barlow Semi Condensed"', '600 20px "Barlow Semi Condensed"', '400 16px "JetBrains Mono"'].map(f => document.fonts.load(f))), new Promise(r => setTimeout(r, 4000))]); } catch (e) { /* system fonts then */ }
}
function footerLayout(o) {
  const fh = o.fh, pad = Math.round(fh * 0.1), L = fh - Math.round(pad * 1.2), gap = Math.round(pad * 0.6);
  const lx = o.W - pad - L, wx = lx - gap - L;                                               // the tile locator, and the where-on-Earth map left of it
  return { fh, pad, L, lx, wx, ly: o.Hm + Math.round((fh - L) / 2), textW: wx - pad * 2 };
}
/* The whole tile as the satellite saw it, in the clip's view, read from the file's smallest overview. */
async function tileThumb(job, N) {
  const ids = [...new Set(specBands(job.spec))], ras = {};
  await Promise.all(ids.map(async id => {
    const f = await openBand(job.scene, assetFor(job.scene.item, id).href), lv = f.levels[f.levels.length - 1];
    ras[id] = (await readWindow(lv.img, { window: [0, 0, lv.img.getWidth(), lv.img.getHeight()], width: N, height: N, samples: [0], resampleMethod: id === 'SCL' ? 'nearest' : 'bilinear' }))[0];
  }));
  const img = new ImageData(N, N);
  paintPixels(job.spec, ras, job.scene.cal, job.st, img.data);
  const cv = document.createElement('canvas'); cv.width = cv.height = N;
  cv.getContext('2d').putImageData(img, 0, 0);
  return cv;
}
const roundBox = (c, x, y, w, h, r) => { c.beginPath(); if (c.roundRect) c.roundRect(x, y, w, h, r); else c.rect(x, y, w, h); };
/* A small map of the region (Natural Earth country outlines, public domain) with the tile marked. */
function drawWorldInset(c, job, x, y, L, fh) {
  const E = job.scene.extent, toLL = q => ol.proj.transform(q, job.scene.code, 'EPSG:4326');
  const ctr = toLL([(E[0] + E[2]) / 2, (E[1] + E[3]) / 2]), lat0 = ctr[1], lon0 = ctr[0];
  const half = 14, k = L / (2 * half), cos = Math.max(0.2, Math.cos(lat0 * Math.PI / 180));
  const px = (lon, lat) => [x + L / 2 + ((lon - lon0) * cos) * k, y + L / 2 - (lat - lat0) * k];
  c.save();
  roundBox(c, x + 0.5, y + 0.5, L - 1, L - 1, Math.round(L * 0.04)); c.fillStyle = '#0c141c'; c.fill(); c.clip();
  if (countries) {
    c.fillStyle = '#25323d'; c.strokeStyle = 'rgba(231,228,219,.32)'; c.lineWidth = Math.max(0.6, fh / 300); c.lineJoin = 'round';
    const span = half / cos + 2;
    for (const ct of countries) {
      const g = ct.geom, polys = g.getType() === 'Polygon' ? [g.getCoordinates()] : g.getType() === 'MultiPolygon' ? g.getCoordinates() : [];
      for (const poly of polys) {
        const ring = poly[0];
        let mn = 999, mx = -999, ymn = 99, ymx = -99;
        for (const q of ring) { mn = Math.min(mn, q[0]); mx = Math.max(mx, q[0]); ymn = Math.min(ymn, q[1]); ymx = Math.max(ymx, q[1]); }
        if (ymn > lat0 + half + 2 || ymx < lat0 - half - 2) continue;
        const shift = [-360, 0, 360].reduce((best, d) => Math.abs((mn + mx) / 2 + d - lon0) < Math.abs((mn + mx) / 2 + best - lon0) ? d : best, 0);
        if (mn + shift > lon0 + span || mx + shift < lon0 - span) continue;
        c.beginPath();
        ring.forEach((q, i) => { const p = px(q[0] + shift, q[1]); i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]); });
        c.closePath(); c.fill(); c.stroke();
      }
    }
  }
  const corners = [[E[0], E[1]], [E[2], E[1]], [E[2], E[3]], [E[0], E[3]]].map(q => { const ll = toLL(q); return px(ll[0], ll[1]); });
  c.beginPath(); corners.forEach((p, i) => i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])); c.closePath();
  c.fillStyle = 'rgba(255,181,71,.35)'; c.fill(); c.strokeStyle = '#ffb547'; c.lineWidth = Math.max(1.5, fh / 120); c.stroke();
  const m = px(lon0, lat0);                                                                  // a ring so the spot reads even where the tile is tiny
  c.beginPath(); c.arc(m[0], m[1], L * 0.075, 0, Math.PI * 2); c.strokeStyle = 'rgba(255,181,71,.8)'; c.lineWidth = Math.max(1, fh / 180); c.stroke();
  c.restore();
  roundBox(c, x + 0.5, y + 0.5, L - 1, L - 1, Math.round(L * 0.04)); c.strokeStyle = '#3a4754'; c.lineWidth = 1; c.stroke();
}
async function drawStaticFooter(job) {
  const o = job.o, lay = footerLayout(o), cv = document.createElement('canvas');
  cv.width = o.W; cv.height = lay.fh;
  const c = cv.getContext('2d'), fh = lay.fh, pad = lay.pad, x = pad;
  c.fillStyle = '#0a0f14'; c.fillRect(0, 0, o.W, fh);
  c.fillStyle = '#27333e'; c.fillRect(0, 0, o.W, Math.max(1, Math.round(fh / 180)));
  c.textBaseline = 'alphabetic';
  c.font = '600 ' + Math.round(fh * 0.055) + 'px ' + FONT.body; c.fillStyle = '#9aa5b0';
  try { c.letterSpacing = Math.round(fh * 0.016) + 'px'; } catch (e) { /* older canvas */ }
  c.fillText('SENTINEL FLYOVER', x, fh * 0.2);
  try { c.letterSpacing = '0px'; } catch (e) { /* older canvas */ }
  let ts = Math.round(fh * 0.25);                                                           // the title, shrunk to fit
  c.font = '600 ' + ts + 'px ' + FONT.display;
  while (ts > fh * 0.12 && c.measureText(job.title).width > lay.textW) { ts--; c.font = '600 ' + ts + 'px ' + FONT.display; }
  c.fillStyle = '#e7e4db'; c.fillText(job.title, x - ts * 0.03, fh * 0.635);
  let ms = Math.round(fh * 0.075);                                                          // the details line, shrunk to fit
  const metaW = size => { let w = 0; for (const [k, v] of job.meta) { c.font = '400 ' + size + 'px ' + FONT.data; w += c.measureText(k + ' ').width; c.font = '500 ' + size + 'px ' + FONT.data; w += c.measureText(v + '   ').width; } return w; };
  while (ms > fh * 0.05 && metaW(ms) > lay.textW) ms--;
  let mx = x;
  for (const [k, v] of job.meta) {
    c.font = '400 ' + ms + 'px ' + FONT.data; c.fillStyle = '#9aa5b0'; c.fillText(k, mx, fh * 0.79); mx += c.measureText(k + ' ').width;
    c.font = '500 ' + ms + 'px ' + FONT.data; c.fillStyle = '#e7e4db'; c.fillText(v, mx, fh * 0.79); mx += c.measureText(v + '   ').width;
  }
  let cs = Math.round(fh * 0.078);                                                          // the moving coordinates line: size it once for the longest country name it will show
  const sample = (job.fallbackCountry || 'United States of America') + '  ·  88.8888°S 188.8888°W';
  c.font = '500 ' + cs + 'px ' + FONT.data;
  while (cs > fh * 0.05 && c.measureText(sample).width > lay.textW) { cs--; c.font = '500 ' + cs + 'px ' + FONT.data; }
  job.coordFont = '500 ' + cs + 'px ' + FONT.data;
  c.font = '400 ' + Math.round(fh * 0.058) + 'px ' + FONT.data; c.fillStyle = '#c9c6bd';
  c.fillText('Contains modified Copernicus Sentinel data ' + job.year, x, fh * 0.92);
  drawWorldInset(c, job, lay.wx, lay.ly - o.Hm, lay.L, fh);
  // the tile locator: this tile's own picture, its outline and the route this clip will fly
  const lx = lay.lx, ly = lay.ly - o.Hm, L = lay.L, plan = job.plan, s = L / Math.max(plan.GW, plan.GH);
  const ox = lx + (L - plan.GW * s) / 2, oy = ly + (L - plan.GH * s) / 2;
  job.loc = { s, ox, oy: oy + o.Hm };
  c.save();
  roundBox(c, lx + 0.5, ly + 0.5, L - 1, L - 1, Math.round(L * 0.04)); c.fillStyle = '#121a22'; c.fill(); c.clip();
  try {
    const thumb = await tileThumb(job, Math.max(32, Math.round(Math.max(plan.GW, plan.GH) * s * 2)));   // read at twice the size and let the canvas average it down
    c.imageSmoothingEnabled = true; c.imageSmoothingQuality = 'high'; c.drawImage(thumb, ox, oy, plan.GW * s, plan.GH * s);
    c.fillStyle = 'rgba(5,8,11,.18)'; c.fillRect(lx, ly, L, L);                               // a touch darker so the route reads on snow and cloud
  } catch (e) { /* the outline still shows where the clip is */ }
  c.restore();
  roundBox(c, lx + 0.5, ly + 0.5, L - 1, L - 1, Math.round(L * 0.04)); c.strokeStyle = '#3a4754'; c.lineWidth = 1; c.stroke();
  const fp = job.scene.footprint, E = job.scene.extent, r = plan.r;
  c.strokeStyle = 'rgba(231,228,219,.55)'; c.lineWidth = Math.max(1, fh / 160);
  const ring = fp && fp.getType() === 'Polygon' ? fp.getCoordinates()[0] : fp && fp.getType() === 'MultiPolygon' ? fp.getCoordinates()[0][0] : ol.geom.Polygon.fromExtent(E).getCoordinates()[0];
  c.beginPath(); ring.forEach((q, i) => { const px = ox + (q[0] - E[0]) / r * s, py = oy + (E[3] - q[1]) / r * s; i ? c.lineTo(px, py) : c.moveTo(px, py); }); c.closePath(); c.stroke();
  const every = Math.max(1, Math.floor(plan.frames / 600));                                // the route this clip will fly
  const route = () => { c.beginPath(); for (let k = 0; k < plan.frames; k += every) { const px = ox + (plan.pos[2 * k] + plan.hw) * s, py = oy + (plan.pos[2 * k + 1] + plan.hh) * s; k ? c.lineTo(px, py) : c.moveTo(px, py); } c.stroke(); };
  c.lineJoin = 'round';
  c.strokeStyle = 'rgba(5,8,11,.6)'; c.lineWidth = Math.max(2, fh / 70); route();
  c.strokeStyle = 'rgba(255,255,255,.6)'; c.lineWidth = Math.max(1, fh / 200); route();
  return cv;
}
function drawLiveFooter(ctx, job, k) {
  const o = job.o, lay = footerLayout(o), fh = lay.fh, plan = job.plan, E = job.scene.extent, r = plan.r;
  const cx = plan.pos[2 * k] + plan.hw, cy = plan.pos[2 * k + 1] + plan.hh;
  const ll = ol.proj.transform([E[0] + cx * r, E[3] - cy * r], job.scene.code, 'EPSG:4326');
  if (k % job.fps === 0 || job.country === undefined) job.country = countryAt(ll) || job.fallbackCountry || '';
  ctx.font = job.coordFont; ctx.fillStyle = '#ffb547'; ctx.textBaseline = 'alphabetic';
  ctx.fillText((job.country ? job.country + '  ·  ' : '') + fmtLL(ll), lay.pad, o.Hm + fh * 0.345);
  const { s, ox, oy } = job.loc;
  ctx.strokeStyle = '#ffb547'; ctx.lineWidth = Math.max(1.5, fh / 110); ctx.lineJoin = 'round';
  const step = Math.max(1, Math.floor(k / 400));
  ctx.beginPath(); ctx.moveTo(ox + (plan.pos[0] + plan.hw) * s, oy + (plan.pos[1] + plan.hh) * s);
  for (let i = step; i <= k; i += step) ctx.lineTo(ox + (plan.pos[2 * i] + plan.hw) * s, oy + (plan.pos[2 * i + 1] + plan.hh) * s);
  ctx.lineTo(ox + cx * s, oy + cy * s); ctx.stroke();
  const bw = Math.max(3, plan.hw * 2 * s), bh = Math.max(3, plan.hh * 2 * s), bx = ox + cx * s - bw / 2, by = oy + cy * s - bh / 2;
  ctx.fillStyle = 'rgba(255,255,255,.14)'; ctx.fillRect(bx, by, bw, bh);
  ctx.strokeStyle = 'rgba(5,8,11,.75)'; ctx.lineWidth = Math.max(2.5, fh / 80); ctx.strokeRect(bx, by, bw, bh);   // a dark edge so the frame box shows on bright snow
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = Math.max(1.2, fh / 150); ctx.strokeRect(bx, by, bw, bh);
}

/* ---- encoders ---- */
let libs = {};
const loadLib = async (key, url) => libs[key] || (libs[key] = await import(url));
async function pickCodec(W, H, fps, bitrate) {
  if (!window.VideoEncoder) return null;
  const cands = [['avc', 'avc1.640033'], ['avc', 'avc1.64002A'], ['avc', 'avc1.4D0033'], ['avc', 'avc1.42E033'], ['vp9', 'vp09.00.41.08'], ['av1', 'av01.0.09M.08']];
  for (const [mux, codec] of cands) {
    const cfg = { codec, width: W, height: H, bitrate, framerate: fps, latencyMode: 'quality', bitrateMode: 'variable' };
    if (mux === 'avc') cfg.avc = { format: 'avc' };
    try { if ((await VideoEncoder.isConfigSupported(cfg)).supported) return { mux, cfg }; } catch (e) { /* try the next one */ }
  }
  return null;
}
/* An output file: streamed into the chosen folder, or kept in memory and offered as a download. */
async function openSink(name) {
  if (REC.dir) {
    const fh = await REC.dir.getFileHandle(name, { create: true });
    return { name, fh, stream: await fh.createWritable() };
  }
  return { name };
}
async function dropSink(sink) {
  try { if (sink.stream) await sink.stream.abort(); } catch (e) { /* already closed */ }
  try { if (sink.fh) await REC.dir.removeEntry(sink.name); } catch (e) { /* nothing to remove */ }
}
/* The colours a GIF may use: sampled from the whole scene at overview detail, plus the footer. */
async function gifPalette(job, footer, quantize) {
  const ids = [...new Set(specBands(job.spec))], ras = {}, N = 200;
  await Promise.all(ids.map(async id => {
    const f = await openBand(job.scene, assetFor(job.scene.item, id).href), lv = f.levels[f.levels.length - 1];
    ras[id] = (await readWindow(lv.img, { window: [0, 0, lv.img.getWidth(), lv.img.getHeight()], width: N, height: N, samples: [0], resampleMethod: 'nearest' }))[0];
  }));
  const px = new Uint8ClampedArray(N * N * 4);
  paintPixels(job.spec, ras, job.scene.cal, job.st, px);
  const fp = footer.getContext('2d').getImageData(0, 0, footer.width, footer.height).data;
  const all = new Uint8ClampedArray(px.length * 3 + fp.length);
  all.set(px); all.set(px, px.length); all.set(px, px.length * 2); all.set(fp, px.length * 3);      // weight the imagery over the footer
  return quantize(all, 256);
}

/* Render one clip. When the scene runs out of ground before the clip is long enough, the picture fades through
   dark into the next tile east (job.chain finds it) and carries on. Resolves to { blob?, bytes, frames, seconds,
   codec, segments } or null when stopped. */
async function renderClip(job, onProgress) {
  const o = job.o, total = o.frames, B = BLOCK, FADE = Math.max(4, Math.round(o.fps * 0.6));
  await loadFonts();
  const cv = job.canvas, ctx = cv.getContext('2d', { alpha: false, willReadFrequently: true });   // frames are composed on the CPU and copied out every frame
  cv.width = o.W; cv.height = o.H;
  ctx.imageSmoothingEnabled = false;
  let enc = null, muxer = null, gif = null, gifLib = null, palette = null, err = null, codec = '', cache = null;
  const sink = await openSink(job.file), segments = [];
  const cleanup = () => { if (cache) cache.clear(); for (const sg of segments) if (sg !== job && sg.release) sg.release(); };
  try {
    if (o.gif) {
      gifLib = await loadLib('gif', CFG.gifLib);
      gif = gifLib.GIFEncoder();
      palette = await gifPalette(job, await drawStaticFooter(job), gifLib.quantize);
      codec = 'GIF';
    } else {
      const pick = await pickCodec(o.W, o.H, o.fps, o.bitrate);
      if (!pick) throw new Error('this browser cannot encode video. Chrome, Edge, Safari 16.4+ and recent Firefox can; GIF works everywhere');
      const M = await loadLib('mp4', CFG.muxerLib);
      const target = sink.stream ? new M.FileSystemWritableFileStreamTarget(sink.stream) : new M.ArrayBufferTarget();
      muxer = new M.Muxer({ target, video: { codec: pick.mux, width: o.W, height: o.H, frameRate: o.fps }, fastStart: sink.stream ? false : 'in-memory', firstTimestampBehavior: 'offset' });
      enc = new VideoEncoder({ output: (chunk, meta) => muxer.addVideoChunk(chunk, meta), error: e => { err = e; } });
      enc.configure(pick.cfg);
      codec = pick.cfg.codec.startsWith('avc') ? 'H.264' : pick.mux === 'vp9' ? 'VP9' : 'AV1';
    }
    const t0 = performance.now();
    let seg = job, done = 0;
    while (seg) {
      segments.push(seg);
      const plan = seg.plan, footer = await drawStaticFooter(seg);
      const sched = blockSchedule(plan, o.Wm, o.Hm), byKey = new Map(sched.order.map(b => [b.key, b]));
      cache = blockCache(seg, sched);
      const remaining = total - done, n = Math.min(plan.frames, remaining);
      const more = plan.frames < remaining && seg.chain;                                  // this scene ends before the clip should
      const nextP = more ? seg.chain(seg).catch(() => null) : null;
      let fadeOut = false;
      for (let k = 0; k < n; k++) {
        if (err) throw err;
        if (REC.stop) { if (enc) enc.close(); await dropSink(sink); return null; }
        if (more && k === Math.max(0, n - FADE)) { onProgress(done / total, 'Finding the tile next door…'); fadeOut = !!(await nextP); }
        cache.pump(k);
        const x0 = plan.pos[2 * k], y0 = plan.pos[2 * k + 1];
        for (let by = Math.floor(y0 / B); by <= Math.floor((y0 + o.Hm - 1) / B); by++) {
          for (let bx = Math.floor(x0 / B); bx <= Math.floor((x0 + o.Wm - 1) / B); bx++) {
            ctx.drawImage(await cache.get(byKey.get(bx + ',' + by)), bx * B - x0, by * B - y0);
          }
        }
        const fade = Math.min(1, done > 0 ? (k + 1) / FADE : 1, fadeOut ? (n - k) / FADE : 1);
        if (fade < 1) { ctx.globalAlpha = 1 - fade; ctx.fillStyle = '#05080b'; ctx.fillRect(0, 0, o.Wm, o.Hm); ctx.globalAlpha = 1; }
        ctx.drawImage(footer, 0, o.Hm);
        drawLiveFooter(ctx, seg, k);
        if (REC.onFrame) REC.onFrame(k, ctx, seg, fade);                                     // test hook
        const f = done + k;
        if (REC.dry) { /* timing runs only */ } else if (gif) {
          const px = ctx.getImageData(0, 0, o.W, o.H).data;
          gif.writeFrame(gifLib.applyPalette(px, palette), o.W, o.H, f === 0 ? { palette, delay: 1000 / o.fps, repeat: 0 } : { delay: 1000 / o.fps });
        } else {
          const vf = new VideoFrame(cv, { timestamp: Math.round(f * 1e6 / o.fps), duration: Math.round(1e6 / o.fps) });
          enc.encode(vf, { keyFrame: f % (o.fps * 2) === 0 || k === 0 });
          vf.close();
          while (enc.encodeQueueSize > 6 && !err) await encoderDrained(enc);
        }
        cache.evict(k);
        if (f % 8 === 0) {
          const el = (performance.now() - t0) / 1000, eta = f > 30 ? el / f * (total - f) : null;
          onProgress(f / total, 'Frame ' + (f + 1).toLocaleString('en-US') + ' of ' + total.toLocaleString('en-US') + (segments.length > 1 ? ' (tile ' + segments.length + ')' : '') + (eta != null ? '  ·  about ' + fmtDur(eta + 1) + ' left' : ''));
          await yieldUI();
        }
      }
      cache.clear(); cache = null;
      done += n;
      seg = fadeOut ? await nextP : null;
      if (!seg && nextP) nextP.then(sg => { if (sg && sg.release) sg.release(); });
    }
    onProgress(1, 'Finishing the file…');
    let blob = null, bytes = 0;
    if (gif) {
      gif.finish();
      const out = gif.bytes(); bytes = out.length;
      if (sink.stream) { await sink.stream.write(out); await sink.stream.close(); } else blob = new Blob([out], { type: 'image/gif' });
    } else {
      await enc.flush(); enc.close();
      if (err) throw err;
      muxer.finalize();
      if (sink.stream) { await sink.stream.close(); bytes = (await sink.fh.getFile()).size; }
      else { blob = new Blob([muxer.target.buffer], { type: 'video/mp4' }); bytes = blob.size; }
    }
    return { blob, bytes, frames: done, seconds: done / o.fps, codec, fh: sink.fh || null, segments: segments.map(sg => ({ title: sg.title, date: sg.date, year: sg.year, sat: sg.sat, tile: sg.tile })) };
  } catch (e) {
    try { if (enc && enc.state !== 'closed') enc.close(); } catch (x) { /* closed */ }
    await dropSink(sink);
    throw e;
  } finally { cleanup(); }
}
/* The next segment of a long clip: the tile east of where this one ended, entered from the same edge. */
async function chainNext(prev) {
  const plan = prev.plan, last = plan.frames - 1, atBottom = plan.pos[2 * last + 1] + plan.hh > plan.GH / 2;
  const sc = await sceneNextDoor(prev.scene, atBottom);
  if (!sc) return null;
  const E = sc.extent;
  try {
    const job = await clipJob(sc, prev.spec, Object.assign([E[0], atBottom ? E[1] : E[3]], { down: !atBottom }), false, '', prev.o);
    const mid = Math.floor(job.plan.frames / 2), c = [E[0] + (job.plan.pos[2 * mid] + job.plan.hw) * job.plan.r, E[3] - (job.plan.pos[2 * mid + 1] + job.plan.hh) * job.plan.r];
    const ll = ol.proj.transform(c, sc.code, 'EPSG:4326'), nm = await nameNear(ll[0], ll[1]);
    job.title = (nm && nm.name) || countryAt(ll) || sceneCountry(sc) || 'Sentinel-2';
    job.canvas = prev.canvas; job.chain = prev.chain;
    job.release = () => { if (!slots.some(s => s.scene === sc)) releaseScene(sc); };
    recentTiles.push(gridCode(sc.item)); if (recentTiles.length > 10) recentTiles.shift();
    return job;
  } catch (e) { releaseScene(sc); return null; }
}

/* Everything one clip needs, for a scene and a starting point. */
async function clipJob(scene, spec, at, fit, title, o) {
  const st0 = await sceneStretch(scene, spec);
  const st = st0 ? { lo: st0.lo, hi: Math.max(st0.hi / gainFactor(), st0.lo + 0.01), ga: st0.ga } : null;
  const plan = planClip(scene, o, at, fit);
  if (!plan) throw new Error('no recorded ground to fly over here');
  const p = scene.item.properties, sat = (p.platform || '').replace('sentinel-', 'Sentinel-').replace(/[abc]$/, x => x.toUpperCase());
  return {
    scene, spec, st, plan, o, title, fps: o.fps, step: o.step, year: p.datetime.slice(0, 4), date: p.datetime.slice(0, 10), tile: gridCode(scene.item).replace('MGRS-', ''),
    chain: chainNext,
    meta: [['Acquired', fmtDate(new Date(p.datetime))], ['By', sat], ['View', spec.name]], sat,
    fallbackCountry: sceneCountry(scene), canvas: REC.canvas,
  };
}
function captionText(job, place, ll, res) {
  const segs = res.segments, years = [...new Set(segs.map(sg => sg.year))].join(', ');
  const when = segs.length > 1
    ? ', crossing ' + segs.length + ' tiles (' + segs.map(sg => sg.title + ', ' + fmtDate(new Date(sg.date + 'T00:00:00Z')) + ' by ' + sg.sat).join('; ') + ').'
    : ', acquired ' + fmtDate(new Date(job.scene.item.properties.datetime)) + ' by ' + job.sat + '.';
  return [
    job.title + (place && place.region && place.region !== job.title ? ', ' + place.region : ''),
    fmtDur(res.seconds) + ' Sentinel-2 flyover in ' + job.spec.name.toLowerCase() + when,
    fmtLL(ll),
    'Contains modified Copernicus Sentinel data ' + years,
    '#Sentinel2 #EarthObservation #RemoteSensing',
  ].join('\n') + '\n';
}
async function writeFile(name, blob) {
  if (REC.dir) { const fh = await REC.dir.getFileHandle(name, { create: true }), w = await fh.createWritable(); await w.write(blob); await w.close(); return; }
  const a = h('a', { href: URL.createObjectURL(blob), download: name }); document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}

/* ---- the panel ---- */
const recLog = (text, cls) => { const li = h('li', { class: cls || '' }, text); $('#rec-log').prepend(li); return li; };
function recProgress(frac, msg) { $('#rec-bar i').style.width = (frac * 100).toFixed(1) + '%'; if (msg != null) $('#rec-msg').textContent = msg; }
function renderRec() {
  const r = P.rec;
  $('#rec-len').value = r.len; $('#rec-unit').value = String(r.unit); $('#rec-fmt').value = r.fmt; $('#rec-n').value = r.n;
  $('#rec-places').value = r.places; $('#rec-view').value = r.view; $('#rec-fps').value = String(r.fps); $('#rec-q').value = r.q;
  const o0 = recOpts();
  const size = $('#rec-size'); size.textContent = '';
  for (const [k, v] of Object.entries(REC_SIZES)) size.append(h('option', { value: k }, v.name + '  ·  ' + v[o0.gif ? 'gif' : 'mp4'].join(' × ')));
  size.value = r.size;
  const res = $('#rec-res'); res.textContent = '';
  for (const m of [5, 10, 20, 40]) res.append(h('option', { value: m }, m + ' m per pixel' + (m === 10 ? ' (native)' : m === 5 ? ' (enlarged)' : '')));
  res.value = String(r.res);
  const sp = $('#rec-step'); sp.textContent = '';
  for (const s of [1, 2, 3, 4, 6]) sp.append(h('option', { value: s }, s + ' px a frame  ·  ' + Math.round(s * o0.r * o0.fps * 3.6).toLocaleString('en-US') + ' km/h'));
  sp.value = String(r.step);
  for (const id of ['#rec-fps-l', '#rec-fps-r', '#rec-q-l', '#rec-q-r']) $(id).hidden = o0.gif;
  const est = $('#rec-est'), lines = [], warn = [];
  if (o0.gif) {
    const bytes = o0.W * o0.Hm * o0.frames * GIF_BYTES_PER_PX + o0.W * o0.fh * 0.05 * o0.frames;
    lines.push(o0.frames + ' frames at ' + o0.fps + ' fps, roughly ' + fmtMB(bytes));
    if (r.len * r.unit > GIF_MAX_S) warn.push('GIFs are capped at ' + GIF_MAX_S + ' s.');
    if (bytes > 15 * 1048576) warn.push('Over X\'s 15 MB GIF limit. MP4 is far smaller for the same clip.');
  } else {
    const bytes = o0.bitrate * o0.seconds / 8;
    lines.push(o0.frames.toLocaleString('en-US') + ' frames, about ' + fmtMB(bytes) + ' at ' + (o0.bitrate / 1e6).toFixed(1) + ' Mbit/s');
    if (o0.seconds > 140) warn.push('X takes videos up to 2 min 20 s; longer clips suit YouTube.');
    if (!REC.dir && bytes > 400 * 1048576) warn.push('A clip this size should go straight to a folder rather than memory: choose a folder first.');
  }
  const a = slots[act];
  if (a.scene) {
    const plan = planClip(a.scene, o0, herePoint(a), false);
    if (plan && plan.room < o0.frames - 1) lines.push('This tile has about ' + fmtDur(plan.room / o0.fps) + ' of ground from here at this detail and speed; after that the clip fades into the tile next door.');
  }
  est.textContent = lines.join(' ') + (warn.length ? '  ' + warn.join(' ') : '');
  est.className = 'note' + (warn.length ? ' warn' : '');
  $('#rec-folder-name').textContent = REC.dir ? 'Saving to: ' + REC.dir.name : (window.showDirectoryPicker ? 'No folder chosen: files download one at a time.' : 'This browser downloads files one at a time.');
  $('#rec-folder').hidden = !window.showDirectoryPicker;
  $('#rec-here').disabled = $('#rec-batch').disabled = REC.busy;
}
function herePoint(a) {
  const c = a.map.getView().getCenter(), ahead = legPos(a.leg, Math.min(a.leg.total, a.s + 50));
  return Object.assign([c[0], c[1]], { down: ahead[1] <= c[1] - 0.01 || Math.abs(ahead[1] - c[1]) < 0.01 });
}
function openRec(show) {
  const el = $('#rec');
  el.hidden = show === undefined ? !el.hidden : !show;
  if (!el.hidden) renderRec();
}
async function withRecording(fn) {
  if (REC.busy) return;
  REC.busy = true; REC.stop = false;
  const wasPlaying = P.playing; setPlaying(false); $('#paused').hidden = true;
  document.body.classList.add('recording');
  let lock = null;
  try { lock = await navigator.wakeLock.request('screen'); } catch (e) { /* the screen may sleep; recording continues */ }
  $('#rec-run').hidden = false; $('#rec-save').hidden = true; $('#rec-stop').hidden = false; $('#rec-stop').textContent = 'Stop';
  const pv = $('#rec-preview'); pv.textContent = ''; REC.canvas = h('canvas', { 'aria-label': 'Frame being recorded' }); pv.append(REC.canvas);
  renderRec();
  try { await fn(); }
  finally {
    REC.busy = false; $('#rec-stop').hidden = true;
    document.body.classList.remove('recording');
    try { if (lock) await lock.release(); } catch (e) { /* released */ }
    setPlaying(wasPlaying);
    renderRec();
  }
}
function showResult(res, name) {
  const pv = $('#rec-preview'); pv.textContent = '';
  if (REC.url) URL.revokeObjectURL(REC.url);
  const show = blob => { REC.url = URL.createObjectURL(blob); pv.append(blob.type === 'image/gif' ? h('img', { src: REC.url, alt: 'Recorded GIF' }) : h('video', { src: REC.url, controls: true, loop: true, muted: true, autoplay: true, playsinline: true })); return REC.url; };
  if (res.blob) { const a = $('#rec-save'); a.href = show(res.blob); a.download = name; a.hidden = false; }
  else if (res.fh) res.fh.getFile().then(show).catch(() => {});
  window.flyover.lastClip = res;
}
async function recordHere() {
  const a = slots[act];
  if (!a.scene) { toast('Wait for the first scene to appear, then record.'); return; }
  await withRecording(async () => {
    const o = recOpts(), sc = a.scene, spec = MODE[a.modeId] || MODE[P.modeId];
    let title = sc.place && sc.place.name;
    if (!title) {                                                                           // name the clip after the nearest town, as the batch does
      recProgress(0, 'Looking up a place name…');
      const ll = ol.proj.transform(a.map.getView().getCenter(), sc.code, 'EPSG:4326'), nm = await nameNear(ll[0], ll[1]);
      title = (nm && nm.name) || sc.label || sceneCountry(sc) || 'Sentinel-2';
    }
    const ext = o.gif ? '.gif' : '.mp4';
    let job;
    try { job = await clipJob(sc, spec, herePoint(a), false, title, o); }
    catch (e) { recProgress(0, 'Could not plan the clip: ' + e.message); return; }
    const name = 'sentinel-flyover-' + slug(title) + '-' + slug(spec.name) + '-' + job.date + '-' + Date.now().toString(36).slice(-4) + ext;
    job.file = name;
    recProgress(0, 'Starting…');
    try {
      const res = await renderClip(job, recProgress);
      if (!res) { recProgress(0, 'Stopped. Nothing was saved.'); return; }
      const c = job.plan.pos, E = sc.extent;
      const ll = ol.proj.transform([E[0] + (c[0] + job.plan.hw) * o.r, E[3] - (c[1] + job.plan.hh) * o.r], sc.code, 'EPSG:4326');
      if (REC.dir) await writeFile(name.replace(/\.\w+$/, '.txt'), new Blob([captionText(job, sc.place, ll, res)], { type: 'text/plain' }));
      recProgress(1, (REC.dir ? 'Saved ' + name + ' to ' + REC.dir.name : 'Done: ' + name) + '  ·  ' + fmtDur(res.seconds) + ', ' + fmtMB(res.bytes) + ', ' + res.codec + (res.frames < o.frames ? '  ·  ended early: no more land in reach' : '') + (res.segments.length > 1 ? '  ·  crossed ' + res.segments.length + ' tiles' : ''));
      showResult(res, name);
      if (res.codec !== 'H.264' && res.codec !== 'GIF') recLog('This browser could only make ' + res.codec + ' video. It plays in browsers and on YouTube, but X wants H.264: record in Chrome or Edge for posting there.', 'skip');
    } catch (e) { recProgress(0, 'Recording failed: ' + ((e && e.message) || e)); }
  });
}
function randomLandPoint() {
  for (let i = 0; i < 400; i++) {
    const lon = Math.random() * 360 - 180, lat = Math.asin(Math.random() * 1.75 - 0.82) * 180 / Math.PI;    // equal-area, roughly 55S to 68N
    if (!countries || countryAt([lon, lat])) return [lon, lat];
  }
  return [Math.random() * 360 - 180, Math.random() * 100 - 40];
}
async function nameNear(lon, lat) {
  try {
    const f = ((await (await fetch(CFG.photon + '/reverse?lon=' + lon + '&lat=' + lat)).json()).features || [])[0];
    if (!f) return null;
    const p = f.properties, town = p.city || p.town || p.village || p.hamlet || p.district || p.county || p.state;
    return town ? { name: 'Near ' + town, region: [...new Set([p.county, p.state, p.country])].filter(x => x && x !== town).join(', ') } : null;
  } catch (e) { return null; }
}
const csvCell = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
async function runBatch() {
  if (window.showDirectoryPicker && !REC.dir) { toast('Choose a folder first, so the clips land in one place.'); return; }
  await withRecording(async () => {
    const r = P.rec, n = clamp(+r.n || 5, 1, 50), o = recOpts(), ext = o.gif ? '.gif' : '.mp4';
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, ''), rows = [];
    const composites = MODES.filter(m => m.kind === 'rgb');
    $('#rec-log').textContent = '';
    let made = 0, tries = 0;
    while (made < n && tries < n * 4 && !REC.stop) {
      tries++;
      const curated = r.places === 'curated' || (r.places === 'mix' && tries % 2 === 1);
      let place, lon, lat;
      if (curated) { place = nextPlace(); lon = place.lon; lat = place.lat; }
      else { [lon, lat] = randomLandPoint(); place = { name: null, region: null, mode: null }; }
      const where = place.name || fmtLL([lon, lat]), line = recLog('Looking at ' + where + '…');
      recProgress(0, 'Clip ' + (made + 1) + ' of ' + n + ': looking at ' + where);
      let passes;
      try { passes = await findPasses(lon, lat); } catch (e) { line.textContent = 'Catalog unreachable, retrying…'; await new Promise(res => setTimeout(res, 3000)); continue; }
      if (!passes.length) { line.textContent = 'Skipped ' + where + ': no clear pass'; line.className = 'skip'; continue; }
      if (!place.name) { const nm = await nameNear(lon, lat); if (nm) Object.assign(place, nm); }
      const spec = r.view === 'current' ? MODE[P.modeId] : r.view === 'true' ? MODE.true : r.view === 'random' ? composites[Math.floor(Math.random() * composites.length)] : (MODE[place.mode] || MODE[P.modeId]);
      let scene = null;
      try {
        scene = sceneAt(passes[0], place.name ? place : null, lon, lat);
        const title = place.name || sceneCountry(scene) || fmtLL([lon, lat]);
        const job = await clipJob(scene, spec, Object.assign([scene.from.x, scene.from.y], { down: true }), true, title, o);
        const cover = clipCoverage(scene, job.plan);
        if (cover < 0.85) throw new Error('the route runs off the edge of the recorded swath');
        const base = String(made + 1).padStart(2, '0') + '-' + slug(title) + '-' + slug(spec.name) + '-' + job.date;
        job.file = base + ext;
        line.textContent = 'Recording ' + where + '…';
        const res = await renderClip(job, (f, msg) => recProgress(f, 'Clip ' + (made + 1) + ' of ' + n + ', ' + title + ': ' + msg));
        if (!res) break;
        made++;
        const ll = [lon, lat];
        await writeFile(base + '.txt', new Blob([captionText(job, place, ll, res)], { type: 'text/plain' }));
        if (res.blob) await writeFile(job.file, res.blob);
        rows.push([job.file, title, place.region || '', lat.toFixed(5), lon.toFixed(5), spec.name, res.segments.map(sg => sg.date).join(' '), res.segments.map(sg => sg.sat).join(' '), res.segments.map(sg => sg.tile).join(' '), res.seconds.toFixed(1), o.fps, o.r, o.W, o.H, (res.bytes / 1048576).toFixed(2), res.codec, [...new Set(res.segments.map(sg => sg.year))].join(' ')]);
        line.textContent = 'Saved ' + job.file + ' (' + fmtDur(res.seconds) + ', ' + fmtMB(res.bytes) + (res.codec === 'H.264' || res.codec === 'GIF' ? '' : ', ' + res.codec) + ')'; line.className = 'ok';
      } catch (err) {
        line.textContent = 'Skipped ' + where + ': ' + ((err && err.message) || err); line.className = 'skip';
      } finally { if (scene && !slots.some(s => s.scene === scene)) releaseScene(scene); }
    }
    if (rows.length) {
      const head = ['file', 'place', 'region', 'lat', 'lon', 'view', 'acquired', 'satellite', 'tiles', 'seconds', 'fps', 'm_per_px', 'width', 'height', 'mb', 'codec', 'credit_year'];
      await writeFile('captions-' + stamp + '.csv', new Blob([[head, ...rows].map(x => x.map(csvCell).join(',')).join('\n') + '\n'], { type: 'text/csv' }));
    }
    recProgress(1, (REC.stop ? 'Stopped. ' : 'Done. ') + made + ' clip' + (made === 1 ? '' : 's') + ' saved' + (REC.dir ? ' to ' + REC.dir.name : ' to your downloads') + '.');
    recLog((REC.stop ? 'Stopped. ' : 'Done. ') + made + ' saved.', 'ok');
  });
}
const recBind = (id, key, num) => $(id).addEventListener('change', e => { P.rec[key] = num ? +e.target.value : e.target.value; if (key === 'len') P.rec.len = clamp(Math.round(+e.target.value || 1), 1, 600); save(); renderRec(); });
recBind('#rec-len', 'len', true); recBind('#rec-unit', 'unit', true); recBind('#rec-fmt', 'fmt'); recBind('#rec-size', 'size'); recBind('#rec-res', 'res', true);
recBind('#rec-step', 'step', true); recBind('#rec-fps', 'fps', true); recBind('#rec-q', 'q'); recBind('#rec-n', 'n', true); recBind('#rec-places', 'places'); recBind('#rec-view', 'view');
$('#rec-len').addEventListener('input', e => { if (+e.target.value > 0) { P.rec.len = clamp(Math.round(+e.target.value), 1, 600); save(); renderRec(); } });
$('#b-rec').addEventListener('click', () => openRec());
$('#rec-close').addEventListener('click', () => openRec(false));
$('#rec-here').addEventListener('click', recordHere);
$('#rec-batch').addEventListener('click', runBatch);
$('#rec-stop').addEventListener('click', () => { REC.stop = true; $('#rec-stop').textContent = 'Stopping…'; });
$('#rec-folder').addEventListener('click', async () => {
  try { REC.dir = await window.showDirectoryPicker({ id: 'sentinel-clips', mode: 'readwrite' }); } catch (e) { /* cancelled */ }
  renderRec();
});
if (!REC_SIZES[P.rec.size]) P.rec.size = 'portrait';

/* ================= picking a place on a map =================
   Only for choosing where to fly. These basemaps never go into recorded clips, so their licences (EOX Sentinel-2
   cloudless is free for non-commercial use, CC BY-NC-SA) stay with the picker. */
const EOX = id => 'https://tiles.maps.eox.at/wmts/1.0.0/' + id + '/default/g/{z}/{y}/{x}.' + (/^(osm|s2|terrain)/.test(id) ? 'jpg' : 'png');
const PICK_BASES = {
  street: { url: EOX('osm_3857'), max: 18, credit: 'Map data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors, rendering © <a href="https://maps.eox.at" target="_blank" rel="noopener">EOX</a>.' },
  sat: { url: EOX('s2cloudless-2025_3857'), max: 14, credit: '<a href="https://cloudless.eox.at" target="_blank" rel="noopener">EOxCloudless 2025</a> by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2025), CC BY-NC-SA 4.0, non-commercial use.' },
  terrain: { url: EOX('terrain-light_3857'), max: 16, credit: 'Terrain light: data © OpenStreetMap contributors and others, rendering © <a href="https://maps.eox.at" target="_blank" rel="noopener">EOX</a>.' },
};
let picker = null;
async function searchPlaces(q) {
  const r = await fetch(CFG.photon + '/api/?limit=7&q=' + encodeURIComponent(q));
  if (!r.ok) throw new Error('place search answered ' + r.status);
  return ((await r.json()).features || []).map(f => {
    const p = f.properties, [lon, lat] = f.geometry.coordinates;
    const near = p.city || p.town || p.village || p.hamlet || p.district || p.county || p.state;
    return { name: p.name || near || 'Unnamed place', region: [...new Set([p.county, p.state, p.country])].filter(x => x && x !== p.name).join(', '), kind: (p.osm_value || '').replace(/_/g, ' '), lon, lat, extent: p.extent };
  });
}
function buildPicker() {
  const bases = {};
  for (const [k, b] of Object.entries(PICK_BASES)) bases[k] = new ol.layer.Tile({ visible: k === 'street', source: new ol.source.XYZ({ url: b.url, maxZoom: b.max, transition: 150 }) });
  const labels = new ol.layer.Tile({ visible: false, source: new ol.source.XYZ({ url: EOX('overlay_bright_3857'), maxZoom: 14 }) });
  const marks = new ol.source.Vector();
  const m = new ol.Map({
    target: 'pick-map', controls: [new ol.control.Zoom(), new ol.control.ScaleLine({ units: 'metric' })],
    layers: [new ol.layer.Vector({ source: landSource, style: { 'fill-color': '#22303a', 'stroke-color': 'rgba(231,228,219,.35)', 'stroke-width': 0.7 } }), ...Object.values(bases), labels,
      new ol.layer.Vector({ source: marks, style: [
        { filter: ['==', ['get', 'kind'], 'scene'], style: { 'stroke-color': '#ffb547', 'stroke-width': 1.5, 'fill-color': 'rgba(255,181,71,.1)' } },
        { filter: ['==', ['get', 'kind'], 'here'], style: { 'stroke-color': '#ffffff', 'stroke-width': 1.5, 'fill-color': 'rgba(255,255,255,.2)' } },
        { filter: ['==', ['get', 'kind'], 'pick'], style: { 'circle-radius': 7, 'circle-fill-color': '#ffb547', 'circle-stroke-color': '#0c1116', 'circle-stroke-width': 2 } },
      ] })],
    view: new ol.View({ center: [0, 2500000], zoom: 2, maxZoom: 18 }),
  });
  const what = h('b', {}), sub = h('small', {}), go = h('button', { type: 'button' }, 'Fly here');
  const ov = new ol.Overlay({ element: h('div', { class: 'pick-bubble' }, what, sub, go), positioning: 'bottom-center', offset: [0, -14], stopEvent: true });
  m.addOverlay(ov);
  picker = { m, bases, labels, marks, ov, what, sub, chosen: null, base: 'street', showLabels: true };
  m.on('singleclick', e => { const ll = ol.proj.toLonLat(e.coordinate); ll[0] = wrapLon(ll[0]); choosePlace(ll, null); $('#pick-results').hidden = true; });
  go.addEventListener('click', () => {
    const c = picker.chosen; if (!c) return;
    if (Math.abs(c.ll[1]) > 82) { toast('Sentinel-2 does not image beyond about 82° latitude. Pick somewhere closer to the equator.'); return; }
    closePicker();
    flyTo(c.ll[0], c.ll[1], c.place);
  });
  setPickBase('street');
}
function setPickBase(base) {
  picker.base = base;
  for (const [k, l] of Object.entries(picker.bases)) l.setVisible(k === base);
  picker.labels.setVisible(base !== 'street' && picker.showLabels);
  for (const b of document.querySelectorAll('#pick-base [data-base]')) b.setAttribute('aria-pressed', String(b.dataset.base === base));
  $('#pick-labels').setAttribute('aria-pressed', String(picker.showLabels));
  $('#pick-labels').hidden = base === 'street';
  $('#pick-credit').innerHTML = 'Click anywhere to choose a spot. ' + PICK_BASES[base].credit +
    (base !== 'street' && picker.showLabels ? ' Labels: OpenStreetMap and Natural Earth, rendering © EOX.' : '') + ' Search by <a href="https://photon.komoot.io" target="_blank" rel="noopener">Photon</a> (OpenStreetMap data). This map is only for picking; it never appears in recordings.';
}
function choosePlace(ll, place) {
  picker.chosen = { ll, place };
  for (const f of picker.marks.getFeatures()) if (f.get('kind') === 'pick') picker.marks.removeFeature(f);
  picker.marks.addFeature(new ol.Feature({ geometry: new ol.geom.Point(ol.proj.fromLonLat(ll)), kind: 'pick' }));
  picker.what.textContent = place ? place.name : 'This spot';
  picker.sub.textContent = (place && place.region ? place.region + '  ·  ' : '') + fmtLL(ll);
  picker.ov.setPosition(ol.proj.fromLonLat(ll));
}
function openPicker() {
  if (!picker) buildPicker();
  $('#pick').hidden = false;
  picker.m.updateSize();
  picker.marks.clear(); picker.ov.setPosition(undefined); picker.chosen = null;
  const a = slots[act];
  if (a.scene) {                                                             // show the tile being flown and the view on it
    const sc = a.scene, to = g => g.transform(sc.code, 'EPSG:3857');
    picker.marks.addFeature(new ol.Feature({ geometry: sc.footprint ? to(sc.footprint.clone()) : to(ol.geom.Polygon.fromExtent(sc.extent)), kind: 'scene' }));
    const c = a.map.getView().getCenter(), res = a.map.getView().getResolution(), hw = innerWidth * res / 2, hh = innerHeight * res / 2;
    picker.marks.addFeature(new ol.Feature({ geometry: to(ol.geom.Polygon.fromExtent([c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh])), kind: 'here' }));
    picker.m.getView().setCenter(ol.proj.transform(c, sc.code, 'EPSG:3857')); picker.m.getView().setZoom(6);
  }
  setTimeout(() => $('#pick-q').focus(), 30);
}
function closePicker() { $('#pick').hidden = true; $('#pick-results').hidden = true; }
$('#b-pick').addEventListener('click', openPicker);
$('#pick-close').addEventListener('click', closePicker);
$('#pick').addEventListener('click', e => { if (e.target.id === 'pick') closePicker(); });
for (const b of document.querySelectorAll('#pick-base [data-base]')) b.addEventListener('click', () => setPickBase(b.dataset.base));
$('#pick-labels').addEventListener('click', () => { picker.showLabels = !picker.showLabels; setPickBase(picker.base); });
$('#pick-form').addEventListener('submit', async e => {
  e.preventDefault();
  const q = $('#pick-q').value.trim(), list = $('#pick-results');
  if (!q) return;
  const note = t => { list.textContent = ''; list.append(h('li', {}, h('small', { style: 'padding:6px 8px;display:block' }, t))); };
  list.hidden = false; note('Searching…');
  let found;
  try { found = await searchPlaces(q); }
  catch (err) { note('Search is not reachable right now (' + err.message + '). You can still click the map.'); return; }
  if (!found.length) { note('Nothing found for “' + q + '”.'); return; }
  list.textContent = '';
  for (const f of found) list.append(h('li', {}, h('button', { type: 'button', onclick: () => {
    list.hidden = true;
    choosePlace([f.lon, f.lat], { name: f.name, region: f.region, lat: f.lat, lon: f.lon, blurb: f.region || null });
    const v = picker.m.getView();
    if (f.extent) v.fit(ol.proj.transformExtent([f.extent[0], f.extent[3], f.extent[2], f.extent[1]], 'EPSG:4326', 'EPSG:3857'), { padding: [80, 80, 80, 80], maxZoom: 14, duration: 600 });
    else v.animate({ center: ol.proj.fromLonLat([f.lon, f.lat]), zoom: 12, duration: 600 });
  } }, f.name, h('small', {}, [f.kind, f.region].filter(Boolean).join('  ·  ')))));
});


let idleTimer = 0;
const wake = () => { document.body.classList.remove('idle'); clearTimeout(idleTimer); idleTimer = setTimeout(() => { if (!document.activeElement || !document.activeElement.matches('input[type=text], select')) document.body.classList.add('idle'); }, 3500); };
for (const ev of ['mousemove', 'mousedown', 'keydown', 'touchstart']) document.addEventListener(ev, wake, { passive: true });
wake();
let resizeTimer = 0;
addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => replan(null), 300); });

const start = (location.hash.match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
if (start.length >= 2 && Math.abs(start[0]) <= 82 && Math.abs(start[1]) <= 180) pending = { lat: start[0], lon: start[1] };
renderControls();
setStatus('Searching the Sentinel-2 archive…');
window.flyover = { CFG, P, REC, slots, next, mini, get act() { return act; }, skipScene, setMode, replan, flyTo, planClip, recOpts, recordHere, runBatch, openRec, openPicker, get picker() { return picker; } };   // debugging handle: inspect state from the browser console
requestAnimationFrame(tick);
})();
