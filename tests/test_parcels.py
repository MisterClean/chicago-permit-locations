"""Regression checks for county joins that could inflate permitted housing."""

from chicago_housing.parcels import (
    build_permit_crosswalk,
    coherent_current_centroid,
    normalize_pin,
    parse_pin10_list,
)


def test_pin_normalization_preserves_leading_zero_and_rejects_junk() -> None:
    assert normalize_pin("09-99-999-991-0000", digits=14) == "09999999910000"
    assert normalize_pin("9999999910000", digits=14) == "09999999910000"
    assert normalize_pin("123", digits=14) is None
    assert normalize_pin("123456789e0", digits=10) is None


def test_city_pin10_list_deduplicates_without_creating_condo_unit_counts() -> None:
    assert parse_pin10_list("9999999991 | 9999999992 | 9999999991")[0] == ["9999999991", "9999999992"]
    assert parse_pin10_list("99-99-999-991-1001 | 99999999911002")[0] == ["9999999991"]
    assert parse_pin10_list("garbage | 0000000000")[1] == ["garbage", "0000000000"]


def test_county_join_preserves_permits_despite_condo_and_multiclass_rows() -> None:
    permits = [{"id": "1", "permit_": "100", "pin_list": "9999999991 | 9999999992"}, {"id": "2", "permit_": "101", "pin_list": None}]
    county = [
        {"pin10": "9999999991", "year": "2026", "class": "299", "distinct_pin14": "100"},
        {"pin10": "9999999991", "year": "2026", "class": "200", "distinct_pin14": "1"},
    ]
    rows = build_permit_crosswalk(permits, county)
    assert len(rows) == 2
    assert rows[0]["match_status"] == "partial_current_match"
    assert rows[0]["current_county_pin14_count"] == 101
    assert rows[0]["unit_count_use"] == "corroboration_only_never_permitted_units"
    assert rows[1]["match_status"] == "no_valid_pin10"


def test_current_coordinate_recovery_rejects_ambiguous_parcel_centroids() -> None:
    permits = [{"id": "1", "pin_list": "9999999991"}]
    county = [{"pin10": "9999999991", "year": "2026", "class": "299", "distinct_pin14": "100", "parcel_rows": "100", "coordinate_rows": "100", "min_lat": "41.9", "max_lat": "41.9", "min_lon": "-87.6", "max_lon": "-87.6"}]
    row = build_permit_crosswalk(permits, county)[0]
    assert coherent_current_centroid(row)["eligible"]
    county[0]["max_lat"] = "41.901"
    row = build_permit_crosswalk(permits, county)[0]
    assert coherent_current_centroid(row) == {"eligible": False, "reason": "county_centroids_disagree"}
