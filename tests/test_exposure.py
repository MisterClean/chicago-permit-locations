"""Checks that protect the exposure denominator and spatial threshold meaning."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from chicago_housing.exposure import (
    _decode_noise_tile,
    annotate_noise_exposure,
    annotate_road_exposure,
    road_geometry_2d,
    tile_coordinates,
)


def test_nearest_quiet_road_does_not_hide_farther_busy_road() -> None:
    roads = {"features": [
        {"geometry": {"type": "LineString", "coordinates": [[-87.6501, 41.89], [-87.6501, 41.91]]},
         "properties": {"ROAD_NAME": "Quiet", "AADT": 5000, "HCV_AADT": 0}},
        {"geometry": {"type": "LineString", "coordinates": [[-87.6515, 41.89], [-87.6515, 41.91]]},
         "properties": {"ROAD_NAME": "Busy", "AADT": 150000, "HCV_AADT": 6000}},
    ]}
    records = annotate_road_exposure([{"longitude": -87.65, "latitude": 41.9}], roads)
    record = records[0]
    assert record["nearest_idot_road_name"] == "Quiet"
    assert 110 < record["nearest_aadt_25000_distance_m"] < 135
    assert record["near_aadt_25000_100m"] is False
    assert record["near_aadt_25000_200m"] is True
    assert record["nearest_hcv_1000_distance_m"] == record["nearest_aadt_25000_distance_m"]


def test_missing_coordinates_are_not_unexposed() -> None:
    roads = {"features": [{
        "geometry": {"type": "LineString", "coordinates": [[-87.65, 41.89], [-87.65, 41.91]]},
        "properties": {"AADT": 150000, "HCV_AADT": 6000},
    }]}
    result = annotate_road_exposure([{"longitude": None, "latitude": 41.9}], roads)[0]
    assert result["road_exposure_status"] == "missing_or_outside_coordinates"
    assert result["near_aadt_25000_200m"] is None


def test_xyz_tile_position() -> None:
    x, y = tile_coordinates(0, 0, 12)
    assert x == pytest.approx(2048)
    assert y == pytest.approx(2048)


def test_arcgis_null_z_and_m_do_not_drop_road_segments() -> None:
    line = road_geometry_2d({"type": "LineString", "coordinates": [
        [-87.65, 41.9, None, None], [-87.66, 41.9, None, None],
    ]})
    assert len(line.coords) == 2
    assert line.length == pytest.approx(0.01)


def _test_raster(path: Path, values: np.ndarray) -> None:
    x, y = Transformer.from_crs(4326, 3857, always_xy=True).transform(-87.65, 41.9)
    with rasterio.open(path, "w", driver="GTiff", height=5, width=5, count=1, dtype="uint8",
                       crs="EPSG:3857", transform=from_origin(x-50, y+50, 20, 20), nodata=0) as dst:
        dst.write(values, 1)


def test_noise_band_boundary_sensitivity_and_65db_ambiguity(tmp_path: Path) -> None:
    values = np.full((5,5), 60, dtype="uint8")
    values[2,3] = 50
    path = tmp_path / "noise.tif"
    _test_raster(path, values)
    result = annotate_noise_exposure([{"longitude": -87.65, "latitude": 41.9}], path)[0]
    assert result["noise_db_lower"] == 60
    assert result["noise_db_upper"] == 69.9
    assert result["noise_ge_55"] is True
    assert result["noise_ge_55_all_3x3"] is False
    assert result["noise_ge_55_any_3x3"] is True
    assert result["noise_boundary_3x3"] is True
    assert result["noise_ge_65"] is None
    assert result["noise_ge_65_any_3x3"] is None


def test_transparent_noise_is_unknown_and_sampling_is_tracked(tmp_path: Path) -> None:
    path = tmp_path / "noise.tif"
    _test_raster(path, np.zeros((5,5), dtype="uint8"))
    sampled, missing = annotate_noise_exposure([
        {"longitude": -87.65, "latitude": 41.9}, {"longitude": None, "latitude": 41.9},
    ], path)
    assert sampled["noise_status"] == "transparent_below_45_or_unmapped"
    assert sampled["noise_raster_sampled"] is True
    assert sampled["noise_ge_55"] is None
    assert sampled["noise_ge_55_any_3x3"] is None
    assert missing["noise_raster_sampled"] is False


def test_only_exact_legend_colors_are_classified(tmp_path: Path) -> None:
    path = tmp_path / "tile.png"
    values = np.zeros((256,256), dtype="uint8")
    values[0,0] = 1
    values[0,1] = 2
    with rasterio.open(path, "w", driver="PNG", height=256, width=256, count=1, dtype="uint8",
                       transform=from_origin(10, 20, 1, 1)) as dst:
        dst.write(values, 1)
        dst.write_colormap(1, {0: (0,0,0,0), 1: (255,0,0,255), 2: (254,1,0,255)})
    codes, unexpected = _decode_noise_tile(path, {(255,0,0,255): 55})
    assert codes[0,0] == 55
    assert codes[0,1] == 0
    assert unexpected == 1
