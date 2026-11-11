"""Cached official boundary and CDOT profiles, separate from the IDOT exposure model."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import click
import requests
from requests.adapters import HTTPAdapter
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from urllib3.util.retry import Retry


def fetch_json(url: str, destination: Path, *, params: dict | None = None) -> dict | list:
    """Cache a public JSON response and its exact request and digest for offline reruns."""
    if destination.exists():
        return json.loads(destination.read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=4, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
        )))
        response = session.get(url, params=params, timeout=120)
        response.raise_for_status()
        value = response.json()
    content = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    destination.write_bytes(content)
    destination.with_suffix(destination.suffix + ".source.json").write_text(json.dumps({
        "url": url,
        "parameters": params,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
    }, indent=2), encoding="utf-8")
    return value


def fetch_cdot_profiles(raw_dir: Path) -> dict:
    """Profile published rolling camera counts; do not combine them with IDOT AADT."""
    base = "https://data.cityofchicago.org/resource/gc7y-n4xa.json"
    fetch_json("https://data.cityofchicago.org/api/views/gc7y-n4xa.json",
               raw_dir / "cdot_metadata.json")
    totals = fetch_json(base, raw_dir / "cdot_totals.json", params={"$select":
        "count(*) as rows,count(distinct recordid) as distinct_records,"
        "count(distinct segmentid) as segments,min(timestamp) as first_date,"
        "max(timestamp) as last_date,count(vehiclecount) as counted_rows,"
        "min(vehiclecount) as min_count,max(vehiclecount) as max_count"})
    annual = fetch_json(base, raw_dir / "cdot_annual.json", params={
        "$select": "date_extract_y(timestamp) as year,count(*) as rows,"
                   "count(distinct segmentid) as segments,min(vehiclecount) as min_count,"
                   "max(vehiclecount) as max_count",
        "$group": "year", "$order": "year"})
    latest_date = totals[0]["last_date"]
    latest = fetch_json(base, raw_dir / "cdot_latest_segments.json", params={
        "$where": f"timestamp='{latest_date}'", "$order": "recordid", "$limit": 50000})
    return _cdot_profile(totals, annual, latest)


def profile_cdot_cache(raw_dir: Path) -> dict:
    """Reconstruct the supplemental profile offline; missing files fail explicitly."""
    rows = [json.loads((raw_dir / name).read_text(encoding="utf-8")) for name in
            ["cdot_totals.json", "cdot_annual.json", "cdot_latest_segments.json"]]
    return _cdot_profile(*rows)


def _cdot_profile(totals: list[dict], annual: list[dict], latest: list[dict]) -> dict:
    latest_date = totals[0]["last_date"]
    if len(latest) == 50000:
        raise ValueError("CDOT snapshot reached the pagination limit; extend pagination.")
    valid_coords = sum(
        41.5 <= float(r.get("midpointlat", 0)) <= 42.1
        and -88 <= float(r.get("midpointlon", 0)) <= -87.4 for r in latest
    )
    return {
        "totals": totals, "annual": annual, "latest_date": latest_date,
        "latest_rows": len(latest), "latest_plausible_coordinates": valid_coords,
        "role": "Supplementary coverage check only; not added to or averaged with IDOT AADT.",
        "limitations": [
            "Each daily value is a trailing 30-day average, not an independent daily count.",
            "Directional camera segments have incomplete and changing city coverage.",
            "CDOT reports higher accuracy and consistency from May 2026 onward.",
            "No truck classification is published in this dataset.",
        ],
    }


def fetch_boundaries(raw_dir: Path) -> dict:
    """Read the backing datasets identified by the official map metadata."""
    datasets = {"city": "qqq8-j68g", "communities": "igwz-8jzy", "wards": "p293-wvbd"}
    result = {}
    for name, dataset_id in datasets.items():
        fetch_json(f"https://data.cityofchicago.org/api/views/{dataset_id}.json",
                   raw_dir / f"boundary_{name}_metadata.json")
        result[name] = fetch_json(
            f"https://data.cityofchicago.org/resource/{dataset_id}.geojson",
            raw_dir / f"boundary_{name}.geojson", params={"$limit": 1000})
    if len(result["communities"]["features"]) != 77:
        raise ValueError("Expected Chicago's 77 community areas.")
    validate_wards(result["wards"])
    return result


def validate_wards(collection: dict) -> None:
    """Require all 50 current wards with usable polygon geometry."""
    features = collection["features"]
    if len(features) != 50 or {int(f["properties"]["ward"]) for f in features} != set(range(1, 51)):
        raise ValueError("Expected exactly one boundary for each Chicago ward, 1–50.")
    for feature in features:
        geometry = shape(feature["geometry"])
        if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"Invalid polygon for ward {feature['properties']['ward']}.")


def annotate_geography(rows: list[dict], boundaries: dict) -> list[dict]:
    """Assign communities and current wards without dropping unlocated units.

    Wards use the unsimplified 2023 polygons for all years. A point on a shared
    boundary, in a coverage gap, or outside Chicago stays unassigned. A shared
    project total is assigned only when all member locations resolve to the
    same ward, avoiding an arbitrary allocation of its units.
    """
    city = unary_union([shape(f["geometry"]) for f in boundaries["city"]["features"]])
    communities = [(shape(f["geometry"]), f["properties"])
                   for f in boundaries["communities"]["features"]]
    ward_features = boundaries["wards"]["features"]
    ward_tree = STRtree([shape(feature["geometry"]) for feature in ward_features])
    ward_numbers = [str(int(feature["properties"]["ward"])) for feature in ward_features]
    result = []
    for row in rows:
        item = dict(row)
        lon, lat = row.get("longitude"), row.get("latitude")
        item["geocode_status"] = "missing"
        item["community_name"] = "Unlocated"
        item["ward_2023"] = None
        item["ward_2023_status"] = "unlocated"
        if lon is not None and lat is not None:
            point = Point(float(lon), float(lat))
            item["geocode_status"] = "inside_city" if city.covers(point) else "outside_city"
            for geom, props in communities:
                if geom.covers(point):
                    item["community_name"] = props["community"].title()
                    item["community_number"] = int(props["area_numbe"])
                    break
            if item["geocode_status"] == "outside_city":
                item["ward_2023_status"] = "outside_city"
            else:
                matches = ward_tree.query(point, predicate="covered_by")
                if len(matches) == 1:
                    item["ward_2023"] = ward_numbers[matches[0]]
                    item["ward_2023_status"] = "assigned"
                elif len(matches) > 1:
                    item["ward_2023_status"] = "boundary_ambiguous"
                else:
                    item["ward_2023_status"] = "uncovered"
        result.append(item)

    by_id = {row["permit_id"]: row for row in result if row.get("permit_id") is not None}
    for row in result:
        members = row.get("shared_project_member_ids")
        if not row.get("included") or not members:
            continue
        member_rows = [by_id.get(permit_id) for permit_id in members]
        member_wards = {member["ward_2023"] for member in member_rows if member is not None}
        if (any(member is None or member["ward_2023_status"] != "assigned" for member in member_rows)
                or len(member_wards) != 1):
            row["ward_2023"] = None
            row["ward_2023_status"] = "shared_project_ambiguous"
    return result


def display_boundaries(boundaries: dict) -> dict:
    """Small display-only geometry; analysis always uses the full source polygons."""
    result = {}
    for name, collection in boundaries.items():
        features = []
        for feature in collection["features"]:
            geom = shape(feature["geometry"])
            props = feature["properties"]
            features.append({
                "type": "Feature", "properties": props,
                "geometry": mapping(geom.simplify(0.00015, preserve_topology=True)),
            })
        result[name] = {"type": "FeatureCollection", "features": features}
    return result


@click.command()
@click.option("--raw-dir", type=click.Path(path_type=Path), default="data/raw")
def main(raw_dir: Path) -> None:
    result = fetch_cdot_profiles(raw_dir)
    output = Path("data/processed/cdot_profile.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    click.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
