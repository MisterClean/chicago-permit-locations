import React, { memo, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Button } from "../../data-app-public.jsx";
import "./housing-map.css";

const MAX_ZOOM = 24;
const MIN_POINT_RADIUS = 2.5;
const MAX_POINT_RADIUS = 20;

function initialView(geometry) {
  const width = Number(geometry?.width) || 1;
  const height = Number(geometry?.height) || 1;
  return { x: -width * 0.04, y: -height * 0.04, width: width * 1.08, height: height * 1.08 };
}

function pointRadius(units) {
  return Math.max(MIN_POINT_RADIUS, Math.min(MAX_POINT_RADIUS, 1.5 * Math.sqrt(Math.max(0, Number(units) || 0))));
}

function exposureColor(result) {
  return result === true ? "var(--chart-2)" : result === false ? "var(--chart-1)" : "var(--chart-8)";
}

function screenPoint(svg, clientX, clientY) {
  const matrix = svg?.getScreenCTM();
  if (!matrix) return null;
  const point = svg.createSVGPoint();
  point.x = clientX;
  point.y = clientY;
  return point.matrixTransform(matrix.inverse());
}

function hasNoiseOverlay(noise) {
  return typeof noise?.href === "string" && noise.href.startsWith("data:image/png;base64,")
    && [noise.x, noise.y, noise.width, noise.height].every((value) => value != null && Number.isFinite(Number(value)))
    && Number(noise.width) > 0 && Number(noise.height) > 0;
}

const Geography = memo(function Geography({ geometry, aadt, metresPerPixel, zoom, showWards, showNoise }) {
  const clipId = useId();
  const roads = useMemo(() => {
    const groups = [[], [], []];
    for (const road of geometry.roads || []) {
      if (!road.path || !Number.isFinite(Number(road.aadt)) || Number(road.aadt) < Number(aadt)) continue;
      groups[Number(road.aadt) >= 100000 ? 2 : Number(road.aadt) >= 50000 ? 1 : 0].push(road.path);
    }
    return groups.map((paths) => paths.join(" "));
  }, [geometry.roads, aadt]);
  const noise = geometry.noiseOverlay;
  const showImage = showNoise && hasNoiseOverlay(noise);
  return <g aria-hidden="true" pointerEvents="none">
    {showImage && <defs><clipPath id={clipId}>
      {(geometry.cityPaths || []).map((path, index) => <path key={index} d={path} clipRule="evenodd" />)}
    </clipPath></defs>}
    {(geometry.cityPaths || []).map((path, index) => <path key={index} d={path} className="housing-map-city" vectorEffect="non-scaling-stroke" />)}
    {showImage && <image href={noise.href} x={noise.x} y={noise.y} width={noise.width} height={noise.height}
      preserveAspectRatio="none" clipPath={`url(#${clipId})`} className="housing-map-noise" />}
    {!showWards && (geometry.communities || []).map((community) => <path key={community.id} d={community.path} className="housing-map-community" vectorEffect="non-scaling-stroke" />)}
    {roads.map((path, index) => path && <path key={index} d={path} className="housing-map-road" strokeWidth={[0.8, 1.25, 1.8][index]} vectorEffect="non-scaling-stroke" />)}
    {!showWards && zoom >= 1.8 && (geometry.communities || []).map((community) => (
      Number.isFinite(Number(community.labelX)) && Number.isFinite(Number(community.labelY))
        ? <text key={community.id} x={community.labelX} y={community.labelY} className="housing-map-community-label"
          fontSize={11 * metresPerPixel} strokeWidth={3 * metresPerPixel} textAnchor="middle">{community.name}</text>
        : null
    ))}
  </g>;
});

const WardOverlay = memo(function WardOverlay({ wards, metresPerPixel, selectedWard, showNoise }) {
  return <g className="housing-map-wards" aria-hidden="true" pointerEvents="none">
    {showNoise && wards.map((ward) => <path key={`halo-${ward.id}`} d={ward.path}
      className={Number(ward.id) === Number(selectedWard) ? "housing-map-ward-halo is-selected" : "housing-map-ward-halo"}
      vectorEffect="non-scaling-stroke" />)}
    {wards.map((ward) => <path key={ward.id} d={ward.path}
      className={Number(ward.id) === Number(selectedWard) ? "housing-map-ward is-selected" : "housing-map-ward"}
      vectorEffect="non-scaling-stroke" />)}
    {wards.map((ward) => <text key={ward.id} x={ward.labelX} y={ward.labelY}
      className="housing-map-ward-label" fontSize={11 * metresPerPixel} strokeWidth={4 * metresPerPixel}
      textAnchor="middle" dominantBaseline="central">{ward.id}</text>)}
  </g>;
});

// The hit areas sit below permit circles so selecting a permit still takes
// precedence. The visible ward lines and numbers remain non-interactive.
const WardHitTargets = memo(function WardHitTargets({ wards, selectedWard }) {
  return <g aria-hidden="true">
    {wards.map((ward) => <path key={ward.id} d={ward.path} data-ward-id={ward.id}
      className={Number(ward.id) === Number(selectedWard) ? "housing-map-ward-hit is-selected" : "housing-map-ward-hit"}
      pointerEvents="fill" />)}
  </g>;
});

const PermitMarks = memo(function PermitMarks({ points, metresPerPixel, selectedId, activeId, tooltipId }) {
  return <g className="housing-map-marks">
    {points.map(({ row, exposure }) => <circle key={row.permit_id}
      data-permit-id={row.permit_id}
      role="button"
      tabIndex={String(row.permit_id) === activeId ? 0 : -1}
      aria-hidden={String(row.permit_id) === activeId ? undefined : true}
      aria-label={`${row.address}, ${Number(row.unit_count).toLocaleString()} permitted homes, ${row.permit_year}. ${exposure === true ? "Meets selected exposure screen" : exposure === false ? "Below selected exposure screen" : "Exposure data unavailable"}. Enter to inspect.`}
      aria-describedby={String(row.permit_id) === activeId ? tooltipId : undefined}
      cx={Number(row.map_x)} cy={Number(row.map_y)}
      r={pointRadius(row.unit_count) * metresPerPixel}
      fill={exposureColor(exposure)}
      className={String(row.permit_id) === String(selectedId) ? "housing-map-dot is-selected" : "housing-map-dot"}
      vectorEffect="non-scaling-stroke"
    />)}
  </g>;
});

function ZoomIcon({ minus }) {
  return <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
    <path d="M3.5 9h11" stroke="currentColor" strokeWidth="1.7" />
    {!minus && <path d="M9 3.5v11" stroke="currentColor" strokeWidth="1.7" />}
  </svg>;
}

/** Actual projected Chicago geometry. Filtering, source inspection and the
 * accessible permit table belong to the parent evidence component. */
export function HousingMap({ rows, geometry, aadt, showWards = false, showNoise = false, selectedWard = 0, onSelectWard, selectedId, onSelect, screen }) {
  const svgRef = useRef(null);
  const stageRef = useRef(null);
  const gesture = useRef(null);
  const pendingWardClick = useRef(null);
  const [view, setView] = useState(() => initialView(geometry));
  const [size, setSize] = useState({ width: 900, height: 600 });
  const [hover, setHover] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [activeMarkerId, setActiveMarkerId] = useState(null);
  const tooltipId = useId();
  const base = useMemo(() => initialView(geometry), [geometry?.width, geometry?.height]);

  const cancelWardClick = useCallback(() => {
    if (pendingWardClick.current !== null) clearTimeout(pendingWardClick.current);
    pendingWardClick.current = null;
  }, []);

  useEffect(() => { setView(base); }, [base]);
  useEffect(() => () => cancelWardClick(), [cancelWardClick]);
  useEffect(() => { if (!showWards) cancelWardClick(); }, [showWards, cancelWardClick]);
  useEffect(() => {
    const element = stageRef.current;
    if (!element) return undefined;
    const update = () => {
      const bounds = element.getBoundingClientRect();
      setSize({ width: Math.max(1, bounds.width), height: Math.max(1, bounds.height) });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const points = useMemo(() => (rows || []).filter((row) => (
    row.map_x != null && row.map_y != null && Number.isFinite(Number(row.map_x)) && Number.isFinite(Number(row.map_y))
  )).map((row) => ({ row, exposure: screen(row) })).sort((a, b) => Number(b.row.unit_count) - Number(a.row.unit_count)), [rows, screen]);
  const pointsById = useMemo(() => new Map(points.map((point) => [String(point.row.permit_id), point])), [points]);
  const activeId = activeMarkerId && pointsById.has(activeMarkerId) ? activeMarkerId : String(points[0]?.row.permit_id ?? "");
  const metresPerPixel = Math.max(view.width / size.width, view.height / size.height);
  const zoom = base.width / view.width;
  const selected = selectedId == null ? null : pointsById.get(String(selectedId));
  const hovered = hover ? pointsById.get(hover.id) : null;
  const unknownLocationCount = (rows?.length || 0) - points.length;
  const scaleDistance = [100, 200, 500, 1000, 2000, 5000, 10000].filter((distance) => distance / metresPerPixel <= 115).at(-1) || 100;

  const changeZoom = useCallback((factor, anchor = null) => {
    setView((previous) => {
      const nextWidth = Math.max(base.width / MAX_ZOOM, Math.min(base.width, previous.width / factor));
      const ratio = nextWidth / previous.width;
      const centerX = anchor?.x ?? previous.x + previous.width / 2;
      const centerY = anchor?.y ?? previous.y + previous.height / 2;
      return { x: centerX - (centerX - previous.x) * ratio, y: centerY - (centerY - previous.y) * ratio, width: nextWidth, height: previous.height * ratio };
    });
    setHover(null);
  }, [base]);

  const reset = useCallback(() => { setView(base); setHover(null); }, [base]);

  const pointerDown = (event) => {
    if (gesture.current || (event.pointerType === "mouse" && event.button !== 0)) return;
    cancelWardClick();
    const point = screenPoint(svgRef.current, event.clientX, event.clientY);
    if (!point) return;
    gesture.current = {
      pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
      point, view, moved: false,
      permitId: event.target.closest?.("[data-permit-id]")?.getAttribute("data-permit-id"),
      wardId: event.target.closest?.("[data-ward-id]")?.getAttribute("data-ward-id"),
    };
    svgRef.current.setPointerCapture(event.pointerId);
    setHover(null);
  };

  const pointerMove = (event) => {
    const active = gesture.current;
    if (active) {
      if (active.pointerId !== event.pointerId) return;
      const deltaX = event.clientX - active.startX;
      const deltaY = event.clientY - active.startY;
      if (!active.moved && Math.hypot(deltaX, deltaY) < 4) return;
      active.moved = true;
      setDragging(true);
      const scale = Math.max(active.view.width / size.width, active.view.height / size.height);
      setView({ ...active.view, x: active.view.x - deltaX * scale, y: active.view.y - deltaY * scale });
      return;
    }
    const id = event.target.closest?.("[data-permit-id]")?.getAttribute("data-permit-id");
    if (!id) { setHover(null); return; }
    const bounds = stageRef.current.getBoundingClientRect();
    setHover({ id, x: event.clientX - bounds.left, y: event.clientY - bounds.top });
  };

  const pointerUp = (event) => {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (!active.moved && active.permitId && onSelect) onSelect(active.permitId);
    else if (!active.moved && active.wardId && onSelectWard) {
      // Leave a brief window for the existing double-click zoom gesture.
      const ward = Number(active.wardId);
      pendingWardClick.current = setTimeout(() => {
        pendingWardClick.current = null;
        onSelectWard(ward);
      }, 350);
    }
    gesture.current = null;
    setDragging(false);
    if (svgRef.current.hasPointerCapture(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId);
  };

  const pointerCancel = (event) => {
    if (gesture.current?.pointerId !== event.pointerId) return;
    gesture.current = null;
    setDragging(false);
  };

  const focusMarker = (id) => {
    const point = pointsById.get(id);
    if (!point) return;
    setActiveMarkerId(id);
    const x = Number(point.row.map_x);
    const y = Number(point.row.map_y);
    if (x < view.x || x > view.x + view.width || y < view.y || y > view.y + view.height) {
      setView((previous) => ({ ...previous, x: x - previous.width / 2, y: y - previous.height / 2 }));
    }
    requestAnimationFrame(() => svgRef.current?.querySelector(`[data-permit-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true }));
  };

  const markerFocus = (event) => {
    const id = event.target.getAttribute?.("data-permit-id");
    if (!id) return;
    setActiveMarkerId(id);
    const dot = event.target.getBoundingClientRect();
    const stage = stageRef.current.getBoundingClientRect();
    setHover({ id, x: dot.left + dot.width / 2 - stage.left, y: dot.top + dot.height / 2 - stage.top, keyboard: true });
  };

  const keyboardMove = (event) => {
    const markerId = event.target.getAttribute?.("data-permit-id");
    if (markerId) {
      const offset = { ArrowLeft: -1, ArrowRight: 1, PageUp: -25, PageDown: 25 }[event.key];
      if (offset != null && points.length > 0) {
        event.preventDefault();
        const index = points.findIndex((point) => String(point.row.permit_id) === markerId);
        const nextIndex = (index + offset % points.length + points.length) % points.length;
        focusMarker(String(points[nextIndex].row.permit_id));
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onSelect?.(markerId);
      } else if (event.key === "Escape") {
        event.preventDefault();
        setHover(null);
      }
      return;
    }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); changeZoom(1.5); }
    else if (event.key === "-") { event.preventDefault(); changeZoom(1 / 1.5); }
    else if (event.key === "Home") { event.preventDefault(); reset(); }
    else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      const x = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      const y = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
      setView((previous) => ({ ...previous, x: previous.x + x * previous.width * 0.12, y: previous.y + y * previous.height * 0.12 }));
      setHover(null);
    }
  };

  if (!geometry || !Array.isArray(geometry.cityPaths) || geometry.cityPaths.length === 0) {
    return <div className="housing-map-unavailable" role="status">Chicago map geometry is unavailable. The permit table retains the reviewed records.</div>;
  }

  const noise = geometry.noiseOverlay;
  const hasNoise = hasNoiseOverlay(noise);
  const displayNoise = showNoise && hasNoise;

  return <div className="housing-map" data-reviewed-rows>
    <div ref={stageRef} className={`housing-map-stage${dragging ? " is-dragging" : ""}${displayNoise ? " has-noise-layer" : ""}`}>
      <svg ref={svgRef} className="housing-map-svg" viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
        tabIndex={0} role="group" aria-label={`Chicago housing permit map${displayNoise ? " with modeled road-noise layer" : ""}. Arrow keys pan; plus and minus zoom; Home resets. Tab to a permit, then Left and Right change permits, Page Up and Page Down jump 25 permits, Enter selects, and Escape dismisses its tooltip.`}
        onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerCancel}
        onPointerLeave={() => { if (!gesture.current) setHover((previous) => previous?.keyboard ? previous : null); }} onKeyDown={keyboardMove}
        onFocus={markerFocus} onBlur={(event) => { if (event.target.hasAttribute?.("data-permit-id")) setHover(null); }}
        onDoubleClick={(event) => { cancelWardClick(); changeZoom(1.8, screenPoint(svgRef.current, event.clientX, event.clientY)); }}>
        <Geography geometry={geometry} aadt={aadt} metresPerPixel={metresPerPixel} zoom={zoom} showWards={showWards} showNoise={displayNoise} />
        {showWards && <WardHitTargets wards={geometry.wards || []} selectedWard={selectedWard} />}
        <PermitMarks points={points} metresPerPixel={metresPerPixel} selectedId={selectedId} activeId={activeId} tooltipId={tooltipId} />
        {showWards && <WardOverlay wards={geometry.wards || []} metresPerPixel={metresPerPixel} selectedWard={selectedWard} showNoise={displayNoise} />}
        {selected && <circle cx={selected.row.map_x} cy={selected.row.map_y}
          r={(pointRadius(selected.row.unit_count) + 4) * metresPerPixel} className="housing-map-selection-ring"
          vectorEffect="non-scaling-stroke" pointerEvents="none" aria-hidden="true" />}
      </svg>

      <div className="housing-map-north" aria-hidden="true">
        <span>N</span><svg width="14" height="27" viewBox="0 0 14 27" fill="none"><path d="M7 26V4M2 10l5-7 5 7" stroke="currentColor" strokeWidth="1.4" /></svg>
      </div>
      <div className="housing-map-controls" role="group" aria-label="Map view controls">
        <Button className="housing-map-zoom" aria-label="Zoom in" title="Zoom in" disabled={zoom >= MAX_ZOOM - 0.01} onClick={() => changeZoom(1.6)}><ZoomIcon /></Button>
        <Button className="housing-map-zoom" aria-label="Zoom out" title="Zoom out" disabled={zoom <= 1.001} onClick={() => changeZoom(1 / 1.6)}><ZoomIcon minus /></Button>
        <Button className="housing-map-reset" onClick={reset} aria-label="Reset map view">Reset</Button>
      </div>

      {hovered && <div className="housing-map-tooltip" role="tooltip" id={tooltipId}
        style={{ left: Math.max(8, Math.min(hover.x + 14, size.width - 260)), top: Math.max(8, Math.min(hover.y + 14, size.height - 180)) }}>
        <strong>{hovered.row.address}</strong>
        <span>{Number(hovered.row.unit_count).toLocaleString()} permitted homes · {hovered.row.permit_year}</span>
        <span>{hovered.exposure === true ? "Meets selected exposure screen" : hovered.exposure === false ? "Below selected exposure screen" : "Exposure data unavailable"}</span>
        <small>{hover.keyboard ? "Enter" : "Click"} to inspect permit {hovered.row.permit_id}</small>
      </div>}

      <div className="housing-map-scale" aria-label={`Map scale ${scaleDistance >= 1000 ? `${scaleDistance / 1000} kilometres` : `${scaleDistance} metres`}`}>
        <div className="housing-map-scale-rule" style={{ width: scaleDistance / metresPerPixel }} />
        <span>{scaleDistance >= 1000 ? `${scaleDistance / 1000} km` : `${scaleDistance} m`}</span>
      </div>
      {points.length === 0 && <div className="housing-map-empty" role="status">No mapped permits in this selection.</div>}
    </div>
    <div className="housing-map-footer">
      <div className="housing-map-key" aria-label="Permit and road legend">
        <strong className="housing-map-key-title">Permits</strong>
        <span><i className="housing-map-swatch is-exposed" />Meets screen</span>
        <span><i className="housing-map-swatch is-below" />Below screen</span>
        <span><i className="housing-map-swatch is-unknown" />Unknown</span>
        <span><i className="housing-map-road-swatch" />Roads ≥{Number(aadt).toLocaleString()} vehicles/day</span>
        {showWards && <span><i className="housing-map-ward-swatch" />Ward boundaries · 2023–</span>}
      </div>
      {displayNoise && <div className="housing-map-noise-legend" role="group" aria-label="Modeled road noise legend">
        <strong>Modeled road noise · dBA, 24-hour</strong>
        <div className="housing-map-noise-categories">
          {(Array.isArray(noise.categories) ? noise.categories : []).map((category) => <span key={category.value}>
            <i aria-hidden="true" style={{backgroundColor:category.color}} />{category.label}
          </span>)}
        </div>
      </div>}
      {displayNoise && <p className="housing-map-note">The colored surface is the <a href="https://www.bts.gov/geospatial/national-transportation-noise-map" target="_blank" rel="noreferrer">BTS modeled road-noise map</a> using 2023 road inputs. Transparent areas may be below 45 dBA or unmapped/unknown; they are not necessarily quiet. Colors show modeled categories, not precise sound levels at homes.</p>}
      {showNoise && !hasNoise && <p className="housing-map-note" role="status">The road-noise map layer is unavailable in this snapshot.</p>}
      {showWards && <p className="housing-map-note">Click a ward or use the ward selector to filter. <a href="https://data.cityofchicago.org/d/p293-wvbd" target="_blank" rel="noreferrer">City boundaries effective May 2023</a> are used for all permit years.</p>}
      <p className="housing-map-note">Circle area shows permitted homes; small projects are enlarged and large circles capped. Drag to pan; use the zoom controls or double-click.</p>
      <p className="housing-map-note">Keyboard: Tab to a permit; Left/Right move between permits, Page Up/Down jump 25, Enter selects, and Escape closes the tooltip.</p>
      {unknownLocationCount > 0 && <p className="housing-map-note">{unknownLocationCount.toLocaleString()} selected {unknownLocationCount === 1 ? "permit has" : "permits have"} no usable map location and {unknownLocationCount === 1 ? "remains" : "remain"} in the table.</p>}
    </div>
  </div>;
}
