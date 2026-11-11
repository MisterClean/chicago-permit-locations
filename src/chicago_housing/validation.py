"""Conservative parcel corroboration; assessed housing never replaces permit counts."""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from chicago_housing.analysis import finite_number


def _read_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def county_unit_evidence(raw_dir: Path) -> dict[str, list[dict]]:
    evidence: dict[str, list[dict]] = defaultdict(list)
    apartment_codes = {"Two": 2, "Three": 3, "Four": 4, "Five": 5, "Six": 6}
    residential = _read_rows(raw_dir / "parcels_recent_residential_characteristics.json")
    for row in residential:
        if row["class"] not in {"211", "212"} or row.get("char_apts") not in apartment_codes:
            continue
        if float(row.get("pin_num_cards", 0)) != 1 or float(row.get("tieback_proration_rate", 0)) != 1:
            continue
        evidence[row["pin"][:10]].append({
            "source": "2026 small-residential characteristic card", "tax_year": 2026,
            "units": apartment_codes[row["char_apts"]],
            "year_built": finite_number(row.get("char_yrblt")),
        })
    condos = _read_rows(raw_dir / "parcels_condo_building_groups.json")
    for row in condos:
        if row.get("is_parking_space") is not False or row.get("is_common_area") is not False:
            continue
        # Only fully classified, internally coherent single-land-PIN building groups.
        counts = [finite_number(row.get(key)) for key in
                  ("min_building_pins", "max_building_pins", "min_non_units", "max_non_units")]
        if any(value is None for value in counts):
            continue
        if counts[0] != counts[1] or counts[2] != counts[3]:
            continue
        units = int(row["distinct_pin14"])
        if units != counts[0] - counts[2]:
            continue
        if row.get("min_year_built") != row.get("max_year_built"):
            continue
        evidence[row["pin10"]].append({
            "source": "2026 condominium living-PIN group", "tax_year": 2026,
            "units": units, "year_built": finite_number(row.get("min_year_built")),
        })
    commercial = _read_rows(raw_dir / "parcels_commercial_sample.json")
    for row in commercial:
        if not row.get("sheet", "").endswith("-Multifamily"):
            continue
        pins = {re.sub(r"\D", "", pin)[:10] for pin in row.get("pins", "").split(",")}
        pins.discard("")
        if len(pins) != 1:
            continue
        units = finite_number(row.get("tot_units"))
        if units is None or units <= 0 or not units.is_integer():
            continue
        evidence[next(iter(pins))].append({
            "source": "2024 commercial multifamily characteristic", "tax_year": 2024,
            "units": int(units), "year_built": finite_number(row.get("yearbuilt")),
        })
    return dict(evidence)


def corroborate_counts(rows: list[dict], raw_dir: Path) -> tuple[list[dict], dict]:
    evidence = county_unit_evidence(raw_dir)
    permits_by_pin: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("included") is True:
            for pin in row.get("permit_pin10s", []):
                permits_by_pin[pin].append(row)
    result = []
    for row in rows:
        item = dict(row)
        pins = row.get("permit_pin10s", [])
        item["county_count_status"] = "not_comparable"
        item["county_observed_units"] = None
        item["county_count_sources"] = []
        if len(pins) == 1 and row.get("unit_count") is not None:
            candidates = [e for e in evidence.get(pins[0], [])
                          if e["year_built"] is not None
                          and row["permit_year"] - 1 <= e["year_built"] <= row["permit_year"] + 5]
            if candidates and any(
                sum(other["permit_year"] - 1 <= candidate["year_built"] <= other["permit_year"] + 5
                    for other in permits_by_pin[pins[0]]) > 1
                for candidate in candidates
            ):
                item["county_count_status"] = "multiple_building_permits_on_land_pin"
                result.append(item)
                continue
            counts = {e["units"] for e in candidates}
            if len(counts) == 1:
                units = next(iter(counts))
                item["county_observed_units"] = units
                item["county_count_sources"] = sorted({e["source"] for e in candidates})
                item["county_count_status"] = "agrees" if units == row["unit_count"] else "differs"
            elif len(counts) > 1:
                item["county_count_status"] = "county_sources_disagree"
        result.append(item)
    selected = [row for row in result if row.get("included") is True]
    return result, {
        "included_permit_status": dict(Counter(row["county_count_status"] for row in selected)),
        "included_unit_status": dict(Counter({
            status: sum(row["unit_count"] for row in selected if row["county_count_status"] == status)
            for status in sorted({row["county_count_status"] for row in selected})
        })),
        "rule": "One land PIN without competing included building permits in the assessed year-built window, unambiguous class-specific unit evidence, and assessed year-built within permit year−1 through +5. Corroboration only; later stock can differ legitimately.",
        "excluded_evidence": "Multi-land-PIN portfolios, parking/common-area condo PINs, hotels, nursing homes, multi-card/tieback-prorated properties, missing/ambiguous counts, and incompatible construction years.",
    }
