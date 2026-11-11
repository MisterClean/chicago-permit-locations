from chicago_housing.supplemental import annotate_geography


def test_missing_and_outside_points_preserve_units():
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    feature = {"type": "Feature", "geometry": polygon,
               "properties": {"community": "TEST", "area_numbe": "1"}}
    ward = {**feature, "properties": {"ward": "1"}}
    boundaries = {"city": {"features": [feature]}, "communities": {"features": [feature]},
                  "wards": {"features": [ward]}}
    rows = [
        {"unit_count": 10, "longitude": None, "latitude": None},
        {"unit_count": 20, "longitude": 3, "latitude": 3},
        {"unit_count": 30, "longitude": 0.5, "latitude": 0.5},
    ]
    out = annotate_geography(rows, boundaries)
    assert sum(row["unit_count"] for row in out) == 60
    assert [row["geocode_status"] for row in out] == ["missing", "outside_city", "inside_city"]


def test_multiple_buildings_on_one_pin_are_not_compared_to_parcel_total(monkeypatch, tmp_path):
    from chicago_housing import validation

    monkeypatch.setattr(validation, "county_unit_evidence", lambda _: {
        "9999999993": [{"units": 72, "year_built": 2014, "source": "commercial"}],
    })
    rows = [{"included": True, "unit_count": 6, "permit_year": 2014,
             "permit_pin10s": ["9999999993"]} for _ in range(12)]
    result, summary = validation.corroborate_counts(rows, tmp_path)
    assert sum(row["unit_count"] for row in result) == 72
    assert all(row["county_count_status"] == "multiple_building_permits_on_land_pin" for row in result)
    assert summary["included_permit_status"]["multiple_building_permits_on_land_pin"] == 12


def test_shared_project_envelope_preserves_original_locations():
    from chicago_housing.analysis import noise_screen, traffic_screen
    from chicago_housing.pipeline import shared_project_exposure

    def member(permit_id, included, lower, distance):
        row = {"permit_id": permit_id, "address": permit_id, "included": included,
               "unit_count": 109, "latitude": 41.88, "longitude": -87.64,
               "geocode_status": "inside_city", "noise_band": str(lower),
               "noise_db_lower": lower, "noise_db_upper": lower + 5}
        for aadt in [10000, 25000, 50000, 100000]:
            row[f"nearest_aadt_{aadt}_distance_m"] = distance
        for hcv in [500, 1000, 2500, 5000]:
            row[f"nearest_hcv_{hcv}_distance_m"] = distance
        for threshold in [50, 55, 60, 65, 70]:
            for suffix in ["all", "any"]:
                row[f"noise_ge_{threshold}_{suffix}_3x3"] = False
        return row

    rows = [member("a", True, 45, 164), member("b", False, 50, 131)]
    rows[0]["shared_project_member_ids"] = ["a", "b"]
    result = shared_project_exposure(rows)
    assert sum(row["unit_count"] for row in result if row["included"]) == 109
    assert result[0]["longitude"] == -87.64
    assert len(result[0]["shared_project_locations"]) == 2
    assert noise_screen(result[0], threshold_db=50) is None
    assert noise_screen(result[0], threshold_db=55) is False
    assert result[0]["noise_ge_50"] is None
    assert traffic_screen(result[0], aadt=25000, distance_m=150) is None
