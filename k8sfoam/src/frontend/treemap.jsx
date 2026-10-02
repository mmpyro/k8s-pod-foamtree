// Squarified treemap. Pure function: given a rect + items[{value,...}],
// returns items with x/y/w/h placed.

const { workloadKey } = window.k8sWorkload;
const { worstSeverity, NodeWarnBadge } = window.k8sNodeStatus;
const { PodAuditBadge } = window.k8sPodAudit;
const { qosHue } = window.k8sQos;
const { metricValue, nodeCap } = window.k8sResources;
const { groupNodes } = window.k8sTopology;

function squarify(items, x, y, w, h) {
  const sorted = items.filter(i => i.value > 0).sort((a, b) => b.value - a.value);
  if (!sorted.length || w <= 0 || h <= 0) return [];
  const total = sorted.reduce((s, i) => s + i.value, 0);
  const scale = (w * h) / total;
  const scaled = sorted.map(i => ({ ...i, _a: i.value * scale }));

  const out = [];
  let cx = x, cy = y, cw = w, ch = h;
  let rest = scaled;

  while (rest.length) {
    const short = Math.min(cw, ch);
    if (short <= 0.5) break;

    let row = [];
    let worst = Infinity;

    const evalRow = (r) => {
      const sum = r.reduce((s, i) => s + i._a, 0);
      let max = -Infinity, min = Infinity;
      for (const i of r) { if (i._a > max) max = i._a; if (i._a < min) min = i._a; }
      const s2 = short * short;
      const sum2 = sum * sum;
      return Math.max((s2 * max) / sum2, sum2 / (s2 * min));
    };

    for (let i = 0; i < rest.length; i++) {
      const trial = [...row, rest[i]];
      const r = evalRow(trial);
      if (r > worst) break;
      row = trial; worst = r;
    }
    if (!row.length) row = [rest[0]];

    const sum = row.reduce((s, i) => s + i._a, 0);
    const along = sum / short;

    let off = 0;
    if (cw >= ch) {
      for (const r of row) {
        const h2 = r._a / along;
        out.push({ ...r, x: cx, y: cy + off, w: along, h: h2 });
        off += h2;
      }
      cx += along; cw -= along;
    } else {
      for (const r of row) {
        const w2 = r._a / along;
        out.push({ ...r, x: cx + off, y: cy, w: w2, h: along });
        off += w2;
      }
      cy += along; ch -= along;
    }
    rest = rest.slice(row.length);
  }
  return out;
}

// Each node slot is drawn this much smaller than its squarified rect, which is
// what leaves the gutter between cards.
const SLOT_GAP = 6;
// Group box chrome: the gutter between boxes, the inset around the cards in
// one, and the band its label sits in.
const ZONE_GAP = 10;
const ZONE_PAD = 8;
const ZONE_HEADER = 24;

// The top-level 2D layout: a slot per node and, when grouped, a box per zone /
// pool around its nodes. Nodes are squarified inside their group's rect, so a
// group's area is its share of cluster capacity and a node's area within it is
// its share of the group. Shared with the SVG exporter.
function gridLayout(nodes, metric, groupBy, w, h) {
  // Nodes without an extended resource are left out of its map; cpu and mem
  // keep every node.
  const shown = node => metric === "cpu" || metric === "mem" || nodeCap(node, metric) > 0;
  if (!groupBy || groupBy === "none") {
    const items = nodes
      .map((node, idx) => ({ node, idx, value: nodeCap(node, metric) }))
      .filter(it => shown(it.node));
    return { zones: [], slots: squarify(items, 0, 0, w, h) };
  }
  const zones = [], slots = [];
  // Grouped over every node so `idx` stays the node's index in `nodes`, then
  // trimmed to the shown ones; a group left empty has no capacity and drops out.
  const groups = groupNodes(nodes, groupBy, metric)
    .map(g => ({ ...g, items: g.items.filter(it => shown(it.node)) }))
    .filter(g => g.items.length > 0)
    .map(group => ({ group, value: group.value }));
  for (const z of squarify(groups, 0, 0, w, h)) {
    const zw = z.w - ZONE_GAP, zh = z.h - ZONE_GAP;
    if (zw <= 0 || zh <= 0) continue;
    zones.push({ group: z.group, x: z.x, y: z.y, w: zw, h: zh });
    // The slots shrink by SLOT_GAP on their own, so the inner area gets it back
    // to keep the right and bottom inset equal to the left and top.
    const items = z.group.items.map(({ node, idx }) => ({ node, idx, value: nodeCap(node, metric) }));
    slots.push(...squarify(items, z.x + ZONE_PAD, z.y + ZONE_HEADER,
      zw - ZONE_PAD * 2 + SLOT_GAP, zh - ZONE_HEADER - ZONE_PAD + SLOT_GAP));
  }
  return { zones, slots };
}

// Card chrome sizes per density. Shared with the SVG exporter so an exported
// map is laid out exactly like the one on screen.
function cardMetrics(density) {
  return density === "compact" ? { padding: 4, headerH: 26 } : { padding: 6, headerH: 32 };
}

// Query dimming. A node ruled out by a node: glob dims as a whole card, so
// its pods stay at full strength inside it rather than fading twice.
function queryState(node, match) {
  const queryActive = !!(match && match.active);
  return {
    queryActive,
    nodeDim: queryActive && match.dimNodes.has(node.name),
    podMatched: pod => queryActive && match.pods.has(pod),
  };
}

// Everything about a node card that does not depend on the pixel box:
// pod weights, free capacity, utilisation and the worst warning.
function cardStats(node, metric, match) {
  const { podMatched } = queryState(node, match);
  // Compute pod values + empty space. Size each pod by its effective request
  // (max(sum regular, max init)) — summing containers would double-count
  // init containers, which run sequentially before the regular ones.
  const podValue = p => metricValue(p, metric);
  const cap = nodeCap(node, metric);
  const used = node.pods.reduce((s, p) => s + podValue(p), 0);
  // A pod requesting nothing on this metric (every BestEffort pod, by
  // definition) weighs 0 and squarify's `value > 0` guard drops it — so a
  // matched one would raise the header count while the card drew nothing.
  // Floor those to a thin sliver so the 2D and 3D views agree on what a query
  // highlights. Unmatched pods keep their real value, layout untouched.
  const sliver = cap * 0.002;
  const podItems = node.pods.map(p => ({
    pod: p,
    value: podValue(p) > 0 ? podValue(p) : (podMatched(p) ? sliver : 0),
  }));
  const empty = Math.max(0, cap - used);
  const items = [...podItems, { pod: null, value: empty, empty: true }];

  // A node without the resource never reaches a card (the grid filters it),
  // but a refresh can race the filter — keep the colour maths finite.
  const utilization = cap > 0 ? used / cap : 0;
  const utilColor = utilization > 0.85 ? "#ef4444" :
                    utilization > 0.6 ? "#f59e0b" :
                    utilization > 0.3 ? "#10b981" : "#3b82f6";

  // Free capacity on a node that refuses pods is not really free, so the idle
  // foam gets hatched in the worst warning's colour instead of the neutral one.
  const warnSev = worstSeverity(node.warnings);
  return { items, cap, used, empty, utilization, utilColor, warnSev };
}

// Pod and idle rects inside a card of the given outer size.
function cardLayout(items, w, h, density) {
  const { padding, headerH } = cardMetrics(density);
  const innerW = Math.max(0, w - padding * 2);
  const innerH = Math.max(0, h - headerH - padding);
  return innerW > 0 && innerH > 0 ? squarify(items, padding, headerH, innerW, innerH) : [];
}

// Container rects inside a pod rect.
const POD_INSET = 2;
function podHeaderH(rect) {
  return rect.h > 28 ? 12 : 0;
}
function podLayout(pod, metric, rect) {
  const headerH = podHeaderH(rect);
  const containers = pod.containers.map(c => ({
    container: c,
    value: metricValue(c, metric),
  }));
  return squarify(
    containers,
    POD_INSET,
    headerH + POD_INSET,
    Math.max(0, rect.w - POD_INSET * 2),
    Math.max(0, rect.h - headerH - POD_INSET * 2)
  );
}

// Card style depending on tweak — hsl for max renderer compat. The gradient
// comes back as its two stops so the SVG exporter can build it too.
function cardColors(hue, nodeStyle) {
  const gradient = nodeStyle === "gradient"
    ? [`hsl(${hue} 60% 22%)`, `hsl(${hue} 50% 12%)`]
    : null;
  const fill = nodeStyle === "solid"
    ? `hsl(${hue} 55% 24%)`
    : `hsl(${hue} 20% 12%)`;
  const border = nodeStyle === "outlined"
    ? `hsl(${hue} 70% 55%)`
    : `hsla(${hue}, 40%, 45%, 0.4)`;
  return {
    gradient, fill, border,
    borderWidth: nodeStyle === "outlined" ? 1.5 : 1,
    css: gradient ? `linear-gradient(135deg, ${gradient[0]} 0%, ${gradient[1]} 100%)` : fill,
    dot: `hsl(${hue} 80% 65%)`,
  };
}

function podColors(hue, nodeStyle) {
  const fill = nodeStyle === "solid"
    ? `hsla(${hue}, 65%, 38%, 0.65)`
    : nodeStyle === "gradient"
    ? `hsla(${hue}, 55%, 32%, 0.9)`
    : `hsla(${hue}, 45%, 25%, 0.7)`;
  return { fill, border: `hsla(${hue}, 60%, 55%, 0.5)` };
}

// Init containers render desaturated — they explain the effective request
// but don't run alongside the regular containers.
function containerColor(hue, init) {
  return init ? `hsla(${hue}, 10%, 55%, 0.85)` : `hsla(${hue}, 70%, 68%, 0.92)`;
}

// Render a node card: header + nested treemap of pods (each pod = treemap of containers).
function NodeCard({
  node, match, metric, hue, colorBy, style: nodeStyle, showLabels, density, onClick,
  highlight, highlightActive, onPodSelect, onPodHover,
}) {
  const ref = React.useRef(null);
  const [box, setBox] = React.useState({ w: 0, h: 0 });

  React.useLayoutEffect(() => {
    if (!ref.current) return;
    const el = ref.current;
    const ro = new ResizeObserver(() => {
      setBox({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    setBox({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const { queryActive, nodeDim, podMatched } = queryState(node, match);
  const { headerH } = cardMetrics(density);
  const { items, cap, empty, utilization, utilColor, warnSev } = cardStats(node, metric, match);

  // Memoised because a highlight change re-renders every card: `items` is a
  // pure function of node + metric, so those two plus the box are the whole
  // input to the layout, and hovering a pod must not redo this math per card.
  const laid = React.useMemo(
    () => cardLayout(items, box.w, box.h, density),
    [node, metric, box.w, box.h, density]
  );

  const colors = cardColors(hue, nodeStyle);

  return (
    <div
      ref={ref}
      onClick={onClick}
      className={`node-card ${nodeDim ? "is-dim" : ""}`}
      style={{
        background: colors.css,
        borderColor: colors.border,
        borderWidth: colors.borderWidth,
      }}
    >
      {/* Header */}
      <div className="node-header" style={{ height: headerH }}>
        <div className="node-header-dot" style={{ background: colors.dot }}></div>
        <span className="node-name">{node.name}</span>
        <span className="node-meta">
          <NodeWarnBadge warnings={node.warnings} />
          <span className="node-util" style={{ color: utilColor }}>{Math.round(utilization * 100)}%</span>
        </span>
      </div>

      {/* Pods */}
      {laid.map((it, i) => {
        if (it.empty) {
          return (
            <div key={`empty-${i}`} className={`pod-empty${warnSev ? ` warn-${warnSev}` : ""}`}
              style={{
                left: it.x, top: it.y, width: it.w - 2, height: it.h - 2,
              }}>
              {it.w > 60 && it.h > 30 && <span>idle · {Math.round((empty/cap)*100)}%</span>}
            </div>
          );
        }
        return (
          <PodBox key={`pod-${i}`} pod={it.pod} rect={it}
                  hue={colorBy === "qos" ? qosHue(it.pod.qos) : hue}
                  metric={metric} showLabels={showLabels} nodeStyle={nodeStyle}
                  matched={podMatched(it.pod)}
                  dim={queryActive && !nodeDim && !podMatched(it.pod)}
                  highlight={highlight} highlightActive={highlightActive}
                  onPodSelect={onPodSelect} onPodHover={onPodHover} />
        );
      })}
    </div>
  );
}

function PodBox({
  pod, rect, hue, metric, showLabels, nodeStyle, matched, dim,
  highlight, highlightActive, onPodSelect, onPodHover,
}) {
  // Workload identity is cheap to derive and only ever needed here, so it is
  // recomputed rather than cached on the pod — the highlight itself is a plain
  // class toggle, so a re-render costs nothing beyond this string compare.
  const wl = workloadKey(pod.name);
  const cls = ["pod-box"];
  if (dim) cls.push("is-dim");
  if (matched) cls.push("is-match");
  if (highlight) {
    cls.push(wl === highlight ? "wl-peer" : "wl-dim");
    if (!highlightActive) cls.push("wl-preview");
  }

  const headerH = podHeaderH(rect);
  // Same reasoning as the card layout: the container rects depend only on the
  // pod, the metric and the rect handed down, so a highlight-only re-render
  // reuses them instead of re-squarifying every pod in the cluster.
  const laid = React.useMemo(() => podLayout(pod, metric, rect), [pod, metric, rect]);

  const colors = podColors(hue, nodeStyle);

  return (
    <div className={cls.join(" ")}
      // stopPropagation keeps the node card's own click (the focus overlay)
      // from firing on top of the workload selection.
      onClick={e => { e.stopPropagation(); onPodSelect(wl); }}
      onMouseEnter={() => onPodHover(wl)}
      onMouseLeave={() => onPodHover(null)}
      style={{
        left: rect.x, top: rect.y, width: rect.w - 2, height: rect.h - 2,
        background: colors.fill,
        borderColor: colors.border,
      }}>
      {headerH > 0 && showLabels && rect.w > 50 && (
        <div className="pod-label">{pod.shortName}</div>
      )}
      {/* Too small for a glyph? The sidebar panel and audit: query still reach it. */}
      {rect.w > 24 && rect.h > 16 && <PodAuditBadge findings={pod.findings} />}
      {laid.map((it, i) => (
        <div key={i} className="container-box"
          style={{
            left: it.x, top: it.y, width: Math.max(0, it.w - 1), height: Math.max(0, it.h - 1),
            background: containerColor(hue, it.container.init),
          }}>
          {showLabels && it.w > 40 && it.h > 18 && (
            <span>{it.container.name}</span>
          )}
        </div>
      ))}
    </div>
  );
}

window.k8sTreemap = {
  squarify, NodeCard, PodBox, gridLayout, SLOT_GAP, ZONE_HEADER,
  cardMetrics, queryState, cardStats, cardLayout, podHeaderH, podLayout,
  cardColors, podColors, containerColor,
};
