// Squarified treemap. Pure function: given a rect + items[{value,...}],
// returns items with x/y/w/h placed.

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
function NodeCard({ node, metric, hue, style: nodeStyle, showLabels, density, onClick }) {
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

  const padding = density === "compact" ? 4 : 6;
  const headerH = density === "compact" ? 26 : 32;

  // Compute pod values + empty space
  const podItems = node.pods.map(p => ({
    pod: p,
    value: p.containers.reduce((s, c) => s + (metric === "cpu" ? c.cpu : c.mem), 0),
  }));
  const used = podItems.reduce((s, p) => s + p.value, 0);
  const cap = metric === "cpu" ? node.cpuCapacity : node.memCapacity;
  const empty = Math.max(0, cap - used);
  const items = [...podItems, { pod: null, value: empty, empty: true }];

  const innerW = Math.max(0, box.w - padding * 2);
  const innerH = Math.max(0, box.h - headerH - padding);
  const laid = innerW > 0 && innerH > 0
    ? squarify(items, padding, headerH, innerW, innerH)
    : [];

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
      className="node-card"
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
          <span className="node-util" style={{ color: utilColor }}>{Math.round(utilization * 100)}%</span>
        </span>
      </div>

      {/* Pods */}
      {laid.map((it, i) => {
        if (it.empty) {
          return (
            <div key={`empty-${i}`} className="pod-empty"
              style={{
                left: it.x, top: it.y, width: it.w - 2, height: it.h - 2,
              }}>
              {it.w > 60 && it.h > 30 && <span>idle · {Math.round((empty/cap)*100)}%</span>}
            </div>
          );
        }
        return (
          <PodBox key={`pod-${i}`} pod={it.pod} rect={it} hue={hue}
                  metric={metric} showLabels={showLabels} nodeStyle={nodeStyle} />
        );
      })}
    </div>
  );
}

function PodBox({ pod, rect, hue, metric, showLabels, nodeStyle }) {
  const containers = pod.containers.map(c => ({
    container: c,
    value: metric === "cpu" ? c.cpu : c.mem,
  }));
  const inset = 2;
  const headerH = rect.h > 28 ? 12 : 0;
  const laid = squarify(
    containers,
    inset,
    headerH + inset,
    Math.max(0, rect.w - inset * 2),
    Math.max(0, rect.h - headerH - inset * 2)
  );

  const podBg = nodeStyle === "solid"
    ? `hsla(${hue}, 65%, 38%, 0.65)`
    : nodeStyle === "gradient"
    ? `hsla(${hue}, 55%, 32%, 0.9)`
    : `hsla(${hue}, 45%, 25%, 0.7)`;

  return (
    <div className="pod-box"
      style={{
        left: rect.x, top: rect.y, width: rect.w - 2, height: rect.h - 2,
        background: podBg,
        borderColor: `hsla(${hue}, 60%, 55%, 0.5)`,
      }}>
      {headerH > 0 && showLabels && rect.w > 50 && (
        <div className="pod-label">{pod.shortName}</div>
      )}
      {laid.map((it, i) => (
        <div key={i} className="container-box"
          style={{
            left: it.x, top: it.y, width: Math.max(0, it.w - 1), height: Math.max(0, it.h - 1),
            background: `hsla(${hue}, 70%, 68%, 0.92)`,
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
