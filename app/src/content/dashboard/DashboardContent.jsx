import React, { useMemo, useState } from "react";
import {
  Button, DataComponent, DataTable, Dropdown, EvidenceChart, Filters, MetricCard,
  RangeSlider, Section, SegmentedControl, SortableItem, SortableRegion, Switch, useDataApp, useDashboardTabs,
} from "../../data-app-public.jsx";
import { HousingMap } from "./HousingMap.jsx";
import { PlaceSummary } from "./PlaceSummary.jsx";
import { annualRows, changeRows, evaluate, grouped, housingGroups, matchesHousingGroup, number, pct, sizeOrder, totals, trafficSensitivity } from "./housing-data.js";
import "./dashboard.css";

const tabs = [
  { id: "housing", label: "Housing & roads", filterIds: ["community", "ward", "building"] },
  { id: "methods", label: "Counts & methods", filterIds: [] },
];
const modes = [
  { value: "traffic", label: "Busy roads" }, { value: "noise", label: "Road noise" },
  { value: "both", label: "Both" }, { value: "trucks", label: "Truck corridors" },
];
const annualSpec = { type: "stackedBar", x: "year", y: "units", series: "status",
  colors: { "Meets screen": "var(--chart-2)", "Other located units": "var(--chart-1)", "Unknown exposure": "var(--chart-8)" } };
const sizeSpec = { type: "horizontalBar", x: "building_size", y: "exposedShare", stackable: false, valueDecimals: 1 };
const sensitivitySpec = { type: "line", x: "distance", y: "exposedShare", series: "traffic", xLabel: "Distance to road (metres)", stackable: false,
  colors:{"10,000+ vehicles/day":"var(--chart-4)","25,000+ vehicles/day":"var(--chart-1)","50,000+ vehicles/day":"var(--chart-3)","100,000+ vehicles/day":"var(--chart-2)"} };
const permitColumns = [
  { field: "address", label: "Address", presentation: "identity", secondaryField: "community_name" },
  { field: "permit_id", label: "Permit" }, { field: "permit_year", label: "Year" },
  { field: "unit_count", label: "Homes" }, { field: "screenLabel", label: "Selected screen" },
  { field: "unit_source", label: "Count evidence", renderCell:(value)=>value?.replaceAll("_"," ") },
];
const communityColumns = [
  { field: "community_name", label: "Community" }, { field: "units", label: "Identified homes", renderCell:number },
  { field: "exposed", label: "Meets screen", renderCell:number }, { field: "exposedShare", label: "Share", presentation: "percent", renderCell:pct },
  { field: "unknown", label: "Exposure unknown", renderCell:number },
];

function PermitDetail({ row }) {
  if (!row) return <aside className="ch-detail"><h3>Inspect a permit</h3><p>Select a circle on the map or a row in the permit table. Larger circles represent more homes.</p><p className="ch-note">Locations are permit address points, not dwelling footprints. Map distances are measured to road centrelines.</p></aside>;
  return <aside className="ch-detail" data-reviewed-rows>
    <h3>{row.address}</h3><p>{row.community_name} · {row.permit_year} · Permit {row.permit_id}</p>
    <dl><div><dt>Identified homes</dt><dd>{number(row.unit_count)}</dd></div>
      <div><dt>Count evidence</dt><dd>{row.unit_source?.replaceAll("_"," ")}</dd></div>
      <div><dt>Road noise band</dt><dd>{row.noise_band || "Unknown"}</dd></div>
      <div><dt>Distance to ≥25,000 vehicles/day</dt><dd>{row.nearest_aadt_25000_distance_m == null ? "Unknown" : `${number(Math.round(row.nearest_aadt_25000_distance_m))} m`}</dd></div>
      <div><dt>Coordinate source</dt><dd>{row.geocode_source.replaceAll("_", " ")}</dd></div>
      <div><dt>County count check</dt><dd>{row.county_count_status.replaceAll("_", " ")}</dd></div></dl>
    <details><summary>Original work description</summary><p className="ch-record-text">{row.work_description}</p></details>
    {row.review_flags?.length > 0 && <p className="ch-note">Flags: {row.review_flags.map((s)=>s.replaceAll("_"," ")).join("; ")}</p>}
    {row.shared_project_locations?.length > 1 && <p className="ch-note">Shared project total across {row.shared_project_locations.map((x)=>x.address).join(" and ")}. The marker uses this permit’s original location. Exposure requires the member locations to agree; disagreement is unknown because units per building are not established.</p>}
    {row.review_source_url && <p><a href={row.review_source_url} target="_blank" rel="noreferrer">Project count review evidence ↗</a></p>}
    <a href={`https://data.cityofchicago.org/resource/ydr8-5enu.json?permit_=${encodeURIComponent(row.permit_id)}`} target="_blank" rel="noreferrer">Open City source record ↗</a>
  </aside>;
}

function Methods({ snapshot, queries }) {
  const profile = snapshot.permitProfile;
  const summary = snapshot.analysisSummary;
  const review = queries.permits.rows.filter((r) => !r.included && r.analysis_class === "new_housing");
  return <div>
    <p className="ch-scope">Full source audit · 2006–2026 · all communities. This view is independent of the map filters.</p>
    <Section id="count-method-heading" title="Count homes once, with an audit trail" columns={2}>
      <DataComponent id="count-method" title="How a permit becomes a housing count" queryId="count_profile" kind="custom" variant="card" displayRows={queries.count_profile.rows} sourceRows={queries.count_profile.rows}>
        <ol className="ch-method-list"><li>Start with all new-construction records from 2006. Exclude garages, nonresidential buildings, revisions and canceled records.</li>
          <li>Identify full-building authorizations. Foundation and superstructure phases are linked to later permits where evidence allows, and excluded from the headline.</li>
          <li>Use explicit dwelling counts in descriptions and the City's net unit-change field. Conflicting or missing counts stay in review; missing is never zero.</li>
          <li>Sum dwelling units, not permits or parcels. Retain known units with missing exposure in every denominator.</li></ol>
        <p>The City added its unit-change column on October 27, 2026. Its initial coverage is Q1 2024–Q3 2026; a foundation for 100 homes explicitly counts as zero until a later authorization.</p>
        <p>Some garage-only or existing-building records carry positive City values. Housing scope is checked before using that field; a positive number alone does not establish a new home.</p>
        <p><a href="https://data.cityofchicago.org/stories/s/at9p-zwgd" target="_blank" rel="noreferrer">Read the City’s counting rules ↗</a></p>
      </DataComponent>
      <DataComponent id="count-coverage" title="What the headline leaves unresolved" queryId="count_profile" kind="custom" variant="card" displayRows={queries.count_profile.rows} sourceRows={queries.count_profile.rows}>
        <p><strong>{number(profile.included_units)} identified homes</strong> in {number(profile.included_projects)} accepted permits across all available years, including partial 2026.</p>
        <p>{number(profile.review_flags.missing_unit_count || 0)} records have missing-count flags. They may represent housing absent from the denominator. Exposure bounds cannot correct that missing housing.</p>
        <p>{number(profile.preliminary_records)} preliminary-phase records are outside the headline. After linking and consolidating phases, {number(profile.unlinked_preliminary_candidate_projects)} unlinked candidates describe {number(profile.unlinked_preliminary_candidate_units)} units. These are a sensitivity scenario, not additional verified homes.</p>
        <p>For complete permit years, adding the unlinked phase candidates would change the busy-road share from {pct(summary.complete_years.traffic.share_of_identified_units)} to {pct(summary.unlinked_preliminary_scenario.traffic.share_of_identified_units)}. This is an alternative authorization scope; some candidates may later have been canceled, changed or linked under another address.</p>
        <p>{number(profile.known_count_full_building_review_projects)} unresolved full-building records contain {number(profile.known_count_full_building_review_units)} candidate units. Duplicate, conflicting and mixed hotel counts need record-level review.</p>
      </DataComponent>
    </Section>
    <Section id="annual-quality-heading" title="Coverage changes across permit years">
      <DataComponent id="annual-quality" title="Counts and source-field coverage" queryId="annual_profile" kind="table" variant="card" displayRows={queries.annual_profile.rows} sourceRows={queries.annual_profile.rows}>
        <DataTable rows={queries.annual_profile.rows} label="Annual permit data coverage" columns={[
          {field:"year",label:"Year",renderCell:String},{field:"new_construction_type_records",label:"New-construction records"},
          {field:"included_projects",label:"Accepted full permits"},{field:"included_units",label:"Identified homes"},
          {field:"new_housing_review_records",label:"Housing records in review"},
          {field:"structured_unit_change_present",label:"Net-change field present*"},
        ]} />
        <p className="ch-note">*Field coverage uses the full fetched population, including housing-related renovations. The City’s permit date usually indicates readiness for issuance, subject to fees. A permit does not establish construction or occupancy.</p>
      </DataComponent>
    </Section>
    <Section id="exposure-method-heading" title="What road exposure means" columns={2}>
      <DataComponent id="traffic-method" title="Traffic is an exposure proxy" queryId="audit_summary" kind="custom" variant="card" displayRows={queries.audit_summary.rows} sourceRows={queries.audit_summary.rows}>
        <p>IDOT’s 2025 inventory supplies 25,566 road segments in Chicago and a surrounding buffer. Actual count years vary from 2016 to 2025. Distances use unsimplified centrelines in a metre-based projection.</p>
        <p>The nearest qualifying road is found independently for each traffic threshold. The truck view uses recorded heavy-commercial-vehicle counts of at least 1,000 per day. Zero or undated truck values do not prove an absence of trucks.</p>
        <p>CDOT’s separate series was profiled: 2,433,403 observations, 2,561 directional segments, July 2022–November 2026 in this snapshot. Values are trailing 30-day averages. It is supplementary coverage evidence and is not averaged into IDOT.</p>
        <p>These are recent conditions applied to historical permit locations, not historical exposure at permit issuance. Proximity does not measure pollutants, health effects or indoor conditions.</p>
      </DataComponent>
      <DataComponent id="noise-method" title="Road-only modeled noise, with large uncertainty" queryId="audit_summary" kind="custom" variant="card" displayRows={queries.audit_summary.rows} sourceRows={queries.audit_summary.rows}>
        <p>BTS road-only categories represent outdoor 24-hour LAeq. The service name says 2022; its June 2026 methods identify 2023 road inputs. Aircraft and rail layers are excluded.</p>
        <p>The raw numerical download was inaccessible. This analysis instead archives official category tiles, approximately 28.5 metres per pixel. It reports category thresholds only, never invented exact dBA.</p>
        <p>Transparent pixels may be below 45 dBA or unmapped, and remain unknown. The model omits building and barrier shielding. Noise shares are confirmed lower endpoints; the upper endpoint assigns all unknown exposure to exposed.</p>
        <p>A one-pixel shift can cross a category boundary. The map view shows a 3×3-neighbourhood sensitivity check. It is not a confidence interval or a substitute for site measurements.</p>
      </DataComponent>
    </Section>
    <Section id="parcel-method-heading" title="A tax PIN is not a home">
      <DataComponent id="parcel-method" title="Cook County checks identity and plausibility" queryId="audit_summary" kind="custom" variant="card" displayRows={queries.audit_summary.rows} sourceRows={queries.audit_summary.rows}>
        <p>City permits use ten-digit land PINs; the county universe uses fourteen-digit tax PINs. Condominium parking and common-area PINs make raw parcel counts unsuitable for units. The historical universe has no dwelling-unit field.</p>
        <p>Current small-building characteristics, nonparking condo groups and commercial multifamily records provide restrictive corroboration only. Checks require compatible parcel grain and construction years. Conflicts never overwrite a permit silently, and present assessed stock does not reconstruct the originally permitted count.</p>
        <p className="ch-note">County comparison: {Object.entries(summary.county_corroboration.included_permit_status).map(([key,value]) => `${key.replaceAll("_"," ")}: ${number(value)} permits`).join(" · ")}</p>
      </DataComponent>
    </Section>
    <Section id="review-heading" title="Inspect unresolved full-building records">
      <DataComponent id="count-review" title="Permit count review queue" queryId="permits" kind="table" variant="card" sourceRows={review} displayRows={review}>
        <DataTable rows={review} label="Unresolved housing permits" columns={[
          {field:"permit_id",label:"Permit"},{field:"address",label:"Address"},{field:"permit_year",label:"Year",renderCell:String},
          {field:"text_units",label:"Text candidate"},{field:"structured_unit_change",label:"City net change"},
          {field:"work_description",label:"Original description"},
        ]} />
      </DataComponent>
    </Section>
  </div>;
}

export function DashboardContent() {
  const { snapshot, queries, filters, setFilter, reviewedRows, assumptions, setAssumptions } = useDataApp();
  const { activeTabId } = useDashboardTabs(tabs);
  const [selectedId, setSelectedId] = useState(null);
  const config = snapshot.analysisSummary.config;
  const firstYear = Number(config.population.start_date.slice(0,4)), lastYear = Number(config.population.cutoff_date.slice(0,4));
  const yearStart = Number(assumptions.yearStart ?? firstYear), yearEnd = Number(assumptions.yearEnd ?? config.population.last_complete_year);
  const aadt = Number(assumptions.aadt ?? config.exposure.default_traffic_aadt), distance = Number(assumptions.distance ?? config.exposure.default_distance_m), noise = Number(assumptions.noise ?? config.exposure.default_noise_db);
  const mode = modes[Number(assumptions.screenMode ?? 0)]?.value || "traffic";
  const housingGroup = housingGroups[Number(assumptions.housingGroup ?? 0)]?.value || "all";
  const showWards = Number(assumptions.wardOverlay ?? 0) === 1;
  const change = (key, value) => setAssumptions((current) => ({...current, [key]: Number(value)}));
  const geometry = useMemo(() => JSON.parse(queries.map_geometry.rows[0].geometry_json), [queries.map_geometry.rows]);
  // Prefix the reviewed ward IDs so the page filter presents the full list in
  // numerical order. These small rows are only used to enumerate choices.
  const filterQueries = useMemo(() => ({...queries, permits:{...queries.permits, rows:[
    ...(geometry.wards || []).map((ward) => ({ward_2023:String(ward.id)})), ...queries.permits.rows,
  ]}}), [queries, geometry]);
  const pageFilters = useMemo(() => snapshot.filters.map((filter) => filter.id === "ward" ? {
    ...filter, searchable:true, choiceLabels:Object.fromEntries((geometry.wards || []).map((item) => [String(item.id), `Ward ${item.id}`])),
  } : filter), [snapshot.filters, geometry]);
  const ward = filters.ward ?? "all";
  const community = filters.community ?? "all";
  const onFilterChange = (id, value) => {
    if (id === "ward" && value !== "all") {
      setFilter("community", "all");
      setAssumptions((current) => ({...current, wardOverlay:1}));
    } else if (id === "community" && value !== "all") setFilter("ward", "all");
    if (id === "ward" || id === "community") setSelectedId(null);
    setFilter(id, value);
  };
  const allScoped = reviewedRows("permits", ["permit_id"]);
  const scoped = allScoped.filter((r) => r.permit_year >= yearStart && r.permit_year <= yearEnd && matchesHousingGroup(r,housingGroup));
  const housing = scoped.filter((r) => r.included);
  const screen = (r) => evaluate(r,mode,aadt,distance,noise);
  const t = totals(housing,(r) => evaluate(r,"traffic",aadt,distance,noise));
  const n = totals(housing,(r) => evaluate(r,"noise",aadt,distance,noise));
  const b = totals(housing,(r) => evaluate(r,"both",aadt,distance,noise));
  const current = totals(housing,screen);
  const selected = housing.find((r) => r.permit_id === selectedId);
  const bySize = grouped(housing,"building_size",screen).sort((a,b) => sizeOrder.indexOf(a.building_size)-sizeOrder.indexOf(b.building_size));
  const byCommunity = grouped(housing,"community_name",screen).sort((a,b) => b.exposed-a.exposed);
  const largeBuilding = totals(housing.filter((r)=>r.unit_count >= 50),screen);
  const singleFamily = totals(housing.filter((r)=>r.unit_count === 1),screen);
  const topThree = byCommunity.filter((r)=>r.exposed > 0).slice(0,3);
  const tableRows = housing.map((r) => ({ ...r, permit_year: String(r.permit_year), screenLabel: screen(r) === true ? "Meets screen" : screen(r) === false ? "Does not meet" : "Unknown" })).sort((a,b) => b.unit_count-a.unit_count);
  const changes = scoped.filter((r) => r.change_included === true);
  const unresolvedChanges = scoped.filter((r) => r.change_review_flags?.some((flag)=>flag !== "missing_net_change"));
  const missingCount = scoped.filter((r) => r.analysis_class === "new_housing" && !r.included && !r.duplicate_of).length;
  const noiseAll = totals(housing,(r) => r[`noise_ge_${noise}_all_3x3`] === true);
  const noiseAny = totals(housing,(r) => r[`noise_ge_${noise}_any_3x3`] === true);
  const period = `${yearStart}–${yearEnd}${yearEnd > config.population.last_complete_year ? ` (${lastYear} partial)` : ""}`;
  const unassigned = useMemo(() => {
    if (ward === "all") return {units:0, permits:0};
    return queries.permits.rows.reduce((result, row) => {
      if (row.included && !row.ward_2023 && row.permit_year >= yearStart && row.permit_year <= yearEnd
          && matchesHousingGroup(row, housingGroup) && ((filters.building ?? "all") === "all" || row.building_size === filters.building)) {
        result.units += row.unit_count;
        result.permits += 1;
      }
      return result;
    }, {units:0, permits:0});
  }, [ward, queries.permits.rows, yearStart, yearEnd, housingGroup, filters.building]);
  const screenLabel = mode === "noise" ? `road noise ≥${noise} dBA`
    : mode === "trucks" ? `within ${distance} m of a road with ≥1,000 recorded heavy commercial vehicles/day`
    : mode === "both" ? `both road noise ≥${noise} dBA and within ${distance} m of a road carrying ≥${number(aadt)} vehicles/day`
    : `within ${distance} m of a road carrying ≥${number(aadt)} vehicles/day`;
  const placeSummary = ward !== "all" || community !== "all" ? {
    type: ward !== "all" ? "ward" : "community",
    name: ward !== "all" ? (community !== "all" ? `${community} · Ward ${ward}` : `Ward ${ward}`) : community,
    period, modeLabel:screenLabel, total:current,
    unassignedUnits:unassigned.units, unassignedPermits:unassigned.permits,
  } : null;
  const scopeFilters = [
    { field:"permit_year", label: "Permit years", value: period },
    { field:"unit_count", label:"Housing group", value:housingGroup === "single" ? "Single family (1 unit per authorization)" : housingGroup === "multi" ? "Multifamily (2+ units per authorization)" : "All housing groups" },
    { field:`nearest_aadt_${aadt}_distance_m`, label:"Traffic screen", value:`${number(aadt)}+ vehicles/day within ${distance} m` },
    { field:"noise_db_lower", label:"Noise screen", value:`≥${noise} dBA` },
    ...(mode === "trucks" ? [{field:"nearest_hcv_1000_distance_m",label:"Truck screen",value:`1,000+ recorded heavy commercial vehicles/day within ${distance} m`}] : []),
  ];
  const sourceProps = { queryId:"permits", sourceRows:housing, scopeFilters };
  const metricCards = [
    { id:"identified-homes", title:"Identified new homes", value:number(t.units), detail:`${number(t.permits)} full-building permits · ${period}`, data:t },
    { id:"traffic-share", title:"Near a busy road", value:pct(t.exposedShare), detail:`${number(t.exposed)} homes · ≥${number(aadt)} vehicles/day within ${distance} m`, data:t },
    { id:"noise-share", title:`Road noise ≥${noise} dBA`, value:n.units ? `≥${pct(n.exposedShare)}` : "—", detail:`${number(n.exposed)} confirmed · ${pct(n.unknownShare)} exposure unknown`, data:n },
    { id:"both-share", title:"Both traffic and noise", value:b.units ? `${pct(b.exposedShare)}–${pct(b.upperShare)}` : "—", detail:"Lower–upper endpoints assign unknown exposure to neither / both", data:b },
  ];
  if (activeTabId === "methods") return <Methods snapshot={snapshot} queries={queries} />;
  return <>
    <Filters sticky filters={pageFilters} queries={filterQueries} values={filters} onChange={onFilterChange}
      trailingControls={<Button onClick={() => {setFilter("community","all");setFilter("ward","all");setFilter("building","all");setSelectedId(null);setAssumptions((x)=>({...x,yearStart:firstYear,yearEnd:config.population.last_complete_year,aadt:config.exposure.default_traffic_aadt,distance:config.exposure.default_distance_m,noise:config.exposure.default_noise_db,screenMode:0,housingGroup:0}));}}>Reset view</Button>} />
    <p className="ch-scope">Where Chicago permits new homes · 2006 onward · permits through {snapshot.analysisSummary.latest_permit_date}. Recent road conditions are overlaid on historical permits. Choose a community or a ward for a local summary; selecting one clears the other.</p>
    <div className="ch-controls">
      <div className="ch-housing-group"><span>Housing group</span><SegmentedControl label="Housing group" value={housingGroup} options={housingGroups}
        onChange={(value)=>{setFilter("building","all");setSelectedId(null);change("housingGroup",housingGroups.findIndex((group)=>group.value===value));}} /></div>
      <div className="ch-years"><RangeSlider label="Permit years" min={firstYear} max={lastYear} step={1} value={[yearStart,yearEnd]} onChange={([lo,hi]) => setAssumptions((x)=>({...x,yearStart:lo,yearEnd:hi}))} formatValue={(v)=>String(v)} /></div>
      <Dropdown label="Traffic" showLabel value={aadt} choices={config.exposure.traffic_aadt} formatChoice={(v)=>`${number(v)}+ / day`} onChange={(v)=>change("aadt",v)} />
      <Dropdown label="Within" showLabel value={distance} choices={config.exposure.distance_m} formatChoice={(v)=>`${v} m`} onChange={(v)=>change("distance",v)} />
      <Dropdown label="Noise" showLabel value={noise} choices={config.exposure.road_noise_db} formatChoice={(v)=>`≥${v} dBA`} onChange={(v)=>change("noise",v)} />
    </div>
    <p className="ch-group-note">Single family = 1 unit; multifamily = 2+ units per authorization. These groups use permit counts; a combined project can cover several buildings.</p>
    <SortableRegion id="housing:metrics" label="Housing measures" variant="freeform" className="ch-kpis" authoredRevision={1}>
      {metricCards.map((m)=><SortableItem id={m.id} key={m.id} label={m.title} kind="metric"><MetricCard id={m.id} title={m.title} value={m.value} {...sourceProps} displayRows={[{measure:m.title,period,housing_group:housingGroup,aadt,distance_m:distance,noise_db:noise,...m.data}]}><p className="ch-note">{m.detail}</p></MetricCard></SortableItem>)}
    </SortableRegion>
    <p className="ch-review-warning">Shares use identified homes, not a complete census of new housing. {number(missingCount)} candidate full-building records in this scope remain outside the headline. {housingGroup !== "all" && <>Records without a unit count cannot be assigned to this housing group. </>}Unknown exposure stays in the denominator. <a href="#noise-sensitivity">Noise location sensitivity ↓</a></p>
    <Section id="map-heading" title="Explore the permit locations" spacing="after-metrics"
      filters={<div className="ch-map-options"><SegmentedControl label="Exposure screen" value={mode} options={modes} onChange={(v)=>change("screenMode",modes.findIndex((x)=>x.value===v))} /><Switch label="Ward boundaries (2023–)" checked={showWards} onChange={(value)=>change("wardOverlay",value ? 1 : 0)} /></div>}>
      <DataComponent id="housing-map" title="Housing and road exposure" variant="card" kind="custom" {...sourceProps}
        queryIds={["permits","map_geometry"]} sourceRowsByQuery={{permits:housing,map_geometry:queries.map_geometry.rows}} displayRows={tableRows}>
        <div className="ch-map-layout"><HousingMap rows={housing} geometry={geometry} aadt={aadt} showWards={showWards} selectedWard={ward === "all" ? 0 : Number(ward)} onSelectWard={(value)=>onFilterChange("ward",String(value))} selectedId={selectedId} onSelect={(r)=>setSelectedId(r?.permit_id ?? r)} screen={screen} />
          <div><PlaceSummary summary={placeSummary} /><PermitDetail row={selected} /></div></div>
        <p className="ch-note" data-reviewed-rows>{number(current.exposed)} of {number(current.units)} homes meet the selected screen ({pct(current.exposedShare)}). {number(current.unknown)} homes have unknown exposure. Gray lines show roads meeting the selected total-traffic threshold in every view.</p>
        {mode === "trucks" && <p className="ch-note">Truck screen: within {distance} m of a road with at least 1,000 recorded heavy commercial vehicles/day. Zero or undated truck counts do not establish zero truck traffic.</p>}
      </DataComponent>
    </Section>
    <DataComponent id="scope-findings" title="Read the selected comparison" variant="plain" kind="custom" {...sourceProps} displayRows={bySize}>
      <p data-reviewed-rows>{largeBuilding.units > 0 && singleFamily.units > 0
        ? <>For the selected {modes.find((m)=>m.value===mode).label.toLowerCase()} screen, {pct(largeBuilding.exposedShare)} of homes in authorizations for 50 or more units meet the screen, compared with {pct(singleFamily.exposedShare)} of one-unit authorizations. </>
        : current.units > 0 ? <>{pct(current.exposedShare)} of the selected homes are confirmed to meet the screen. </> : <>No identified homes match these filters. </>}
        {topThree.length > 0 && <>The largest numbers are in {topThree.map((r)=>`${r.community_name} (${number(r.exposed)})`).join(", ")}.</>}</p>
      <p className="ch-note">Size is the unit count per permit or consolidated project, which may cover multiple buildings. These are comparisons within newly permitted housing. They do not establish differences from existing housing, historical exposure, or a causal effect of zoning.</p>
    </DataComponent>
    <Section id="distribution-heading" title="Which homes, and which years?" columns={2}>
      <EvidenceChart id="size-share" title="Share by authorization size" variant="card" {...sourceProps} spec={sizeSpec} rows={bySize} height={280} />
      <EvidenceChart id="annual-units" title={`Identified homes by permit year · ${period}`} variant="card" {...sourceProps} spec={annualSpec} rows={annualRows(housing,screen)} height={280} />
    </Section>
    <Section id="sensitivity-heading" title="How the definition changes the result" columns={2}>
      <EvidenceChart id="traffic-sensitivity" title="Traffic proximity thresholds" variant="card" {...sourceProps} spec={sensitivitySpec} rows={trafficSensitivity(housing,config.exposure.traffic_aadt,config.exposure.distance_m)} height={280} />
      <DataComponent id="noise-sensitivity" title="Noise is sensitive to the address point" variant="card" kind="custom" {...sourceProps} displayRows={[{threshold_db:noise,allNineCellsShare:noiseAll.exposedShare,pointShare:n.exposedShare,anyNineCellsShare:noiseAny.exposedShare}]}>
        <div id="noise-sensitivity"><p>Confirmed noise ≥{noise} dBA: <strong>{pct(n.exposedShare)}</strong> at the permit point.</p>
          <p>Within a 3×3-pixel neighbourhood, <strong>{pct(noiseAll.exposedShare)}</strong> of identified homes meet this threshold in every pixel, and <strong>{pct(noiseAny.exposedShare)}</strong> in at least one pixel.</p>
          <p className="ch-note">This tests shifts of about 28 metres per axis (40 metres diagonally). It is a location sensitivity check, not a statistical interval. Transparent pixels cannot establish low noise; coarse bands and address points limit precision.</p>
          <p>With unknown exposure assigned to either endpoint, the point-based noise share is {pct(n.exposedShare)}–{pct(n.upperShare)}. Missing housing counts are outside this range.</p></div>
      </DataComponent>
    </Section>
    <Section id="community-heading" title="Where screened homes are concentrated">
      <DataComponent id="community-table" title="Community areas" variant="card" kind="table" {...sourceProps} displayRows={byCommunity}><DataTable label="Community area exposure totals" rows={byCommunity} columns={communityColumns} /></DataComponent>
    </Section>
    <Section id="permit-heading" title="The permits behind the numbers">
      <DataComponent id="permit-table" title="Included full-building permits" variant="card" kind="table" {...sourceProps} displayRows={tableRows}><DataTable label="Included housing permits" rows={tableRows} columns={permitColumns} rowKey="permit_id" selectedRowKey={selectedId} onRowSelect={(r)=>setSelectedId(r.permit_id)} rowActionLabel={(r)=>`Inspect permit ${r.permit_id}`} />
        {selected && <PermitDetail row={selected} />}
      </DataComponent>
    </Section>
    <Section id="change-heading" title="Additions and conversions, separately">
      <EvidenceChart id="housing-changes" title="Explicit net changes in housing-related renovations" variant="card" queryId="permits" sourceRows={changes} scopeFilters={scopeFilters}
        rows={changeRows(changes)} spec={{type:"bar",x:"year",y:"added",fields:["added","removed"],colors:{added:"var(--chart-1)",removed:"var(--chart-2)"}}} height={240}>
        <p className="ch-note">Positive and negative changes are kept separate. Description totals such as “renovate a 20-unit building” are never treated as 20 new homes. Coverage is incomplete and changes sharply when the City’s structured field begins in 2024. These units are excluded from the new-construction headline. Housing-group and authorization-size filters use headline unit counts and may exclude these change records.</p>
        <p className="ch-note" data-reviewed-rows>{number(unresolvedChanges.length)} records with conflicting net counts, preliminary scope or duplicate change authorizations are excluded in this view. Original values and review reasons remain in the secondary-change audit.</p>
      </EvidenceChart>
    </Section>
    <footer className="ch-footer">Source-backed research prototype · See Counts & methods for the counting audit and limitations. Inspired by <a href="https://housing.acitythatworks.org" target="_blank" rel="noreferrer">A City That Works’ Chicago Housing Tracker</a>. Data: City of Chicago, Cook County Assessor, IDOT and U.S. BTS. Thresholds are analytical screens, not safety standards.</footer>
  </>;
}
