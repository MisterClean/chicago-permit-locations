# Road exposure: sources, acquisition, and interpretation

Snapshot retrieved November 10, 2026. Code: `src/chicago_housing/exposure.py`.

## What the resulting variables mean

The traffic variables screen the distance from a permit point to a road centerline with an observed traffic volume meeting a chosen threshold. They are a **traffic-related air-pollution proxy**, not modeled or measured pollutant concentrations. The noise variables locate a permit point within a **published BTS road-noise category**; they do not measure sound at a building, indoors, on a particular floor, or at a particular time.

All permit years use the same contemporary exposure overlays: IDOT's 2025 road inventory and the BTS 2023 road-noise model published in the 2026 release. Annual differences therefore describe where housing was permitted relative to this fixed road environment. They do not measure annual changes in traffic, noise, or pollution since 2006.

Neither the distance thresholds nor the noise thresholds are asserted to be regulatory or health-based cutoffs. The threshold sweep shows sensitivity to operational definitions. Noise and traffic are related inputs and should not be interpreted as independent risk factors in this descriptive analysis.

## IDOT traffic counts

Authoritative source: [IDOT 2025 Average Annual Daily Traffic layer](https://gis1.dot.illinois.gov/arcgis/rest/services/AdministrativeData/AADT_Historical/FeatureServer/2025), within the [historical traffic service](https://gis1.dot.illinois.gov/arcgis/rest/services/AdministrativeData/AADT_Historical/FeatureServer). [IDOT's traffic-map page](https://idot.illinois.gov/travel-and-maps/highway-maps/average-annual-daily-traffic.html) documents the state's traffic-count products.

The layer supplies polyline road geometry, `AADT`, `AADT_YR`, heavy commercial traffic `HCV_AADT` / `HCV_AADTYR`, and multi-unit/single-unit commercial traffic fields. Counts are annual average daily traffic, not hourly traffic or measured pollution. The service describes a mix of continuous count stations and short-duration coverage counts.

### Executed profile

The WGS84 bounding box `[-87.98, 41.62, -87.48, 42.05]` covers Chicago and a buffer beyond its municipal edges. It preserves nearby suburban roads that could influence housing near the boundary. It is an acquisition envelope, not the definition of the Chicago housing universe.

The archived extract contains **25,566 rows and 25,566 unique OBJECTIDs**. All have positive non-null `AADT`. Individual count years range from 2016 to 2025; the largest groups are 2025 (11,836 rows), 2022 (10,403), and 2024 (1,937). A label of “2025 inventory” must not be shortened to “every road was counted in 2025.”

| AADT threshold | Qualifying source segments |
| --- | ---: |
| 10,000 vehicles/day | 16,655 |
| 25,000 | 5,829 |
| 50,000 | 1,663 |
| 100,000 | 1,168 |

Heavy-commercial coverage is much weaker: **19,509 rows (76.3%) have HCV_AADT = 0**, and **19,475 have HCV_AADTYR = 0**. This strong co-occurrence supports treating zero truck counts cautiously as largely unreported, not evidence of no truck traffic. Positive reported truck counts support a truck-corridor screen; a negative screen does not establish the absence of heavy trucks. HCV count years include much older observations (back to 2002). Qualifying HCV segment counts are 5,721 at ≥500/day, 4,330 at ≥1,000, 2,322 at ≥2,500, and 1,285 at ≥5,000.

These are segment counts; segmentation varies, so they should not be presented as numbers of distinct streets or mileage shares.

### Reproducible acquisition checks

Ordinary `resultOffset` pages returned 24,804 rows with only 24,790 unique IDs against a 25,566-row count. The workflow rejected that incomplete result. The implemented acquisition first obtains the complete matching object-ID set, then submits batches of 250 IDs through read-only POST `/query` calls. Each batch must equal its requested ID set exactly, and final uniqueness and count must match the original count. The manifest records the query, retrieval timestamp, row count, profile, and SHA-256 of the archived GeoJSON.

The server emits extra null Z/M ordinates even when `returnZ=false` and `returnM=false`. `road_geometry_2d()` retains the first two coordinates of each point before geometric operations. No road rows are dropped to work around this encoding.

Distances use **EPSG:26916 (NAD83 / UTM zone 16N)** in meters. Each AADT/HCV threshold has its own nearest-road search; selecting the nearest road overall and then inspecting its traffic would miss nearby busy roads when a quiet road is closer. Centerline distance is not distance to the edge of the traffic lanes, and a permit geocode is not a building footprint or facade. Sensitivity values are 100, 200, 300, and 500 meters.

Files: `data/raw/roads_idot_2025.geojson`, `_metadata.json`, and `_manifest.json`.

## BTS road-noise model

Primary documentation: [National Transportation Noise Map](https://www.bts.gov/geospatial/national-transportation-noise-map), [2026 version 3 methodology](https://rosap.ntl.bts.gov/view/dot/90910/dot_90910_DS1.pdf), and the [official road-only tile service](https://tiles.arcgis.com/tiles/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_Noise_2022_CONUS_road/MapServer).

The 2026 release combines 2022 aviation/rail inputs with **2023 road inputs**. BTS selected 2023 HPMS and ARNOLD road data because of quality issues during the 2022 HPMS transition. The service and ZIP filenames still include `2022`; the analysis labels the road data year as 2023 and records both years in the manifest.

The modeled metric is **24-hour equivalent A-weighted sound level, LAeq,24h**, with a native 30-meter receptor grid and receptor height 1.5 meters. It is not Lden, which applies evening/night penalties. It should not be compared directly with a numeric Lden guideline. The simplified model omits shielding by buildings, barriers, and terrain, assumes soft ground, and excludes results below 45 dBA. It includes selected road classes rather than all possible sources of environmental noise. BTS specifically cautions against interpreting these data as precise noise at an individual location or time.

The workflow uses **Road only**, keeping rail and aviation out of this measure.

### Categorical-cache fallback, explicitly retained

The official raw road ZIP at `https://www.bts.gov/bts-net-storage/CONUS_road_noise_2022.zip` returned HTTP 403 from the web server during retrieval. The older official 2020 service did not provide numeric samples through identify. The working alternative is the official published category cache at its maximum resolution, **zoom 12**. This represents about **28.45 meters per pixel on the ground at 41.9°N**, in a Web Mercator display grid; it is not the native numeric raster.

The workflow archives all **56 required original PNG8 tiles**, the official service metadata, and official legend. Legend colors are read from the centers of the actual legend swatches. Only exact opaque RGBA matches are classified. Unrecognized, blended, or interpolated colors would remain unknown; **zero such opaque pixels were found in this complete tile subset**. Published categories are 45–49.9, 50–54.9, 55–59.9, 60–69.9, 70–79.9, 80–89.9, and >90 dBA. No continuous dBA value or band midpoint is invented.

Transparent pixels remain `transparent_below_45_or_unmapped`: they may represent values below the model's 45-dBA display floor or places without modeled coverage. They are **not assigned zero noise** and are **not silently treated as below a selected threshold**. `noise_raster_sampled` distinguishes a successfully sampled transparent pixel from missing/out-of-area permit coordinates. Every required source tile must download successfully; incomplete downloads are not accepted.

`noise_db_lower` / `noise_db_upper` are the displayed category endpoints. `noise_ge_55` and comparable aligned thresholds classify clear categories. A 65-dBA cut through the 60–69.9 category is unknown, not interpolated. For location sensitivity, the `_all_3x3` flag is true only if every cell in a 3×3 neighborhood meets the threshold; `_any_3x3` identifies any known qualifying cell, while retaining ambiguous neighborhoods. This is a spatial robustness check, not a confidence interval. The nominal category is still sampled at the source permit point.

The derived TIFF stores category lower endpoints, with 0 reserved for unknown. Its tags and manifest explicitly describe this meaning. It must not be displayed as a continuous noise surface or exact home-level noise measurement. It is suitable for a transparent, coarse spatial screening of permitted housing against the published model.

Files: `data/raw/noise_bts_2023_categories.tif`, `noise_bts_2023_tiles/`, and the `_service.json`, `_legend.json`, `_manifest.json` records. The manifest retains source URLs, all original tile hashes, source years, grid details, and pixel counts.

## CDOT and air-pollution context

The current [CDOT Average Daily Traffic Counts dataset](https://data.cityofchicago.org/Transportation/Average-Daily-Traffic-Counts/gc7y-n4xa) is distinct from the [2006 historical traffic study](https://data.cityofchicago.org/Transportation/Average-Daily-Traffic-Counts-2006-Study-Historical/pfsx-4n4m). Its metadata says October 2020 onward, but the executed API profile's earliest returned timestamp is **July 28, 2022**; the observed snapshot has 2,433,403 rows, 2,561 distinct segments, and a latest date of November 9, 2026 (2,199 rows on that date). Each daily segment/direction record is already a trailing 30-day average derived from traffic/speed cameras; the city states accuracy and consistency are higher from May 2026 onward. It should not be silently equated to an IDOT AADT or summed across overlapping daily averages. The CDOT profile is a separate supplementary comparison.

The [EPA AirToxScreen FAQ](https://www.epa.gov/AirToxScreen/airtoxscreen-frequent-questions) explains why road proximity is relevant while also warning that broad modeled air-toxics estimates cannot pinpoint exposure along specific roads. Wind, vehicle mix, emissions, building configuration, and the pollutant affect the distance pattern. Thus 100–500-meter traffic screens are sensitivity definitions, not measurements of pollution or claims of specific health outcomes. An actual concentration estimate would require a separately validated fine-scale pollution model or monitoring design.

## Interface and verification

`fetch_exposure_sources(Path('data/raw'))` retrieves only missing cached files, while `refresh=True` explicitly refreshes them. `annotate_exposure(records, raw_dir)` runs entirely offline on records containing longitude/latitude and preserves their original keys. It adds distances, threshold flags, source years, noise categories, and unknown-status flags. The independent `annotate_noise_exposure` and `annotate_road_exposure` functions allow review of each source separately.

`tests/test_exposure.py` covers nearest-busy-road selection, meter-distance thresholds, missing coordinates, XYZ pixel mapping, ArcGIS null Z/M handling, exact-color decoding, transparent pixels, band-cut ambiguity, and 3×3 sensitivity. Seven tests passed, and a full-archive road/noise annotation smoke test completed successfully on November 10, 2026. An independent 8×8 grid of 64 coordinates also matched the sampled derived GeoTIFF categories to their original XYZ tile pixels exactly, checking the assembled raster's georeferencing.
