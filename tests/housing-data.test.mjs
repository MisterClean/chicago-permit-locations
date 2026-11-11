import test from 'node:test';
import assert from 'node:assert/strict';
import { totals, noiseScreen, trafficScreen, both, changeRows, matchesHousingGroup } from '../app/src/content/dashboard/housing-data.js';
test('unit weighting retains unknown exposures and excludes unresolved housing', () => {
  const rows = [{included:true,unit_count:500,x:true},{included:true,unit_count:1,x:false},{included:true,unit_count:99,x:null},{included:false,unit_count:9000,x:true}];
  const result=totals(rows,r=>r.x);
  assert.equal(result.units,600); assert.equal(result.exposedShare,500/600); assert.equal(result.upperShare,599/600);
});
test('missing distances and transparent noise do not turn into zero', () => {
  assert.equal(trafficScreen({geocode_status:'inside_city',nearest_aadt_25000_distance_m:null},25000,200),null);
  assert.equal(noiseScreen({geocode_status:'inside_city',noise_db_lower:null,noise_db_upper:null},55),null);
  assert.equal(noiseScreen({geocode_status:'inside_city',noise_db_lower:60,noise_db_upper:70},65),null);
  assert.equal(both(false,null),false); assert.equal(both(true,null),null);
});
test('a renovation description total is not a net addition',()=>{
  const result=changeRows([{analysis_class:'conversion',permit_year:2024,unit_count:20,net_unit_change:null,change_included:false},{analysis_class:'conversion',permit_year:2024,net_unit_change:2,change_included:true},{analysis_class:'other_renovation',permit_year:2024,net_unit_change:-1,change_included:true},{analysis_class:'addition',permit_year:2024,net_unit_change:62,change_included:false}]);
  assert.deepEqual(result,[{year:'2024-01-01',added:2,removed:-1,permits:2}]);
});

test('browser calculations reconcile to the Python snapshot in every headline mode', async (t) => {
  const { readFile } = await import('node:fs/promises');
  const { evaluate } = await import('../app/src/content/dashboard/housing-data.js');
  let snapshot;
  try { snapshot = JSON.parse(await readFile(new URL('../app/src/data.json',import.meta.url),'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return t.skip('Run the pipeline to create the reviewed snapshot'); throw error; }
  const cfg=snapshot.analysisSummary.config;
  const rows=snapshot.queries.permits.rows.filter(r=>r.permit_year<=cfg.population.last_complete_year);
  for (const mode of ['traffic','noise','both','trucks']) {
    const actual=totals(rows,r=>evaluate(r,mode,cfg.exposure.default_traffic_aadt,cfg.exposure.default_distance_m,cfg.exposure.default_noise_db));
    const expected=snapshot.analysisSummary.complete_years[mode];
    assert.equal(actual.units,expected.units,mode);
    assert.equal(actual.exposed,expected.exposed_units,mode);
    assert.equal(actual.unknown,expected.unknown_units,mode);
    assert.equal(actual.exposedShare,expected.share_of_identified_units,mode);
    assert.equal(actual.upperShare,expected.share_upper_if_unknown_exposed,mode);
    const single=totals(rows.filter(r=>matchesHousingGroup(r,'single')),r=>evaluate(r,mode,cfg.exposure.default_traffic_aadt,cfg.exposure.default_distance_m,cfg.exposure.default_noise_db));
    const multi=totals(rows.filter(r=>matchesHousingGroup(r,'multi')),r=>evaluate(r,mode,cfg.exposure.default_traffic_aadt,cfg.exposure.default_distance_m,cfg.exposure.default_noise_db));
    for(const field of ['units','permits','exposed','unknown']) assert.equal(single[field]+multi[field],actual[field],`${mode} grouped ${field}`);
  }
});

test('housing groups cover every known size without assigning missing counts',()=>{
  for(const unit_count of [1,2,6,7,49,50,800]) {
    const row={unit_count};
    assert.equal(matchesHousingGroup(row,'single'),unit_count===1);
    assert.equal(matchesHousingGroup(row,'multi'),unit_count>=2);
  }
  for(const unit_count of [null,undefined,0,-1,1.5]) {
    assert.equal(matchesHousingGroup({unit_count},'single'),false);
    assert.equal(matchesHousingGroup({unit_count},'multi'),false);
    assert.equal(matchesHousingGroup({unit_count},'all'),true);
  }
});

test('a shared project with unknown allocation retains ambiguous distance thresholds',()=>{
  const row={geocode_status:'inside_city',nearest_aadt_25000_distance_m:164,nearest_aadt_25000_distance_m_min:131,nearest_aadt_25000_distance_m_max:164};
  assert.equal(trafficScreen(row,25000,150),null);
  assert.equal(trafficScreen(row,25000,200),true);
});

test('current ward selections and unassigned homes partition each citywide screen', async (t) => {
  const { readFile } = await import('node:fs/promises');
  const { evaluate } = await import('../app/src/content/dashboard/housing-data.js');
  let snapshot;
  try { snapshot = JSON.parse(await readFile(new URL('../app/src/data.json',import.meta.url),'utf8')); }
  catch (error) { if (error.code === 'ENOENT') return t.skip('Run the pipeline to create the reviewed snapshot'); throw error; }
  const cfg = snapshot.analysisSummary.config;
  const rows = snapshot.queries.permits.rows.filter((row) => row.included && row.permit_year <= cfg.population.last_complete_year);
  const ids = [...new Set(rows.map((row) => row.ward_2023).filter(Boolean))];
  assert.equal(ids.length, 50);
  for (const row of rows) assert.equal(row.ward_2023 !== null, row.ward_2023_status === 'assigned');
  for (const mode of ['traffic','noise','both','trucks']) {
    const screen = (row) => evaluate(row,mode,cfg.exposure.default_traffic_aadt,cfg.exposure.default_distance_m,cfg.exposure.default_noise_db);
    const all = totals(rows,screen);
    const parts = [...ids, null].map((ward) => totals(rows.filter((row) => row.ward_2023 === ward),screen));
    for (const field of ['units','permits','exposed','unknown']) {
      assert.equal(parts.reduce((sum,part) => sum + part[field],0),all[field],`${mode} ward ${field}`);
    }
  }
});
