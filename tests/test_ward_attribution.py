"""Regression checks for current-ward assignment and uncertain locations."""

from shapely.geometry import box, mapping

from chicago_housing.supplemental import annotate_geography


def _feature(geom, properties: dict) -> dict:
    return {"type": "Feature", "geometry": mapping(geom), "properties": properties}


def _boundaries() -> dict:
    return {
        "city": {"features": [_feature(box(0, 0, 6, 4), {})]},
        "communities": {"features": [_feature(box(0, 0, 6, 4), {
            "community": "TEST AREA", "area_numbe": "1",
        })]},
        "wards": {"features": [
            _feature(box(0, 0, 2, 4), {"ward": "1"}),
            _feature(box(2, 0, 4, 4), {"ward": "2"}),
        ]},
    }


def test_ward_join_uses_unique_full_polygon_coverage() -> None:
    rows = [
        {"longitude": 1, "latitude": 1},
        {"longitude": 3, "latitude": 1},
        {"longitude": 2, "latitude": 1},  # exactly on shared boundary
        {"longitude": 5, "latitude": 1},  # inside city, outside ward coverage
        {"longitude": 7, "latitude": 1},
        {"longitude": None, "latitude": 1},
    ]
    result = annotate_geography(rows, _boundaries())
    assert [(row["ward_2023"], row["ward_2023_status"]) for row in result] == [
        ("1", "assigned"), ("2", "assigned"), (None, "boundary_ambiguous"),
        (None, "uncovered"), (None, "outside_city"), (None, "unlocated"),
    ]
    assert result[0]["community_name"] == "Test Area"


def test_shared_project_total_requires_every_member_in_one_ward() -> None:
    rows = [
        {"permit_id": "same-a", "included": True, "longitude": 1, "latitude": 1,
         "shared_project_member_ids": ["same-a", "same-b"]},
        {"permit_id": "same-b", "included": False, "longitude": 1.5, "latitude": 1},
        {"permit_id": "cross-a", "included": True, "longitude": 1, "latitude": 1,
         "shared_project_member_ids": ["cross-a", "cross-b"]},
        {"permit_id": "cross-b", "included": False, "longitude": 3, "latitude": 1},
        {"permit_id": "missing-a", "included": True, "longitude": 1, "latitude": 1,
         "shared_project_member_ids": ["missing-a", "missing-b"]},
        {"permit_id": "missing-b", "included": False, "longitude": None, "latitude": 1},
        {"permit_id": "absent-a", "included": True, "longitude": 1, "latitude": 1,
         "shared_project_member_ids": ["absent-a", "absent-b"]},
    ]
    result = {row["permit_id"]: row for row in annotate_geography(rows, _boundaries())}
    assert result["same-a"]["ward_2023"] == "1"
    assert result["same-a"]["ward_2023_status"] == "assigned"
    for permit_id in ["cross-a", "missing-a", "absent-a"]:
        assert result[permit_id]["ward_2023"] is None
        assert result[permit_id]["ward_2023_status"] == "shared_project_ambiguous"
    assert result["cross-b"]["ward_2023"] == "2"
