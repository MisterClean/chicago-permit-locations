"""Reproducible road-traffic proximity and published BTS noise-category screening.

The BTS tile categories are a documented fallback to the unavailable raw raster
download. They are not numeric sound measurements. Their grid and legend are
archived locally, and transparent pixels remain unknown rather than quiet.
"""

import base64
import hashlib
import json
import math
import warnings
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely import transform
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

IDOT_URL = "https://gis1.dot.illinois.gov/arcgis/rest/services/AdministrativeData/AADT_Historical/FeatureServer/2025"
NOISE_URL = "https://tiles.arcgis.com/tiles/xOi1kZaI0eWDREZv/arcgis/rest/services/NTAD_Noise_2022_CONUS_road/MapServer"
NOISE_RAW_URL = "https://www.bts.gov/bts-net-storage/CONUS_road_noise_2022.zip"
NOISE_DOC_URL = "https://rosap.ntl.bts.gov/view/dot/90910/dot_90910_DS1.pdf"
CHICAGO_BUFFER_BBOX = (-87.98, 41.62, -87.48, 42.05)
AADT_THRESHOLDS = (10000, 25000, 50000, 100000)
HCV_THRESHOLDS = (500, 1000, 2500, 5000)
DISTANCES_M = (100, 200, 300, 500)
NOISE_THRESHOLDS = (50, 55, 60, 65, 70)
WEB_MERCATOR_HALF_WORLD = 20037508.342789244


def _json_response(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    # ArcGIS supports read-only POST queries; ID lists otherwise exceed IIS URL limits.
    response = (session.post(url, data=params, timeout=120) if "objectIds" in params
                else session.get(url, params=params, timeout=120))
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"Public data endpoint returned an error: {url}: {data['error']}")
    return data


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_idot_roads(raw_dir: Path, *, refresh: bool = False) -> Path:
    """Archive all 2025 IDOT segments intersecting Chicago plus a boundary buffer."""
    destination = raw_dir / "roads_idot_2025.geojson"
    if destination.exists() and not refresh:
        return destination
    raw_dir.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        metadata = _json_response(session, IDOT_URL, {"f": "json"})
        query = {
            "f": "json", "where": "1=1", "geometry": ",".join(map(str, CHICAGO_BUFFER_BBOX)),
            "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }
        count = _json_response(session, f"{IDOT_URL}/query", query | {"returnCountOnly": "true"})["count"]
        expected_ids = sorted(_json_response(session, f"{IDOT_URL}/query", query | {"returnIdsOnly": "true"})["objectIds"])
        if len(expected_ids) != count or len(set(expected_ids)) != count:
            raise RuntimeError("IDOT count and complete object-ID list disagree")
        features: list[dict[str, Any]] = []
        # Spatial offset paging can repeat/skip features on this service. An
        # explicit snapshot of IDs, then objectIds batches, makes coverage testable.
        page_size = 250
        for offset in range(0, count, page_size):
            object_ids = expected_ids[offset:offset+page_size]
            page = _json_response(session, f"{IDOT_URL}/query", {
                "f": "geojson", "outFields": "*", "outSR": "4326",
                "returnGeometry": "true", "returnM": "false", "returnZ": "false",
                "objectIds": ",".join(map(str, object_ids)), "orderByFields": "OBJECTID",
            })
            received_ids = [feature["properties"]["OBJECTID"] for feature in page["features"]]
            if sorted(received_ids) != object_ids:
                raise RuntimeError(f"IDOT object-ID batch mismatch at {offset}: requested {len(object_ids)}, received {len(received_ids)}")
            features.extend(page["features"])
        ids = [feature["properties"]["OBJECTID"] for feature in features]
        if len(features) != count or len(set(ids)) != count:
            raise RuntimeError(f"Incomplete or overlapping IDOT pagination: {len(features)} rows, {len(set(ids))} unique; expected {count}")
        _write_json(destination, {"type": "FeatureCollection", "features": features})
        _write_json(raw_dir / "roads_idot_2025_metadata.json", metadata)
        _write_json(raw_dir / "roads_idot_2025_manifest.json", {
            "retrieved_at_utc": datetime.now(UTC).isoformat(), "source": IDOT_URL,
            "query": query, "pagination": "returnIdsOnly followed by objectIds batches of 250; exact ID-set validation",
            "out_fields": "*", "out_crs": "EPSG:4326", "rows": count,
            "snapshot_year": 2025, "count_year_field": "AADT_YR", "truck_count_year_field": "HCV_AADTYR",
            "sha256": _sha256(destination), "profile": profile_roads(features),
        })
    return destination


def profile_roads(features: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Profile count years, missingness, and thresholds without inferring missing traffic."""
    rows = [f["properties"] for f in features]
    return {
        "rows": len(rows), "unique_objectids": len({r["OBJECTID"] for r in rows}),
        "aadt_count_years": dict(Counter(str(r.get("AADT_YR")) for r in rows)),
        "hcv_count_years": dict(Counter(str(r.get("HCV_AADTYR")) for r in rows)),
        "aadt_null": sum(r.get("AADT") is None for r in rows),
        "aadt_zero": sum(r.get("AADT") == 0 for r in rows),
        "hcv_null": sum(r.get("HCV_AADT") is None for r in rows),
        "hcv_zero": sum(r.get("HCV_AADT") == 0 for r in rows),
        "aadt_at_least": {str(t): sum((r.get("AADT") or 0) >= t for r in rows) for t in AADT_THRESHOLDS},
        "hcv_at_least": {str(t): sum((r.get("HCV_AADT") or 0) >= t for r in rows) for t in HCV_THRESHOLDS},
    }


def tile_coordinates(longitude: float, latitude: float, zoom: int) -> tuple[float, float]:
    """Return continuous XYZ tile coordinates for a WGS84 point."""
    n = 2 ** zoom
    return ((longitude + 180) / 360 * n,
            (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2 * n)


def legend_palette(legend: Mapping[str, Any]) -> dict[tuple[int, int, int, int], int]:
    """Read exact opaque category colors from the service's own legend swatches."""
    palette: dict[tuple[int, int, int, int], int] = {}
    layer = next(layer for layer in legend["layers"] if layer["id"] == 3)
    for entry in layer["legend"]:
        label = entry["label"]
        lower = int(float(label.replace(">", "").strip().split(" ")[0]))
        with MemoryFile(base64.b64decode(entry["imageData"])) as memory, warnings.catch_warnings():
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with memory.open() as src:
                pixel = tuple(int(c) for c in src.read()[:, src.height // 2, src.width // 2])
        if len(pixel) != 4 or pixel[3] != 255:
            raise ValueError(f"Unexpected legend swatch at {label}: {pixel}")
        palette[pixel] = lower
    if set(palette.values()) != {45, 50, 55, 60, 70, 80, 90}:
        raise ValueError(f"Unexpected BTS categories: {palette}")
    return palette


def _decode_noise_tile(path: Path, colors: Mapping[tuple[int, int, int, int], int]) -> tuple[np.ndarray, int]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(path) as src:
            if src.count != 1 or src.width != 256 or src.height != 256:
                raise ValueError(f"Unexpected BTS categorical tile layout: {path}")
            raster = src.read(1)
            colormap = src.colormap(1)
    codes = np.zeros(raster.shape, dtype="uint8")
    unexpected = 0
    for index in np.unique(raster):
        color = colormap[int(index)]
        if color in colors:
            codes[raster == index] = colors[color]
        elif color[3] != 0:
            # Antialiased, interpolated, or changed category colors stay unknown.
            unexpected += int(np.count_nonzero(raster == index))
    return codes, unexpected


def fetch_noise_categories(raw_dir: Path, *, refresh: bool = False) -> Path:
    """Archive published 2023 road-noise categories at the cache's finest zoom.

    0 means transparent/unmapped/unknown. No continuous decibels are inferred.
    The service's name says 2022, but the BTS methodology identifies 2023 road
    inputs in the 2022/2023 release. Native modeled grid is 30 m; the published
    EPSG:3857 zoom-12 category cache has about 28.4 m ground pixels in Chicago.
    """
    destination = raw_dir / "noise_bts_2023_categories.tif"
    if destination.exists() and not refresh:
        return destination
    tile_dir = raw_dir / "noise_bts_2023_tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        metadata = _json_response(session, NOISE_URL, {"f": "json"})
        legend = _json_response(session, f"{NOISE_URL}/legend", {"f": "json"})
        colors = legend_palette(legend)
        zoom = metadata["maxLOD"]
        if zoom != 12:
            raise ValueError(f"BTS cache resolution changed: {zoom}; review before reusing the analysis")
        west, south, east, north = CHICAGO_BUFFER_BBOX
        x0, y0 = (math.floor(v) for v in tile_coordinates(west, north, zoom))
        x1, y1 = (math.floor(v) for v in tile_coordinates(east, south, zoom))
        mosaic = np.zeros(((y1-y0+1)*256, (x1-x0+1)*256), dtype="uint8")
        tile_manifest = []
        unexpected = 0
        for tile_y in range(y0, y1+1):
            for tile_x in range(x0, x1+1):
                url = f"{NOISE_URL}/tile/{zoom}/{tile_y}/{tile_x}"
                path = tile_dir / f"{zoom}_{tile_y}_{tile_x}.png"
                if refresh or not path.exists():
                    response = session.get(url, timeout=120)
                    response.raise_for_status()
                    path.write_bytes(response.content)
                codes, unknown = _decode_noise_tile(path, colors)
                unexpected += unknown
                mosaic[(tile_y-y0)*256:(tile_y-y0+1)*256, (tile_x-x0)*256:(tile_x-x0+1)*256] = codes
                tile_manifest.append({"url": url, "file": str(path.relative_to(raw_dir)), "sha256": _sha256(path)})
    pixel_size = WEB_MERCATOR_HALF_WORLD * 2 / (2 ** zoom * 256)
    transform_grid = from_origin(-WEB_MERCATOR_HALF_WORLD+x0*256*pixel_size,
                                 WEB_MERCATOR_HALF_WORLD-y0*256*pixel_size, pixel_size, pixel_size)
    with rasterio.open(destination, "w", driver="GTiff", height=mosaic.shape[0], width=mosaic.shape[1],
                       count=1, dtype="uint8", crs="EPSG:3857", transform=transform_grid,
                       nodata=0, compress="deflate") as dst:
        dst.write(mosaic, 1)
        dst.update_tags(interpretation="Published BTS road-noise band lower endpoint; not exact dBA", data_year="2023",
                        unknown="0 = transparent/unmapped/unknown, not zero noise", source=NOISE_URL)
    _write_json(raw_dir / "noise_bts_2023_service.json", metadata)
    _write_json(raw_dir / "noise_bts_2023_legend.json", legend)
    _write_json(raw_dir / "noise_bts_2023_manifest.json", {
        "retrieved_at_utc": datetime.now(UTC).isoformat(), "source": NOISE_URL,
        "official_raw_download": NOISE_RAW_URL, "raw_download_access": "HTTP403 from official public download endpoint during retrieval",
        "method": "Exact published legend category from finest available PNG8 tile cache; no numeric dBA estimated",
        "documentation": NOISE_DOC_URL, "road_data_year": 2023, "service_label_year": 2022, "release_year": 2026,
        "metric": "LAeq,24h dBA; not Lden", "native_model_grid_m": 30,
        "tile_zoom": zoom, "cache_pixel_web_mercator_m": pixel_size,
        "cache_pixel_ground_m_at_41_9N": pixel_size*math.cos(math.radians(41.9)),
        "unrecognized_opaque_pixels": unexpected,
        "legend_palette": {str(key): value for key, value in colors.items()},
        "bbox_wgs84": CHICAGO_BUFFER_BBOX, "tiles": tile_manifest, "sha256": _sha256(destination),
        "pixel_counts": {str(int(code)): int(n) for code, n in zip(*np.unique(mosaic, return_counts=True), strict=True)},
    })
    return destination


def _valid_coordinate(record: Mapping[str, Any]) -> tuple[float, float] | None:
    longitude, latitude = record.get("longitude"), record.get("latitude")
    if longitude is None or latitude is None or longitude == "" or latitude == "":
        return None
    lon, lat = float(longitude), float(latitude)
    if not math.isfinite(lon) or not math.isfinite(lat):
        return None
    west, south, east, north = CHICAGO_BUFFER_BBOX
    if not (west <= lon <= east and south <= lat <= north):
        return None
    return lon, lat


def road_geometry_2d(geometry: Mapping[str, Any]) -> BaseGeometry:
    """Discard ArcGIS null Z/M ordinates, retaining the published XY road line."""
    geometry_type = geometry["type"]
    if geometry_type == "LineString":
        coordinates = [coordinate[:2] for coordinate in geometry["coordinates"]]
    elif geometry_type == "MultiLineString":
        coordinates = [[coordinate[:2] for coordinate in part] for part in geometry["coordinates"]]
    else:
        raise ValueError(f"Unexpected IDOT road geometry: {geometry_type}")
    return shape({"type": geometry_type, "coordinates": coordinates})


def annotate_road_exposure(records: Sequence[Mapping[str, Any]], roads: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Append metric distance to IDOT lines, with missing traffic kept explicit.

    Each qualifying threshold is an independent nearest-road query. This avoids
    missing a farther busy road when the nearest road is quiet. Distances use
    NAD83 / UTM zone 16N (EPSG:26916); the source is the IDOT road centerline.
    """
    projector = Transformer.from_crs("EPSG:4326", "EPSG:26916", always_xy=True)
    features = roads["features"]
    geometries = [transform(road_geometry_2d(feature["geometry"]), projector.transform, interleaved=False) for feature in features]
    all_tree = STRtree(geometries)
    subset_trees: dict[str, STRtree] = {}
    for field, thresholds, prefix in (("AADT", AADT_THRESHOLDS, "aadt"), ("HCV_AADT", HCV_THRESHOLDS, "hcv")):
        for threshold in thresholds:
            selected = [g for g, f in zip(geometries, features, strict=True) if (f["properties"].get(field) or 0) >= threshold]
            if len(selected) == 0:
                raise ValueError(f"No roads qualify for {field}>={threshold}; review source coverage")
            subset_trees[f"{prefix}_{threshold}"] = STRtree(selected)
    result = []
    for record in records:
        out = dict(record)
        coordinate = _valid_coordinate(record)
        out["road_exposure_status"] = "classified" if coordinate is not None else "missing_or_outside_coordinates"
        out["traffic_snapshot_year"] = 2025
        out["nearest_idot_road_name"] = None
        out["nearest_idot_aadt"] = None
        out["nearest_idot_aadt_year"] = None
        out["nearest_idot_hcv_aadt"] = None
        out["nearest_idot_hcv_year"] = None
        out["nearest_idot_road_distance_m"] = None
        for name in subset_trees:
            out[f"nearest_{name}_distance_m"] = None
            for distance in DISTANCES_M:
                out[f"near_{name}_{distance}m"] = None
        if coordinate is not None:
            point = Point(*projector.transform(*coordinate))
            nearest = int(all_tree.nearest(point))
            props = features[nearest]["properties"]
            out.update({"nearest_idot_road_name": props.get("ROAD_NAME"), "nearest_idot_aadt": props.get("AADT"),
                        "nearest_idot_aadt_year": props.get("AADT_YR"), "nearest_idot_hcv_aadt": props.get("HCV_AADT"),
                        "nearest_idot_hcv_year": props.get("HCV_AADTYR"),
                        "nearest_idot_road_distance_m": round(float(point.distance(geometries[nearest])), 3)})
            for name, tree in subset_trees.items():
                nearest_index = int(tree.nearest(point))
                distance_m = float(point.distance(tree.geometries[nearest_index]))
                out[f"nearest_{name}_distance_m"] = round(distance_m, 3)
                for distance in DISTANCES_M:
                    out[f"near_{name}_{distance}m"] = distance_m <= distance
        result.append(out)
    return result


def annotate_noise_exposure(records: Sequence[Mapping[str, Any]], raster_path: Path) -> list[dict[str, Any]]:
    """Append published bands and 3x3-pixel threshold sensitivity, never exact dBA."""
    projector = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    result = []
    with rasterio.open(raster_path) as src:
        raster = src.read(1)
        for record in records:
            out = dict(record)
            coordinate = _valid_coordinate(record)
            out.update({"noise_data_year": 2023, "noise_db_lower": None, "noise_db_upper": None, "noise_band": None,
                        "noise_status": "missing_or_outside_coordinates", "noise_boundary_3x3": None,
                        "noise_raster_sampled": False})
            for threshold in NOISE_THRESHOLDS:
                out[f"noise_ge_{threshold}"] = None
                out[f"noise_ge_{threshold}_all_3x3"] = None
                out[f"noise_ge_{threshold}_any_3x3"] = None
            if coordinate is not None:
                x, y = projector.transform(*coordinate)
                row, col = src.index(x, y)
                if 0 <= row < src.height and 0 <= col < src.width:
                    out["noise_raster_sampled"] = True
                    category = int(raster[row, col])
                    neighborhood = raster[max(0,row-1):min(src.height,row+2), max(0,col-1):min(src.width,col+2)]
                    out["noise_status"] = "published_category" if category != 0 else "transparent_below_45_or_unmapped"
                    out["noise_boundary_3x3"] = bool(np.any(neighborhood != category))
                    if category != 0:
                        out["noise_db_lower"] = category
                        upper = {45: 49.9, 50: 54.9, 55: 59.9, 60: 69.9, 70: 79.9, 80: 89.9}.get(category)
                        out["noise_db_upper"] = upper
                        out["noise_band"] = f"{category}–{upper}" if upper is not None else ">90"
                    for threshold in NOISE_THRESHOLDS:
                        ambiguous_band = threshold == 65 and category == 60
                        out[f"noise_ge_{threshold}"] = category >= threshold if category != 0 and not ambiguous_band else None
                        out[f"noise_ge_{threshold}_all_3x3"] = bool(np.all(neighborhood >= threshold))
                        ambiguous_neighbors = np.any(neighborhood == 0) or (threshold == 65 and np.any(neighborhood == 60))
                        out[f"noise_ge_{threshold}_any_3x3"] = bool(np.any(neighborhood >= threshold)) if np.any(neighborhood >= threshold) or not ambiguous_neighbors else None
            result.append(out)
    return result


def annotate_exposure(records: Sequence[Mapping[str, Any]], raw_dir: Path) -> list[dict[str, Any]]:
    """Annotate from archived files only; fetching is deliberately separate."""
    roads = json.loads((raw_dir / "roads_idot_2025.geojson").read_text(encoding="utf-8"))
    return annotate_noise_exposure(annotate_road_exposure(records, roads), raw_dir / "noise_bts_2023_categories.tif")


def fetch_exposure_sources(raw_dir: Path, *, refresh: bool = False) -> dict[str, str]:
    """Fetch the two explicit static exposure snapshots used for all permit years."""
    return {"roads": str(fetch_idot_roads(raw_dir, refresh=refresh)),
            "noise": str(fetch_noise_categories(raw_dir, refresh=refresh))}
