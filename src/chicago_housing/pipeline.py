"""Offline-first reproduction of permit counts, parcel checks and road exposure."""

import hashlib
import json
import tomllib
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import click
import duckdb
import pandas as pd
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from chicago_housing.analysis import (
    building_size,
    finite_number,
    grouped_summary,
    noise_screen,
    sensitivity_table,
    summarize,
)
from chicago_housing.exposure import (
    IDOT_URL,
    NOISE_DOC_URL,
    NOISE_URL,
    annotate_exposure,
    fetch_exposure_sources,
    project_noise_overlay,
)
from chicago_housing.parcels import fetch_supporting_profiles, run_parcel_audit
from chicago_housing.permits import (
    classify_permits,
    fetch_permits,
    load_reviewed_decisions,
    profile_permits,
    write_candidate_review,
)
from chicago_housing.supplemental import (
    annotate_geography,
    fetch_boundaries,
    fetch_cdot_profiles,
    profile_cdot_cache,
    validate_wards,
)
from chicago_housing.validation import corroborate_counts


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def attach_parcels(rows: list[dict], crosswalk: list[dict], *, last_complete_year: int = 2025) -> list[dict]:
    lookup = {row["id"]: row for row in crosswalk}
    if len(lookup) != len(crosswalk):
        raise ValueError("Parcel crosswalk is not unique by City record ID.")
    output = []
    for row in rows:
        item = dict(row)
        parcel = lookup.get(str(row["id"]))
        if parcel is None:
            raise ValueError(f"No crosswalk row for {row['id']}; regenerate the parcel audit.")
        item["permit_pin10s"] = parcel["permit_pin10s"]
        item["parcel_match_status"] = parcel["match_status"]
        item["county_pin14_count"] = parcel["current_county_pin14_count"]
        item["original_latitude"] = finite_number(row.get("latitude"))
        item["original_longitude"] = finite_number(row.get("longitude"))
        item["latitude"] = item["original_latitude"]
        item["longitude"] = item["original_longitude"]
        item["geocode_source"] = "city_permit"
        if item["latitude"] is None or item["longitude"] is None:
            item["geocode_source"] = "missing"
            recovery = parcel.get("coordinate_recovery", {})
            if recovery.get("eligible") is True:
                item["latitude"] = recovery["latitude"]
                item["longitude"] = recovery["longitude"]
                item["geocode_source"] = "current_county_centroid"
        item["building_size"] = building_size(item.get("unit_count"))
        item["partial_year"] = item["permit_year"] > last_complete_year
        output.append(item)
    return output


def _path(geometry, *, origin_x: float, top_y: float) -> str:
    if geometry.geom_type in {"MultiPolygon", "MultiLineString", "GeometryCollection"}:
        return " ".join(_path(part, origin_x=origin_x, top_y=top_y) for part in geometry.geoms)
    if geometry.geom_type == "Polygon":
        rings = [geometry.exterior, *geometry.interiors]
        return " ".join(_path(ring, origin_x=origin_x, top_y=top_y) + " Z" for ring in rings)
    coords = list(geometry.coords)
    return " ".join(
        f"{'M' if i == 0 else 'L'}{x-origin_x:.1f},{top_y-y:.1f}"
        for i, (x, y, *_) in enumerate(coords)
    )


def map_geometry(
    boundaries: dict, roads: dict, rows: list[dict], *, raw_dir: Path,
) -> tuple[dict, list[dict]]:
    projector = Transformer.from_crs("EPSG:4326", "EPSG:26916", always_xy=True)
    city = transform(projector.transform, shape(boundaries["city"]["features"][0]["geometry"]))
    min_x, min_y, max_x, max_y = city.bounds
    origin_x, top_y = min_x - 1200, max_y + 1200
    geometry = {"width": max_x-min_x+2400, "height": max_y-min_y+2400,
                "cityPaths": [_path(city.simplify(18), origin_x=origin_x, top_y=top_y)],
                "communities": [], "wards": [], "roads": []}
    geometry["noiseOverlay"] = project_noise_overlay(
        raw_dir / "noise_bts_2023_categories.tif",
        legend_path=raw_dir / "noise_bts_2023_legend.json",
        map_bounds=(origin_x, top_y-geometry["height"], origin_x+geometry["width"], top_y),
        resolution_m=30,
    )
    for feature in boundaries["communities"]["features"]:
        geom = transform(projector.transform, shape(feature["geometry"]))
        label = geom.representative_point()
        geometry["communities"].append({
            "id": feature["properties"]["area_numbe"],
            "name": feature["properties"]["community"].title(),
            "path": _path(geom.simplify(20), origin_x=origin_x, top_y=top_y),
            "labelX": label.x-origin_x, "labelY": top_y-label.y,
        })
    validate_wards(boundaries["wards"])
    for feature in sorted(boundaries["wards"]["features"], key=lambda f: int(f["properties"]["ward"])):
        geom = transform(projector.transform, shape(feature["geometry"]))
        label = geom.representative_point()
        geometry["wards"].append({
            "id": str(feature["properties"]["ward"]),
            "path": _path(geom.simplify(20), origin_x=origin_x, top_y=top_y),
            "labelX": label.x-origin_x, "labelY": top_y-label.y,
        })
    display_extent = city.buffer(700)
    for feature in roads["features"]:
        props = feature["properties"]
        if (props.get("AADT") or 0) < 10000 and (props.get("HCV_AADT") or 0) < 500:
            continue
        raw_geom = feature["geometry"]
        # IDOT serializes unused Z/M coordinates as null even when they are disabled.
        coordinates = raw_geom["coordinates"]
        if raw_geom["type"] == "MultiLineString":
            coordinates = [[point[:2] for point in part] for part in coordinates]
        else:
            coordinates = [point[:2] for point in coordinates]
        geom = transform(projector.transform, shape({**raw_geom, "coordinates": coordinates}))
        if not geom.intersects(display_extent):
            continue
        geometry["roads"].append({
            "path": _path(geom.simplify(20), origin_x=origin_x, top_y=top_y),
            "aadt": props.get("AADT"), "hcv": props.get("HCV_AADT"),
            "name": props.get("ROAD_NAME"),
        })
    plotted = []
    for row in rows:
        item = dict(row)
        item["map_x"], item["map_y"] = None, None
        if row.get("geocode_status") == "inside_city":
            x, y = projector.transform(row["longitude"], row["latitude"])
            item["map_x"], item["map_y"] = round(x-origin_x, 1), round(top_y-y, 1)
        plotted.append(item)
    return geometry, plotted


def flat_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
         for key, value in row.items()} for row in rows
    ])


def shared_project_exposure(rows: list[dict]) -> list[dict]:
    """Keep a shared project total once, without inventing units at each address."""
    by_id = {row["permit_id"]: row for row in rows}
    for row in rows:
        ids = row.get("shared_project_member_ids")
        if not ids or not row["included"]:
            continue
        members = [by_id[permit_id] for permit_id in ids]
        row["shared_project_locations"] = [
            {"permit_id": item["permit_id"], "address": item["address"],
             "latitude": item["latitude"], "longitude": item["longitude"],
             "ward_2023": item.get("ward_2023"), "ward_2023_status": item.get("ward_2023_status"),
             "noise_band": item["noise_band"]} for item in members]
        row["shared_project_exposure_incomplete"] = any(
            item["geocode_status"] != "inside_city" for item in members)
        fields = [f"nearest_aadt_{value}_distance_m" for value in [10000, 25000, 50000, 100000]]
        fields += [f"nearest_hcv_{value}_distance_m" for value in [500, 1000, 2500, 5000]]
        for field in fields:
            distances = [finite_number(item.get(field)) for item in members]
            if all(value is not None for value in distances):
                row[field + "_min"], row[field + "_max"] = min(distances), max(distances)
        lower = [finite_number(item.get("noise_db_lower")) for item in members]
        upper = [finite_number(item.get("noise_db_upper")) for item in members]
        row["noise_db_lower"] = min(lower) if all(value is not None for value in lower) else None
        row["noise_db_upper"] = max(upper) if all(value is not None for value in upper) else None
        if row["shared_project_exposure_incomplete"]:
            row["noise_db_lower"], row["noise_db_upper"] = None, None
        row["noise_status"] = "multi_address_band_envelope"
        row["noise_band"] = (
            f"{row['noise_db_lower']:g}–{row['noise_db_upper']:g} (project envelope)"
            if row["noise_db_lower"] is not None and row["noise_db_upper"] is not None else "Unknown project envelope")
        for threshold in [50, 55, 60, 65, 70]:
            row[f"noise_ge_{threshold}"] = noise_screen(row, threshold_db=threshold)
            for suffix in ["all", "any"]:
                field = f"noise_ge_{threshold}_{suffix}_3x3"
                values = [item.get(field) for item in members]
                row[field] = values[0] if all(value is values[0] for value in values) else None
    return rows


def source_metadata(raw_dir: Path, config: dict) -> dict:
    manifest = read_json(raw_dir / "permits_manifest.json")
    return {
        "label": "Chicago permits, Cook County parcels, IDOT traffic and BTS road-noise bands",
        "tables": ["City of Chicago ydr8-5enu", "Cook County pabr-t5kh", "City of Chicago p293-wvbd", "IDOT AADT Historical 2025",
                   "BTS road-noise published categories"],
        "files": ["permits_rows.json", "parcels_permit_crosswalk.json", "roads_idot_2025.geojson",
                  "noise_bts_2023_categories.tif", "boundary_wards.geojson", "reviewed_decisions.json"],
        "executedAt": manifest["retrieved_at"],
        "links": [
            {"label": "Chicago Building Permits", "url": "https://data.cityofchicago.org/d/ydr8-5enu"},
            {"label": "Cook County PIN universe", "url": "https://datacatalog.cookcountyil.gov/d/pabr-t5kh"},
            {"label": "Cook County residential characteristics", "url": "https://datacatalog.cookcountyil.gov/d/x54s-btds"},
            {"label": "City unit-change methodology", "url": "https://data.cityofchicago.org/stories/s/at9p-zwgd"},
            {"label": "Chicago ward boundaries (2023 onward)", "url": "https://data.cityofchicago.org/d/p293-wvbd"},
            {"label": "CDOT rolling traffic counts", "url": "https://data.cityofchicago.org/d/gc7y-n4xa"},
            {"label": "IDOT historical road counts", "url": IDOT_URL},
            {"label": "BTS road-noise map service", "url": NOISE_URL},
            {"label": "BTS noise methods", "url": NOISE_DOC_URL},
        ],
        "filters": [f"Permit dates {config['population']['start_date']} through {config['population']['cutoff_date']}",
                    "New construction and housing-related renovation candidates; not all permits"],
        "metricDefinitions": [
            {"label": "Identified new-construction units", "definition": "Sum of positive resolved dwelling-unit counts in included full-building new-construction records; excludes unresolved counts, preliminary phases, revisions, and duplicate authorizations. Not completed homes or net stock growth."},
            {"label": "Near a busy road", "definition": "Permit point lies within selected metres of an IDOT centreline meeting the selected annual-average daily vehicle count. A traffic-related pollution proxy, not measured pollution."},
            {"label": "Road noise", "definition": "Official BTS published road-only 24-hour LAeq bands, sampled from archived zoom-12 category tiles; approximately 28.5 m pixel width in Chicago. This is neither Lden nor indoor noise."},
            {"label": "Authorization size", "definition": "Dwelling units in one counted permit or consolidated project. Multi-building projects may share one count; this is not a census of physical building sizes."},
            {"label": "Housing groups", "definition": "Single family groups one-unit authorizations; multifamily groups authorizations with two or more units. This is a grouping of permitted unit counts, not an independently verified structure type. Multi-building projects can contain several one-family structures. Missing unit counts cannot be assigned to either group."},
            {"label": "Ward (2023 boundaries)", "definition": "Ward containing the permit point using the City's unsimplified May 2023 boundaries for every permit year. Unlocated, uncovered and boundary-ambiguous points are unassigned. Shared project totals are assigned only if every member point resolves to the same ward; unresolved totals remain unassigned."},
            {"label": "Share", "definition": "Exposed identified units divided by all identified units in the selected scope. Unknown exposure remains in the denominator. The upper endpoint assigns all unknown exposure to exposed; it does not bound missing housing counts."},
        ],
        "caveats": [
            "Permit unit counts are incomplete and some records remain unresolved; these are shares of identified units, not a certified census of all new housing.",
            "2026 is partial; permit dates usually indicate readiness for issuance and do not establish completion.",
            "Static 2025 road inventory and 2023-input road-noise model are applied to all historical permits; IDOT count vintages vary by segment.",
            "The BTS service name says 2022; the June 2026 documentation identifies road inputs as 2023. Raw numerical ZIP could not be retrieved; exact official category colours are archived instead.",
            "Transparent noise pixels mean below 45 dBA or unmapped; they are unknown, never quiet. The model omits building/barrier shielding.",
            "IDOT truck counts of zero commonly have no count year and do not establish zero truck traffic.",
            "Cook County current assessed stock corroborates permits only under restrictive grain/year checks; PINs are never counted as permitted homes.",
            "Ward groups use present 2023-onward boundaries, not wards as they existed when a historical permit was issued. Unassigned locations are excluded from ward-specific totals.",
            "Exact count reproduction requires a separately retained private file of reviewed record-specific decisions.",
        ],
        "evidenceFlow": [
            {"title": "Public City API", "detail": json.dumps({"provider": "Socrata", "requests": manifest["urls"], "sha256": manifest["sha256"]})},
            {"title": "Counting and parcel audit", "detail": "Run python -m chicago_housing.pipeline from the repository. Count rules: permits.py and the private reviewed_decisions.json; parcel identity/centroid rules: parcels.py; class-specific corroboration: validation.py."},
            {"title": "Exposure and weighting", "detail": "exposure.py samples archived road-noise bands and computes independent nearest-threshold-road distances in EPSG:26916; analysis.py sums units and retains unknown exposure. Exact source requests and hashes are cached in manifests."},
        ],
    }


def make_snapshot(rows: list[dict], geometry: dict, summaries: dict, profile: dict,
                  source: dict, *, app_path: Path) -> dict:
    snapshot_path = app_path / "src/data.json"
    previous = read_json(snapshot_path if snapshot_path.exists()
                         else app_path / "src/content/shared/snapshot-seed.json")
    fields = ["permit_id", "issue_date", "permit_year", "address", "unit_count", "unit_source",
              "analysis_class", "included", "review_flags", "duplicate_of", "project_id",
              "preliminary_candidate", "linked_full_permit_ids", "preliminary_link_method", "exclusion_reason",
              "shared_project_member_ids", "shared_project_locations", "shared_project_exposure_incomplete",
              "review_source_url",
              "structured_unit_change", "text_units", "work_description", "permit_type",
              "change_included", "text_net_unit_change", "change_review_flags", "change_duplicate_of",
              "latitude", "longitude", "geocode_status", "geocode_source", "community_name", "ward_2023", "ward_2023_status",
              "building_size", "partial_year", "parcel_match_status", "county_pin14_count",
              "county_count_status", "county_observed_units", "county_count_sources",
              "noise_db_lower", "noise_db_upper", "noise_band", "noise_status", "noise_boundary_3x3",
              "nearest_idot_road_name", "nearest_idot_aadt", "nearest_idot_aadt_year",
              "nearest_idot_hcv_aadt", "nearest_idot_road_distance_m", "net_unit_change",
              "map_x", "map_y"]
    fields += [f"nearest_aadt_{value}_distance_m" for value in [10000, 25000, 50000, 100000]]
    fields += [f"nearest_hcv_{value}_distance_m" for value in [500, 1000, 2500, 5000]]
    fields += [field + suffix for field in list(fields) if field.startswith("nearest_") and field.endswith("distance_m")
               for suffix in ["_min", "_max"]]
    fields += [f"noise_ge_{value}_{suffix}_3x3" for value in [50,55,60,65,70] for suffix in ["all", "any"]]
    app_rows = [{key: row.get(key) for key in fields} for row in rows]
    return {
        "id": previous["id"], "surface": "dashboard", "title": "Chicago housing & busy roads",
        "generatedAt": datetime.now(UTC).isoformat(), "status": "observed",
        "buildStatus": previous.get("buildStatus", "creating"), "exportPreferences": previous["exportPreferences"],
        "filters": [
            {"id": "community", "label": "Community", "field": "community_name", "defaultValue": "all", "queryIds": ["permits"], "searchable": True},
            {"id": "ward", "label": "Ward", "field": "ward_2023", "defaultValue": "all", "queryIds": ["permits"]},
            {"id": "building", "label": "Authorization size", "field": "building_size", "defaultValue": "all", "queryIds": ["permits"]},
        ],
        "queries": {
            "permits": {"rows": app_rows, "source": source},
            "count_profile": {"rows": [{"classification": k, "records": v} for k,v in profile["by_class"].items()], "source": source},
            "annual_profile": {"rows": [{"year": int(k), **v} for k,v in profile["by_year"].items()], "source": source},
            "audit_summary": {"rows": [{"scope": "All fetched years", "county_corroboration": json.dumps(summaries["county_corroboration"]),
                "cdot_profile": json.dumps(summaries["cdot_profile"]),
                "unlinked_preliminary_scenario": json.dumps(summaries["unlinked_preliminary_scenario"])}], "source": source},
            "map_geometry": {"rows": [{"geometry_json": json.dumps(geometry, separators=(",", ":"))}],
                             "payloadColumns": ["geometry_json"],
                             "source": {"label": "Chicago boundaries, IDOT roads and BTS road-noise categories", "links": [
                                 {"label": "City boundary", "url": "https://data.cityofchicago.org/d/qqq8-j68g"},
                                 {"label": "Community areas", "url": "https://data.cityofchicago.org/d/igwz-8jzy"},
                                 {"label": "Ward boundaries (May 2023 onward)", "url": "https://data.cityofchicago.org/d/p293-wvbd"},
                                 {"label": "IDOT roads", "url": IDOT_URL},
                                 {"label": "BTS road-noise map service", "url": NOISE_URL},
                                 {"label": "BTS noise methods", "url": NOISE_DOC_URL}],
                                 "files": ["boundary_city.geojson", "boundary_communities.geojson", "boundary_wards.geojson",
                                           "noise_bts_2023_categories.tif", "noise_bts_2023_legend.json"],
                                 "caveats": ["Projected in EPSG:26916; simplified 18–20 metres for display only. Analysis uses unsimplified geometry.",
                                             "Ward selection uses the May 2023-onward boundaries for every permit year, not historic ward boundaries; it does not change exposure inputs.",
                                             "BTS road-only category colors use 2023 inputs and are reprojected with nearest-neighbour sampling for display. Transparent areas may be below 45 dBA or unavailable; they are not classified as quiet."]}},
        },
        "analysisSummary": summaries,
        "permitProfile": profile,
    }


def run_pipeline(root: Path, *, fetch: bool) -> dict:
    config = tomllib.loads((root / "analysis.toml").read_text(encoding="utf-8"))
    population, exposure = config["population"], config["exposure"]
    start_date = date.fromisoformat(population["start_date"])
    cutoff_date = date.fromisoformat(population["cutoff_date"])
    complete_year = population["last_complete_year"]
    if start_date > cutoff_date or complete_year >= cutoff_date.year:
        raise ValueError("The configured dates must be ordered, with a separate partial latest year.")
    if (population["include_conversions_in_headline"]
            or not population["retain_unlocated_units_in_denominator"]
            or exposure["metric_crs"] != "EPSG:26916"
            or config["audit"]["missing_unit_count_is_zero"]
            or config["audit"]["parcel_count_is_unit_count"]
            or config["audit"]["unknown_exposure_is_unexposed"]):
        raise ValueError("The configured accounting invariants are not supported by this analysis.")
    supported = {"traffic_aadt": {10000, 25000, 50000, 100000},
                 "truck_hcv_aadt": {500, 1000, 2500, 5000},
                 "road_noise_db": {50, 55, 60, 65, 70}}
    for field, values in supported.items():
        if not set(exposure[field]).issubset(values):
            raise ValueError(f"Unsupported {field}; extend the archived exposure fields first.")
    for default, values in [("default_traffic_aadt", "traffic_aadt"),
                            ("default_noise_db", "road_noise_db"),
                            ("default_distance_m", "distance_m")]:
        if exposure[default] not in exposure[values]:
            raise ValueError(f"{default} must be one of the configured sensitivity values.")
    raw_dir, output = root / "data/raw", root / "data/processed"
    reviewed = load_reviewed_decisions(raw_dir / "reviewed_decisions.json")
    output.mkdir(parents=True, exist_ok=True)
    if fetch:
        click.echo("Fetching and caching public sources…")
        raw = fetch_permits(raw_dir, start_year=start_date.year,
                            end_date=(cutoff_date + timedelta(days=1)).isoformat())
        run_parcel_audit(raw, raw_dir)
        fetch_supporting_profiles(raw_dir)
        fetch_exposure_sources(raw_dir)
        fetch_boundaries(raw_dir)
        write_json(output / "cdot_profile.json", fetch_cdot_profiles(raw_dir))
    raw = read_json(raw_dir / "permits_rows.json")
    permit_manifest = read_json(raw_dir / "permits_manifest.json")
    if (start_date.year < permit_manifest["start_year"]
            or cutoff_date.isoformat() >= permit_manifest["end_date_exclusive"]):
        raise ValueError("The configured dates exceed the frozen permit cache. Acquire a separate matching snapshot.")
    raw = [row for row in raw if start_date.isoformat() <= row["issue_date"][:10] <= cutoff_date.isoformat()]
    click.echo("Classifying permit counts and checking parcel evidence…")
    classified = classify_permits(raw, reviewed=reviewed)
    write_candidate_review(classified, output)
    profile = profile_permits(raw, classified)
    classified = attach_parcels(classified, read_json(raw_dir / "parcels_permit_crosswalk.json"),
                                last_complete_year=complete_year)
    classified, county_profile = corroborate_counts(classified, raw_dir)
    frame = flat_frame(classified)
    frame.to_parquet(output / "permits_classified.parquet", index=False)
    # Preserve the full selected source population, including exclusions, for inspection.
    frame.to_csv(output / "permits_classified.csv", index=False)
    secondary = [r for r in classified if r["analysis_class"] in
                 {"conversion", "other_renovation", "addition"}]
    flat_frame(secondary).to_csv(output / "secondary_change_audit.csv", index=False)
    relevant = [r for r in classified if r["analysis_class"] in {"new_housing", "preliminary_housing"}
                or r.get("change_included")
                or (r["analysis_class"] in {"conversion", "other_renovation", "addition"}
                    and (r.get("text_net_unit_change") is not None
                         or r.get("structured_unit_change") is not None))]
    boundaries = {name: read_json(raw_dir / f"boundary_{name}.geojson") for name in ["city", "communities", "wards"]}
    relevant = annotate_geography(relevant, boundaries)
    click.echo(f"Computing road exposure for {len(relevant):,} housing and change records…")
    exposed = annotate_exposure(relevant, raw_dir)
    exposed = shared_project_exposure(exposed)
    geometry, exposed = map_geometry(boundaries, read_json(raw_dir / "roads_idot_2025.geojson"), exposed,
                                     raw_dir=raw_dir)
    thresholds = {"aadt": exposure["default_traffic_aadt"], "distance_m": exposure["default_distance_m"],
                  "noise_db": exposure["default_noise_db"]}
    full_years = [r for r in exposed if r["permit_year"] <= complete_year]
    current_year = [r for r in exposed if r["permit_year"] > complete_year]
    phase_candidates = [dict(r, included=True) for r in full_years if r.get("preliminary_candidate")]
    summaries = {
        "complete_years": summarize(full_years, **thresholds),
        "year_to_date": summarize(current_year, **thresholds),
        "county_corroboration": county_profile,
        "geocode_source": dict(Counter(r["geocode_source"] for r in exposed if r["included"])),
        "geocode_status": dict(Counter(r["geocode_status"] for r in exposed if r["included"])),
        "latest_permit_date": max(r["issue_date"] for r in raw)[:10],
        "thresholds": thresholds,
        "config": config,
        "cdot_profile": profile_cdot_cache(raw_dir),
        "unlinked_preliminary_scenario": summarize(
            [r for r in full_years if r["included"]] + phase_candidates, **thresholds),
        "unlinked_preliminary_candidates": summarize(phase_candidates, **thresholds),
    }
    summaries["city_geocodes_only"] = summarize(
        [r for r in full_years if r["geocode_source"] == "city_permit"], **thresholds)
    summaries["recent_complete_years_2024_2025"] = summarize(
        [r for r in full_years if 2024 <= r["permit_year"] <= 2025], **thresholds)
    write_json(output / "summary.json", summaries)
    write_json(output / "permit_profile.json", profile)
    write_json(output / "county_count_corroboration.json", county_profile)
    flat_frame(exposed).to_parquet(output / "permits_exposure.parquet", index=False)
    flat_frame(exposed).to_csv(output / "permits_exposure.csv", index=False)
    review = [r for r in exposed if r["analysis_class"] in {"new_housing", "preliminary_housing"} and not r["included"]]
    flat_frame(review).to_csv(output / "review_queue.csv", index=False)
    flat_frame([r for r in exposed if r["county_count_status"] == "differs" and r["included"]]).to_csv(
        output / "county_count_differences.csv", index=False)
    for field in ["permit_year", "building_size", "community_name", "unit_source"]:
        pd.DataFrame(grouped_summary(full_years, field, **thresholds)).to_csv(output / f"by_{field}.csv", index=False)
    pd.DataFrame(sensitivity_table(full_years, **{key: exposure[key] for key in
        ["traffic_aadt", "distance_m", "road_noise_db", "truck_hcv_aadt"]})).to_csv(
            output / "threshold_sensitivity.csv", index=False)
    flat_frame([r for r in exposed if r.get("preliminary_candidate")]).to_csv(
        output / "unlinked_preliminary_candidates.csv", index=False)
    flat_frame([r for r in exposed if r["included"] and r["unit_count"] >= config["audit"]["large_project_review_units"]]).to_csv(
        output / "large_project_audit.csv", index=False)
    exposed_frame = flat_frame(exposed)
    with duckdb.connect(str(output / "analysis.duckdb")) as db:
        db.register("all_rows", frame)
        db.register("exposed_rows", exposed_frame)
        db.execute("CREATE OR REPLACE TABLE permits AS SELECT * FROM all_rows")
        db.execute("CREATE OR REPLACE TABLE housing_exposure AS SELECT * FROM exposed_rows")
        sql_units = db.execute("SELECT coalesce(sum(unit_count),0) FROM housing_exposure WHERE included AND permit_year<=?",
                              [complete_year]).fetchone()[0]
        if sql_units != summaries["complete_years"]["traffic"]["units"]:
            raise AssertionError("Independent DuckDB unit total disagrees with weighted summary.")
    snapshot = make_snapshot(exposed, geometry, summaries, profile, source_metadata(raw_dir, config), app_path=root / "app")
    write_json(root / "app/src/data.json", snapshot)
    input_names = ["permits_rows.json", "parcels_permit_crosswalk.json", "roads_idot_2025.geojson",
                   "noise_bts_2023_categories.tif", "noise_bts_2023_legend.json", "boundary_city.geojson", "boundary_communities.geojson",
                   "boundary_wards.geojson", "boundary_wards.geojson.source.json", "boundary_wards_metadata.json",
                   "parcels_recent_residential_characteristics.json", "parcels_condo_building_groups.json",
                   "parcels_commercial_sample.json", "cdot_totals.json", "cdot_annual.json",
                   "cdot_latest_segments.json", "reviewed_decisions.json"]
    paths = [*[raw_dir / name for name in input_names], root / "analysis.toml", root / "uv.lock",
             root / "pyproject.toml", *sorted((root / "src/chicago_housing").glob("*.py")),
             *sorted((root / "app/src/content/dashboard").glob("*")), root / "app/src/theme.css"]
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    write_json(output / "run_manifest.json", {"generated_at": snapshot["generatedAt"], "input_sha256": hashes,
               "raw_permits": len(raw), "classified_permits": len(classified), "exposure_rows": len(exposed)})
    return summaries


@click.command()
@click.option("--fetch", is_flag=True, help="Fetch missing public source caches before recomputing.")
@click.option("--root", type=click.Path(path_type=Path), default=".")
def main(fetch: bool, root: Path) -> None:
    summary = run_pipeline(root.resolve(), fetch=fetch)
    click.echo(json.dumps(summary["complete_years"], indent=2))


if __name__ == "__main__":
    main()
