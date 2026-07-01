/* Shepherd trace viewer — renders shepherd.trace-view.v3 over cytoscape. */

const INK = "#16110a";
const PAPER = "#efe9dd";
const VERMILLION = "#c4453c";
const OCHRE = "#a87b1a";
const STEEL = "#4a6478";
const ASH = "#9a9089";
const GREEN = "#61784a";
const PURPLE = "#735a8f";

let CY = null;
let MODEL = null;
let LANE_DRAG = null;
let PLAYBACK_INDEX = 0;

const FAMILY_COLOURS = {
  task: OCHRE,
  substrate: STEEL,
  run: VERMILLION,
  supervisor: PURPLE,
  step: GREEN,
  check: "#b05f2c",
  model: "#5a7f8f",
  judge: "#73804b",
  effect: "#2f6f5f",
  event: ASH,
};

async function boot() {
  cytoscape.use(window.cytoscapeDagre);
  if (window.__TRACE__) {
    MODEL = window.__TRACE__;
  } else {
    const res = await fetch("/api/trace");
    MODEL = await res.json();
  }
  await ensureFonts();
  renderHeader();
  renderGraph();
  renderRunSummary();
  setupInspectorToggle();
  setupPlayback();
  window.CY = CY;
  window.MODEL = MODEL;
  window.selectNode = selectNode;
}

async function ensureFonts() {
  if (!document.fonts || !document.fonts.load) return;
  try {
    await Promise.all([
      document.fonts.load("14px 'Instrument Serif'"),
      document.fonts.load("12px 'JetBrains Mono'"),
    ]);
    await document.fonts.ready;
  } catch (_e) { /* fall back to system fonts */ }
}

function renderHeader() {
  const run = MODEL.run || {};
  const source = MODEL.source || {};
  const summary = run.summary || {};
  const causalCount = drawableEdges().length;
  const compact = window.matchMedia?.("(max-width: 720px)").matches;
  document.getElementById("run-meta").textContent = compact
    ? [
        run.id || source.trace_owner_id || "trace",
        `${summary.events ?? 0} ev`,
        `${summary.lanes ?? 0} ln`,
        `${causalCount} links`,
      ].join(" · ")
    : [
        run.id || source.trace_owner_id || "trace",
        formatCount(summary.events ?? 0, "event"),
        formatCount(summary.lanes ?? 0, "lane"),
        formatCausalCount(causalCount),
      ].join("  ·  ");
  const counts = document.getElementById("graph-counts");
  if (counts) counts.textContent = "";
  const badge = document.getElementById("badge");
  if (run.terminal_status) {
    badge.hidden = false;
    badge.textContent = run.terminal_status;
  }
}

function renderGraph() {
  const elements = buildElements();
  CY = cytoscape({
    container: document.getElementById("cy"),
    elements,
    style: graphStyle(),
    wheelSensitivity: 0.2,
    layout: {
      name: "preset",
    },
  });
  CY.ready(() => {
    drawLaneOverlays();
    fitStory(42);
  });
  CY.on("pan zoom resize", renderLaneLabels);
  CY.on("tap", "node.event", (e) => selectNode(e.target.id()));
  CY.on("tap", "edge", (e) => selectEdge(e.target.id()));
  CY.on("tap", "node.lane-bg", (e) => selectLane(e.target.data("laneId")));
  CY.on("tap", (e) => {
    if (e.target === CY) {
      clearSelection();
      renderRunSummary();
    }
  });
  setupGraphDragging();
  setupHoverPreview();
}

function buildElements() {
  const els = [];
  const positions = defaultPositions();
  const orderedNodes = nodesInSequence();
  for (const [index, node] of orderedNodes.entries()) {
    els.push({
      group: "nodes",
      data: {
        id: node.id,
        label: node.label || node.kind,
        shortLabel: nodeShortLabel(node),
        seqLabel: String((node.sequence ?? index) + 1),
        kind: node.kind,
        family: node.family,
        role: node.role,
        sequence: node.sequence ?? index,
      },
      position: positions[node.id],
      classes: `event family-${cssClass(node.family)} role-${cssClass(node.role)}`,
    });
  }
  for (const edge of MODEL.edges || []) {
    if (edge.kind === "owner_path") continue;
    const storyKind = edgeStoryKind(edge);
    els.push({
      group: "edges",
      data: { id: edge.id, source: edge.source, target: edge.target, label: edge.label, storyKind },
      classes: `edge edge-${cssClass(edge.kind)} edge-${cssClass(storyKind)}`,
    });
  }
  return els;
}

function defaultPositions() {
  const nodes = nodesInSequence();
  const lanes = MODEL.lanes || [];
  const laneIds = [];
  const laneByNode = new Map();
  for (const lane of lanes) {
    laneIds.push(lane.id);
    for (const nodeId of lane.node_ids || []) {
      if (!laneByNode.has(nodeId)) laneByNode.set(nodeId, lane.id);
    }
  }
  for (const node of nodes) {
    const laneId = (node.lane_ids || [])[0];
    if (laneId && !laneByNode.has(node.id)) laneByNode.set(node.id, laneId);
  }
  if (nodes.some((node) => !laneByNode.has(node.id))) laneIds.push("__unassigned__");

  const orderedLaneIds = replayAwareLaneOrder(laneIds, laneByNode);
  const laneGap = laneIds.length > 3 ? 178 : 188;
  const laneCenters = new Map(orderedLaneIds.map((laneId, index) => [laneId, 136 + index * laneGap]));

  const ranks = causalRanks(nodes, laneByNode);
  const maxRank = Math.max(1, ...[...ranks.values()]);
  const xGap = maxRank > 8 ? 178 : 198;
  const positions = {};
  for (const [index, node] of nodes.entries()) {
    const laneId = laneByNode.get(node.id) || "__unassigned__";
    const rank = ranks.get(node.id) ?? index;
    const laneY = laneCenters.get(laneId) || 140;
    positions[node.id] = {
      x: 120 + rank * xGap,
      y: node.role === "external_anchor" ? laneY + 76 : laneY,
    };
  }
  return positions;
}

function replayAwareLaneOrder(laneIds, laneByNode) {
  const childrenByParent = replayChildLanes(laneByNode);
  if (!childrenByParent.size) return laneIds;
  const laneIdSet = new Set(laneIds);
  const parentByChild = new Map();
  for (const [parentId, childIds] of childrenByParent.entries()) {
    for (const childId of childIds) parentByChild.set(childId, parentId);
  }
  const emitted = new Set();
  const ordered = [];

  function emit(laneId) {
    if (emitted.has(laneId)) return;
    emitted.add(laneId);
    ordered.push(laneId);
    for (const childId of childrenByParent.get(laneId) || []) emit(childId);
  }

  for (const laneId of laneIds) {
    const parentId = parentByChild.get(laneId);
    if (parentId && laneIdSet.has(parentId) && !emitted.has(parentId)) continue;
    emit(laneId);
  }
  for (const laneId of laneIds) emit(laneId);
  return ordered;
}

function replayChildLanes(laneByNode) {
  const replayRelationIds = new Set(
    drawableEdges()
      .filter((edge) => edge.kind === "replay_control")
      .map((edge) => edge.target)
  );
  const childrenByParent = new Map();
  for (const edge of drawableEdges()) {
    if (!replayRelationIds.has(edge.source)) continue;
    const parentLane = laneByNode.get(edge.source);
    const childLane = laneByNode.get(edge.target);
    if (!parentLane || !childLane || parentLane === childLane) continue;
    if (!laneStartsWith(childLane, edge.target)) continue;
    if (!childrenByParent.has(parentLane)) childrenByParent.set(parentLane, []);
    const children = childrenByParent.get(parentLane);
    if (!children.includes(childLane)) children.push(childLane);
  }
  return childrenByParent;
}

function laneStartsWith(laneId, nodeId) {
  const lane = laneById(laneId);
  return lane?.node_ids?.[0] === nodeId;
}

function graphStyle() {
  const style = [
    {
      selector: "node.event",
      style: {
        "background-color": (e) => FAMILY_COLOURS[e.data("family")] || ASH,
        "border-width": 1.5,
        "border-color": INK,
        "shape": "ellipse",
        "width": 34,
        "height": 34,
        "label": "data(shortLabel)",
        "font-family": "JetBrains Mono, ui-monospace, monospace",
        "font-size": 10,
        "font-weight": 600,
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 7,
        "color": INK,
        "text-wrap": "wrap",
        "text-max-width": 96,
        "text-background-color": PAPER,
        "text-background-opacity": 0.82,
        "text-background-padding": 2,
        "overlay-opacity": 0,
        "z-index": 10,
      },
    },
    {
      selector: "node.event.hover",
      style: {
        "label": "data(label)",
        "font-size": 12,
        "font-weight": 600,
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 10,
        "color": INK,
        "text-background-color": PAPER,
        "text-background-opacity": 0.92,
        "text-background-padding": 3,
        "text-wrap": "wrap",
        "text-max-width": 118,
      },
    },
    {
      selector: "node.event:selected",
      style: {
        "label": "data(label)",
        "font-size": 12,
        "text-valign": "bottom",
        "text-halign": "center",
        "text-margin-y": 10,
        "text-background-color": PAPER,
        "text-background-opacity": 0.94,
        "text-background-padding": 3,
        "text-wrap": "wrap",
        "text-max-width": 126,
      },
    },
    {
      selector: "node.event:selected",
      style: {
        "border-width": 3,
        "border-color": VERMILLION,
      },
    },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        "target-arrow-shape": "triangle",
        "target-arrow-color": ASH,
        "line-color": ASH,
        "width": 1.8,
        "opacity": 0.78,
      },
    },
    {
      selector: "edge.edge-causal",
      style: {
        "line-color": VERMILLION,
        "target-arrow-color": VERMILLION,
        "line-style": "solid",
        "width": 2.6,
        "opacity": 0.94,
        "z-index": 6,
      },
    },
    {
      selector: "edge.edge-branch",
      style: {
        "line-style": "dashed",
      },
    },
    {
      selector: "edge.edge-reference",
      style: {
        "line-color": STEEL,
        "target-arrow-color": STEEL,
        "line-style": "dotted",
        "width": 1.9,
        "opacity": 0.86,
        "curve-style": "unbundled-bezier",
        "control-point-distance": 96,
        "control-point-weight": 0.28,
      },
    },
    {
      selector: "edge.edge-replay",
      style: {
        "line-color": VERMILLION,
        "target-arrow-color": VERMILLION,
        "line-style": "dashed",
        "width": 2.8,
        "curve-style": "unbundled-bezier",
        "control-point-distance": 70,
        "control-point-weight": 0.38,
      },
    },
    {
      selector: "edge.edge-basis",
      style: {
        "line-color": STEEL,
        "target-arrow-color": STEEL,
        "line-style": "dotted",
        "width": 1.7,
        "opacity": 0.72,
        "curve-style": "unbundled-bezier",
        "control-point-distance": -92,
        "control-point-weight": 0.28,
      },
    },
    {
      selector: "edge.edge-join",
      style: {
        "line-color": GREEN,
        "target-arrow-color": GREEN,
        "line-style": "solid",
        "width": 2.4,
        "curve-style": "unbundled-bezier",
        "control-point-distance": -48,
        "control-point-weight": 0.5,
      },
    },
    {
      selector: ".pending",
      style: {
        "opacity": 0.16,
      },
    },
    {
      selector: "edge.pending",
      style: {
        "opacity": 0.08,
      },
    },
    {
      selector: "edge:selected",
      style: {
        "line-color": STEEL,
        "target-arrow-color": STEEL,
        "width": 4,
        "opacity": 1,
      },
    },
    {
      selector: "node.lane-bg",
      style: {
        "shape": "round-rectangle",
        "background-opacity": 0.045,
        "background-color": OCHRE,
        "border-width": 1,
        "border-color": "#c7bdae",
        "border-style": "dashed",
        "label": "",
        "z-index": -20,
      },
    },
    {
      selector: "node.lane-bg:selected",
      style: {
        "background-opacity": 0.13,
        "border-color": VERMILLION,
        "border-width": 2,
      },
    },
  ];
  return style;
}

function drawLaneOverlays() {
  CY.nodes(".lane-bg").remove();
  const labelLayer = document.getElementById("lane-labels");
  if (labelLayer) labelLayer.replaceChildren();
  const paddingX = 48, paddingY = 42;
  const overlays = [];
  for (const [index, lane] of (MODEL.lanes || []).entries()) {
    const memberIds = new Set(lane.node_ids || []);
    for (const node of MODEL.nodes || []) {
      if ((node.lane_ids || []).includes(lane.id)) memberIds.add(node.id);
    }
    const members = [...memberIds].map((id) => CY.getElementById(id))
      .filter((n) => n && n.length);
    if (!members.length) continue;
    let bb = null;
    for (const node of members) {
      const nbb = node.boundingBox();
      bb = bb ? {
        x1: Math.min(bb.x1, nbb.x1),
        y1: Math.min(bb.y1, nbb.y1),
        x2: Math.max(bb.x2, nbb.x2),
        y2: Math.max(bb.y2, nbb.y2),
      } : nbb;
    }
    const width = Math.max(bb.x2 - bb.x1 + paddingX * 2, 220);
    const height = bb.y2 - bb.y1 + paddingY * 2;
    overlays.push({
      group: "nodes",
      data: { id: `lane:${lane.id}`, laneId: lane.id, label: laneDisplayLabel(lane, index) },
      position: { x: (bb.x1 + bb.x2) / 2, y: (bb.y1 + bb.y2) / 2 },
      selectable: true,
      grabbable: true,
      locked: false,
      classes: "lane-bg",
      style: { width, height },
    });
  }
  CY.add(overlays);
  CY.nodes(".lane-bg").move({ parent: null });
  CY.nodes(".event").move({ parent: null });
  renderLaneLabels();
}

function renderLaneLabels() {
  if (!CY) return;
  const labelLayer = document.getElementById("lane-labels");
  if (!labelLayer) return;
  labelLayer.replaceChildren();
  for (const [index, lane] of (MODEL.lanes || []).entries()) {
    const laneNode = CY.getElementById(`lane:${lane.id}`);
    if (!laneNode.length) continue;
    const bb = laneNode.renderedBoundingBox({ includeLabels: false });
    const label = document.createElement("button");
    label.type = "button";
    label.className = "lane-label";
    label.dataset.laneId = lane.id;
    label.textContent = laneDisplayLabel(lane, index);
    label.style.left = `${Math.max(10, bb.x1 + 10)}px`;
    label.style.top = `${Math.max(34, bb.y1 - 11)}px`;
    label.style.maxWidth = `${Math.max(120, Math.min(260, bb.w - 20))}px`;
    label.addEventListener("click", () => selectLane(lane.id));
    labelLayer.appendChild(label);
  }
}

function setupGraphDragging() {
  CY.on("grab", "node.lane-bg", (e) => {
    const laneNode = e.target;
    const lane = laneById(laneNode.data("laneId"));
    const start = laneNode.position();
    LANE_DRAG = {
      laneId: laneNode.data("laneId"),
      start: { x: start.x, y: start.y },
      members: (lane?.node_ids || []).map((id) => {
        const member = CY.getElementById(id);
        const pos = member.position();
        return { id, x: pos.x, y: pos.y };
      }),
    };
  });
  CY.on("drag", "node.lane-bg", (e) => {
    if (!LANE_DRAG || LANE_DRAG.laneId !== e.target.data("laneId")) return;
    const pos = e.target.position();
    const dx = pos.x - LANE_DRAG.start.x;
    const dy = pos.y - LANE_DRAG.start.y;
    for (const member of LANE_DRAG.members) {
      CY.getElementById(member.id).position({ x: member.x + dx, y: member.y + dy });
    }
    renderLaneLabels();
  });
  CY.on("free", "node.lane-bg", () => {
    LANE_DRAG = null;
    drawLaneOverlays();
  });
  CY.on("free", "node.event", () => {
    drawLaneOverlays();
  });
}

function setupHoverPreview() {
  const tip = document.getElementById("tooltip");
  CY.on("mouseover", "node.event", (e) => {
    const node = nodeById(e.target.id());
    if (!node) return;
    e.target.addClass("hover");
    tip.hidden = false;
    tip.innerHTML =
      `<div class="tt-head">${escapeHtml(node.label || node.kind)}</div>` +
      `<div>${escapeHtml(node.role)} · ${escapeHtml(node.family)}</div>`;
    const rp = e.target.renderedPosition();
    tip.style.left = `${Math.min(rp.x + 18, CY.width() - 260)}px`;
    tip.style.top = `${Math.max(46, rp.y - 18)}px`;
  });
  CY.on("mouseout", "node.event", (e) => {
    e.target.removeClass("hover");
    tip.hidden = true;
  });
}

function renderRunSummary() {
  const detail = document.getElementById("detail");
  const run = MODEL.run || {};
  const source = MODEL.source || {};
  const summary = run.summary || {};
  detail.innerHTML =
    `<section class="inspect-block run-summary">` +
    `<div class="kicker">trace summary</div>` +
    `<h2>${escapeHtml(run.id || "durable trace")}</h2>` +
    `<dl>` +
    `<dt>terminal</dt><dd>${escapeHtml(run.terminal_status || "unknown")}</dd>` +
    `<dt>events</dt><dd>${summary.events ?? 0}</dd>` +
    `<dt>lanes</dt><dd>${summary.lanes ?? 0}</dd>` +
    `<dt>causal links</dt><dd>${drawableEdges().length}</dd>` +
    `</dl>` +
    `<p class="summary-hint">Select an event or link in the trace to inspect details.</p>` +
    `</section>`;
}

function selectNode(id) {
  const node = nodeById(id);
  if (!node) return;
  openInspector({ fit: false });
  clearSelection();
  CY.getElementById(id).select();
  updatePlaybackForNode(id);
  const detail = document.getElementById("detail");
  const summaryRows = Object.entries(displaySummary(node))
    .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(formatValue(v))}</dd>`)
    .join("");
  const title = nodeTitle(node);
  const payloadRows = Object.entries(node.payload || {})
    .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(formatValue(v))}</dd>`)
    .join("");
  const body = Object.keys(node.body || {}).length
    ? `<h3>content body</h3><pre>${escapeHtml(JSON.stringify(node.body, null, 2))}</pre>`
    : "";
  detail.innerHTML =
    `<section class="inspect-block">` +
    `<div class="kicker">${escapeHtml(humanizeKind(node.kind))} · ${escapeHtml(node.role)}</div>` +
    `<h2>${escapeHtml(title)}</h2>` +
    `<dl>` +
    `<dt>sequence</dt><dd>${node.sequence ?? "unknown"}</dd>` +
    `<dt>lanes</dt><dd>${escapeHtml(displayLaneList(node.lane_ids || []))}</dd>` +
    summaryRows +
    `</dl>` +
    body +
    `<details><summary>technical details</summary><dl>` +
    `<dt>kind</dt><dd>${escapeHtml(node.kind)}</dd>` +
    `<dt>id</dt><dd>${escapeHtml(node.id)}</dd>` +
    `<dt>identity domain</dt><dd>${escapeHtml(node.identity_domain || "none")}</dd>` +
    `<dt>record digest</dt><dd>${escapeHtml(node.record_digest || "none")}</dd>` +
    payloadRows +
    `</dl>` +
    `</details>` +
    `</section>`;
}

function selectEdge(id) {
  const edge = edgeById(id);
  if (!edge) return;
  const source = nodeById(edge.source);
  const target = nodeById(edge.target);
  openInspector({ fit: false });
  clearSelection();
  CY.getElementById(id).select();
  const sourceSeq = source?.sequence ?? 0;
  const targetSeq = target?.sequence ?? 0;
  const storyKind = edgeStoryKind(edge);
  const sharedLanes = (source?.lane_ids || []).filter((laneId) => (target?.lane_ids || []).includes(laneId));
  const detail = document.getElementById("detail");
  detail.innerHTML =
    `<section class="inspect-block">` +
    `<div class="kicker">link · ${escapeHtml(storyKind)}</div>` +
    `<h2>${escapeHtml(edgeDisplayName(storyKind))}</h2>` +
    `<dl>` +
    `<dt>source</dt><dd>${escapeHtml(eventRef(source, edge.source, { technical: false }))}</dd>` +
    `<dt>target</dt><dd>${escapeHtml(eventRef(target, edge.target, { technical: false }))}</dd>` +
    `<dt>meaning</dt><dd>${escapeHtml(edgeMeaning(storyKind))}</dd>` +
    `<dt>sequence delta</dt><dd>${targetSeq - sourceSeq}</dd>` +
    `<dt>shared lanes</dt><dd>${escapeHtml(displayLaneList(sharedLanes))}</dd>` +
    `</dl>` +
    `<details><summary>technical details</summary><dl>` +
    `<dt>id</dt><dd>${escapeHtml(edge.id)}</dd>` +
    `<dt>kind</dt><dd>${escapeHtml(edge.kind)}</dd>` +
    `<dt>source kind</dt><dd>${escapeHtml(source?.kind || "unknown")}</dd>` +
    `<dt>target kind</dt><dd>${escapeHtml(target?.kind || "unknown")}</dd>` +
    `</dl></details>` +
    eventListHtml([source, target].filter(Boolean)) +
    `</section>`;
  bindEventList(detail);
}

function selectLane(id) {
  const lane = laneById(id);
  if (!lane) return;
  openInspector({ fit: false });
  clearSelection();
  CY.getElementById(`lane:${id}`).select();
  const laneNodeIds = new Set(lane.node_ids || []);
  for (const node of MODEL.nodes || []) {
    if ((node.lane_ids || []).includes(lane.id)) laneNodeIds.add(node.id);
  }
  const nodes = [...laneNodeIds].map(nodeById).filter(Boolean);
  const kinds = countBy(nodes, (node) => node.kind);
  const roles = countBy(nodes, (node) => node.role);
  const transition = nodes.find((node) => node.kind === "substrate.transition");
  const lifecycle = nodes.find((node) => node.kind === "run.lifecycle");
  const detail = document.getElementById("detail");
  detail.innerHTML =
    `<section class="inspect-block">` +
    `<div class="kicker">owner lane</div>` +
    `<h2>${escapeHtml(lane.label || lane.id)}</h2>` +
    `<dl>` +
    `<dt>events</dt><dd>${nodes.length}</dd>` +
    `<dt>first</dt><dd>${escapeHtml(eventRef(nodes[0], lane.node_ids?.[0], { technical: false }))}</dd>` +
    `<dt>last</dt><dd>${escapeHtml(eventRef(nodes[nodes.length - 1], lane.node_ids?.[lane.node_ids.length - 1], { technical: false }))}</dd>` +
    `<dt>terminal</dt><dd>${escapeHtml(lifecycle?.payload?.terminal_status || "none")}</dd>` +
    `<dt>world</dt><dd>${escapeHtml(worldSummary(transition))}</dd>` +
    `</dl>` +
    `<details><summary>technical details</summary><dl>` +
    `<dt>id</dt><dd>${escapeHtml(lane.id)}</dd>` +
    `<dt>roles</dt><dd>${escapeHtml(formatCounts(roles))}</dd>` +
    `<dt>kinds</dt><dd>${escapeHtml(formatCounts(kinds))}</dd>` +
    `</dl></details>` +
    eventListHtml(nodes) +
    `</section>`;
  bindEventList(detail);
}

function clearSelection() {
  CY.elements().unselect();
}

function eventListHtml(nodes) {
  if (!nodes.length) return "";
  const rows = nodes.map((node, i) =>
    `<button class="event-row" type="button" data-node-id="${escapeHtml(node.id)}">` +
    `<span>${i + 1}</span>` +
    `<strong>${escapeHtml(nodeTitle(node))}</strong>` +
    `<em>${escapeHtml(humanizeKind(node.kind))}</em>` +
    `</button>`
  ).join("");
  return `<div class="event-list">${rows}</div>`;
}

function bindEventList(root) {
  root.querySelectorAll(".event-row").forEach((button) => {
    button.addEventListener("click", () => selectNode(button.dataset.nodeId));
  });
}

function setupPlayback() {
  const nodes = MODEL.nodes || [];
  const range = document.getElementById("play-range");
  const count = document.getElementById("play-count");
  const play = document.getElementById("play-btn");
  if (!range || !count || !play || !nodes.length) return;
  range.max = String(Math.max(0, nodes.length - 1));
  range.value = String(nodes.length - 1);
  renderPlaybackTicks(nodes);
  setPlaybackIndex(nodes.length - 1, { select: false, fit: true });
  range.addEventListener("input", () => setPlaybackIndex(Number(range.value || 0), { select: true }));
  play.addEventListener("click", () => {
    const current = Number(range.value || 0);
    const next = current >= nodes.length - 1 ? 0 : current + 1;
    setPlaybackIndex(next, { select: true });
  });
}

function renderPlaybackTicks(nodes) {
  const ticks = document.getElementById("play-ticks");
  if (!ticks) return;
  ticks.replaceChildren();
  const positions = timelinePositions(nodes);
  for (const [index, node] of nodes.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "play-tick";
    button.dataset.index = String(index);
    button.style.left = `${positions[index]}%`;
    button.title = `${index + 1}. ${nodeTitle(node)}`;
    button.setAttribute("aria-label", button.title);
    button.addEventListener("click", () => setPlaybackIndex(index, { select: true }));
    ticks.appendChild(button);
  }
}

function setupInspectorToggle() {
  const main = document.getElementById("main");
  const close = document.getElementById("inspector-close");
  const open = document.getElementById("inspector-open");
  if (!main || !close || !open) return;
  open.hidden = !main.classList.contains("inspector-collapsed");
  close.setAttribute("aria-expanded", main.classList.contains("inspector-collapsed") ? "false" : "true");
  close.addEventListener("click", () => closeInspector());
  open.addEventListener("click", () => openInspector());
}

function closeInspector() {
  const main = document.getElementById("main");
  const close = document.getElementById("inspector-close");
  const open = document.getElementById("inspector-open");
  if (!main || !close || !open) return;
  main.classList.add("inspector-collapsed");
  open.hidden = false;
  close.setAttribute("aria-expanded", "false");
  setTimeout(() => {
    CY?.resize();
    fitStory(42);
    renderLaneLabels();
  }, 180);
}

function openInspector(options = {}) {
  const main = document.getElementById("main");
  const close = document.getElementById("inspector-close");
  const open = document.getElementById("inspector-open");
  if (!main || !close || !open || !main.classList.contains("inspector-collapsed")) return;
  main.classList.remove("inspector-collapsed");
  open.hidden = true;
  close.setAttribute("aria-expanded", "true");
  setTimeout(() => {
    CY?.resize();
    if (options.fit !== false) fitStory(42);
    renderLaneLabels();
  }, 180);
}

function setPlaybackIndex(index, options = {}) {
  const nodes = nodesInSequence();
  if (!nodes.length) return;
  PLAYBACK_INDEX = Math.max(0, Math.min(nodes.length - 1, index));
  const visibleIds = new Set(nodes.slice(0, PLAYBACK_INDEX + 1).map((node) => node.id));
  for (const node of nodes) {
    CY.getElementById(node.id).toggleClass("pending", !visibleIds.has(node.id));
  }
  for (const edge of drawableEdges()) {
    const visible = visibleIds.has(edge.source) && visibleIds.has(edge.target);
    const ele = CY.getElementById(edge.id);
    if (ele.length) ele.toggleClass("pending", !visible);
  }
  const range = document.getElementById("play-range");
  if (range) range.value = String(PLAYBACK_INDEX);
  updatePlaybackLabel(PLAYBACK_INDEX);
  updatePlaybackTicks(PLAYBACK_INDEX);
  if (options.select) selectNode(nodes[PLAYBACK_INDEX].id, { syncPlayback: false });
  else clearSelection();
  if (options.fit) fitStory(42);
  renderLaneLabels();
}

function updatePlaybackForNode(id) {
  const index = nodesInSequence().findIndex((node) => node.id === id);
  if (index < 0) return;
  if (index > PLAYBACK_INDEX) setPlaybackIndex(index, { select: false });
  else {
    const range = document.getElementById("play-range");
    if (range) range.value = String(PLAYBACK_INDEX);
    updatePlaybackLabel(PLAYBACK_INDEX);
    updatePlaybackTicks(PLAYBACK_INDEX);
  }
}

function updatePlaybackLabel(index) {
  const count = document.getElementById("play-count");
  const total = (MODEL.nodes || []).length;
  if (count) count.textContent = total ? `${index + 1}/${total}` : "";
}

function updatePlaybackTicks(index) {
  const ticks = document.getElementById("play-ticks");
  const positions = timelinePositions(nodesInSequence());
  if (ticks) ticks.style.setProperty("--play-progress", `${positions[index] ?? 0}%`);
  document.querySelectorAll(".play-tick").forEach((tick) => {
    const tickIndex = Number(tick.dataset.index || 0);
    tick.classList.toggle("seen", tickIndex <= index);
    tick.classList.toggle("active", tickIndex === index);
  });
}

function nodeById(id) {
  return (MODEL.nodes || []).find((n) => n.id === id);
}

function edgeById(id) {
  return (MODEL.edges || []).find((edge) => edge.id === id);
}

function laneById(id) {
  return (MODEL.lanes || []).find((lane) => lane.id === id);
}

function laneDisplayLabel(lane, index) {
  const raw = String(lane.label || lane.id || "");
  const fallback = `lane ${index + 1}`;
  if (!raw) return fallback;
  const [prefix, rest] = raw.split(":", 2);
  if (prefix === "scope" && rest) return `scope: ${rest.replaceAll("-", " ")}`;
  if (prefix === "exec") return `execution ${index + 1}`;
  if (prefix === "task") return `task ${index + 1}`;
  if (/^[a-z0-9]+:[0-9a-f]{16,}$/i.test(raw)) return fallback;
  return raw.length > 34 ? fallback : raw;
}

function causalRanks(nodes, laneByNode = null) {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const ranks = new Map(nodes.map((node) => [node.id, node.sequence ?? 0]));
  applyReplayRankAnchors(ranks, laneByNode || nodeLaneIndex());
  const orderedEdges = [...drawableEdges(), ...ownerLayoutEdges()]
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .sort((a, b) => (nodeById(a.source)?.sequence ?? 0) - (nodeById(b.source)?.sequence ?? 0));
  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    for (const edge of orderedEdges) {
      const nextRank = (ranks.get(edge.source) ?? 0) + 1;
      if (nextRank > (ranks.get(edge.target) ?? 0)) {
        ranks.set(edge.target, nextRank);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return ranks;
}

function applyReplayRankAnchors(ranks, laneByNode) {
  const laneNodeIds = new Map((MODEL.lanes || []).map((lane) => [lane.id, lane.node_ids || []]));
  const replayRelationIds = new Set(
    drawableEdges()
      .filter((edge) => edge.kind === "replay_control")
      .map((edge) => edge.target)
  );
  for (const edge of drawableEdges()) {
    if (!replayRelationIds.has(edge.source)) continue;
    const childLane = laneByNode.get(edge.target);
    if (!childLane || !laneStartsWith(childLane, edge.target)) continue;
    const desiredStart = (ranks.get(edge.source) ?? 0) + 1;
    const currentStart = ranks.get(edge.target) ?? desiredStart;
    const shift = currentStart - desiredStart;
    if (shift <= 0) continue;
    for (const nodeId of laneNodeIds.get(childLane) || []) {
      if (ranks.has(nodeId)) ranks.set(nodeId, Math.max(0, (ranks.get(nodeId) ?? 0) - shift));
    }
  }
}

function nodeLaneIndex() {
  const laneByNode = new Map();
  for (const lane of MODEL.lanes || []) {
    for (const nodeId of lane.node_ids || []) {
      if (!laneByNode.has(nodeId)) laneByNode.set(nodeId, lane.id);
    }
  }
  for (const node of MODEL.nodes || []) {
    const laneId = (node.lane_ids || [])[0];
    if (laneId && !laneByNode.has(node.id)) laneByNode.set(node.id, laneId);
  }
  return laneByNode;
}

function ownerLayoutEdges() {
  const edges = [];
  for (const lane of MODEL.lanes || []) {
    for (const [source, target] of zipPairs(lane.node_ids || [])) {
      edges.push({ source, target });
    }
  }
  return edges;
}

function zipPairs(items) {
  const pairs = [];
  for (let index = 0; index < items.length - 1; index += 1) {
    pairs.push([items[index], items[index + 1]]);
  }
  return pairs;
}

function fitStory(padding) {
  if (!CY) return;
  CY.fit(CY.nodes(".event").union(CY.edges()), padding);
  if (CY.zoom() > 1) {
    CY.zoom({
      level: 1,
      renderedPosition: { x: CY.width() / 2, y: CY.height() / 2 },
    });
  }
  if (CY.width() < 620 && CY.zoom() < 0.44) {
    CY.zoom({
      level: 0.44,
      renderedPosition: { x: CY.width() / 2, y: CY.height() / 2 },
    });
    const bb = CY.nodes(".event").renderedBoundingBox({ includeLabels: false });
    CY.panBy({ x: 42 - bb.x1, y: 0 });
  }
  renderLaneLabels();
}

function nodeShortLabel(node) {
  return compactWords(nodeTitle(node), 3);
}

function nodeTitle(node) {
  const label = String(node.label || "").trim();
  if (label) return label;
  return humanizeKind(node.kind);
}

function humanizeKind(kind) {
  return String(kind || "event")
    .replace(/[._-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function compactWords(text, maxWords) {
  const words = humanizeKind(text).split(" ").filter(Boolean);
  if (!words.length) return "event";
  return words.slice(0, maxWords).join("\n");
}

function edgeStoryKind(edge) {
  if (edge.kind === "replay_control") return "replay";
  if (edge.kind === "replay_basis") return "basis";
  const source = nodeById(edge.source);
  const target = nodeById(edge.target);
  if (!source || !target) return "direct";
  const targetParents = drawableEdges().filter((candidate) => candidate.target === edge.target);
  const sourceLanes = new Set(source.lane_ids || []);
  const targetLanes = new Set(target.lane_ids || []);
  const sharedLane = [...sourceLanes].some((laneId) => targetLanes.has(laneId));
  const sourceSeq = source.sequence ?? 0;
  const targetSeq = target.sequence ?? 0;
  const targetStartsLane = (MODEL.lanes || []).some((lane) => lane.node_ids?.[0] === target.id);
  if (targetParents.length > 1 && targetStartsLane && targetSeq - sourceSeq > 1) return "reference";
  if (!sharedLane && targetStartsLane) return "branch";
  if (!sharedLane && !targetStartsLane) return "join";
  return "direct";
}

function edgeDisplayName(storyKind) {
  return {
    branch: "branch starts",
    replay: "replay starts",
    basis: "replay basis",
    reference: "refers back",
    join: "joins result",
    direct: "then",
  }[storyKind] || storyKind;
}

function edgeMeaning(storyKind) {
  return {
    branch: "A new lane starts from this event.",
    replay: "The revert point creates a replay scope.",
    basis: "The replay uses this earlier checkpoint as its basis.",
    reference: "The target also depends on an older event or restored state.",
    join: "A different lane contributes back into this event.",
    direct: "The target follows from the source on the trace story.",
  }[storyKind] || "Explicit causal relationship.";
}

function nodesInSequence() {
  return [...(MODEL.nodes || [])].sort((a, b) => {
    const aSeq = a.sequence ?? Number.MAX_SAFE_INTEGER;
    const bSeq = b.sequence ?? Number.MAX_SAFE_INTEGER;
    if (aSeq !== bSeq) return aSeq - bSeq;
    return String(a.id).localeCompare(String(b.id));
  });
}

function timelinePositions(nodes) {
  if (nodes.length <= 1) return [0];
  const timestamps = nodes.map((node) => node.timestamp).filter((value) => typeof value === "number");
  if (timestamps.length >= 2) {
    const min = Math.min(...timestamps);
    const max = Math.max(...timestamps);
    if (max > min) {
      return nodes.map((node, index) => {
        if (typeof node.timestamp !== "number") return (index / (nodes.length - 1)) * 100;
        return ((node.timestamp - min) / (max - min)) * 100;
      });
    }
  }
  return nodes.map((_node, index) => (index / (nodes.length - 1)) * 100);
}

function drawableEdges() {
  return (MODEL.edges || []).filter((edge) => edge.kind !== "owner_path");
}

function displaySummary(node) {
  const summary = node.payload?.display_summary;
  if (!summary || typeof summary !== "object" || Array.isArray(summary)) return {};
  return summary;
}

function eventRef(node, fallback, options = {}) {
  if (!node) return fallback || "unknown";
  const ref = nodeTitle(node);
  return options.technical === false ? ref : `${ref} (${node.id})`;
}

function countBy(items, fn) {
  const counts = {};
  for (const item of items) {
    const key = fn(item) || "unknown";
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

function formatCounts(counts) {
  const entries = Object.entries(counts);
  if (!entries.length) return "none";
  return entries.map(([key, value]) => `${key}: ${value}`).join(", ");
}

function formatCount(value, label) {
  return `${value} ${label}${value === 1 ? "" : "s"}`;
}

function formatCausalCount(value) {
  return `${value} causal link${value === 1 ? "" : "s"}`;
}

function displayLaneList(laneIds) {
  if (!laneIds.length) return "none";
  return laneIds.map((laneId, index) => {
    const lane = laneById(laneId);
    return lane ? laneDisplayLabel(lane, index) : laneId;
  }).join(", ");
}

function worldSummary(node) {
  if (!node) return "none";
  const from = shortDigest(node.payload?.head_from || "");
  const to = shortDigest(node.payload?.head_to || "");
  if (!from && !to) return "transition recorded";
  return `${from || "none"} → ${to || "none"}`;
}

function cssClass(s) {
  return String(s || "unknown").replace(/[^a-zA-Z0-9_-]/g, "-");
}

function shortDigest(s) {
  if (!s) return "";
  return s.length > 20 ? `${s.slice(0, 14)}…${s.slice(-6)}` : s;
}

function formatValue(v) {
  if (v == null) return "null";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

document.addEventListener("DOMContentLoaded", boot);
