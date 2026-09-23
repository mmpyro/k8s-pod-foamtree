// Node health vocabulary. The backend decides *what* is wrong with a node and
// sends slugs; this file is the single place that decides how each slug looks,
// so the 2D card, the 3D plate and the focus overlay can never disagree.

// Ordered worst-first, matching the order the backend emits warnings in.
const NODE_WARNINGS = {
  "cordoned":        { label: "cordoned",      sev: "danger", pill: "scheduling-disabled" },
  "not-ready":       { label: "not ready",     sev: "danger", pill: "scheduling-disabled" },
  "memory-pressure": { label: "mem pressure",  sev: "warn",   pill: "pressure" },
  "disk-pressure":   { label: "disk pressure", sev: "warn",   pill: "pressure" },
  "pid-pressure":    { label: "pid pressure",  sev: "warn",   pill: "pressure" },
  "tainted":         { label: "tainted",       sev: "info",   pill: "tainted" },
};

// Canonical worst-first order, so a legend built from a Set of slugs still
// reads in the same order as a single node's warning list.
const WARNING_ORDER = Object.keys(NODE_WARNINGS);

const SEV_RANK = { danger: 3, warn: 2, info: 1 };

function warnInfo(slug) {
  // An unknown slug from a newer backend still renders, just without a nicer
  // label — better a plain chip than a node that silently looks healthy.
  return NODE_WARNINGS[slug] || { label: slug, sev: "warn", pill: "pressure" };
}

// Worst wins: a cordoned node under memory pressure is a cordon problem first.
function worstSeverity(warnings) {
  let worst = null;
  for (const w of warnings || []) {
    const sev = warnInfo(w).sev;
    if (!worst || SEV_RANK[sev] > SEV_RANK[worst]) worst = sev;
  }
  return worst;
}

// Feeds the `status-${...}` pill class the focus overlay already renders.
function statusOf(warnings) {
  if (!warnings || !warnings.length) return "ready";
  return warnInfo(warnings[0]).pill;
}

// Native title= text — the node header is 26-32px tall, so the badge is a glyph
// and the reasons live in the tooltip.
function warnTitle(warnings) {
  return (warnings || []).map(w => warnInfo(w).label).join(" · ");
}

// Warning triangle, matching the 16-viewBox inline-SVG convention used by the
// view and metric icons.
function WarnIcon({ size = 11 }) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none">
      <path d="M8 2.2 L14.4 13.4 H1.6 Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M8 6.4 V9.4 M8 11.2 V11.4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

// The badge shared by the 2D header and the billboarded 3D plate label.
function NodeWarnBadge({ warnings, size }) {
  const sev = worstSeverity(warnings);
  if (!sev) return null;
  return (
    <span className={`node-warn sev-${sev}`} title={warnTitle(warnings)}>
      <WarnIcon size={size} />
      {warnings.length > 1 && <span className="node-warn-count">{warnings.length}</span>}
    </span>
  );
}

window.k8sNodeStatus = { NODE_WARNINGS, WARNING_ORDER, warnInfo, worstSeverity, statusOf, warnTitle, NodeWarnBadge };
