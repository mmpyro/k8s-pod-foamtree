// Snapshot loader: reads a JSON report written by export.jsx's reportJson back
// into the GET /resources payload shape, so app.jsx's toNodes builds the very
// same node model it builds for live data. Plain JS, no React and no other
// window.* globals, so `node --test` can load it (k8sfoam/tests/frontend/).

// Bump when a report field changes meaning; adding a field needs no bump.
const SCHEMA_VERSION = 1;

// Reports hold memory in MiB; the payload holds decimal kB. Exact inverse of
// app.jsx's kbToMib for the integer kB the backend sends.
const mibToKb = mib => ((mib || 0) * 1024 * 1024) / 1000;

function extToKb(extended, isBytes) {
  const out = {};
  for (const [name, v] of Object.entries(extended || {})) out[name] = isBytes(name) ? mibToKb(v) : v;
  return out;
}

function toPayloadPod(p, isBytes) {
  const containers = (p.containers || []).map(c => ({
    name: c.name, init: !!c.init, cpu: c.cpu || 0, memory: mibToKb(c.mem), extended: extToKb(c.ext, isBytes),
  }));
  return {
    name: p.name, namespace: p.namespace, owner: p.owner || null, qos: p.qos, labels: p.labels,
    findings: p.findings,
    // Reports before schema 1 lack hasInit; their containers still say which are init.
    hasInitContainers: p.hasInit !== undefined ? !!p.hasInit : containers.some(c => c.init),
    cpu: p.cpu || 0, memory: mibToKb(p.mem), extended: extToKb(p.extended, isBytes),
    containers,
  };
}

function toPayloadNode(n, isBytes) {
  const ext = n.extended || {};
  return {
    name: n.name, topology: n.topology,
    cpu: n.cpuCapacity || 0, memory: mibToKb(n.memCapacity), extended: extToKb(ext.capacity, isBytes),
    unschedulable: n.unschedulable, warnings: n.warnings, taints: n.taints, conditions: n.conditions,
    price: n.price || null,
    pods: (n.pods || []).map(p => toPayloadPod(p, isBytes)),
  };
}

// `isBytes(name)` says which extended resources are byte-sized (resources.jsx).
// Throws an Error whose message is meant for the user.
function parseReport(text, isBytes) {
  let report;
  try {
    report = JSON.parse(text);
  } catch (err) {
    throw new Error(`not a JSON file (${err.message})`);
  }
  if (!report || typeof report !== "object" || !Array.isArray(report.nodes)) {
    throw new Error("not a k8sfoams JSON report: it has no node list");
  }
  // Reports from before schema versioning carry none and read as version 1.
  const schema = report.schemaVersion === undefined ? 1 : report.schemaVersion;
  if (!Number.isInteger(schema) || schema < 1) throw new Error(`unknown report schemaVersion ${JSON.stringify(schema)}`);
  if (schema > SCHEMA_VERSION) {
    const by = report.k8sfoamsVersion ? `k8sfoams ${report.k8sfoamsVersion}` : "a newer k8sfoams";
    throw new Error(`this report was made by ${by} (schema ${schema}); this version reads up to schema ${SCHEMA_VERSION}, please upgrade`);
  }
  return {
    context: report.context || "",
    generatedAt: report.generatedAt || null,
    version: report.k8sfoamsVersion || null,
    payload: {
      nodes: report.nodes.map(n => toPayloadNode(n, isBytes)),
      pendingPods: report.pendingPods || 0,
      pricing: report.pricing || null,
    },
  };
}

window.k8sSnapshot = { SCHEMA_VERSION, parseReport, mibToKb };
