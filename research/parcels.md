# Cook County parcel audit

Profile executed 2026-11-10. This is a supporting data-quality record for the
Chicago housing-permit analysis, not an estimate of completed housing.

## Authoritative sources and grains

| Source | Portal ID | Actual grain / use |
| --- | --- | --- |
| [Parcel Universe, Current](https://datacatalog.cookcountyil.gov/d/pabr-t5kh) | `pabr-t5kh` | Current PIN14, tax year, property class and geography; no housing-unit count. |
| [Parcel Universe, Historical](https://datacatalog.cookcountyil.gov/d/nj4t-kc8j) | `nj4t-kc8j` | PIN14 × tax year, starting 1999; do not join only on PIN and multiply permits over years. |
| [Single and Multi-Family Improvement Characteristics](https://datacatalog.cookcountyil.gov/d/x54s-btds) | `x54s-btds` | PIN14 × tax year × improvement/card. Scope is small residential properties, generally fewer than seven units. `char_apts` contains labels such as `Two`, `Three`, and `None`, primarily for classes 211/212. |
| [Residential Condominium Unit Characteristics](https://datacatalog.cookcountyil.gov/d/3r7i-mrz4) | `3r7i-mrz4` | Condo PIN14 × year; PIN10 groups the building. Parking, storage and common-area parcels also appear. |
| [Commercial Valuation Data](https://datacatalog.cookcountyil.gov/d/csik-bsws) | `csik-bsws` | Commercial entity/key PIN, year, model/use/sheet; not guaranteed unique by any available key. One entity can span multiple land PINs. `tot_units` can mean apartments, hotel rooms, nursing-home rooms or commercial units. |
| [Parcel Addresses](https://datacatalog.cookcountyil.gov/d/3723-97qp) | `3723-97qp` | PIN14 × tax year. Property addresses are separate from the universe. Owner and taxpayer fields are irrelevant and were not downloaded. |

The metadata snapshots are `data/raw/parcels_metadata_<id>.json`. The descriptions
explicitly warn about assessment lag, current-year incompleteness, changed PINs,
multiple cards, and condo non-livable parcels. The [Assessor's 2023 data
refresh](https://datacatalog.cookcountyil.gov/stories/s/9bqn-cfsv) also documents
that spatial geography predating 2013 can be filled backward using later
available geography. Historical parcel geography therefore is not a historical
measurement of road exposure.

## Executed profiles

The current county universe contains 1,864,469 rows and 1,864,469 distinct PIN14s,
but 1,417,783 distinct PIN10s. 2,998 current rows lack coordinates. Within the
spatial municipality label **CITY OF CHICAGO**, there are 881,586 PIN14s but only
604,843 PIN10s. This spatial municipality filter necessarily depends on successful
geographic attachment; it should not be used to hide unknown locations.

For a longitudinal coverage check, the City's assessment triad (`triad_code='1'`)
has 810,202 parcel-year rows in 2006 and 883,101 in 2026. Every tax year from 2006
through 2026 is present. The 2025 triad has 882,741 rows, of which 880,028 have a
latitude; 2026 has 883,101 rows, of which 881,620 have a latitude. These triad counts
are not identical to the legal/spatial municipal boundary counts. The complete
21-year profile is retained in `parcels_historical_summary.json`.

Current condominium characteristics in the eight City-triad townships contain
287,532 condo PIN14s. Of these, 219,380 have both non-livable flags false; 67,368 are
parking/storage only, 39 common area only, and 745 have both flags true. Thus
68,152 parcels (23.7%) are explicitly identified as non-livable. Counting PINs as
homes would create a material overcount even before considering assessment lag.
The provider notes that identification of non-livable parcels is still being
improved, so a false flag is not a guarantee of a dwelling.

For classes 211 and 212, `char_apts` values are words rather than integers. There
are also 5,660 null apartment counts and 274 literal `None` labels in those two
classes in the current City-township profile. A null or `None` is not a measured
zero. Small numbers of positive apartment labels appear in other property
classes, reinforcing the need to retain class and card context.

The 2024 commercial extract with a populated `tot_units` contains 11,026 rows;
10,726 are on `*-Multifamily` sheets and the remaining 300 are hotel or
nursing-home sheets. Several of the largest `tot_units` values are hotels.
Chicago commercial data is reassessment-year data (2021, 2024 here), not a complete
annual history. Repeated entity rows and multi-PIN properties need explicit
deduplication before any corroborative unit comparison.

Additional bounded evidence is saved for investigation:

- `parcels_recent_residential_characteristics.json`: 18,778 current improvement
  records in City townships with recorded year built ≥2004, including class,
  card, tieback/proration and apartment label. This is a selected recent-built
  subset, not all residential buildings.
- `parcels_condo_building_groups.json`: 16,729 PIN10 × parking/common-area flag
  groups for current City-township condos, with building count and year-built
  ranges. Repeated `char_building_pins` values must not be summed across units.
- `parcels_commercial_sample.json`: despite the exploratory filename, all 11,026
  2024 rows with populated total units from the bounded query, including sheet,
  key PIN, associated PINs and year built. This table includes non-housing units.

## Permit linkage

The current City permit schema supplies a pipe-separated `pin_list` of **10-digit
land PINs**. County PIN14s must be zero-padded to 14 characters; the first ten
characters identify the land PIN. The implementation normalizes PINs as strings,
deduplicates repeated entries, preserves malformed tokens and queries current
county summaries by PIN10. It collapses the result back to one original permit
row, retaining parcel/class multiplicity as evidence only.

The executed crosswalk includes all 67,215 candidate permit rows: 32,173 City
new-construction records and 35,042 housing-related renovation candidates. These
are input candidates, **not the final newly permitted housing denominator**.

| Current linkage outcome | Candidate permit rows |
| --- | ---: |
| All listed PIN10s matched | 41,047 |
| Some listed PIN10s matched | 205 |
| Valid PIN10s, no current match | 7,604 |
| No valid PIN10 | 18,359 |

2,219 candidate permits list multiple land PINs; 4,356 match more than one current
county PIN14; 181 contain at least one malformed PIN token. Missing historic
matches are not evidence that the permit is invalid. Subdivision, consolidation
and condominiumization can change the parcel identifiers.

All 2006 permit candidates lack a usable City PIN10, and all but one 2007 candidate
do. That is a **parcel-linkage gap**, not a general location gap: the analysis
checked City new-construction rows directly and found 3,365 of 3,380 geocoded in
2006, and 2,434 of 2,440 in 2007. City coordinates remain the primary location.

`coherent_current_centroid()` offers a location fallback only where the permit has
exactly one land PIN10, every current associated county row has coordinates, and
the min/max coordinates agree within 0.000001 degrees. 39,648 candidates meet this
test, but just 93 of those have a missing City latitude or longitude. The fallback
is explicitly labeled `cook_county_current_pin10_centroid`, retains tax year, and
does not establish that the historical project was completed. The final analysis
should retain a City-coordinate-only sensitivity comparison.

## Reproduction and limits

`src/chicago_housing/parcels.py` provides:

```python
sources = fetch_supporting_profiles(Path("data/raw"), refresh=False)
profile = run_parcel_audit(permit_rows, Path("data/raw"), refresh=False)
```

The first call reproduces all three corroboration extracts, six schema snapshots
and the supporting source profiles, including from an empty cache. The study
characteristics tax year is pinned to 2026 and the commercial reassessment to
2024. `parcels_supporting_profile_manifest.json` records their row counts,
metadata hashes and source update timestamps.

An empty-directory acquisition was executed successfully on 2026-11-10. Its
18,778 residential, 16,729 condo-group and 11,026 commercial rows exactly matched
the existing saved extracts, including ordering. This checks that these inputs
do not depend on an unrecorded manual download.

It writes `parcels_permit_crosswalk.json`, `parcels_current_pin10_groups.json` and
`parcels_linkage_profile.json`. `refresh=False` uses the saved exact-query cache;
`refresh=True` executes current public API queries again. Cached query envelopes
under `data/raw/parcels_queries/` retain source URL, exact SoQL parameters,
retrieval time and row-content SHA-256. Other profile envelopes retain exact
queries and URLs; the raw metadata snapshots retain their source update fields.

Four regression tests exercise leading-zero normalization, invalid tokens,
condo/multiclass join expansion, and rejection of incoherent coordinate recovery.
They passed with `PYTHONPATH=src .venv/bin/python -m pytest tests/test_parcels.py -q`.

No assessed stock field is used to impute a permit unit count. Appropriate use of
the characteristics extracts is to flag disagreements or corroborate a matched
project after checking year built, permit timing, building/card grain, entity
boundaries and non-livable condo flags. A property can accumulate multiple
permits; a permit can span multiple properties. Permitted units, completed units,
current assessed units and net additions to housing stock remain different
quantities.
