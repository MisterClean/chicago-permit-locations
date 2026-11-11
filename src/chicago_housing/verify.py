"""Read-only verification of the frozen inputs and independent SQL headline totals."""

import hashlib
import json
import math
from pathlib import Path

import click
import duckdb


def verify(root: Path) -> dict:
    output = root / "data/processed"
    manifest = json.loads((output / "run_manifest.json").read_text())
    failures = [name for name, digest in manifest["input_sha256"].items()
                if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
    if failures:
        raise ValueError(f"Files changed since this result was generated: {failures}")
    summary = json.loads((output / "summary.json").read_text())
    thresholds = summary["thresholds"]
    year = summary["config"]["population"]["last_complete_year"]
    with duckdb.connect(str(output / "analysis.duckdb"), read_only=True) as db:
        columns = {row[0] for row in db.execute("DESCRIBE housing_exposure").fetchall()}

        def distance_case(field: str) -> str:
            lower = f"coalesce({field}_min, {field})" if field + "_min" in columns else field
            upper = f"coalesce({field}_max, {field})" if field + "_max" in columns else field
            incomplete = " OR shared_project_exposure_incomplete" if "shared_project_exposure_incomplete" in columns else ""
            return (f"CASE WHEN geocode_status != 'inside_city'{incomplete} THEN NULL "
                    f"WHEN {upper} <= {thresholds['distance_m']} THEN TRUE "
                    f"WHEN {lower} > {thresholds['distance_m']} THEN FALSE ELSE NULL END")

        traffic = distance_case(f"nearest_aadt_{thresholds['aadt']}_distance_m")
        trucks = distance_case("nearest_hcv_1000_distance_m")
        noise = ("CASE WHEN geocode_status != 'inside_city' THEN NULL "
                 f"WHEN noise_db_lower >= {thresholds['noise_db']} THEN TRUE "
                 f"WHEN noise_db_upper <= {thresholds['noise_db']} THEN FALSE ELSE NULL END")
        db.execute(f"""CREATE TEMP VIEW screened AS SELECT *, {traffic} AS traffic,
                   {noise} AS noise, {trucks} AS trucks FROM housing_exposure
                   WHERE included AND permit_year <= {int(year)}""")
        count, unique = db.execute("SELECT count(*),count(distinct permit_id) FROM screened").fetchone()
        if count != unique:
            raise ValueError("Duplicate included permit identities.")
        for metric, expression in {"traffic": "traffic", "noise": "noise", "trucks": "trucks",
                                   "both": "traffic AND noise", "either": "traffic OR noise"}.items():
            total, exposed, unknown = db.execute(f"""SELECT coalesce(sum(unit_count),0),
                coalesce(sum(unit_count) FILTER (WHERE {expression}),0),
                coalesce(sum(unit_count) FILTER (WHERE ({expression}) IS NULL),0) FROM screened""").fetchone()
            expected = summary["complete_years"][metric]
            if (total, exposed, unknown) != (expected["units"], expected["exposed_units"], expected["unknown_units"]):
                raise ValueError(f"Independent SQL disagrees on {metric}.")
            if total and not math.isclose(exposed / total, expected["share_of_identified_units"], abs_tol=1e-12):
                raise ValueError(f"Independent SQL share disagrees on {metric}.")
    return {"verified_input_hashes": len(manifest["input_sha256"]),
            "unique_included_permits": count, "independent_sql_measures": 5}


@click.command()
@click.option("--root", type=click.Path(path_type=Path), default=".")
def main(root: Path) -> None:
    click.echo(json.dumps(verify(root.resolve()), indent=2))


if __name__ == "__main__":
    main()
