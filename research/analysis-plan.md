# Chicago housing and road exposure

The analysis covers permit years from 2006, with a new-construction headline,
additions and conversions reported separately, and traffic-based pollution proxies
alongside road-only noise. The deliverable is an interactive map and report plus its analysis code.

## Measures and comparisons

- Primary denominator: identified housing units authorized by full-building construction
  records after scope classification and deduplication. Permit counts are a separate measure.
- Keep known units without usable exposure in the denominator and report their share.
  Show exposed units / located units as a conditional rate, and exposed / all identified
  units plus the unknown-location range. This is not a bound on unidentified unit counts.
- Separate total identified units, source count gaps, unresolved projects, preliminary
  phases, and coordinate gaps. Do not call a parser-derived total all permitted housing.
- Analyze road-only modeled noise and near-road traffic separately and jointly; offer
  distance, AADT, HCV and noise threshold sensitivity. Thresholds are screening choices,
  not health cutoffs and not measured pollutant concentration.
- Compare one-unit, 2–6-unit, 7–49-unit and 50+-unit buildings, annual trends,
  and community areas. Annual quantities use permit dates; exposure uses explicitly
  labeled static recent source layers. 2026 is partial and separate.
- A city land-area proximity comparison is descriptive context only, if included;
  land is not a population/housing-stock denominator.
- Additions, ADUs, conversions and deconversions need explicit net-change evidence.
  Their series is not net housing-stock growth because demolition is outside it.

## Source hierarchy

City permit records establish authorized construction and its described unit count.
Cook County PIN universe establishes parcel identity, classification and location;
class-specific characteristic records corroborate counts only within documented grain.
Current assessed stock is not evidence of units authorized at an earlier permit date.
IDOT supplies line geometry and annual vehicle/truck traffic. CDOT camera counts are
supplementary rolling means and are not averaged with IDOT. BTS supplies road-only
noise, with mode, year, metric, spatial resolution and censoring kept explicit.

## Presentation

Use the supplied Chicago Housing Tracker as visual direction: restrained near-black
type, white surfaces, blue/cyan quantitative marks, coral for the selected exposure,
square edges and a prominent map. Preserve source inspection and accessible record
detail. Opening map and totals; building-type and annual comparison; threshold
sensitivity; geographic breakdown; count/parcel audit and methodology.

## Reproduction

Cache exact public API responses and retrieval metadata, retain source digests, pin
dependencies, expose count rules in tested code and keep review decisions as data.
Offline recomputation must not silently refetch or substitute missing sources.
