// Pod audit vocabulary. The backend decides *which* best-practice rules a pod
// breaks and sends slugs; this file is the single place that decides how each
// slug looks, so the pod badge, the sidebar panel and the query agree.

// Ordered the way the backend emits findings.
const POD_FINDINGS = {
  "missing-requests": { label: "missing requests", sev: "warn", why: "no CPU or memory request — the scheduler packs it blind" },
  "missing-limits":   { label: "no memory limit",  sev: "info", why: "no memory limit — a leak can take the node down" },
  "limit-overcommit": { label: "limit far above request", sev: "info", why: "memory limit over 4× the request — the node is packed by request but OOM-kills by usage" },
  "monolith":         { label: "monolith",         sev: "warn", why: "reserves over 80% of its node — nowhere else to reschedule it" },
  "ratio-asymmetry":  { label: "ratio asymmetry",  sev: "info", why: "CPU:memory ratio far from the node's — strands the other resource" },
  "naked-pod":        { label: "naked pod",        sev: "warn", why: "no controller — a drain or node failure deletes it for good" },
  // Workload rules: every replica of the workload carries the same finding.
  "single-replica":   { label: "single replica",   sev: "info", why: "the only replica — every rollout, drain or eviction is downtime" },
  "spof-node":        { label: "replicas on one node", sev: "warn", why: "every replica on one node — losing that node takes the workload down" },
  "spof-zone":        { label: "replicas in one zone", sev: "warn", why: "every replica in one zone — a zone outage takes the workload down" },
  "spof-spot":        { label: "spot-only replicas",   sev: "warn", why: "every replica on spot capacity — one reclaim wave can take them all" },
};

const FINDING_ORDER = Object.keys(POD_FINDINGS);

function findingInfo(slug) {
  // An unknown slug from a newer backend still renders, just without a nicer
  // label — better a plain chip than a finding that silently vanishes.
  return POD_FINDINGS[slug] || { label: slug, sev: "warn", why: slug };
}

function findingsTitle(findings) {
  return (findings || []).map(f => findingInfo(f).why).join("\n");
}

// Worst wins, using the same ranking as the node badges.
function worstFindingSeverity(findings) {
  const rank = { warn: 2, info: 1 };
  let worst = null;
  for (const f of findings || []) {
    const sev = findingInfo(f).sev;
    if (!worst || rank[sev] > rank[worst]) worst = sev;
  }
  return worst;
}

// Corner glyph on a pod box; the reasons live in the native tooltip.
function PodAuditBadge({ findings, size = 9 }) {
  const sev = worstFindingSeverity(findings);
  if (!sev) return null;
  const { WarnIcon } = window.k8sNodeStatus;
  return (
    <span className={`pod-audit sev-${sev}`} title={findingsTitle(findings)}>
      <WarnIcon size={size} />
    </span>
  );
}

window.k8sPodAudit = { POD_FINDINGS, FINDING_ORDER, findingInfo, findingsTitle, worstFindingSeverity, PodAuditBadge };
