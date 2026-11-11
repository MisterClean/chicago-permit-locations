"""Cook County parcel crosschecks that never turn assessed stock into permit units.

City permits expose land PIN10s. County universe rows are PIN14 × tax year,
and a land PIN can contain condominium dwelling, parking and common-area PINs.
Every join below therefore collapses back to one row per original permit.
"""

import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode

import requests

COUNTY_DOMAIN: str = "https://datacatalog.cookcountyil.gov"
CURRENT_UNIVERSE: str = "pabr-t5kh"
HISTORICAL_UNIVERSE: str = "nj4t-kc8j"
RESIDENTIAL_CHARACTERISTICS: str = "x54s-btds"
CONDOMINIUM_CHARACTERISTICS: str = "3r7i-mrz4"
COMMERCIAL_VALUATIONS: str = "csik-bsws"
PARCEL_ADDRESSES: str = "3723-97qp"
STUDY_TAX_YEAR: int = 2026
COMMERCIAL_REASSESSMENT_YEAR: int = 2024


def normalize_pin(value: str, *, digits: int) -> str | None:
    """Normalize documented numeric PINs; never interpret numbers as unit counts."""
    cleaned = re.sub(r"[\s-]", "", str(value))
    if not cleaned.isascii() or not cleaned.isdigit():
        return None
    # County exports can lose a leading zero; do not invent any other digits.
    if len(cleaned) not in {digits, digits - 1}:
        return None
    return cleaned.zfill(digits)


def parse_pin10_list(value: str | None) -> tuple[list[str], list[str]]:
    """Parse the City's pipe-separated PIN10 list, retaining malformed tokens."""
    if value is None or str(value).strip() == "":
        return [], []
    pins: set[str] = set()
    invalid: list[str] = []
    for token in re.split(r"[|,;]", str(value)):
        cleaned = re.sub(r"[\s-]", "", token)
        if cleaned == "":
            continue
        width = 14 if len(cleaned) in {13, 14} else 10
        normalized = normalize_pin(token, digits=width)
        if normalized is None or set(normalized) == {"0"}:
            invalid.append(token.strip())
        else:
            pins.add(normalized[:10])
    return sorted(pins), invalid


def query_county(
    dataset: str,
    *,
    params: dict[str, str | int],
    raw_dir: Path,
    label: str,
    refresh: bool,
) -> list[dict]:
    """Execute and cache an exact public SoQL query, with time and content hash."""
    query = {key: str(value) for key, value in params.items()}
    url = f"{COUNTY_DOMAIN}/resource/{dataset}.json?{urlencode(query)}"
    query_hash = sha256(url.encode()).hexdigest()[:16]
    cache_dir = raw_dir / "parcels_queries"
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / f"{label}_{query_hash}.json"
    if output.exists() and not refresh:
        return json.loads(output.read_text(encoding="utf-8"))["rows"]
    for attempt in range(4):
        try:
            response = requests.get(url, timeout=(20, 180))
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            response.raise_for_status()
            rows = response.json()
            break
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))
    if not isinstance(rows, list):
        raise TypeError(f"Expected county query rows from {dataset}: {rows!r}")
    if len(rows) >= int(query.get("$limit", "1000")):
        raise ValueError(f"Possible API truncation for {label}; reduce the query batch")
    payload = {
        "dataset_id": dataset,
        "source_url": f"{COUNTY_DOMAIN}/d/{dataset}",
        "query_url": url,
        "query": query,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "rows_sha256": sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
        "rows": rows,
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return rows


def fetch_current_pin10_groups(
    pin10s: list[str], raw_dir: Path, *, refresh: bool
) -> list[dict]:
    """Fetch class-stratified current PIN10 summaries without downloading condo PINs."""
    unique = sorted(set(pin10s))
    batches = [unique[index:index + 150] for index in range(0, len(unique), 150)]

    def fetch_batch(batch: list[str]) -> list[dict]:
        literals = ",".join(f"'{pin}'" for pin in batch)
        return query_county(
            CURRENT_UNIVERSE,
            params={
                "$select": "pin10,year,class,count(*) as parcel_rows,"
                "count(distinct pin) as distinct_pin14,count(lat) as coordinate_rows,"
                "min(lat) as min_lat,max(lat) as max_lat,min(lon) as min_lon,"
                "max(lon) as max_lon",
                "$where": f"pin10 in ({literals})",
                "$group": "pin10,year,class",
                "$order": "pin10,year,class",
                "$limit": 50000,
            },
            raw_dir=raw_dir,
            label="current_pin10",
            refresh=refresh,
        )

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for index, result in enumerate(executor.map(fetch_batch, batches), start=1):
            rows.extend(result)
            if index % 20 == 0 or index == len(batches):
                print(f"County current PIN10 batches: {index}/{len(batches)}", flush=True)
    return rows


def build_permit_crosswalk(
    permit_rows: list[dict], county_groups: list[dict]
) -> list[dict]:
    """Return exactly one row per permit; multiplicity remains evidence, not weights."""
    by_pin10: dict[str, list[dict]] = defaultdict(list)
    for row in county_groups:
        by_pin10[row["pin10"]].append(row)
    result: list[dict] = []
    for permit in permit_rows:
        pins, invalid = parse_pin10_list(permit.get("pin_list"))
        matched = [pin for pin in pins if pin in by_pin10]
        groups = [group for pin in matched for group in by_pin10[pin]]
        if len(pins) == 0:
            match_status = "no_valid_pin10"
        elif len(matched) == 0:
            match_status = "no_current_match"
        elif len(matched) < len(pins):
            match_status = "partial_current_match"
        else:
            match_status = "all_current_pin10_matched"
        result.append({
            "id": str(permit.get("id", "")),
            "permit_number": str(permit.get("permit_", permit.get("permit_number", ""))),
            "issue_date": permit.get("issue_date"),
            "permit_pin10s": pins,
            "invalid_pin_tokens": invalid,
            "matched_current_pin10s": matched,
            "unmatched_current_pin10s": [pin for pin in pins if pin not in by_pin10],
            "match_status": match_status,
            "current_county_pin14_count": sum(int(row["distinct_pin14"]) for row in groups),
            "current_county_classes": sorted({row["class"] for row in groups}),
            "current_county_tax_years": sorted({int(float(row["year"])) for row in groups}),
            "current_county_groups": groups,
            "unit_count_use": "corroboration_only_never_permitted_units",
        })
    if len(result) != len(permit_rows):
        raise AssertionError("Parcel crosswalk changed the permit population")
    return result


def profile_crosswalk(crosswalk: list[dict]) -> dict:
    """Summarize linkage and temporal limits without discarding unmatched permits."""
    years: dict[str, Counter] = defaultdict(Counter)
    for row in crosswalk:
        year = str(row.get("issue_date", "unknown"))[:4]
        years[year][row["match_status"]] += 1
    return {
        "permit_rows": len(crosswalk),
        "match_status": dict(Counter(row["match_status"] for row in crosswalk)),
        "multi_pin10_permits": sum(len(row["permit_pin10s"]) > 1 for row in crosswalk),
        "invalid_pin_token_permits": sum(len(row["invalid_pin_tokens"]) > 0 for row in crosswalk),
        "matched_multiple_county_pin14s": sum(row["current_county_pin14_count"] > 1 for row in crosswalk),
        "by_permit_issue_year": {year: dict(counts) for year, counts in sorted(years.items())},
        "join_grain": "one row per input permit; county PIN14 and class rows collapsed within PIN10",
        "limitation": "Current parcel identities corroborate locations only. PIN splits, mergers,"
        " condominiumization and assessment lag prevent treating unmatched historic permits as"
        " invalid or current parcel counts as newly permitted housing units.",
    }


def coherent_current_centroid(crosswalk_row: dict) -> dict:
    """Offer a current location only for one completely matched, coherent PIN10.

    This is an explicit geolocation fallback, never evidence that the historical
    permitted building has been completed. A 1e-6 degree coordinate spread is
    approximately one tenth of a metre in Chicago and tolerates rounding only.
    """
    if len(crosswalk_row["permit_pin10s"]) != 1:
        return {"eligible": False, "reason": "requires_one_land_pin10"}
    if crosswalk_row["match_status"] != "all_current_pin10_matched":
        return {"eligible": False, "reason": "current_pin10_unmatched"}
    groups = crosswalk_row["current_county_groups"]
    if len(groups) == 0 or any(
        int(row.get("coordinate_rows", 0)) != int(row["parcel_rows"])
        or any(key not in row for key in ("min_lat", "max_lat", "min_lon", "max_lon"))
        for row in groups
    ):
        return {"eligible": False, "reason": "incomplete_county_coordinates"}
    min_lat = min(float(row["min_lat"]) for row in groups)
    max_lat = max(float(row["max_lat"]) for row in groups)
    min_lon = min(float(row["min_lon"]) for row in groups)
    max_lon = max(float(row["max_lon"]) for row in groups)
    if max_lat - min_lat > 0.000001 or max_lon - min_lon > 0.000001:
        return {"eligible": False, "reason": "county_centroids_disagree"}
    return {
        "eligible": True,
        "latitude": (min_lat + max_lat) / 2,
        "longitude": (min_lon + max_lon) / 2,
        "source": "cook_county_current_pin10_centroid",
        "tax_years": crosswalk_row["current_county_tax_years"],
        "reason": "one_land_pin10_all_county_centroids_agree",
    }


def run_parcel_audit(
    permit_rows: list[dict], raw_dir: Path, *, refresh: bool = False
) -> dict:
    """Fetch current PIN10 evidence and write a stable, inspectable permit crosswalk."""
    pins = sorted({pin for row in permit_rows for pin in parse_pin10_list(row.get("pin_list"))[0]})
    groups = fetch_current_pin10_groups(pins, raw_dir, refresh=refresh)
    crosswalk = build_permit_crosswalk(permit_rows, groups)
    for row in crosswalk:
        row["coordinate_recovery"] = coherent_current_centroid(row)
    profile = profile_crosswalk(crosswalk)
    for name, value in {"current_pin10_groups": groups, "permit_crosswalk": crosswalk, "linkage_profile": profile}.items():
        (raw_dir / f"parcels_{name}.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
    return profile


def fetch_supporting_profiles(raw_dir: Path, *, refresh: bool = False) -> dict:
    """Reproduce county schemas, quality profiles and unit-corroboration extracts.

    Tax-year observations remain fixed at the study's 2026/2024 vintages. Live
    APIs can backfill those years, so exact fetched responses and their hashes
    are retained. Current-universe summaries state the year returned by the API.
    No names, taxpayer mailing addresses or owner fields are downloaded.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    datasets = (
        CURRENT_UNIVERSE, HISTORICAL_UNIVERSE, RESIDENTIAL_CHARACTERISTICS,
        CONDOMINIUM_CHARACTERISTICS, COMMERCIAL_VALUATIONS, PARCEL_ADDRESSES,
    )
    metadata_receipt = []
    for dataset in datasets:
        path = raw_dir / f"parcels_metadata_{dataset}.json"
        if refresh or not path.exists():
            response = requests.get(f"{COUNTY_DOMAIN}/api/views/{dataset}.json", timeout=(20, 90))
            response.raise_for_status()
            path.write_text(json.dumps(response.json(), indent=2), encoding="utf-8")
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata_receipt.append({
            "dataset": dataset,
            "name": metadata["name"],
            "source_url": f"{COUNTY_DOMAIN}/api/views/{dataset}.json",
            "source_rows_updated_at": metadata.get("rowsUpdatedAt"),
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "fields": [column["fieldName"] for column in metadata["columns"] if not column["fieldName"].startswith(":")],
        })

    city_townships = "township_code IN ('70','71','72','73','74','75','76','77')"
    current_year_scope = f"year={STUDY_TAX_YEAR} AND {city_townships}"
    specs = {
        "current_summary": (CURRENT_UNIVERSE, {
            "$select": "year,count(*) as rows,count(distinct pin) as distinct_pins,count(distinct pin10) as distinct_pin10,count(lat) as latitude_present,count(lon) as longitude_present",
            "$group": "year", "$order": "year", "$limit": 100,
        }),
        "current_chicago_correct": (CURRENT_UNIVERSE, {
            "$select": "year,count(*) as rows,count(distinct pin) as distinct_pins,count(distinct pin10) as distinct_pin10,count(lat) as latitude_present,count(lon) as longitude_present",
            "$where": "cook_municipality_name='CITY OF CHICAGO'", "$group": "year", "$limit": 100,
        }),
        "historical_summary": (HISTORICAL_UNIVERSE, {
            "$select": "year,count(*) as rows,count(lat) as coordinate_rows",
            "$where": f"year>=2006 AND year<={STUDY_TAX_YEAR} AND triad_code='1'",
            "$group": "year", "$order": "year", "$limit": 100,
        }),
        "apt_values": (RESIDENTIAL_CHARACTERISTICS, {
            "$select": "class,char_apts,count(*) as rows", "$where": current_year_scope,
            "$group": "class,char_apts", "$order": "class,char_apts", "$limit": 1000,
        }),
        "condo_flag_profile": (CONDOMINIUM_CHARACTERISTICS, {
            "$select": "is_parking_space,is_common_area,count(*) as rows,count(distinct pin) as distinct_pin14,count(distinct pin10) as distinct_pin10,count(char_yrblt) as year_built_present",
            "$where": current_year_scope, "$group": "is_parking_space,is_common_area", "$limit": 100,
        }),
        "recent_residential_characteristics": (RESIDENTIAL_CHARACTERISTICS, {
            "$select": "pin,year,card,class,tieback_key_pin,tieback_proration_rate,pin_is_multicard,pin_num_cards,char_yrblt,char_apts",
            "$where": f"year={STUDY_TAX_YEAR} AND char_yrblt>=2004 AND {city_townships}",
            "$order": "pin,year,card", "$limit": 50000,
        }),
        "condo_building_groups": (CONDOMINIUM_CHARACTERISTICS, {
            "$select": "pin10,is_parking_space,is_common_area,count(*) as rows,count(distinct pin) as distinct_pin14,min(char_yrblt) as min_year_built,max(char_yrblt) as max_year_built,min(char_building_pins) as min_building_pins,max(char_building_pins) as max_building_pins,min(char_building_non_units) as min_non_units,max(char_building_non_units) as max_non_units",
            "$where": current_year_scope, "$group": "pin10,is_parking_space,is_common_area",
            "$order": "pin10,is_parking_space,is_common_area", "$limit": 50000,
        }),
        "commercial_sample": (COMMERCIAL_VALUATIONS, {
            "$select": "keypin,pins,year,township,sheet,model,subclass2,tot_units,apt,yearbuilt",
            "$where": f"year={COMMERCIAL_REASSESSMENT_YEAR} AND tot_units IS NOT NULL",
            "$order": "tot_units DESC", "$limit": 20000,
        }),
        "commercial_chicago": (COMMERCIAL_VALUATIONS, {
            "$select": "year,township,model,count(*) as rows,count(distinct keypin) as keys,count(apt) as apt_present,count(tot_units) as total_present",
            "$where": f"year<={STUDY_TAX_YEAR}",
            "$group": "year,township,model", "$order": "year,township,model", "$limit": 2000,
        }),
    }
    envelope_outputs = {"current_summary", "current_chicago_correct", "apt_values", "commercial_chicago"}

    def fetch_profile(item: tuple[str, tuple[str, dict]]) -> tuple[str, int]:
        name, (dataset, params) = item
        rows = query_county(dataset, params=params, raw_dir=raw_dir, label=name, refresh=refresh)
        if len(rows) == 0:
            raise ValueError(f"County profile {name} unexpectedly returned no records")
        payload = {"url": f"{COUNTY_DOMAIN}/resource/{dataset}.json?{urlencode(params)}", "query": params, "rows": rows} if name in envelope_outputs else rows
        (raw_dir / f"parcels_{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return name, len(rows)

    with ThreadPoolExecutor(max_workers=4) as executor:
        counts = dict(executor.map(fetch_profile, specs.items()))
    receipt = {
        "study_tax_year": STUDY_TAX_YEAR,
        "commercial_reassessment_year": COMMERCIAL_REASSESSMENT_YEAR,
        "profile_rows": counts,
        "metadata": metadata_receipt,
        "unit_count_use": "corroboration_only_never_permitted_units",
    }
    (raw_dir / "parcels_supporting_profile_manifest.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt
