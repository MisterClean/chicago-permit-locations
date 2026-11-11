from chicago_housing.analysis import (
    joint_screen,
    noise_screen,
    traffic_screen,
    weighted_summary,
)


def test_unit_weighting_and_unlocated_denominator():
    rows = [
        {"included": True, "unit_count": 500, "outcome": True},
        {"included": True, "unit_count": 1, "outcome": False},
        {"included": True, "unit_count": 99, "outcome": None},
        {"included": False, "unit_count": 9000, "outcome": True},
    ]
    result = weighted_summary(rows, lambda row: row["outcome"])
    assert result["units"] == 600
    assert result["share_of_identified_units"] == 500 / 600
    assert result["share_of_screened_units"] == 500 / 501
    assert result["share_upper_if_unknown_exposed"] == 599 / 600
    assert result["exposed_permits"] == 1


def test_noise_bands_do_not_invent_decibels():
    row = {"geocode_status": "inside_city", "noise_db_lower": 55, "noise_db_upper": 60}
    assert noise_screen(row, threshold_db=55) is True
    assert noise_screen(row, threshold_db=60) is False
    assert noise_screen(row, threshold_db=57) is None
    assert noise_screen({"geocode_status": "inside_city"}, threshold_db=55) is None


def test_unknown_exposure_propagates_for_joint_screens():
    assert joint_screen(True, None, operator="either") is True
    assert joint_screen(False, None, operator="either") is None
    assert joint_screen(False, None, operator="both") is False
    assert joint_screen(True, None, operator="both") is None


def test_distance_boundary_and_missing_value():
    row = {"geocode_status": "inside_city", "nearest_aadt_25000_distance_m": 200.0}
    assert traffic_screen(row, aadt=25000, distance_m=200) is True
    assert traffic_screen(row, aadt=25000, distance_m=100) is False
    assert traffic_screen(row, aadt=10000, distance_m=200) is None


def test_empty_population_is_not_zero_percent():
    assert weighted_summary([], lambda row: False)["share_of_identified_units"] is None


def test_shared_project_unknown_allocation_uses_distance_envelope():
    row = {"geocode_status": "inside_city", "nearest_aadt_25000_distance_m": 164,
           "nearest_aadt_25000_distance_m_min": 131, "nearest_aadt_25000_distance_m_max": 164}
    assert traffic_screen(row, aadt=25000, distance_m=100) is False
    assert traffic_screen(row, aadt=25000, distance_m=150) is None
    assert traffic_screen(row, aadt=25000, distance_m=200) is True
