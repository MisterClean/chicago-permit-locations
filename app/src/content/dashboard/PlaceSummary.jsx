import React from "react";
import { number, pct } from "./housing-data.js";
import "./housing-map.css";

/** Summary of the active geographic selection, calculated by the parent from
 * the same scoped permit rows and screen used in the map. */
export function PlaceSummary({ summary }) {
  if (!summary) return null;
  const { type, name, period, modeLabel, total, unassignedUnits, unassignedPermits } = summary;
  const hasHomes = total.units > 0;

  return <section className="housing-map-place-summary" aria-label={`${name} summary`} data-reviewed-rows>
    <p className="housing-map-place-kind">{type === "ward" ? "Ward summary" : "Community summary"}</p>
    <h3>{name}</h3>
    <p className="housing-map-place-period">{period} · current filters</p>
    {hasHomes ? <>
      <dl className="housing-map-place-counts">
        <div><dt>Identified homes</dt><dd>{number(total.units)}</dd></div>
        <div><dt>Full-building permits</dt><dd>{number(total.permits)}</dd></div>
      </dl>
      <p className="housing-map-place-result"><strong>{pct(total.exposedShare)}</strong> of homes ({number(total.exposed)}) meet the selected screen.</p>
      <p className="housing-map-place-note">Screen: {modeLabel}. Exposure is unknown for {number(total.unknown)} homes. Shares use identified homes, including those with unknown exposure.</p>
    </> : <p className="housing-map-place-result">No identified homes match the current filters.</p>}
    {type === "ward" && <>
      <p className="housing-map-place-note">Boundaries effective May 2023 are used for all permit years. Projects without a unique ward assignment are excluded.</p>
      {unassignedUnits > 0 && <p className="housing-map-place-note">Across Chicago, {number(unassignedUnits)} identified homes in {number(unassignedPermits)} permits lack a unique ward assignment under the current year and size filters and are outside ward totals.</p>}
    </>}
  </section>;
}
