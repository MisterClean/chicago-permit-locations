"""Parser and counting regressions using synthetic record identifiers."""

import json
from pathlib import Path

import pytest

from chicago_housing.permits import (
    classify_permit as classify_permit_with_reviews,
)
from chicago_housing.permits import (
    classify_permits as classify_permits_with_reviews,
)
from chicago_housing.permits import (
    conversion_net_units,
    extract_units,
    is_preliminary_only,
    load_reviewed_decisions,
)

SYNTHETIC_REVIEWS = {
    "schema_version": 1,
    "unit_counts": {"EXAMPLE-UNIT": [12, "Synthetic unit-count review."]},
    "scope_decisions": {"EXAMPLE-SCOPE": ["uncertain", "Synthetic scope review."]},
    "shared_projects": [{
        "permit_ids": ["EXAMPLE-PROJECT-A", "EXAMPLE-PROJECT-B"],
        "units": 109,
        "reason": "Synthetic shared-project review.",
        "source_url": "https://example.org/shared-project",
    }],
}


def classify_permit(row: dict) -> dict:
    return classify_permit_with_reviews(row, reviewed=SYNTHETIC_REVIEWS)


def classify_permits(rows: list[dict]) -> list[dict]:
    return classify_permits_with_reviews(rows, reviewed=SYNTHETIC_REVIEWS)


def test_review_file_is_required_and_example_is_not_silently_accepted(tmp_path: Path) -> None:
    path = tmp_path / "reviewed_decisions.json"
    with pytest.raises(FileNotFoundError, match="Missing private reviewed decisions"):
        load_reviewed_decisions(path)
    path.write_text(json.dumps({**SYNTHETIC_REVIEWS, "example_only": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic reviewed-decisions example"):
        load_reviewed_decisions(path)
    path.write_text(json.dumps(SYNTHETIC_REVIEWS), encoding="utf-8")
    assert load_reviewed_decisions(path) == SYNTHETIC_REVIEWS


def test_injected_reviews_resolve_unit_count_and_preserve_scope_uncertainty() -> None:
    unit_row = classify_permit(permit(
        "ERECT AN 8 DWELLING UNIT BUILDING AND A 4 UNIT BUILDING", permit_="EXAMPLE-UNIT"
    ))
    assert unit_row["included"]
    assert unit_row["unit_count"] == 12
    assert unit_row["unit_source"] == "reviewed_permit_description"
    scope_row = classify_permit(permit(
        "ERECT NEW 2 DWELLING UNIT RESIDENTIAL BUILDING", permit_="EXAMPLE-SCOPE"
    ))
    assert not scope_row["included"]
    assert "uncertain_primary_scope" in scope_row["review_flags"]


def permit(description: str, **changes: object) -> dict:
    row = {
        "id": "1",
        "permit_": "109000001",
        "issue_date": "2020-01-01T00:00:00.000",
        "permit_type": "PERMIT - NEW CONSTRUCTION",
        "work_description": description,
        "street_number": "100",
        "street_direction": "N",
        "street_name": "EXAMPLE ST",
        "latitude": "41.9",
        "longitude": "-87.7",
    }
    row.update(changes)
    return row


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("ERECT A 3 STORY 6 DWELLING UNIT RESIDENTIAL BUILDING", 6),
        ("ERECT A THREE-FLAT", 3),
        ("NEW ONE HUNDRED TEN DWELLING UNIT BUILDING", 110),
        ("NEW TWENTY-TWO DWELLING UNIT BUILDING", 22),
        ("ERECT NEW SINGLE FAMILY RESIDENCE AND 2 CAR GARAGE", 1),
        ("NEW BUILDING 154 EFFICIENCY UNITS & 339 DWELLING UNITS", 493),
        ("NEW BUILDING 132 EFFICIENCY UNITS, 133 DWELLING UNITS", 265),
        ("32 UNIT BUILDING (INCL. 6 EFFICIENCY UNITS)", 32),
        ("100-DWELLING UNITS AND 76-EFFICIENCY UNITS FOR A TOTAL OF 176-DWELLING UNITS", 176),
        ("2 RESIDENTIAL APARTMENT TOWERS TOTALING 178 UNITS", 178),
        ("ERECT 9 NEW TOWNHOMES. ADDRESS 4302 UNITS A & B, 4306 UNITS A & B", 9),
        ("40 RESIDENTIAL UNITS. [SEE REVISION PERMIT #109000003 CONVERT 40 DU TO 50 DU]", 40),
    ],
)
def test_explicit_housing_count_examples(description: str, expected: int) -> None:
    assert extract_units(description)["units"] == expected


def test_conflicting_building_counts_require_review() -> None:
    result = extract_units("ERECT A 411 DWELLING UNIT BUILDING AND A 23 UNIT BUILDING")
    assert result["units"] is None
    assert result["ambiguous"]


def test_accessory_and_existing_work_are_not_new_housing() -> None:
    assert not classify_permit(permit("ERECT A 2 CAR GARAGE FOR EXISTING SINGLE FAMILY RESIDENCE"))[
        "included"
    ]
    result = classify_permit(
        permit(
            "PROPOSED ARCHITECTURAL CONVERSION OF 21 DWELLING UNIT BUILDING TO 17 DWELLING UNIT BUILDING",
            permit_type="PERMIT - RENOVATION/ALTERATION",
        )
    )
    assert result["analysis_class"] == "conversion"


@pytest.mark.parametrize(
    ("description", "units"),
    [
        (
            "NEW CONSTRUCTION OF 51 STORY MIXED USE TOWER WITH 608 DWELLING UNITS, AND 5-STORY PARKING GARAGE",
            608,
        ),
        (
            "NEW CONSTRUCTION OF A 499-UNIT, MIX-USE, RESIDENTIAL APARTMENT BUILDING: 45 STORIES WITH 5-LEVEL GARAGE BELOW GRADE",
            499,
        ),
        (
            "FULL BUILDING PERMIT - ERECT A 509 UNIT APARTMENT TOWER WITH ATTACHED 200 CAR PARKING GARAGE",
            509,
        ),
        (
            "FULL BUILDING PERMIT FOR FLOORS LOWER LEVEL 2 THROUGH ROOF, INCLUDING EXTERIOR AMENITY DECK. 46-STORY BUILDING CONTAINING 464 DWELLING UNITS. INCLUDES 140 EFFICIENCY UNITS AND 81 AFFORDABLE UNITS",
            464,
        ),
        (
            "PROPOSED SIX-STORY MIXED USE BUILDING WITH FIRST FLOOR COMMERCIAL, SECOND FLOOR PARKING DECK, AND THIRD THRU SIXTH FLOOR 63 DWELLING UNITS",
            63,
        ),
        (
            "NEW THREE STORY SIX UNIT MASONRY RESIDENTIAL BUILDING WITH 6 PARKING SPACES. EXISTING WOOD FENCE REMAINS",
            6,
        ),
        ("6 ATTACHED SINGLE FAMILY HOMES WITH DETACHED GARAGES", 6),
        ("ERECT A 2-STORY S.F.R. WITH ATTACHED GARAGE AND BASEMENT", 1),
        ("ERECT 3 STORY WITH BASEMENT AND ATTACHED GARAGE 5 DU BUILDING", 5),
        (
            "NEW CONSTRUCTION, 6 DWELLING UNITS ON 2ND AND 3RD FLOORS OVER GROUND FLOOR RETAIL TENANT SPACE",
            6,
        ),
        ("ERECT 3 FLAT. 3 UNIT, 3 STORY APARTMENT NEW CONSTRUCTION ON EXISTING VACANT LOT", 3),
        (
            "NEW CONSTRUCTION OF A (1,407) SQ. FT., 3-STORY, TYPE VA FRAME, 37' HT., 2-UNIT RESIDENCE. REMOVAL OF EXISTING PARKWAY TREE",
            2,
        ),
        (
            "3-STORY, 5-DWELLING UNIT BUILDING WITH BASEMENT AND DETACHED GARAGE. EXISTING OCCUPANCY R-2, PROPOSED OCCUPANCY R-2",
            5,
        ),
        ("ERECT NEW TWO-STORY SFH AND GARAGE", 1),
        (
            "NEW SINGLE FAMILY RESIDENCE, INCLUDING DEMOLITION OF EXISTING WOOD FRAME TWO-FLAT BUILDING",
            1,
        ),
    ],
)
def test_parking_or_amenities_do_not_erase_new_housing(description: str, units: int) -> None:
    row = classify_permit(permit(description))
    assert row["included"]
    assert row["unit_count"] == units


@pytest.mark.parametrize(
    "description",
    [
        "CONVERSION OF EXISTING 12-STORY BUILDING INTO 32 NEW RESIDENTIAL UNITS WITH ALL NEW BUILDING SYSTEMS",
        "INTERIOR BUILD-OUT OF RESIDENTIAL UNIT 1803 IN NEW 37-STORY, 71 UNIT RESIDENTIAL TOWER",
        "CURRENT SINGLE FAMILY RESIDENTIAL, PROPOSED SINGLE FAMILY RESIDENTIAL, NO CHANGE. INTERIOR ALTERATIONS, SECOND FLOOR DORMER ADDITION",
        "EXISTING AND PROPOSED 6 UNIT APARTMENT BUILDING. DOCUMENTATION OF RADIATORS AND BOILERS. NEW STAIRS FOR EXISTING REAR PORCHES",
        "FULL BUILDING PERMIT FOR PROPOSED NEW 12 STORY RESIDENTIAL ADDITION TO EXISTING 6 STORY BUILDING. TOTAL 210 DWELLING UNITS",
        "DE-CONVERSION OF EXISTING 3 UNIT BUILDING INTO A NEW SINGLE FAMILY RESIDENCE",
        "FULL BUILDING PERMIT FOR GENERAL RESTORATION OF AN EXISTING SINGLE FAMILY DWELLING",
        "FULL BUILDING PERMIT FOR NEW MASONRY PRIVATE GARAGE AND ELECTRICAL SERVICE FOR EXISTING SINGLE FAMILY RESIDENCE",
        "FULL BUILDING PERMIT FOR NEW ADDITIONAL DWELLING UNIT CONVERSION UNIT IN EXISTING BASEMENT",
        "COMPLETE DEMO AND REMODEL OF 3-UNIT APARTMENT INTO A NEW 2-UNIT APARTMENT BUILDING",
        "CURRENT 2 STORY 2 UNITS RESIDENCE TO HAVE INTERIOR REMODELING AND MASONRY REPLACEMENT",
        "ERECT A THREE STORY ADDITION ON EXISTING SINGLE FAMILY RESIDENCE AND RENOVATION OF THE COACH HOUSE",
        "GUT REHAB OF SINGLE FAMILY RESIDENCE. CONSTRUCT NEW LANDING AND STEPS AT REAR OF RESIDENCE",
        "INTERIOR RENOVATION OF EXISTING SINGLE FAMILY RESIDENCE. EXISTING AND NEW CONSTRUCTION TYPE V-A, R-5 OCCUPANCY SINGLE FAMILY RESIDENCE",
        "FULL BUILDING PERMIT FOR EXISTING FOUR STORY BUILDING WITH CONVERSION FROM OFFICE TO 12 DWELLING UNITS",
    ],
)
def test_existing_scope_does_not_become_new_housing_because_of_new_parts(description: str) -> None:
    assert not classify_permit(permit(description))["included"]


def test_housing_foundation_with_garage_remains_a_preliminary_record() -> None:
    row = classify_permit(
        permit(
            "FOUNDATION ONLY FOR PROPOSED 47-STORY, 444 DWELLING UNIT RESIDENTIAL TOWER WITH PARKING GARAGE AND SKY DECK"
        )
    )
    assert row["analysis_class"] == "preliminary_housing"
    assert row["unit_count"] == 444
    assert not row["included"]


def test_caisson_scope_does_not_count_a_proposed_tower_twice() -> None:
    rows = classify_permits(
        [
            permit(
                "ERECT CAISSONS FOR PROPOSED 507 UNIT APARTMENT TOWER WITH ATTACHED 200 CAR PARKING GARAGE"
            ),
            permit(
                "FULL BUILDING PERMIT FOR A 509 UNIT APARTMENT TOWER", id="2", permit_="109000002"
            ),
        ]
    )
    assert rows[0]["analysis_class"] == "preliminary_housing"
    assert rows[0]["linked_full_permit_ids"] == ["109000002"]
    assert sum(row["unit_count"] for row in rows if row["included"]) == 509


def test_zoning_amendment_is_not_a_permit_revision() -> None:
    row = classify_permit(
        permit(
            "PROPOSED NEW 4 STORY 43 UNIT RESIDENCE. ZONING AMENDMENT #22370-T1 APPROVED",
            residential_unit_change="43",
        )
    )
    assert row["included"]
    assert row["unit_count"] == 43


@pytest.mark.parametrize(
    "description",
    [
        "REVISIONS TO PERMIT #109000004 FOR HVAC IN A 60 DWELLING UNIT BUILDING",
        "AMENDED PERMIT #109000011 FOR PROPOSED 3 DU RESIDENCE, ADDING BALCONY",
        "AMEND PERMIT 109000013 TO ADD 1 NEW DU WITH NEW KITCHEN",
        "AMEND SITE LOCATION FOR PROPOSED SINGLE FAMILY RESIDENCE WITH PREVIOUS PERMIT #109000005",
    ],
)
def test_revision_variants_do_not_create_additional_housing(description: str) -> None:
    row = classify_permits([permit(description, residential_unit_change="1")])[0]
    assert row["analysis_class"] == "revision"
    assert not row["included"]
    assert not row["change_included"]


def test_retrospective_plural_revision_note_keeps_original_authorization() -> None:
    row = classify_permit(
        permit(
            "NEW CONSTRUCTION/BUILD-OUT PHASE FOR 7 TOWNHOMES. {SEE REVISIONS #109000008 TO CORRECT ADDRESS}"
        )
    )
    assert row["included"]
    assert row["unit_count"] == 7


def test_occupancy_class_number_is_not_a_housing_count() -> None:
    assert extract_units("ERECT FRAME HOUSE OCCUPANCYR-5 DWELLING UNITS")["units"] is None


def test_new_garage_with_adu_is_housing_but_a_garage_alone_is_not() -> None:
    row = classify_permit(
        permit("NEW MASONRY 2 CAR GARAGE W/ A.D.U. ON SECOND FLOOR", residential_unit_change="1")
    )
    assert row["included"]
    assert row["unit_count"] == 1


def test_legalization_of_already_built_house_requires_separate_review() -> None:
    row = classify_permit(
        permit("LEGALIZATION OF SINGLE FAMILY RESIDENCE THAT WAS BUILT WITHOUT A PERMIT")
    )
    assert not row["included"]
    assert row["unit_count"] == 1
    assert "legalization_of_existing_construction" in row["review_flags"]


def test_recent_zero_is_not_an_automatic_nonhousing_count() -> None:
    result = classify_permit(
        permit("ERECT NEW 6 DWELLING UNIT RESIDENTIAL BUILDING", residential_unit_change="0")
    )
    assert result["text_units"] == 6
    assert not result["included"]
    assert "city_zero_or_negative_text_positive" in result["review_flags"]


def test_city_text_disagreement_stays_visible() -> None:
    result = classify_permit(
        permit("ERECT NEW 6 DWELLING UNIT RESIDENTIAL BUILDING", residential_unit_change="4")
    )
    assert not result["included"]
    assert result["structured_unit_change"] == 4
    assert result["text_units"] == 6


def test_preliminary_and_full_building_are_separate() -> None:
    assert is_preliminary_only("FOUNDATION ONLY FOR NEW 54 STORY 498 UNIT BUILDING")
    assert not is_preliminary_only(
        "FULL BUILDING PERMIT FOR A 490 UNIT BUILDING. FOUNDATION PERMIT #109000001"
    )
    classified = classify_permits(
        [
            permit("FOUNDATION ONLY FOR NEW 54 STORY 498 UNIT RESIDENTIAL BUILDING"),
            permit(
                "FULL BUILDING PERMIT FOR A 490 UNIT RESIDENTIAL BUILDING. FOUNDATION PERMIT #109000001",
                id="2",
                permit_="109000002",
                issue_date="2020-06-01T00:00:00.000",
            ),
        ]
    )
    assert sum(row["unit_count"] for row in classified if row["included"]) == 490


@pytest.mark.parametrize(
    "description",
    [
        "NEW CONSTRUCTION OF 60-STORY RESIDENTIAL BUILDING WITH 698 UNITS. FOUNDATION AND SUPERSTRUCTURE LL6 UP TO 1ST FLOOR PERMITTED UNDER 109000009",
        "ERECT 17-STORY 318 DU RESIDENTIAL BUILDING UPON PREVIOUSLY APPROVED FOUNDATION AND SUPERSTRUCTURE UPTO UNDERSIDE OF 6TH FLOOR PERMIT 109000010",
        "NEW CONSTRUCTION OF A 10 STORY RESIDENTIAL BUILDING WITH 126 UNITS. PILES ONLY PERMIT UNDER 109000012.",
        "ERECT 10 STORY 56 DU RESIDENTIAL BUILDING UPON SEPARATE CAISSON ONLY PERMIT #109000014",
    ],
)
def test_reference_to_previous_structural_work_does_not_exclude_full_building(
    description: str,
) -> None:
    assert not is_preliminary_only(description)


def test_nonresidential_unit_counts_are_not_housing() -> None:
    row = classify_permit(
        permit("ERECT NEW 216 UNIT CONCRETE CRYPT MAUSOLEUM WITHIN EXISTING CEMETERY")
    )
    assert not row["included"]


def test_preliminary_linked_to_full_building_is_not_added_twice() -> None:
    classified = classify_permits(
        [
            permit("FOUNDATION ONLY FOR NEW 54 STORY 498 UNIT RESIDENTIAL BUILDING"),
            permit(
                "FULL BUILDING PERMIT FOR A 490 UNIT RESIDENTIAL BUILDING. FOUNDATION PERMIT #109000001",
                id="2",
                permit_="109000002",
                issue_date="2020-06-01T00:00:00.000",
            ),
        ]
    )
    assert classified[0]["linked_full_permit_ids"] == ["109000002"]
    assert classified[0]["preliminary_link_method"] == "explicit_permit_reference"
    assert not classified[0]["preliminary_candidate"]


def test_address_count_repeat_is_ambiguous_without_project_evidence() -> None:
    classified = classify_permits(
        [
            permit("ERECT NEW 6 DWELLING UNIT BUILDING, BUILDING A"),
            permit("ERECT NEW 6 DWELLING UNIT BUILDING, BUILDING B", id="2", permit_="109000002"),
        ]
    )
    assert classified[1]["duplicate_of"] is None
    assert "possible_repeat_address_count_within_730_days" in classified[1]["review_flags"]


def test_identical_scope_repeated_at_address_is_deduplicated() -> None:
    classified = classify_permits(
        [
            permit("ERECT NEW 6 DWELLING UNIT BUILDING"),
            permit("ERECT NEW 6 DWELLING UNIT BUILDING", id="2", permit_="109000002"),
        ]
    )
    assert classified[1]["duplicate_of"] == "109000001"
    assert sum(row["unit_count"] for row in classified if row["included"]) == 6


def test_reviewed_shared_project_keeps_both_source_locations() -> None:
    description = "TWO NEW 7 STORY MID-RISES, ALL RESIDENTIAL TOTALING 109 DWELLING UNITS"
    rows = classify_permits(
        [
            permit(description, permit_="EXAMPLE-PROJECT-A", street_number="650", longitude="-87.644"),
            permit(description, permit_="EXAMPLE-PROJECT-B", street_number="660", longitude="-87.645"),
        ]
    )
    assert sum(row["unit_count"] for row in rows if row["included"]) == 109
    assert rows[0]["shared_project_member_ids"] == ["EXAMPLE-PROJECT-A", "EXAMPLE-PROJECT-B"]
    assert rows[1]["duplicate_of"] == "EXAMPLE-PROJECT-A"
    assert rows[0]["longitude"] == "-87.644"
    assert rows[1]["longitude"] == "-87.645"
    assert "spatial_allocation_unresolved" in rows[0]["review_flags"]


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("CONVERT 5 D.U. TO 6 D.U.", 1),
        ("DECONVERT FROM 3 DWELLING UNITS TO A SINGLE FAMILY RESIDENCE", -2),
        ("ADD 2 NEW DWELLING UNITS TO EXISTING BUILDING", 2),
        ("RENOVATE EXISTING 30 APARTMENTS", None),
        (
            "SECOND FLOOR DECONVERTED FROM 2 DU TO 1 DU. BUILDING DECONVERSION FROM 1 RETAIL AND 3 DU TO 1 RETAIL SPACE AND 1 DWELLING UNIT",
            -2,
        ),
    ],
)
def test_conversion_is_net_change(description: str, expected: int | None) -> None:
    assert conversion_net_units(description) == expected


def test_secondary_city_text_conflict_is_not_summed() -> None:
    rows = classify_permits(
        [
            permit(
                "CONVERT THE EXISTING 3 STORY, 3 DWELLING UNIT BUILDING INTO A SINGLE-FAMILY RESIDENCE",
                permit_type="PERMIT - RENOVATION/ALTERATION",
                residential_unit_change="3",
            )
        ]
    )
    assert rows[0]["text_net_unit_change"] == -2
    assert rows[0]["structured_unit_change"] == 3
    assert rows[0]["net_unit_change"] is None
    assert not rows[0]["change_included"]


def test_secondary_foundation_does_not_add_the_conversion_total_twice() -> None:
    rows = classify_permits(
        [
            permit(
                "FOUNDATION ONLY FOR BUILDING ADDITION TO CONVERT EXISTING BUILDING TO RESIDENTIAL (62 DWELLING UNITS)",
                permit_type="PERMIT - RENOVATION/ALTERATION",
                residential_unit_change="62",
            )
        ]
    )
    assert not rows[0]["change_included"]
    assert rows[0]["net_unit_change"] is None
    assert "preliminary_change_authorization_only" in rows[0]["change_review_flags"]
