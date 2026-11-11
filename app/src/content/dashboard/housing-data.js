export const number = (value) => Number.isFinite(value) ? new Intl.NumberFormat("en-US").format(value) : "—";
export const pct = (value) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
export const sizeOrder = ["1 unit", "2–6 units", "7–49 units", "50+ units"];
export const housingGroups = [
  { value: "all", label: "All" },
  { value: "single", label: "Single family" },
  { value: "multi", label: "Multifamily" },
];
export function matchesHousingGroup(row, group) {
  if (group === "all") return true;
  if (!Number.isInteger(row.unit_count) || row.unit_count < 1) return false;
  return group === "single" ? row.unit_count === 1 : group === "multi" && row.unit_count >= 2;
}
const finite = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
function distanceScreen(row, field, threshold) {
  if (row.geocode_status !== "inside_city" || row.shared_project_exposure_incomplete) return null;
  const lower = row[field + "_min"], upper = row[field + "_max"];
  if (finite(lower) && finite(upper)) return upper <= threshold ? true : lower > threshold ? false : null;
  return finite(row[field]) ? Number(row[field]) <= threshold : null;
}

export function trafficScreen(row, aadt, distance) {
  return distanceScreen(row, `nearest_aadt_${aadt}_distance_m`, distance);
}
export function truckScreen(row, distance) {
  return distanceScreen(row,"nearest_hcv_1000_distance_m",distance);
}
export function noiseScreen(row, noise) {
  if (row.geocode_status !== "inside_city") return null;
  if (finite(row.noise_db_lower) && Number(row.noise_db_lower) >= noise) return true;
  if (finite(row.noise_db_upper) && Number(row.noise_db_upper) <= noise) return false;
  return null;
}
export function both(left, right) {
  return left === false || right === false ? false : left === true && right === true ? true : null;
}
export function evaluate(row, mode, aadt, distance, noise) {
  if (mode === "noise") return noiseScreen(row, noise);
  if (mode === "trucks") return truckScreen(row, distance);
  if (mode === "both") return both(trafficScreen(row, aadt, distance), noiseScreen(row, noise));
  return trafficScreen(row, aadt, distance);
}
export function totals(rows, screen) {
  const result = { units: 0, permits: 0, exposed: 0, unknown: 0, exposedPermits: 0 };
  for (const row of rows) {
    if (row.included !== true) continue;
    if (!Number.isInteger(row.unit_count) || row.unit_count <= 0) throw new Error("Included permit lacks positive units");
    result.permits += 1; result.units += row.unit_count;
    const outcome = screen(row);
    if (outcome === true) { result.exposed += row.unit_count; result.exposedPermits += 1; }
    else if (outcome === null) result.unknown += row.unit_count;
  }
  return { ...result, exposedShare: result.units ? result.exposed / result.units : null,
    unknownShare: result.units ? result.unknown / result.units : null,
    upperShare: result.units ? (result.exposed + result.unknown) / result.units : null };
}
export function grouped(rows, field, screen) {
  const groups = new Map();
  for (const row of rows) {
    if (!row.included) continue;
    const key = row[field] ?? "Unknown";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups].map(([key, members]) => ({ [field]: key, ...totals(members, screen) }));
}
export function annualRows(rows, screen) {
  return grouped(rows, "permit_year", screen).sort((a,b) => a.permit_year-b.permit_year).flatMap((row) => [
    { year: `${row.permit_year}-01-01`, status: "Meets screen", units: row.exposed },
    { year: `${row.permit_year}-01-01`, status: "Other located units", units: row.units-row.exposed-row.unknown },
    { year: `${row.permit_year}-01-01`, status: "Unknown exposure", units: row.unknown },
  ]);
}
export function trafficSensitivity(rows, aadts=[10000,25000,50000,100000], distances=[100,200,300,500]) {
  return aadts.flatMap((aadt) => distances.map((distance) => ({
    distance, traffic: `${number(aadt)}+ vehicles/day`, ...totals(rows, (row) => trafficScreen(row,aadt,distance)),
  })));
}
export function changeRows(rows) {
  const groups = new Map();
  for (const row of rows.filter((r) => r.change_included === true && ["conversion","other_renovation","addition"].includes(r.analysis_class) && finite(r.net_unit_change))) {
    const year = `${row.permit_year}-01-01`;
    if (!groups.has(year)) groups.set(year, { year, added: 0, removed: 0, permits: 0 });
    const item = groups.get(year); item.permits += 1;
    if (row.net_unit_change > 0) item.added += row.net_unit_change;
    else item.removed += row.net_unit_change;
  }
  return [...groups.values()].sort((a,b) => a.year.localeCompare(b.year));
}
