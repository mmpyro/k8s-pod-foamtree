// Squarified treemap. Pure function: given a rect + items[{value,...}],
// returns items with x/y/w/h placed.

const { workloadKey } = window.k8sWorkload;
const { worstSeverity, NodeWarnBadge } = window.k8sNodeStatus;
const { PodAuditBadge } = window.k8sPodAudit;

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

// Render a node card: header + nested treemap of pods (each pod = treemap of containers).
function NodeCard({
  node, match, metric, hue, style: nodeStyle, showLabels, density, onClick,
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

  // Query dimming. A node ruled out by a node: glob dims as a whole card, so
  // its pods stay at full strength inside it rather than fading twice.
  const queryActive = !!(match && match.active);
  const nodeDim = queryActive && match.dimNodes.has(node.name);
  const podMatched = pod => queryActive && match.pods.has(pod);

  const padding = density === "compact" ? 4 : 6;
  const headerH = density === "compact" ? 26 : 32;

  // Compute pod values + empty space. Size each pod by its effective request
  // (max(sum regular, max init)) — summing containers would double-count
  // init containers, which run sequentially before the regular ones.
  const podValue = p => (metric === "cpu" ? p.cpu : p.mem);
  const cap = metric === "cpu" ? node.cpuCapacity : node.memCapacity;
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

  const innerW = Math.max(0, box.w - padding * 2);
  const innerH = Math.max(0, box.h - headerH - padding);
  // Memoised because a highlight change re-renders every card: `items` is a
  // pure function of node + metric, so those two plus the box are the whole
  // input to the layout, and hovering a pod must not redo this math per card.
  const laid = React.useMemo(
    () => (innerW > 0 && innerH > 0 ? squarify(items, padding, headerH, innerW, innerH) : []),
    [node, metric, innerW, innerH, padding, headerH]
  );

  // Free capacity on a node that refuses pods is not really free, so the idle
  // foam gets hatched in the worst warning's colour instead of the neutral one.
  const warnSev = worstSeverity(node.warnings);

  const utilization = used / cap;
  const utilColor = utilization > 0.85 ? "#ef4444" :
                    utilization > 0.6 ? "#f59e0b" :
                    utilization > 0.3 ? "#10b981" : "#3b82f6";

  // Card style depending on tweak — hsl for max renderer compat.
  const cardBg = nodeStyle === "solid"
    ? `hsl(${hue} 55% 24%)`
    : nodeStyle === "gradient"
    ? `linear-gradient(135deg, hsl(${hue} 60% 22%) 0%, hsl(${hue} 50% 12%) 100%)`
    : `hsl(${hue} 20% 12%)`;

  const borderColor = nodeStyle === "outlined"
    ? `hsl(${hue} 70% 55%)`
    : `hsla(${hue}, 40%, 45%, 0.4)`;

  return (
    <div
      ref={ref}
      onClick={onClick}
      className={`node-card ${nodeDim ? "is-dim" : ""}`}
      style={{
        background: cardBg,
        borderColor,
        borderWidth: nodeStyle === "outlined" ? 1.5 : 1,
      }}
    >
      {/* Header */}
      <div className="node-header" style={{ height: headerH }}>
        <div className="node-header-dot" style={{ background: `hsl(${hue} 80% 65%)` }}></div>
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
          <PodBox key={`pod-${i}`} pod={it.pod} rect={it} hue={hue}
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

  const inset = 2;
  const headerH = rect.h > 28 ? 12 : 0;
  // Same reasoning as the card layout: the container rects depend only on the
  // pod, the metric and the rect handed down, so a highlight-only re-render
  // reuses them instead of re-squarifying every pod in the cluster.
  const laid = React.useMemo(() => {
    const containers = pod.containers.map(c => ({
      container: c,
      value: metric === "cpu" ? c.cpu : c.mem,
    }));
    return squarify(
      containers,
      inset,
      headerH + inset,
      Math.max(0, rect.w - inset * 2),
      Math.max(0, rect.h - headerH - inset * 2)
    );
  }, [pod, metric, rect, headerH]);

  const podBg = nodeStyle === "solid"
    ? `hsla(${hue}, 65%, 38%, 0.65)`
    : nodeStyle === "gradient"
    ? `hsla(${hue}, 55%, 32%, 0.9)`
    : `hsla(${hue}, 45%, 25%, 0.7)`;

  return (
    <div className={cls.join(" ")}
      // stopPropagation keeps the node card's own click (the focus overlay)
      // from firing on top of the workload selection.
      onClick={e => { e.stopPropagation(); onPodSelect(wl); }}
      onMouseEnter={() => onPodHover(wl)}
      onMouseLeave={() => onPodHover(null)}
      style={{
        left: rect.x, top: rect.y, width: rect.w - 2, height: rect.h - 2,
        background: podBg,
        borderColor: `hsla(${hue}, 60%, 55%, 0.5)`,
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
            // Init containers render desaturated — they explain the effective
            // request but don't run alongside the regular containers.
            background: it.container.init
              ? `hsla(${hue}, 10%, 55%, 0.85)`
              : `hsla(${hue}, 70%, 68%, 0.92)`,
          }}>
          {showLabels && it.w > 40 && it.h > 18 && (
            <span>{it.container.name}</span>
          )}
        </div>
      ))}
    </div>
  );
}

window.k8sTreemap = { squarify, NodeCard, PodBox };
