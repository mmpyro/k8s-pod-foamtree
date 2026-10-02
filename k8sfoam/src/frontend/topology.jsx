// Node placement vocabulary — zone, region, node pool, instance type and
// capacity type. The backend reads the provider labels and hands each node a
// `topology` of plain values; this file decides how nodes are grouped by them,
// so the 2D map, the cubes, the sidebar panel and the exporter agree.

const { nodeCap, nodeUsed } = window.k8sResources;

// `field` is the node.topology key, `token` the query prefix that selects one
// group (query.jsx owns the grammar), `none` names the group of nodes missing
// the label.
const GROUP_MODES = [
  { id: "none", label: "None" },
  { id: "zone", label: "Zone", field: "zone", token: "zone", none: "no zone label" },
  { id: "region", label: "Region", field: "region", token: "region", none: "no region label" },
  { id: "nodePool", label: "Pool", field: "nodePool", token: "pool", none: "no node pool" },
  { id: "instanceType", label: "Type", field: "instanceType", token: "type", none: "no instance type" },
  { id: "capacityType", label: "Capacity", field: "capacityType", token: "capacity", none: "unknown capacity" },
];

function groupMode(id) {
  return GROUP_MODES.find(m => m.id === id) || GROUP_MODES[0];
}

// The node's value for a grouping, or null when the label is missing.
function groupKey(node, mode) {
  const m = groupMode(mode);
  if (!m.field) return null;
  return (node.topology && node.topology[m.field]) || null;
}

// Nodes bucketed by one topology fact, each bucket with its capacity totals.
// `value` is the bucket's capacity on `metric` (cpu, mem or an extended
// resource), which is what the 2D map sizes it by, and `util` its usage of it. Largest first, the unlabelled bucket always last. Every item keeps the
// node's index in `nodes`, so node hues stay put when grouping is switched on.
function groupNodes(nodes, mode, metric) {
  const m = groupMode(mode);
  const buckets = new Map();
  nodes.forEach((node, idx) => {
    const key = m.field ? groupKey(node, mode) : null;
    if (!buckets.has(key)) {
      buckets.set(key, {
        key, label: key === null ? (m.none || "all nodes") : key, labelled: key !== null,
        // Label values are [A-Za-z0-9._-] only, so they never need quoting.
        token: key !== null && m.token ? `${m.token}:${key}` : null,
        items: [], cpuCap: 0, cpuUsed: 0, memCap: 0, memUsed: 0, value: 0, used: 0,
      });
    }
    const b = buckets.get(key);
    b.items.push({ node, idx });
    b.cpuCap += node.cpuCapacity;
    b.cpuUsed += node.cpuUsed;
    b.memCap += node.memCapacity;
    b.memUsed += node.memUsed;
    b.value += nodeCap(node, metric);
    b.used += nodeUsed(node, metric);
  });
  const groups = [...buckets.values()];
  for (const g of groups) {
    g.util = g.used / (g.value || 1);
    g.cpuUtil = g.cpuUsed / (g.cpuCap || 1);
    g.memUtil = g.memUsed / (g.memCap || 1);
  }
  return groups.sort((a, b) => (a.labelled !== b.labelled ? (a.labelled ? -1 : 1) : b.value - a.value));
}

window.k8sTopology = { GROUP_MODES, groupMode, groupKey, groupNodes };
