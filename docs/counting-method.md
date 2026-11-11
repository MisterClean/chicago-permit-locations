# Counting homes and screening road exposure

## Unit of analysis

The headline is the sum of **identified dwelling units in accepted full-building new-construction authorizations**. It is not a count of permits, parcels, completed homes, or net additions to the housing stock. A single authorization can cover more than one building. Additions and conversions are described separately and are counted only where a net unit change is supported.

The pipeline starts with City building-permit records from 2006 onward, identifies housing-related scopes, removes canceled or non-housing records, and distinguishes complete buildings from preliminary construction stages, revisions and repairs. The structured City unit-change field has limited historical coverage and is interpreted alongside permit descriptions; neither a positive field value nor a number in free text is automatically a new dwelling count. Missing and conflicting claims remain unresolved instead of becoming zero.

Potential repeat authorizations and shared project totals are reviewed before summing. Where a project total is shared between multiple addresses and the allocation is unknown, the project is counted once. Road and noise exposures are classified only where the member locations agree; disagreement remains unknown. The local review records contain public-record identifiers and are deliberately excluded from this public repository.

## Cook County parcel information

The City supplies land PINs, while the County data have different grains for current parcel universe records, residential improvement cards, condominium units and commercial entities. Parcel matching is used for location recovery and carefully qualified corroboration, not as a substitute for permitted-unit counts. Parking, common areas, multiple cards and buildings, changed PINs, and assessment timing make an unqualified PIN count unsuitable as a housing estimate. See [the parcel audit](../research/parcels.md).

## Road and noise screens

The default traffic screen identifies units in permits within 200 metres of an IDOT road centreline carrying at least 25,000 vehicles per day. Different volume and distance thresholds are available. Traffic and truck counts are proxies for the road environment; they are not estimates of pollutant concentrations.

The noise screen uses published BTS road-only categories of 24-hour A-weighted equivalent sound level. Transparent, below-display-floor, unmapped and otherwise ambiguous pixels remain unknown. The displayed denominator includes identified homes with unknown exposure; the upper endpoint assigns those homes to the opposite extreme and is not a confidence interval or a bound on homes with missing unit counts. The 3×3-pixel calculation shows sensitivity to point location, not statistical uncertainty. See [the road-exposure methods](../research/exposure.md).

The IDOT inventory and BTS road-noise layer are fixed recent snapshots applied to all permit years. Historical annual comparisons therefore describe where permits fall relative to those snapshots, not the traffic or noise experienced when each permit was issued.

## Geographic and building-size groups

The map uses Chicago community areas and the City's 2023-onward ward boundaries. Ward assignment uses full polygons and leaves unlocated, boundary-ambiguous and cross-ward shared projects unassigned. Current boundaries are applied to every permit year, so a historical permit's displayed ward may differ from its ward at the time.

The single-family group contains authorizations for one unit; the multifamily group contains authorizations for two or more units. These labels are based on unit counts per authorization and do not independently verify the physical structure type.

## Privacy and reproducibility

The public code does not include the original permit-level or parcel-level data, exact-address maps, local snapshots, or record-level audit notes. A fresh acquisition can contain personal information and should stay in the ignored local data directory. Live source revisions and excluded local review decisions can prevent exact reproduction of a previous run. Preserve the original source cache and review decisions privately if exact replication is required.
