# Chicago housing permits and road exposure

A reproducible, unit-weighted analysis of where Chicago authorizes new housing relative to busy roads and modeled road noise. The analysis begins in 2006 and includes an interactive map, annual and community comparisons, ward selection, threshold sensitivity, and a permit-counting audit. Additions and conversions are reported separately.

## Findings from the reviewed snapshot

For the complete permit years **2006–2025**, the reviewed snapshot contains **105,367 identified homes in 15,577 accepted authorizations**. These are permitted units with resolved counts, not completed homes or a complete census of new housing.

| Screen | Identified homes meeting the screen | Share of identified homes | Homes with unknown exposure |
| --- | ---: | ---: | ---: |
| Within 200 m of a road carrying at least 25,000 vehicles/day | 22,300 | 21.2% | 115 |
| Published road-noise category at least 55 dBA | 23,975 | 22.8% | 52,988 |
| Both screens | 7,996 | 7.6% | 5,739 |
| Within 200 m of at least 1,000 recorded heavy commercial vehicles/day | 12,679 | 12.0% | 115 |

Unknown exposure remains in the denominator. Assigning all unknown exposure to the other extreme raises the road-noise share to 73.0% and the joint share to 13.0%; these bounds do not account for housing with unresolved unit counts. The 2026 permit series is partial and is shown separately in the app.

The traffic screen is a proximity-based proxy for traffic-related pollution, not a measured concentration or a health standard. Road noise uses published BTS road-only 24-hour categories. Static recent road and noise layers are applied to historical permits; the comparison does not reconstruct conditions in each permit year. See [the counting method](docs/counting-method.md), [road exposure methods](research/exposure.md), and [parcel audit](research/parcels.md).

## Interactive report

The report filters by year, housing size, community area, current ward, traffic threshold, distance and noise category. The **Single family** and **Multifamily** controls group authorizations with one and two or more units, respectively; they do not independently establish the physical structure type. Select a community or ward to see its summary, or click a ward on the map. Selecting one geography clears the other.

The **Road noise layer** switch displays the BTS road-noise categories behind the permit markers. It turns on automatically in the Road noise and Both views unless a layer preference is selected. The surface uses the same 2023 road-input category data as the analysis; transparent areas may be below the 45-dBA display floor or unmodeled and should not be treated as definitively quiet. These are modeled categories, not precise measurements at each home.

Ward selection uses the City's **2023-onward boundaries for every permit year**. Projects without a unique ward assignment are excluded from individual ward totals and reported separately. The map's display geometry is simplified; analytical assignment uses the full source polygons.

## Privacy and public repository contents

The public repository excludes downloaded permit and parcel records, exact-address or parcel-level exports, precise-location map snapshots, local screenshots, portable report exports and private record-level review notes. These files remain ignored by Git even after a local run. Source portals can contain addresses, parcel identifiers, coordinates or other identifying details: **keep downloaded inputs and locally generated row-level outputs private** and review anything before sharing it.

The previously reviewed snapshot and its record-level review decisions are not distributed here. Public sources can revise historical records, and an exact rerun of that snapshot requires retaining the original inputs and review decisions privately. **This repository alone cannot reproduce the exact reviewed figures.** The code and aggregate findings remain inspectable, and another researcher can construct an independently reviewed set of decisions for a new run.

## Reproduce locally

The Python pipeline requires Python 3.12 and [uv](https://docs.astral.sh/uv/). Dependencies are pinned in `uv.lock`.

```sh
uv sync --python 3.12 --frozen
uv run pytest -q
node --test tests/housing-data.test.mjs
```

The browser-calculation tests that compare against a generated snapshot are skipped until the pipeline runs. Before running the pipeline, place a **privately retained or independently created** `reviewed_decisions.json` in `data/raw/`. Its format is shown in [the synthetic example](examples/reviewed-decisions.example.json); the example itself is deliberately rejected as an analytical input. The pipeline stops if the review file is missing rather than silently omitting the decisions.

To inspect the current permit records while preparing an independent review, fetch that source separately:

```sh
uv run python - <<'PY'
from datetime import date, timedelta
from pathlib import Path
import tomllib

from chicago_housing.permits import fetch_permits

population = tomllib.loads(Path('analysis.toml').read_text())['population']
end = date.fromisoformat(population['cutoff_date']) + timedelta(days=1)
fetch_permits(Path('data/raw'), start_year=int(population['start_date'][:4]), end_date=end.isoformat())
PY
```

After reviewing the records and creating the private decision file, acquire the remaining sources and run the analysis:

```sh
uv run python -m chicago_housing.pipeline --fetch
uv run python -m chicago_housing.verify
```

The acquisition command fetches City, Cook County, IDOT, CDOT and BTS sources into the ignored `data/raw/` cache. The BTS input uses the official published category tiles; the official numeric raster was inaccessible during the reviewed acquisition. Full end-to-end acquisition from a fresh clone has not been verified for future portal states. The default pipeline runs offline against an existing cache and fails when required inputs are missing.

## Build the dashboard

After the pipeline generates `app/src/data.json`, build the app with the installed Codex Data runtime using its `data-app.mjs` build command:

```sh
node /path/to/data-analytics/scripts/data-app.mjs build --project-dir "$PWD/app" --separate-data
python -m http.server 61416 --bind 127.0.0.1 --directory app/dist
```

The build and snapshot are local and ignored by Git. They contain precise permit locations and permit descriptions, so do not publish the generated site or its HTML export without a separate privacy review.

## Sources and implementation

- [Chicago Building Permits](https://data.cityofchicago.org/d/ydr8-5enu) and the City's [unit-change methodology](https://data.cityofchicago.org/stories/s/at9p-zwgd)
- [Cook County Parcel Universe](https://datacatalog.cookcountyil.gov/d/pabr-t5kh) and [residential characteristics](https://datacatalog.cookcountyil.gov/d/x54s-btds)
- [IDOT historical traffic counts](https://gis1.dot.illinois.gov/arcgis/rest/services/AdministrativeData/AADT_Historical/FeatureServer/2025) and [CDOT rolling traffic counts](https://data.cityofchicago.org/d/gc7y-n4xa)
- [BTS National Transportation Noise Map](https://www.bts.gov/geospatial/national-transportation-noise-map) and [Chicago ward boundaries](https://data.cityofchicago.org/d/p293-wvbd)
- `src/chicago_housing/`: permit classification, parcel checks, road exposure, geographic joins and weighted summaries
- `app/src/content/dashboard/`: map, filters, place summaries and interactive report
- `tests/`: counting, spatial, exposure and browser-calculation checks

Visual direction is inspired by [A City That Works' Chicago Housing Tracker](https://housing.acitythatworks.org).
