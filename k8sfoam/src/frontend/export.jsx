// Static export — the 2D map rebuilt as vector SVG (and rasterised to PNG),
// plus JSON/CSV reports of the merged cluster data. The 3D view captures its
// own canvas (see scene3d.jsx); everything here is plain functions.

const {
  cardMetrics, queryState, cardStats, cardLayout, podHeaderH, podLayout,
  cardColors, podColors, containerColor, gridLayout, ZONE_HEADER,
} = window.k8sTreemap;
const { groupMode } = window.k8sTopology;
const { workloadKey } = window.k8sWorkload;
const { qosHue } = window.k8sQos;
const { detectResources, isBytes } = window.k8sResources;

const FONT_MONO = `'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace`;
const FONT_SANS = `'Space Grotesk', Manrope, -apple-system, 'Helvetica Neue', Arial, sans-serif`;
// Idle-foam hatch per warning severity, the same colours as .pod-empty.warn-*.
const IDLE = {
  none: { a: "rgba(255,255,255,.04)", b: "rgba(255,255,255,.08)", stroke: "rgba(255,255,255,.12)", text: "rgba(255,255,255,.45)", band: 4 },
  danger: { a: "rgba(239,68,68,.10)", b: "rgba(239,68,68,.28)", stroke: "rgba(239,68,68,.55)", text: "rgba(255,255,255,.7)", band: 5 },
  warn: { a: "rgba(245,158,11,.10)", b: "rgba(245,158,11,.26)", stroke: "rgba(245,158,11,.5)", text: "rgba(255,255,255,.7)", band: 5 },
  info: { a: "rgba(96,165,250,.08)", b: "rgba(96,165,250,.2)", stroke: "rgba(96,165,250,.45)", text: "rgba(255,255,255,.65)", band: 5 },
};
const SEV_COLOR = { danger: "#ef4444", warn: "#f59e0b", info: "#60a5fa" };
const HEADER_BAND = 64;
const MARGIN = 14;

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const n1 = v => Math.round(v * 10) / 10;

// SVG text has no ellipsis, so truncate on an estimated glyph width. The
// labels are monospace, where ~0.6em per character is close enough.
function fit(text, maxPx, fontSize) {
  const max = Math.floor(maxPx / (fontSize * 0.6));
  if (max <= 0) return "";
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1))}…`;
}

function rect(x, y, w, h, attrs) {
  return `<rect x="${n1(x)}" y="${n1(y)}" width="${n1(Math.max(0, w))}" height="${n1(Math.max(0, h))}" ${attrs}/>`;
}

function text(x, y, s, attrs) {
  return `<text x="${n1(x)}" y="${n1(y)}" ${attrs}>${esc(s)}</text>`;
}

function podSvg(pod, r, hue, nodeStyle, metric, showLabels, state) {
  const w = r.w - 2, h = r.h - 2;
  const colors = podColors(hue, nodeStyle);
  const out = [];
  let stroke = colors.border, strokeW = 1;
  if (state.match || state.peer) { stroke = state.accent; strokeW = 1.5; }
  out.push(`<g transform="translate(${n1(r.x)} ${n1(r.y)})"${state.opacity < 1 ? ` opacity="${state.opacity}"` : ""}>`);
  out.push(rect(0.5, 0.5, w - 1, h - 1, `rx="4" fill="${colors.fill}" stroke="${stroke}" stroke-width="${strokeW}"`));
  const headerH = podHeaderH(r);
  if (headerH > 0 && showLabels && r.w > 50) {
    out.push(text(4, 9, fit(pod.shortName, w - 8, 8), `font-size="8" fill="rgba(255,255,255,.85)" font-family="${FONT_MONO}"`));
  }
  for (const it of podLayout(pod, metric, r)) {
    const cw = Math.max(0, it.w - 1), ch = Math.max(0, it.h - 1);
    out.push(rect(it.x, it.y, cw, ch, `rx="2" fill="${containerColor(hue, it.container.init)}"`));
    if (showLabels && it.w > 40 && it.h > 18) {
      out.push(text(it.x + cw / 2, it.y + ch / 2 + 3, fit(it.container.name, cw - 6, 9),
        `font-size="9" font-weight="500" fill="rgba(0,0,0,.85)" text-anchor="middle" font-family="${FONT_MONO}"`));
    }
  }
  out.push("</g>");
  return out.join("");
}

function cardSvg(node, slot, idx, opts) {
  const { match, metric, hue, colorBy, nodeStyle, density, showLabels, pinned, accent } = opts;
  const w = slot.w - 6, h = slot.h - 6;
  if (w <= 0 || h <= 0) return "";
  const { queryActive, nodeDim, podMatched } = queryState(node, match);
  const { headerH } = cardMetrics(density);
  const { items, cap, empty, utilization, utilColor, warnSev } = cardStats(node, metric, match);
  const colors = cardColors(hue, nodeStyle);
  const out = [];
  const clip = `card-clip-${idx}`;

  out.push(`<g transform="translate(${n1(slot.x)} ${n1(slot.y)})"${nodeDim ? ` opacity=".32"` : ""}>`);
  out.push(`<clipPath id="${clip}">${rect(0, 0, w, h, `rx="10"`)}</clipPath>`);
  let fill = colors.fill;
  if (colors.gradient) {
    out.push(`<linearGradient id="card-grad-${idx}" x1="0" y1="0" x2="1" y2="1">` +
      `<stop offset="0" stop-color="${colors.gradient[0]}"/><stop offset="1" stop-color="${colors.gradient[1]}"/></linearGradient>`);
    fill = `url(#card-grad-${idx})`;
  }
  out.push(rect(0, 0, w, h, `rx="10" fill="${fill}"`));
  out.push(`<g clip-path="url(#${clip})">`);

  // Header: dot, name, warning count, utilisation.
  const cy = headerH / 2;
  out.push(`<circle cx="14" cy="${n1(cy)}" r="3.5" fill="${colors.dot}"/>`);
  const utilText = `${Math.round(utilization * 100)}%`;
  const utilW = utilText.length * 11 * 0.6;
  let tail = w - 10 - utilW;
  out.push(text(w - 10, cy + 4, utilText, `font-size="11" font-weight="600" fill="${utilColor}" text-anchor="end" font-family="${FONT_MONO}"`));
  if (warnSev) {
    const mx = tail - 16, s = 9;
    out.push(`<path d="M${n1(mx + s / 2)} ${n1(cy - s / 2)} L${n1(mx + s)} ${n1(cy + s / 2)} L${n1(mx)} ${n1(cy + s / 2)} Z" fill="${SEV_COLOR[warnSev]}"/>`);
    tail = mx - 4;
  }
  out.push(text(24, cy + 4, fit(node.name, tail - 30, 11), `font-size="11" font-weight="500" fill="rgba(255,255,255,.95)" font-family="${FONT_MONO}"`));

  for (const it of cardLayout(items, w, h, density)) {
    if (it.empty) {
      const style = IDLE[warnSev || "none"];
      const pat = `idle-${idx}`;
      out.push(`<pattern id="${pat}" width="${style.band * 2}" height="${style.band * 2}" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">` +
        `<rect width="${style.band}" height="${style.band * 2}" fill="${style.a}"/><rect x="${style.band}" width="${style.band}" height="${style.band * 2}" fill="${style.b}"/></pattern>`);
      out.push(rect(it.x + 0.5, it.y + 0.5, it.w - 3, it.h - 3,
        `rx="6" fill="url(#${pat})" stroke="${style.stroke}"${warnSev ? "" : ` stroke-dasharray="3 2"`}`));
      if (it.w > 60 && it.h > 30) {
        out.push(text(it.x + (it.w - 2) / 2, it.y + (it.h - 2) / 2 + 3, `idle · ${Math.round((empty / cap) * 100)}%`,
          `font-size="10" fill="${style.text}" text-anchor="middle" font-family="${FONT_MONO}"`));
      }
      continue;
    }
    const pod = it.pod;
    const matched = podMatched(pod);
    const dim = queryActive && !nodeDim && !matched;
    // Only a pinned workload is exported; a hover preview is transient.
    // Query scope wins over workload state, as in styles.css.
    const wl = pinned ? workloadKey(pod.name) : null;
    const peer = !!pinned && !dim && wl === pinned;
    const opacity = dim ? 0.16 : pinned && wl !== pinned ? 0.22 : 1;
    const podHue = colorBy === "qos" ? qosHue(pod.qos) : hue;
    out.push(podSvg(pod, it, podHue, nodeStyle, metric, showLabels, { match: matched, peer, opacity, accent }));
  }
  out.push("</g>");
  out.push(rect(0.5, 0.5, w - 1, h - 1, `rx="10" fill="none" stroke="${colors.border}" stroke-width="${colors.borderWidth}"`));
  out.push("</g>");
  return out.join("");
}

// A zone / pool box with its label band, as .zone-box draws it on screen.
function zoneSvg(z, metric, groupWord, match) {
  const g = z.group;
  const util = g.util;
  const dim = !!(match && match.active) && g.items.every(it => match.dimNodes.has(it.node.name));
  const utilColor = util > 0.85 ? "#ef4444" : util > 0.6 ? "#f59e0b" : util > 0.3 ? "#10b981" : "#60a5fa";
  const cy = ZONE_HEADER / 2 + 4;
  const meta = `${g.items.length} node${g.items.length === 1 ? "" : "s"}`;
  const utilText = `${Math.round(util * 100)}%`;
  const kindW = groupWord.length * 9.5 * 0.6 + 8;
  const tailW = (meta.length + utilText.length + 2) * 11 * 0.6 + 10;
  return `<g transform="translate(${n1(z.x)} ${n1(z.y)})"${dim ? ` opacity=".4"` : ""}>` +
    rect(0.5, 0.5, z.w - 1, z.h - 1,
      `rx="12" fill="${g.labelled ? "rgba(255,255,255,.015)" : "none"}" stroke="#2b3147" stroke-dasharray="${g.labelled ? "4 3" : "1 3"}"`) +
    text(10, cy, groupWord.toUpperCase(), `font-size="9.5" letter-spacing=".08em" fill="#6b7388" font-family="${FONT_MONO}"`) +
    text(10 + kindW, cy, fit(g.label, z.w - 20 - kindW - tailW, 11),
      `font-size="11" font-weight="500" fill="${g.labelled ? "#e7eaf3" : "#9aa3b8"}" font-family="${FONT_MONO}"`) +
    text(z.w - 10, cy, utilText, `font-size="11" font-weight="600" fill="${utilColor}" text-anchor="end" font-family="${FONT_MONO}"`) +
    text(z.w - 10 - (utilText.length * 11 * 0.6) - 8, cy, meta, `font-size="11" fill="#9aa3b8" text-anchor="end" font-family="${FONT_MONO}"`) +
    `</g>`;
}

// The 2D map as a standalone SVG document. `width`/`height` is the map area;
// a title band with the cluster summary sits above it so the image explains
// itself when pasted into a deck.
function treemapSvg({
  nodes, match, metric, hueOf, colorBy, groupBy, nodeStyle, density, showLabels, pinned,
  width, height, meta,
}) {
  const bg = cssVar("--bg", "#07080c");
  const accent = cssVar("--accent", "#7c5cff");
  const W = Math.round(width + MARGIN * 2), H = Math.round(height + HEADER_BAND + MARGIN);

  // Same layout as the on-screen grid, which also leaves out nodes without an
  // extended resource.
  const { zones, slots } = gridLayout(nodes, metric, groupBy, width, height);
  const groupWord = groupMode(groupBy).label.toLowerCase();
  const boxes = zones.map(z => zoneSvg(z, metric, groupWord, match));
  const cards = slots.map((it, i) => cardSvg(it.node, it, i, {
    match, metric, hue: hueOf(it.idx), colorBy, nodeStyle, density, showLabels, pinned, accent,
  }));

  const head = [
    text(MARGIN, 30, meta.title, `font-size="18" font-weight="600" fill="#e7eaf3" font-family="${FONT_SANS}"`),
    text(MARGIN, 50, meta.subtitle, `font-size="11" fill="#9aa3b8" font-family="${FONT_MONO}"`),
    text(W - MARGIN, 30, meta.stats, `font-size="12" fill="#e7eaf3" text-anchor="end" font-family="${FONT_MONO}"`),
    text(W - MARGIN, 50, meta.stamp, `font-size="11" fill="#6b7388" text-anchor="end" font-family="${FONT_MONO}"`),
  ];

  return `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">` +
    `<title>${esc(meta.title)}</title>` +
    `<rect width="100%" height="100%" fill="${bg}"/>` +
    head.join("") +
    `<g transform="translate(${MARGIN} ${HEADER_BAND})">${boxes.join("")}${cards.join("")}</g>` +
    `</svg>`;
}

function canvasBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(b => (b ? resolve(b) : reject(new Error("Canvas export failed"))), "image/png");
  });
}

// Rasterise an SVG string at `scale`× its own size.
function svgToPng(svg, scale) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      const g = c.getContext("2d");
      g.scale(scale, scale);
      g.drawImage(img, 0, 0);
      canvasBlob(c).then(resolve, reject);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Could not rasterise the SVG")); };
    img.src = url;
  });
}

const CSV_COLUMNS = [
  "context", "node", "zone", "region", "node_pool", "instance_type", "capacity_type", "namespace", "pod", "qos", "cpu_millicores", "memory_mib",
  "containers", "init_containers", "findings", "node_warnings", "matched",
];

// Column per extended resource: devices as a count under their own name,
// byte-sized ones in MiB like memory_mib.
function extColumn(name) {
  return isBytes(name) ? `${name.replace(/-/g, "_")}_mib` : name;
}

// The fixed columns, with one column per extended resource on the cluster
// slotted in after memory.
function csvColumns(nodes) {
  const at = CSV_COLUMNS.indexOf("memory_mib") + 1;
  return [...CSV_COLUMNS.slice(0, at), ...detectResources(nodes).map(extColumn), ...CSV_COLUMNS.slice(at)];
}

// One row per pod. `matched` is blank when no query is active.
function podRows(nodes, match, context) {
  const active = !!(match && match.active);
  const extended = detectResources(nodes);
  const rows = [];
  for (const n of nodes) {
    for (const p of n.pods) {
      rows.push({
        context,
        node: n.name,
        zone: topo(n).zone,
        region: topo(n).region,
        node_pool: topo(n).nodePool,
        instance_type: topo(n).instanceType,
        capacity_type: topo(n).capacityType,
        namespace: p.namespace,
        pod: p.name,
        qos: p.qos,
        cpu_millicores: Math.round(p.cpu),
        memory_mib: Math.round(p.mem * 100) / 100,
        ...Object.fromEntries(extended.map(name => {
          const v = p.ext[name] || 0;
          return [extColumn(name), isBytes(name) ? Math.round(v * 100) / 100 : v];
        })),
        containers: p.containers.filter(c => !c.init).map(c => c.name).join(";"),
        init_containers: p.containers.filter(c => c.init).map(c => c.name).join(";"),
        findings: p.findings.join(";"),
        node_warnings: (n.warnings || []).join(";"),
        matched: active ? match.pods.has(p) : "",
      });
    }
  }
  return rows;
}

const topo = n => n.topology || {};

function csvCell(v) {
  const s = String(v ?? "");
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function toCsv(rows, columns = CSV_COLUMNS) {
  const lines = [columns.map(csvCell).join(",")];
  for (const r of rows) lines.push(columns.map(c => csvCell(r[c])).join(","));
  return lines.join("\r\n") + "\r\n";
}

// Memory is MiB and CPU millicores throughout, whatever the UI unit is set to.
// Extended resources are MiB when byte-sized, a plain count otherwise.
function reportJson({ nodes, totals, context, metric, query, match }) {
  const active = !!(match && match.active);
  return JSON.stringify({
    generatedAt: new Date().toISOString(),
    context,
    metric,
    query: query || "",
    units: {
      cpu: "millicores", memory: "MiB",
      ...Object.fromEntries(detectResources(nodes).map(name => [name, isBytes(name) ? "MiB" : "count"])),
    },
    totals,
    nodes: nodes.map(n => ({
      name: n.name,
      topology: n.topology,
      cpuCapacity: n.cpuCapacity, cpuUsed: n.cpuUsed,
      memCapacity: n.memCapacity, memUsed: n.memUsed,
      extended: { capacity: n.extCap, used: n.extUsed },
      unschedulable: n.unschedulable, warnings: n.warnings, taints: n.taints, conditions: n.conditions,
      pods: n.pods.map(p => ({
        name: p.name, namespace: p.namespace, qos: p.qos, labels: p.labels,
        cpu: p.cpu, mem: p.mem, extended: p.ext, findings: p.findings,
        ...(active ? { matched: match.pods.has(p) } : {}),
        containers: p.containers,
      })),
    })),
  }, null, 2);
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function fileName(context, kind, ext, when = new Date()) {
  const pad = v => String(v).padStart(2, "0");
  const stamp = `${when.getFullYear()}${pad(when.getMonth() + 1)}${pad(when.getDate())}-${pad(when.getHours())}${pad(when.getMinutes())}`;
  const ctx = (context || "cluster").split("/").pop().replace(/[^A-Za-z0-9._-]+/g, "_");
  return `k8sfoams-${ctx}-${kind}-${stamp}.${ext}`;
}

window.k8sExport = { treemapSvg, svgToPng, canvasBlob, podRows, csvColumns, toCsv, reportJson, download, fileName };
