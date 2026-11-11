"""Fetch, profile, and conservatively resolve Chicago housing permit records.

The output remains one row per City permit. A unit count is a claim about a
project, not a count of permits, parcels, completed homes, or net stock growth.
Classification decisions and duplicate links are retained for audit.
"""

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

import requests

API_URL = "https://data.cityofchicago.org/resource/ydr8-5enu.json"
METADATA_URL = "https://data.cityofchicago.org/api/views/ydr8-5enu.json"
NEW_CONSTRUCTION = "PERMIT - NEW CONSTRUCTION"
RENOVATION = "PERMIT - RENOVATION/ALTERATION"
SELECT_FIELDS = (
    "id,permit_,permit_status,permit_milestone,permit_type,review_type,"
    "application_start_date,issue_date,street_number,street_direction,street_name,"
    "work_type,work_description,residential_unit_change,subtotal_paid,subtotal_unpaid,"
    "pin_list,community_area,ward,latitude,longitude,reported_cost"
)
NUMBER_WORDS = {
    "ONE": 1,
    "TWO": 2,
    "THREE": 3,
    "FOUR": 4,
    "FIVE": 5,
    "SIX": 6,
    "SEVEN": 7,
    "EIGHT": 8,
    "NINE": 9,
    "TEN": 10,
    "ELEVEN": 11,
    "TWELVE": 12,
    "THIRTEEN": 13,
    "FOURTEEN": 14,
    "FIFTEEN": 15,
    "SIXTEEN": 16,
    "SEVENTEEN": 17,
    "EIGHTEEN": 18,
    "NINETEEN": 19,
    "TWENTY": 20,
    "THIRTY": 30,
    "FORTY": 40,
    "FIFTY": 50,
    "SIXTY": 60,
    "SEVENTY": 70,
    "EIGHTY": 80,
    "NINETY": 90,
}


def load_reviewed_decisions(path: Path) -> dict:
    """Load the private, record-specific review decisions required by this analysis.

    The public repository deliberately excludes record identifiers. Each analysis
    run must use an explicit reviewed file; an absent or example-only file is an
    error, never an instruction to silently skip the corrections.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing private reviewed decisions: {path}. Exact results require the "
            "separately retained review file; see examples/reviewed-decisions.example.json."
        )
    reviewed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(reviewed, dict) or reviewed.get("schema_version") != 1:
        raise ValueError("Reviewed decisions must be a version 1 object.")
    if reviewed.get("example_only") is True:
        raise ValueError("The synthetic reviewed-decisions example is not an analysis input.")
    unit_counts = reviewed.get("unit_counts")
    scope_decisions = reviewed.get("scope_decisions")
    projects = reviewed.get("shared_projects")
    if not isinstance(unit_counts, dict) or not isinstance(scope_decisions, dict):
        raise TypeError("Reviewed unit counts and scope decisions must be objects.")
    if not isinstance(projects, list) or not (unit_counts or scope_decisions or projects):
        raise ValueError("Reviewed decisions must contain at least one documented decision.")
    for identifier, entry in unit_counts.items():
        if (not isinstance(identifier, str) or not identifier
                or not isinstance(entry, list) or len(entry) != 2
                or type(entry[0]) is not int or entry[0] <= 0
                or not isinstance(entry[1], str) or not entry[1].strip()):
            raise ValueError("A reviewed unit count has an invalid identifier, count, or reason.")
    for identifier, entry in scope_decisions.items():
        if (not isinstance(identifier, str) or not identifier
                or not isinstance(entry, list) or len(entry) != 2
                or entry[0] not in {"full", "uncertain"}
                or not isinstance(entry[1], str) or not entry[1].strip()):
            raise ValueError("A reviewed scope decision has an invalid identifier, scope, or reason.")
    project_members: set[str] = set()
    for project in projects:
        if not isinstance(project, dict):
            raise TypeError("Each reviewed shared project must be an object.")
        members = project.get("permit_ids")
        if (not isinstance(members, list) or len(members) < 2
                or any(not isinstance(member, str) or not member for member in members)
                or len(set(members)) != len(members) or project_members.intersection(members)
                or type(project.get("units")) is not int or project["units"] <= 0
                or not isinstance(project.get("reason"), str) or not project["reason"].strip()
                or not isinstance(project.get("source_url"), str) or not project["source_url"].strip()):
            raise ValueError("A reviewed shared project has invalid members, count, reason, or source.")
        project_members.update(members)
    return reviewed


UNIT_NOUN = r"(?:D\.?\s*U\.?S?\b|(?:DWELL?ING\s+)?UNITS?\b|APARTMENTS?\b)"
UNIT_ADJECTIVES = (
    r"(?:(?:NEW|TOTAL|RESIDENTIAL|RESIDENTAL|DWELLING|DWELING|RENTAL|CONDO|"
    r"CONDOMINIUM|STANDARD|EFFICIENCY|AFFORDABLE|APARTMENT)\s+){0,4}"
)


@lru_cache(maxsize=100_000)
def normalized_text(description: str) -> str:
    """Normalize typography and number words without modifying stored source text."""
    text = description.upper().replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\b(\d{1,2}),(\d{3})\b", r"\1\2", text)
    text = re.sub(r"\bA\.\s*D\.\s*U\.?", "ADU", text)
    text = re.sub(r"\bD\.\s*U\.?S?\b\.?", "DU", text)
    text = re.sub(r"\bS\.\s*F\.?\s*R\.?", "SFR", text)
    text = re.sub(r"\bS\.?Q\.?\s*F(?:EE)?T\.?", "SQFT", text)
    text = re.sub(r"\bS\.F\.", "SF", text)
    text = re.sub(r"\bHT\.", "HT", text)
    text = text.replace("EFIICIENCY", "EFFICIENCY")
    text = text.replace("SUPERSTURCTURE", "SUPERSTRUCTURE").replace("BUILD-OUT", "BUILDOUT")
    for misspelling, correction in {
        "THREEE": "THREE",
        "TWELEVE": "TWELVE",
        "FAILY": "FAMILY",
        "UNTI": "UNIT",
        "BUIDING": "BUILDING",
        "BUILIDNG": "BUILDING",
        "BLDG": "BUILDING",
        "APARMENT": "APARTMENT",
        "DETTACHED": "DETACHED",
        "DEETACHED": "DETACHED",
        "EXISITING": "EXISTING",
        "EXIXTING": "EXISTING",
        "EXSTING": "EXISTING",
        "ESISTING": "EXISTING",
        "CONSTRUCION": "CONSTRUCTION",
        "ADDTION": "ADDITION",
        "ADDITTION": "ADDITION",
        "SFH": "SFR",
        "EREC": "ERECT",
    }.items():
        text = re.sub(rf"\b{misspelling}\b", correction, text)
    # Strip retrospective cross-references before counting the original scope.
    text = re.sub(
        r"[\[(]\s*(?:ALSO\s+)?SEE\s+(?:REVISION|PERMIT|APPLICATION)[^\])]*[\])]", " ", text
    )
    text = re.sub(r"\bSEE REVISIONS?\s+#?.*$", " ", text)
    words = "|".join(NUMBER_WORDS)
    text = re.sub(
        rf"\b({words})\s+HUNDRED(?:\s+AND)?(?:[ -]({words}))?(?:[ -]({words}))?\b",
        lambda match: str(
            NUMBER_WORDS[match.group(1)] * 100
            + sum(NUMBER_WORDS[word] for word in match.groups()[1:] if word is not None)
        ),
        text,
    )
    text = re.sub(
        r"\b(TWENTY|THIRTY|FORTY|FIFTY|SIXTY|SEVENTY|EIGHTY|NINETY)[ -]"
        r"(ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE)\b",
        lambda match: str(NUMBER_WORDS[match.group(1)] + NUMBER_WORDS[match.group(2)]),
        text,
    )
    for word, number in NUMBER_WORDS.items():
        text = re.sub(rf"\b{word}\b", str(number), text)
    return text


def parse_integer(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(str(value))
    except ValueError:
        return None
    return int(numeric) if numeric.is_integer() else None


def extract_unit_candidates(description: str) -> list[dict]:
    """Return explicit counts and their matched evidence; do not blindly add them."""
    text = normalized_text(description)
    patterns = (
        (
            "explicit_units",
            rf"(?<![\d.])\(?([0-9]{{1,4}})\)?\s*[- ]?\s*{UNIT_ADJECTIVES}{UNIT_NOUN}",
        ),
        ("flat_type", r"\b([2-9]|1[0-9]|2[0-4])[- ]FLAT\b"),
        (
            "townhomes",
            r"\b([0-9]{1,3})\)?\s+(?:NEW\s+|ATTACHED\s+)?(?:[0-9]+[- ]STORY\s+)?TOWN\s?HO(?:ME|USE)S?\b",
        ),
        ("dwellings", r"\b([0-9]{1,4})\)?\s+DWELLINGS?\b(?!\s+UNITS?\b)"),
        ("construction_units", r"\b([0-9]{1,4})\s+NEW CONSTRUCTION\s+CONDOMINIUM\s+UNITS?\b"),
        ("family_type", r"\b([2-9])[- ]FAMILY\s+(?:RESIDEN\w*|BUILDING|DWELLING)"),
        (
            "single_family_homes",
            r"\b([0-9]{1,3})\)?\s+(?:NEW\s+|ATTACHED\s+|DETACHED\s+)?SINGLE[- ]?FAMILY\s+(?:HOMES|HOUSES|RESIDENCES|DWELLINGS)\b",
        ),
        ("efficiency_units", r"\b([0-9]{1,4})\s+EFFICIENCY(?=\s*[+&,])"),
    )
    candidates = []
    for method, pattern in patterns:
        for match in re.finditer(pattern, text):
            units = int(match.group(1))
            prefix = text[max(0, match.start() - 28) : match.start()]
            suffix = text[match.end() : match.end() + 24]
            if not 1 <= units <= 5000:
                continue
            if re.search(r"(?:HVAC|A/C|AIR CONDITIONING|CONDENSING|ROOF TOP|ROOFTOP)\s*$", prefix):
                continue
            if re.search(r"[RA]\s*-\s*$", prefix):
                continue  # Occupancy codes such as R-5 are not five dwellings.
            if re.match(r"\s*(?:HEATER|AIR|HVAC|CONDENSER)", suffix):
                continue
            if re.match(r"\s+[A-Z]\s*(?:&|AND)\s*[A-Z]\b", suffix):
                continue  # Address labels: '4302 UNITS A & B'.
            if re.match(r"\s+(?:TOWERS|BUILDINGS)\b", suffix):
                continue  # Count of apartment towers, not apartments.
            candidates.append(
                {
                    "units": units,
                    "method": method,
                    "evidence": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                    "existing_context": bool(re.search(r"\bEXIST\w*\b[^.]{0,25}$", prefix)),
                }
            )
    return sorted(candidates, key=lambda candidate: candidate["start"])


def extract_units(description: str) -> dict:
    """Resolve textual evidence, retaining conflicting and nested counts explicitly."""
    text = normalized_text(description)
    candidates = extract_unit_candidates(description)
    if (
        candidates
        and all(candidate["existing_context"] for candidate in candidates)
        and re.search(r"\bSINGLE[- ]?FAMILY\b|\bSFR\b", text)
    ):
        return {
            "units": 1,
            "source": "text_single_family_excluding_existing_count",
            "candidates": candidates,
            "ambiguous": False,
        }
    values = sorted({candidate["units"] for candidate in candidates})
    total = re.search(
        rf"\bTOTAL(?:ING|\s+OF)?\s*\(?([0-9]{{1,4}})\)?\s*[- ]?\s*{UNIT_ADJECTIVES}{UNIT_NOUN}"
        rf"|\b([0-9]{{1,4}})\)?\s+TOTAL\s+{UNIT_ADJECTIVES}{UNIT_NOUN}"
        r"|\b([0-9]{1,4})\)?\s+DWELLINGS?\s+TOTAL\b",
        text,
    )
    if total is not None:
        count = int(next(group for group in total.groups() if group is not None))
        return {
            "units": count,
            "source": "text_explicit_total",
            "candidates": candidates,
            "ambiguous": False,
        }
    split = re.search(
        r"\b([0-9]{1,4})\s+(?:STANDARD\s+)?(?:DWELLING\s+)?UNITS?\s*"
        r"(?:AND|\+|&)\s*([0-9]{1,4})\s+EFFICIENCY\s+(?:DWELLING\s+)?UNITS?",
        text,
    )
    if split is not None:
        return {
            "units": int(split.group(1)) + int(split.group(2)),
            "source": "text_disjoint_unit_types",
            "candidates": candidates,
            "ambiguous": False,
        }
    if len(values) == 1:
        return {
            "units": values[0],
            "source": "text_explicit_count",
            "candidates": candidates,
            "ambiguous": False,
        }
    if len(values) > 1:
        efficiency = [
            candidate for candidate in candidates if "EFFICIENCY" in candidate["evidence"]
        ]
        dwelling = [
            candidate
            for candidate in candidates
            if re.search(r"DWELLING|\bDU\b", candidate["evidence"])
        ]
        if efficiency and len(values) >= 3 and max(values) == sum(values[:-1]):
            return {
                "units": max(values),
                "source": "text_total_reconciled_to_unit_types",
                "candidates": candidates,
                "ambiguous": False,
            }
        if len(efficiency) == 1 and len(dwelling) == 1:
            count = efficiency[0]["units"] + dwelling[0]["units"]
            if set(values) <= {efficiency[0]["units"], dwelling[0]["units"], count}:
                return {
                    "units": count,
                    "source": "text_disjoint_unit_types",
                    "candidates": candidates,
                    "ambiguous": False,
                }
        # Explicit inclusion indicates the smaller figure is a subset of a total.
        nested = re.search(r"\b(?:INCLUDING|INCLUDES?|INCL\.|OF WHICH)", text)
        if nested is not None and candidates[0]["units"] == max(values):
            return {
                "units": max(values),
                "source": "text_count_with_subset",
                "candidates": candidates,
                "ambiguous": False,
            }
        return {
            "units": None,
            "source": "text_conflict",
            "candidates": candidates,
            "ambiguous": True,
        }
    if re.search(r"\bSINGLE[- ]?FAMILY\b|\bS\.?F\.?R\.?\b|\b1[- ]FAMILY\b", text):
        return {"units": 1, "source": "text_single_family", "candidates": [], "ambiguous": False}
    return {"units": None, "source": "no_text_count", "candidates": [], "ambiguous": False}


def is_whole_new_building(description: str) -> bool:
    """Require a construction phrase whose object is a building, not an accessory."""
    text = normalized_text(description)
    if "BUILDOUT OF ALL FLOORS FOR NEW" in text:
        return True
    if "NEW CONSTRUCTION/BUILDOUT PHASE" in text:
        return True
    full = re.search(r"\bFULL BUILDING\b", text)
    if full is not None:
        scope = text[full.end() : full.end() + 130]
        garage = re.search(r"\bGARAGE\b", scope)
        housing = re.search(r"\b(?:RESIDENTIAL|RESIDENCE|DWELLING|UNITS?|TOWER|BUILDING)\b", scope)
        garage_primary = garage is not None and (
            housing is None or garage.start() < housing.start()
        )
        if not garage_primary and not re.search(
            r"\bINTERIOR RENOVATION|\bFOR INTERIOR|\bRESTORATION|\bADDITION TO EXISTING|"
            r"\bADDITIONAL DWELLING UNIT (?:ADDITION|CONVERSION)\b|"
            r"\bFOR (?:THE |AN? )?EXISTING\b|"
            r"\bFOR (?:AN? |THE )?ADDITION|\b(?:PERMIT )?UNDER\b",
            scope,
        ):
            return True
    pattern = (
        r"\b(?:ERECT(?:ION OF)?|CONSTRUCT(?:ION OF)?|BUILD NEW|PROPOSED (?:A )?NEW|NEW CONSTRUCTION(?: OF)?|"
        r"(?:NEW|PROPOSED)(?=\s+(?:[0-9]|RESIDENTIAL|APARTMENT|MIXED|SINGLE|MASONRY|WOOD|FRAME|BRICK|SOLID|TYPE|CLASS))|"
        r"FULL BUILDING PERMIT FOR)(?:\s+|\s*[,/-]\s*)(?:A\s+|AN\s+|THE\s+|NEW\s+)?"
        r"[^.]{0,240}?\b(?:BUILDING|BUILIDNG|RESIDENCE|SFR|SINGLE[- ]FAMILY|HOUSE|HOME|"
        r"TOWERS?|HI-?RISE|HIGHRISE|TOWN\s?HOUSES?|TOWN\s?HOMES?|DWELLING UNITS?|DEVELOPMENT|STRUCTURE|FLAT)\b"
    )
    for match in re.finditer(pattern, text):
        span = match.group(0)
        # A garage can be an attached amenity before the housing noun, e.g.
        # "erect 3 story with basement and garage 6 unit townhouse building".
        span = re.sub(
            r"(?:\bWITH\b|\bW/|&)\s*(?:(?:AN?|BASEMENT|BSMT|ATTACHED|DETACHED|PRIVATE|[0-9]+|CAR|AND)\s+|&\s*)*GARAGES?\b",
            " ",
            span,
        )
        amenity = re.search(r"\b(?:PORCH\w*|DECKS?|ROOF\w*|BALCON\w*|STAIR\w*)\b", span)
        if (
            amenity is not None
            and re.search(r"\b(?:WITH|BASEMENT|BSMT|ATTIC|PLUS)\b|W/", span[: amenity.start()])
            and not re.search(r"\b(?:FOR|TO|AT|OF)\b", span[amenity.end() :])
        ):
            span = re.sub(r"\b(?:PORCH\w*|DECKS?|ROOF\w*|BALCON\w*|STAIR\w*)\b", " ", span)
        if re.search(
            r"\b(?:GARAGES?|PORCH\w*|DECKS?|FENCE|DORMER|ADDITION\w*|INTERIOR|EXIST\w*|ROOF\w*|BALCON\w*|CANOPY|STAIR\w*|CONVERSION|RENOVATION|REPAIR|FLOOR|RAMP|METER|LAUNDRY|CODE|PERGOLA|WALLS?|HANDICAP|1 TO ADDRESS|RETAIL SPACES|PARTITIONS?|ACCESSIBLE ROUTE|HVAC|COOLING|ELECTRICAL|PLUMBING|MECHANICAL|WINDOWS?|DOORS?|PARKING SPACES|PARKING STALLS|LANDSCAPING|ELEVATOR TOWER|LANDING|STEPS)\b",
            span,
        ):
            continue
        if "PROPOSED NEW USE" in span:
            continue
        if span.startswith("NEW CONSTRUCTION TYPE") and re.search(
            r"\b(?:EXIST\w*|RENOV\w*|INTERIOR)\b", text[: match.start()]
        ):
            continue
        prefix = text[max(0, match.start() - 40) : match.start()]
        if re.search(r"EXISTING\s*$|PREVIOUSLY PERMITTED\s*$", prefix):
            continue
        if (
            span.startswith(("NEW ", "PROPOSED "))
            and not span.startswith("NEW CONSTRUCTION")
            and re.search(
                r"\b(?:CURRENT|EXIST\w*|EXSITING|CONVER\w*|DECONVER\w*|RENOV\w*|ALTERATION\w*|INTERIOR|NO CHANGE|INTO|REPLACE)\b",
                text[: match.start()],
            )
            and not (
                re.search(r"\bDEMO(?:LISH\w*|LITION)?\b", text[: match.start()])
                and not re.search(
                    r"\b(?:REMODEL\w*|RENOV\w*|INTERIOR|CONVER\w*|DECONVER\w*)\b",
                    text[: match.start()],
                )
            )
        ):
            continue
        if re.match(r"\s+(?:ADDITION|GARAGE|DETACHED CARPORT|SYSTEMS?)\b", text[match.end() :]):
            continue
        if re.match(
            r"(?:\s+(?:RESIDENCE|BUILDING|[0-9]+|STORY|FRAME)){0,3}\s+ADDITION\b",
            text[match.end() :],
        ):
            continue
        return True
    return False


def has_accessory_or_existing_scope(description: str, extracted: dict) -> bool:
    """Distinguish a garage as the work's object from parking in a new building.

    Housing and parking often share one permit. Merely mentioning a garage,
    roof deck, or interior work must not erase an otherwise explicit building.
    """
    text = normalized_text(description)
    text = re.sub(r"\bEXISTING OCCUPANCY\s*:?\s*[RA]-?[0-9]\b", " ", text)
    if re.search(
        r"\b(?:ADDITIONS?|RENOV\w*|REPAIR\w*|REHAB\w*|REMODEL\w*|RESTORATION|REPLACEMENT|ALTERATIONS?)\b",
        text,
    ):
        return True
    if re.search(r"\b(?:EXISTING|EXIST'G|EXISTNG)\b", text):
        return True
    if re.search(
        r"\b(?:INITIAL|INTERIOR) BUILDOUT|\bTENANT (?:SPACE|BUILDOUT)|\bSFR DETACHED CARPORT", text
    ):
        return True
    accessory = re.search(
        r"\b(?:GARAGES?|PORCH|DECK|FENCE|DORMER|INTERIOR|BUILDOUT|CARPORT)\b",
        text,
    )
    if accessory is None:
        return False
    housing = re.search(
        r"\b(?:RESIDENTIAL|APARTMENTS?|SINGLE[- ]?FAMILY|SFR|DWELLINGS?|TOWERS?|HIGHRISE|HI-RISE|BUILDING|TOWNHOUSES?|TOWNHOMES?)\b",
        text,
    )
    housing_positions = [candidate["start"] for candidate in extracted["candidates"]]
    if housing is not None:
        housing_positions.append(housing.start())
    return not housing_positions or accessory.start() < min(housing_positions)


def is_preliminary_only(description: str) -> bool:
    """Separate limited structural permits from authorization of the full building."""
    text = normalized_text(description)
    full = re.search(r"\bFULL BUILDING\b", text[:250])
    foundation = re.search(r"\b(?:FOUNDATION|CAISSON|SUPERSTRUCTURE)", text[:250])
    if full is not None and (foundation is None or full.start() < foundation.start()):
        return False
    # References to an earlier structural permit do not narrow the current scope.
    text = re.sub(r"\([^)]*(?:FOUNDATION|CAISSON|SUPERSTRUCTURE)[^)]*PERMIT[^)]*\)", " ", text)
    text = re.sub(
        r"\b(?:REFERENCE|UPON PREVIOUSLY APPROVED|PREVIOUSLY PERMITTED|UPON SEPARATE)\s+(?:FOUNDATIONS?|CAISSONS?|PILES?|SUPERSTRUCTURE)[^.]*",
        " ",
        text,
    )
    text = re.sub(
        r"\b(?:FOUNDATIONS?|CAISSONS?|PILES?|SUPERSTRUCTURE)\b[^.]{0,120}?"
        r"(?:PERMITTED UNDER|PERMIT UNDER|UNDER SEPARATE PERMIT|PREVIOUSLY ISSUED UNDER|"
        r"APPROVED UNDER|UNDER\s*#?\s*10[0-9]{7})[^.]*",
        " ",
        text,
    )
    return (
        bool(
            re.search(
                r"\b(?:FOUNDATIONS?|CAISSONS?|PILES?)\s*[- ]*(?:ONLY|PERMIT ONLY)"
                r"|\b(?:FOUNDATIONS?|CAISSONS?|PILES?)\b[^.]{0,100}?\bONLY\b"
                r"|\bFOUNDATIONS?\s+(?:AND|&|/)\s+(?:PARTIAL\s+|PODIUM\s+|CONCRETE\s+)?(?:SUPERSTRUCTURE|STRUCTURE|[0-9]+\s*(?:\([0-9]+\))?\s*BASEMENT)"
                r"|\b(?:CAISSONS|FOUNDATIONS)\b[^.]{0,70}?\b(?:THROUGH|UP TO|UNDERSIDE OF)\b"
                r"|\bEXCAVATION FOR GRADE BEAMS\b"
                r"|\b(?:PARTIAL SUPERSTRUCTURE|SUPERSTRUCTURE FOR LEVEL|BUILDING ELEVATOR CORE PERMIT ONLY)\b"
                r"|\bSUPERSTRUCTURE PERMIT SUBMITTAL\b",
                text,
            )
        )
        or bool(
            re.search(
                r"\bFOUNDATIONS? PERMIT (?:FOR|SCOPE|SUBMITTAL)\b"
                r"|\bFOUNDATION,\s*SUPERSTRUCTURE\b"
                r"|\bFOUNDATION SYSTEMS AND STRUCTURAL SHELL\b"
                r"|\b(?:ERECT|INSTALL|CONSTRUCTION OF)\s+(?:NEW\s+)?CAISSONS?\b"
                r"|\bERECT SUPERSTRUCTURE\b"
                r"|\bPHASE 1 - CAISSON FOUNDATION\b"
                r"|\bCAISSONS, SHALLOW FOUNDATIONS\b"
                r"|\bFOUNDATION AND GROUND FLOOR SLAB FOR A NEW\b"
                r"|\bSUPERSTRUCTURE\s*(?:\(STRUCTURAL REVIEW\)\s*)?ONLY\b"
                r"|\bSCOPE OF WORK\s*:\s*CAISSONS\b",
                text[:180],
            )
        )
        or bool(re.search(r"\bFOUNDATION FOR\b", text[:80]))
    )


def classify_permit(row: dict, *, reviewed: dict) -> dict:
    """Annotate a City record with scope, count evidence, and review requirements."""
    description = str(row.get("work_description") or "")
    text = normalized_text(description)
    text_without_refs = re.sub(
        r"[\[(]?\s*(?:ALSO\s+)?SEE\s+(?:REVISION|PERMIT|APPLICATION)[^\])]*[\])]",
        " ",
        text,
    )
    text_without_refs = re.sub(r"\bZONING AMENDMENT\b", "ZONING APPROVAL", text_without_refs)
    extracted = extract_units(description)
    structured = parse_integer(row.get("residential_unit_change"))
    result = dict(row)
    result.update(
        {
            "permit_id": str(row.get("permit_") or row.get("id") or ""),
            "address": " ".join(
                str(row.get(key) or "").strip().upper()
                for key in ("street_number", "street_direction", "street_name")
            ).strip(),
            "permit_year": int(str(row["issue_date"])[:4]),
            "unit_count": None,
            "unit_source": extracted["source"],
            "text_units": extracted["units"],
            "structured_unit_change": structured,
            "unit_candidates": extracted["candidates"],
            "analysis_class": "unresolved",
            "included": False,
            "review_flags": [],
            "duplicate_of": None,
            "project_id": str(row.get("permit_") or row.get("id") or ""),
        }
    )
    if str(row.get("permit_status", "")).upper() in {"VOID", "REVOKED", "CANCELLED", "CANCELED"}:
        result["analysis_class"] = "excluded_status"
        return result
    if re.search(
        r"\b(?:REVISIONS?|REVISE[SD]?|AMENDMENT|REINSTATE)\b"
        r"|\bAMEND(?:ED|ING)?\s+(?:EXISTING\s+)?PERMIT\b"
        r"|^AMEND SITE LOCATION\b",
        text_without_refs,
    ):
        result["analysis_class"] = "revision"
        return result
    if re.search(r"\b(?:VEHICLE BRIDGE|TOWER CRANE|CONSTRUCTION HOIST)\b", text[:250]):
        result["analysis_class"] = "accessory_or_existing"
        return result
    if re.search(r"\b(?:MAUSOLEUM|CRYPT|SELF[- ]STORAGE)\b", text):
        result["analysis_class"] = "nonresidential_or_unresolved"
        return result
    whole_building = is_whole_new_building(description)
    scope_decision = reviewed["scope_decisions"].get(result["permit_id"])
    if scope_decision is not None:
        whole_building = True
        result["scope_review_decision"] = scope_decision[1]
        if scope_decision[0] == "uncertain":
            result["review_flags"].append("uncertain_primary_scope")
    if (
        row.get("permit_type") == RENOVATION
        and re.search(r"\bREPAIR DUE TO FIRE|\bREMODEL\b", text)
        and "NEW CONSTRUCTION" not in text
    ):
        whole_building = False
    conversion = re.search(r"\b(?:DE[- ]?CONVERT\w*|CONVERT\w*|CONVERSION|DECONVERSION)\b", text)
    if conversion is not None and not whole_building:
        result["analysis_class"] = "conversion"
        result["net_unit_change"] = (
            structured if structured is not None else conversion_net_units(description)
        )
        return result
    if not whole_building and re.search(r"\bADDITIONAL DWELLING UNIT ADDITION\b", text):
        result["analysis_class"] = "addition"
        result["net_unit_change"] = structured
        return result
    if row.get("permit_type") != NEW_CONSTRUCTION and not whole_building:
        result["analysis_class"] = "other_renovation"
        result["net_unit_change"] = (
            structured if structured is not None else conversion_net_units(description)
        )
        return result
    preliminary_only = is_preliminary_only(description)
    if not whole_building and has_accessory_or_existing_scope(description, extracted):
        is_adu = bool(
            re.search(r"COACH\s?HOUSE|\bADU\b|ACCESSORY DWELLING|ADDITIONAL DWELLING UNIT", text)
        ) and not re.search(r"\b(?:ADDITION|RENOV\w*|REPAIR\w*|REMODEL\w*|ALTERATION\w*)\b", text)
        limited_new_building = preliminary_only and not re.search(r"\bADDITION\b", text)
        if not is_adu and not limited_new_building:
            result["analysis_class"] = "accessory_or_existing"
            return result
    residential = bool(
        re.search(
            r"\b(?:RESIDENTIAL|RESIDENCE|SFR|SINGLE[- ]?FAMILY|APARTMENTS?|CONDOMINIUM|"
            r"DWELL?ING|TOWNHOMES?|TOWNHOUSES?|CONDO|D\.?U\.?|[2-9][- ]FLAT)\b",
            text,
        )
    )
    if not residential and (structured is None or structured <= 0) and extracted["units"] is None:
        result["analysis_class"] = "nonresidential_or_unresolved"
        return result
    if re.search(
        r"\b(?:HOTELS?|MOTELS?|DORMITORY|STUDENT HOUSING|NURSING HOME|ASSISTED LIVING|RESIDENCE HALL|GROUP LIVING|SUPPORTIVE LIVING|RETIREMENT FACILITY)\b",
        text,
    ):
        if re.search(r"CONDOMINIUM|\bRENTAL\b|\bDWELLING UNITS RESIDENTIAL\b", text):
            result["review_flags"].append("group_quarters_or_mixed_use")
        else:
            result["analysis_class"] = "group_quarters_or_hotel"
            return result
    if extracted["ambiguous"]:
        result["review_flags"].append("conflicting_text_counts")
    text_count = extracted["units"]
    if structured is not None and structured > 0:
        result["unit_count"] = structured
        result["unit_source"] = "city_net_change" if text_count is None else "city_and_text_agree"
        if text_count is not None and text_count != structured:
            result["review_flags"].append("city_text_disagreement")
            result["unit_source"] = "city_text_disagreement"
    elif text_count is not None:
        result["unit_count"] = text_count
        if structured is not None and structured <= 0:
            result["review_flags"].append("city_zero_or_negative_text_positive")
    if result["unit_count"] is None:
        result["review_flags"].append("missing_unit_count")
    if re.search(r"\b(?:FOUNDATION|CAISSONS?|PILES?|SUPERSTRUCTURE)\b", text):
        result["review_flags"].append("staged_construction")
    if not row.get("latitude") or not row.get("longitude"):
        result["review_flags"].append("missing_location")
    if re.search(r"\bLEGALIZ\w*\b", text) and re.search(r"\bBUILT WITHOUT\b", text):
        result["review_flags"].append("legalization_of_existing_construction")
    result["analysis_class"] = "new_housing"
    blocking_flags = {
        "city_text_disagreement",
        "conflicting_text_counts",
        "group_quarters_or_mixed_use",
        "missing_unit_count",
        "city_zero_or_negative_text_positive",
        "legalization_of_existing_construction",
        "uncertain_primary_scope",
    }
    result["included"] = result["unit_count"] is not None and not (
        blocking_flags & set(result["review_flags"])
    )
    if result["permit_id"] in reviewed["unit_counts"]:
        units, reason = reviewed["unit_counts"][result["permit_id"]]
        result["unit_count"] = units
        result["unit_source"] = "reviewed_permit_description"
        result["review_decision"] = reason
        resolved_flags = {
            "conflicting_text_counts",
            "missing_unit_count",
            "group_quarters_or_mixed_use",
        }
        result["resolved_review_flags"] = [
            flag for flag in result["review_flags"] if flag in resolved_flags
        ]
        result["review_flags"] = [
            flag for flag in result["review_flags"] if flag not in resolved_flags
        ]
        result["included"] = not (blocking_flags & set(result["review_flags"]))
    if preliminary_only:
        result["analysis_class"] = "preliminary_housing"
        result["included"] = False
        result["review_flags"].append("preliminary_structural_authorization_only")
    return result


def conversion_net_units(description: str) -> int | None:
    """Only explicit before/after pairs or explicit added units are resolved."""
    text = normalized_text(description).replace("DE-CONVER", "DECONVER")
    whole_building = re.search(
        rf"\bBUILDING\s+(?:DE)?CONVERSION\s+FROM[^.]{{0,70}}?([0-9]+)\s*{UNIT_NOUN}"
        rf"[^.]{{0,20}}?\bTO[^.]{{0,70}}?([0-9]+)\s*{UNIT_NOUN}",
        text,
    )
    if whole_building is not None:
        return int(whole_building.group(2)) - int(whole_building.group(1))
    pair = re.search(
        rf"(?:FROM|(?:DE)?CONVER\w*(?:\s+OF)?)\s+(?:EXISTING\s+)?\(?([0-9]+)\)?\s*{UNIT_NOUN}"
        rf"[^.]{{0,60}}?\b(?:TO|INTO)\s+(?:A\s+)?\(?([0-9]+)\)?\s*{UNIT_NOUN}",
        text,
    )
    if pair is not None:
        return int(pair.group(2)) - int(pair.group(1))
    single = re.search(
        rf"(?:FROM|(?:DE)?CONVER\w*)[^.]{{0,40}}?\(?([0-9]+)\)?\s*{UNIT_NOUN}"
        r"[^.]{0,50}?\b(?:TO|INTO)\s+(?:A\s+)?SINGLE[- ]FAMILY",
        text,
    )
    if single is not None:
        return 1 - int(single.group(1))
    if "DECONVER" in text:
        return None
    added = re.search(
        rf"\b(?:ADD|ADDING|ADDITION OF)\s+\(?([0-9]+)\)?\s*{UNIT_ADJECTIVES}{UNIT_NOUN}",
        text,
    )
    return int(added.group(1)) if added is not None else None


def resolve_secondary_changes(classified: list[dict]) -> None:
    """Reconcile explicit net changes without treating final building totals as gains."""
    seen: dict[tuple[str, int], dict] = {}
    for row in sorted(classified, key=lambda item: (item["issue_date"], item["permit_id"])):
        row["change_included"] = False
        row["change_review_flags"] = []
        row["text_net_unit_change"] = None
        row["net_unit_change"] = None
        if row["analysis_class"] not in {"conversion", "other_renovation", "addition"}:
            continue
        text_net = conversion_net_units(row["work_description"])
        city_net = row["structured_unit_change"]
        row["text_net_unit_change"] = text_net
        if text_net is not None and city_net is not None and text_net != city_net:
            row["change_review_flags"].append("city_text_net_change_disagreement")
        if is_preliminary_only(row["work_description"]):
            row["change_review_flags"].append("preliminary_change_authorization_only")
        net = city_net if city_net is not None else text_net
        if net is None:
            row["change_review_flags"].append("missing_net_change")
        if row["change_review_flags"]:
            continue
        if net != 0 and row["address"]:
            key = (row["address"], net)
            previous = seen.get(key)
            if previous is not None:
                elapsed = (
                    date.fromisoformat(row["issue_date"][:10])
                    - date.fromisoformat(previous["issue_date"][:10])
                ).days
                same_text = normalized_text(row["work_description"]) == normalized_text(
                    previous["work_description"]
                )
                explicit_reference = previous["permit_id"] in row["work_description"]
                if elapsed <= 730 and (same_text or explicit_reference):
                    row["change_review_flags"].append("duplicate_secondary_change_scope")
                    row["change_duplicate_of"] = previous["permit_id"]
                    continue
            seen[key] = row
        row["net_unit_change"] = net
        row["change_included"] = True


def classify_permits(rows: list[dict], *, reviewed: dict) -> list[dict]:
    """Resolve counts, exclude preliminary stages, and audit repeat authorizations."""
    classified = [classify_permit(row, reviewed=reviewed) for row in rows]
    seen: dict[tuple[str, int], dict] = {}
    for row in sorted(classified, key=lambda item: (item["issue_date"], item["permit_id"])):
        if row["analysis_class"] != "new_housing" or row["unit_count"] is None:
            continue
        key = (row["address"], row["unit_count"])
        if not row["address"]:
            row["review_flags"].append("missing_address")
            continue
        previous = seen.get(key)
        if previous is not None:
            elapsed = (
                date.fromisoformat(row["issue_date"][:10])
                - date.fromisoformat(previous["issue_date"][:10])
            ).days
            if elapsed <= 730:
                same_text = normalized_text(row["work_description"]) == normalized_text(
                    previous["work_description"]
                )
                explicit_reference = previous["permit_id"] in row["work_description"]
                if same_text or explicit_reference:
                    row["duplicate_of"] = previous["permit_id"]
                    row["project_id"] = previous["project_id"]
                    row["included"] = False
                    row["review_flags"].append("duplicate_text_or_explicit_permit_reference")
                    continue
                row["review_flags"].append("possible_repeat_address_count_within_730_days")
                row["included"] = False
        seen[key] = row
    link_shared_projects(classified, reviewed=reviewed)
    link_preliminary_permits(classified)
    resolve_secondary_changes(classified)
    for row in classified:
        row["exclusion_reason"] = (
            None if row["included"] else "; ".join([row["analysis_class"], *row["review_flags"]])
        )
    return classified


def link_shared_projects(classified: list[dict], *, reviewed: dict) -> None:
    """Count independently reviewed multi-address project totals once.

    Original City coordinates remain unchanged. All member locations are exposed
    so an exposure measure can treat a threshold-crossing group as unresolved.
    """
    by_id = {row["permit_id"]: row for row in classified}
    for project in reviewed["shared_projects"]:
        members = [by_id[permit_id] for permit_id in project["permit_ids"] if permit_id in by_id]
        if not members:
            continue
        anchor = members[0]
        locations = [
            {key: row.get(key) for key in ("permit_id", "address", "latitude", "longitude")}
            for row in members
        ]
        for row in members:
            row["unit_count"] = project["units"]
            row["unit_source"] = "reviewed_shared_project_total"
            row["project_id"] = anchor["permit_id"]
            row["shared_project_members"] = locations
            row["shared_project_member_ids"] = [member["permit_id"] for member in members]
            row["review_decision"] = project["reason"]
            row["review_source_url"] = project["source_url"]
            row["review_flags"].extend(["shared_project_total", "spatial_allocation_unresolved"])
            if row is not anchor:
                row["included"] = False
                row["duplicate_of"] = anchor["permit_id"]
                row["review_flags"].append("reviewed_shared_project_duplicate")


def link_preliminary_permits(classified: list[dict]) -> None:
    """Expose which initial structural permits have a later full-building record.

    Explicit cross-references take priority. Address-only matches are labeled
    assumptions and limited to a five-year window. An optional broader scenario
    adds the latest unlinked structural record per address in each two-year
    cluster; it is not a count of completed or definitely additional homes.
    """
    full_by_address: dict[str, list[dict]] = defaultdict(list)
    full_by_id: dict[str, dict] = {}
    reference_to_full: dict[str, list[dict]] = defaultdict(list)
    for row in classified:
        row["preliminary_candidate"] = False
        row["linked_full_permit_ids"] = []
        row["preliminary_link_method"] = None
        if row["analysis_class"] != "new_housing":
            continue
        full_by_address[row["address"]].append(row)
        full_by_id[row["permit_id"]] = row
        for reference in set(re.findall(r"(?<![0-9])10[0-9]{7}(?![0-9])", row["work_description"])):
            reference_to_full[reference].append(row)
    unlinked_by_address: dict[str, list[dict]] = defaultdict(list)
    for row in classified:
        if row["analysis_class"] != "preliminary_housing":
            continue
        explicit = reference_to_full[row["permit_id"]]
        mentioned = re.findall(r"(?<![0-9])10[0-9]{7}(?![0-9])", row["work_description"])
        explicit += [full_by_id[reference] for reference in mentioned if reference in full_by_id]
        if explicit:
            row["linked_full_permit_ids"] = sorted({full["permit_id"] for full in explicit})
            row["preliminary_link_method"] = "explicit_permit_reference"
            continue
        phase_date = date.fromisoformat(row["issue_date"][:10])
        address_matches = [
            full
            for full in full_by_address.get(row["address"], [])
            if -180 <= (date.fromisoformat(full["issue_date"][:10]) - phase_date).days <= 1826
        ]
        if address_matches:
            row["linked_full_permit_ids"] = sorted({full["permit_id"] for full in address_matches})
            row["preliminary_link_method"] = "assumed_same_address_within_five_years"
            continue
        row["preliminary_link_method"] = "no_link_identified"
        if row["unit_count"] is not None and row["address"]:
            unlinked_by_address[row["address"]].append(row)
    for same_address in unlinked_by_address.values():
        previous = None
        for row in sorted(same_address, key=lambda item: (item["issue_date"], item["permit_id"])):
            if previous is not None:
                elapsed = (
                    date.fromisoformat(row["issue_date"][:10])
                    - date.fromisoformat(previous["issue_date"][:10])
                ).days
                if elapsed <= 730:
                    previous["preliminary_candidate"] = False
                    previous["review_flags"].append("superseded_unlinked_phase_scenario")
            row["preliminary_candidate"] = True
            previous = row


def profile_permits(rows: list[dict], classified: list[dict]) -> dict:
    """Compute grain, completeness, conflict, duplicate, and annual coverage checks."""
    yearly: dict[int, dict] = defaultdict(lambda: Counter())
    for raw, derived in zip(rows, classified, strict=True):
        year = derived["permit_year"]
        yearly[year]["fetched_records"] += 1
        yearly[year]["new_construction_type_records"] += raw.get("permit_type") == NEW_CONSTRUCTION
        yearly[year]["structured_unit_change_present"] += (
            raw.get("residential_unit_change") is not None
        )
        yearly[year]["pin_present"] += bool(raw.get("pin_list"))
        yearly[year]["coordinates_present"] += bool(raw.get("latitude") and raw.get("longitude"))
        yearly[year]["included_projects"] += derived["included"]
        yearly[year]["included_units"] += (derived["unit_count"] or 0) if derived["included"] else 0
        yearly[year]["new_housing_review_records"] += (
            derived["analysis_class"] == "new_housing"
            and not derived["included"]
            and derived["duplicate_of"] is None
        )
    ids = [str(row.get("id")) for row in rows]
    permits = [str(row.get("permit_")) for row in rows]
    return {
        "records": len(rows),
        "unique_record_ids": len(set(ids)),
        "unique_permit_numbers": len(set(permits)),
        "duplicate_record_ids": [key for key, n in Counter(ids).items() if n > 1],
        "duplicate_permit_numbers": [key for key, n in Counter(permits).items() if n > 1],
        "by_type": dict(Counter(row["permit_type"] for row in rows)),
        "by_class": dict(Counter(row["analysis_class"] for row in classified)),
        "review_flags": dict(Counter(flag for row in classified for flag in row["review_flags"])),
        "included_projects": sum(row["included"] for row in classified),
        "included_units": sum(row["unit_count"] or 0 for row in classified if row["included"]),
        "preliminary_records": sum(
            row["analysis_class"] == "preliminary_housing" for row in classified
        ),
        "preliminary_link_methods": dict(
            Counter(
                row["preliminary_link_method"]
                for row in classified
                if row["analysis_class"] == "preliminary_housing"
            )
        ),
        "unlinked_preliminary_candidate_projects": sum(
            row["preliminary_candidate"] for row in classified
        ),
        "unlinked_preliminary_candidate_units": sum(
            row["unit_count"] or 0 for row in classified if row["preliminary_candidate"]
        ),
        "known_count_full_building_review_projects": sum(
            row["analysis_class"] == "new_housing"
            and not row["included"]
            and row["unit_count"] is not None
            and row["duplicate_of"] is None
            for row in classified
        ),
        "known_count_full_building_review_units": sum(
            row["unit_count"] or 0
            for row in classified
            if row["analysis_class"] == "new_housing"
            and not row["included"]
            and row["duplicate_of"] is None
        ),
        "secondary_change_review_flags": dict(
            Counter(flag for row in classified for flag in row["change_review_flags"])
        ),
        "secondary_change_records": sum(row["change_included"] for row in classified),
        "secondary_net_unit_change": sum(
            row["net_unit_change"] or 0 for row in classified if row["change_included"]
        ),
        "by_year": {str(year): dict(counts) for year, counts in sorted(yearly.items())},
    }


def write_candidate_review(classified: list[dict], output_dir: Path) -> Path:
    """Export count-bearing exclusions and all unresolved new-housing records.

    Candidate counts are evidence for review, not additional housing totals.
    The export deliberately retains garages describing an existing building's
    units, so excluded text-bearing records remain inspectable.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "permits_candidate_exclusions.csv"
    fields = (
        "permit_id",
        "permit_year",
        "address",
        "permit_type",
        "permit_status",
        "analysis_class",
        "unit_count",
        "text_units",
        "structured_unit_change",
        "unit_source",
        "unit_candidates",
        "review_flags",
        "exclusion_reason",
        "duplicate_of",
        "linked_full_permit_ids",
        "preliminary_link_method",
        "preliminary_candidate",
        "scope_review_decision",
        "review_decision",
        "work_description",
    )
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in classified:
            if row["included"]:
                continue
            count_evidence = (
                row["unit_count"] is not None
                or row["text_units"] is not None
                or row["unit_candidates"]
                or (row["structured_unit_change"] or 0) > 0
            )
            candidate_scope = row["analysis_class"] in {
                "accessory_or_existing",
                "nonresidential_or_unresolved",
                "preliminary_housing",
            }
            if row["analysis_class"] != "new_housing" and not (candidate_scope and count_evidence):
                continue
            writer.writerow(
                {
                    field: json.dumps(row[field])
                    if isinstance(row.get(field), (list, dict))
                    else row.get(field)
                    for field in fields
                }
            )
    return path


def fetch_permits(output_dir: Path, *, start_year: int, end_date: str) -> list[dict]:
    """Fetch the new-construction universe plus housing-related renovation candidates.

    end_date is exclusive and required: reproducing a historical run should
    not silently include a later partial year. Existing cached data are used.
    """
    path = output_dir / "permits_rows.json"
    if path.exists():
        manifest_path = output_dir / "permits_manifest.json"
        if not manifest_path.exists():
            raise ValueError(
                "Cached permit data lack a provenance manifest; use a fresh output directory"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("Cached permit snapshot checksum does not match its manifest")
        if (
            manifest.get("start_year") != start_year
            or manifest.get("end_date_exclusive") != end_date
        ):
            raise ValueError("Cached permit date scope differs; use a fresh output directory")
        return json.loads(path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    terms = [
        "NEW CONSTRUCTION",
        "PROPOSED NEW",
        "ERECT NEW",
        "CONSTRUCTION OF A NEW",
        "DWELLING",
        "D.U.",
        " UNIT",
        "APARTMENT",
        "RESIDENTIAL",
    ]
    housing_filter = " OR ".join(f"upper(work_description) like '%{term}%'" for term in terms)
    where = (
        f"issue_date >= '{start_year}-01-01T00:00:00' AND issue_date < '{end_date}T00:00:00' "
        f"AND (permit_type='{NEW_CONSTRUCTION}' OR (permit_type='{RENOVATION}' "
        f"AND ({housing_filter} OR residential_unit_change != 0)))"
    )
    rows, urls = [], []
    for offset in range(0, 1_000_000, 20_000):
        response = requests.get(
            API_URL,
            params={
                "$select": SELECT_FIELDS,
                "$where": where,
                "$limit": 20_000,
                "$offset": offset,
                "$order": "id",
            },
            timeout=180,
        )
        response.raise_for_status()
        batch = response.json()
        rows.extend(batch)
        urls.append(response.url)
        if len(batch) < 20_000:
            break
    else:
        raise RuntimeError("Permit pagination limit reached; refusing a truncated snapshot")
    path.write_text(json.dumps(rows), encoding="utf-8")
    manifest = {
        "retrieved_at": datetime.now(UTC).isoformat(),
        "urls": urls,
        "row_count": len(rows),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "start_year": start_year,
        "end_date_exclusive": end_date,
    }
    (output_dir / "permits_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return rows
