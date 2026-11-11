"""Unit-weighted screening measures with explicit unknown-exposure denominators."""

import math
from collections.abc import Callable
from functools import partial

type Screen = bool | None


def finite_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def building_size(units: int | None) -> str:
    if units is None:
        return "Unknown units"
    if units == 1:
        return "1 unit"
    if units <= 6:
        return "2–6 units"
    if units <= 49:
        return "7–49 units"
    return "50+ units"


def traffic_screen(row: dict, *, aadt: int, distance_m: int) -> Screen:
    """Proximity to a recorded threshold road, never absence of air pollution."""
    if row.get("geocode_status") != "inside_city":
        return None
    return distance_screen(row, f"nearest_aadt_{aadt}_distance_m", distance_m)


def distance_screen(row: dict, field: str, distance_m: int) -> Screen:
    if row.get("shared_project_exposure_incomplete"):
        return None
    distance = finite_number(row.get(field))
    lower = finite_number(row.get(field + "_min"))
    upper = finite_number(row.get(field + "_max"))
    if lower is not None and upper is not None:
        return True if upper <= distance_m else False if lower > distance_m else None
    return distance <= distance_m if distance is not None else None


def truck_screen(row: dict, *, hcv: int, distance_m: int) -> Screen:
    if row.get("geocode_status") != "inside_city":
        return None
    return distance_screen(row, f"nearest_hcv_{hcv}_distance_m", distance_m)


def noise_screen(row: dict, *, threshold_db: int) -> Screen:
    """A whole modeled band must establish which side of a threshold a home occupies."""
    if row.get("geocode_status") != "inside_city":
        return None
    lower = finite_number(row.get("noise_db_lower"))
    upper = finite_number(row.get("noise_db_upper"))
    if lower is not None and lower >= threshold_db:
        return True
    if upper is not None and upper <= threshold_db:
        return False
    return None


def joint_screen(left: Screen, right: Screen, *, operator: str) -> Screen:
    if operator == "both":
        if left is False or right is False:
            return False
        return True if left is True and right is True else None
    if operator == "either":
        if left is True or right is True:
            return True
        return False if left is False and right is False else None
    raise ValueError(f"Unknown screening operator: {operator}")


def weighted_summary(rows: list[dict], screen: Callable[[dict], Screen]) -> dict:
    """Known units remain in the denominator when their exposure cannot be established."""
    selected = [row for row in rows if row.get("included") is True]
    totals = {
        "permits": len(selected), "units": 0,
        "screened_units": 0, "exposed_units": 0, "unknown_units": 0,
        "exposed_permits": 0, "unknown_permits": 0,
    }
    for row in selected:
        units = row["unit_count"]
        if not isinstance(units, int) or units <= 0:
            raise ValueError("Included housing requires a positive integer unit count.")
        outcome = screen(row)
        totals["units"] += units
        if outcome is None:
            totals["unknown_units"] += units
            totals["unknown_permits"] += 1
        else:
            totals["screened_units"] += units
            if outcome is True:
                totals["exposed_units"] += units
                totals["exposed_permits"] += 1
    total, known = totals["units"], totals["screened_units"]
    return {
        **totals,
        "share_of_identified_units": totals["exposed_units"] / total if total else None,
        "share_of_screened_units": totals["exposed_units"] / known if known else None,
        "share_upper_if_unknown_exposed": (
            (totals["exposed_units"] + totals["unknown_units"]) / total if total else None
        ),
        "share_unknown": totals["unknown_units"] / total if total else None,
    }


def summarize(rows: list[dict], *, aadt: int, distance_m: int, noise_db: int) -> dict:
    screens = {
        "traffic": lambda row: traffic_screen(row, aadt=aadt, distance_m=distance_m),
        "noise": lambda row: noise_screen(row, threshold_db=noise_db),
        "trucks": lambda row: truck_screen(row, hcv=1000, distance_m=distance_m),
    }
    screens["both"] = lambda row: joint_screen(
        screens["traffic"](row), screens["noise"](row), operator="both")
    screens["either"] = lambda row: joint_screen(
        screens["traffic"](row), screens["noise"](row), operator="either")
    return {name: weighted_summary(rows, screen) for name, screen in screens.items()}


def grouped_summary(rows: list[dict], field: str, **thresholds: int) -> list[dict]:
    groups: dict[object, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get(field, "Unknown"), []).append(row)
    output = []
    for value, members in sorted(groups.items(), key=lambda item: str(item[0])):
        for metric, summary in summarize(members, **thresholds).items():
            output.append({field: value, "metric": metric, **summary})
    return output


def sensitivity_table(rows: list[dict], *, traffic_aadt: list[int],
                      distance_m: list[int], road_noise_db: list[int],
                      truck_hcv_aadt: list[int]) -> list[dict]:
    results = []
    for aadt in traffic_aadt:
        for distance in distance_m:
            results.append({
                "metric": "traffic", "aadt": aadt, "distance_m": distance,
                **weighted_summary(rows, partial(traffic_screen, aadt=aadt, distance_m=distance)),
            })
    for threshold in road_noise_db:
        results.append({
            "metric": "noise", "noise_db": threshold,
            **weighted_summary(rows, partial(noise_screen, threshold_db=threshold)),
        })
        for neighborhood in ["all", "any"]:
            field = f"noise_ge_{threshold}_{neighborhood}_3x3"
            results.append({
                "metric": f"noise_{neighborhood}_3x3", "noise_db": threshold,
                **weighted_summary(rows, lambda row, field=field: row.get(field)),
            })
    for hcv in truck_hcv_aadt:
        for distance in distance_m:
            results.append({
                "metric": "trucks", "hcv_aadt": hcv, "distance_m": distance,
                **weighted_summary(rows, partial(truck_screen, hcv=hcv, distance_m=distance)),
            })
    return results
