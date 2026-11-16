// Snapshot comparison: what changed between two node lists (the node model
// app.jsx's toNodes builds). Plain JS with no window.* dependencies, so
// `node --test` can load it (k8sfoam/tests/frontend/).
//
// Pods are matched by namespace/name first. A rollout renames every pod, so
// what is left is paired up within the same workload (keyOf, workload.jsx's
// workloadKey in the app): a replaced replica is not an add plus a remove.
// A matched pair is "resized" when its requests changed and "moved" when it
// sits on another node; leftovers are "added" or "removed".

const DIFF_KINDS = ["added", "removed", "resized", "moved"];
// Pod hue per change in Color by → Diff. Unchanged pods keep the app's own
// violet (the monochrome scheme), so the changes are what stands out.
const DIFF_HUES = { added: 145, removed: 0, resized: 40, moved: 205, unchanged: 265 };
// A pod both resized and moved shows as resized: requests are what the map measures.
const diffHue = pod => DIFF_HUES[(pod.diff && pod.diff[0]) || "unchanged"];

// Memory is MiB converted from kB, so equal requests are equal floats; the
// tolerance only guards against a report written by another conversion.
const near = (a, b) => Math.abs((a || 0) - (b || 0)) < 1e-6;

function sameRequests(a, b) {
  if (!near(a.cpu, b.cpu) || !near(a.mem, b.mem)) return false;
  const names = new Set([...Object.keys(a.ext || {}), ...Object.keys(b.ext || {})]);
  for (const name of names) if (!near((a.ext || {})[name], (b.ext || {})[name])) return false;
  return true;
}

function pairKinds(b, a) {
  const kinds = [];
  if (!sameRequests(b.pod, a.pod)) kinds.push("resized");
  if (b.node !== a.node) kinds.push("moved");
  return kinds;
}

const entries = nodes => nodes.flatMap(n => n.pods.map(pod => ({ pod, node: n.name })));
const podId = e => `${e.pod.namespace}/${e.pod.name}`;
const byName = (x, y) => (x.pod.name < y.pod.name ? -1 : x.pod.name > y.pod.name ? 1 : 0);

function groupBy(list, keyFn) {
  const out = new Map();
  for (const it of list) {
    const k = keyFn(it);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(it);
  }
  return out;
}

// Request deltas (after - before) per label, nonzero ones only, biggest CPU change first.
function deltas(beforeList, afterList, labelOf) {
  const sums = new Map();
  const add = (e, sign) => {
    const k = labelOf(e);
    const d = sums.get(k) || { name: k, cpu: 0, mem: 0 };
    d.cpu += sign * (e.pod.cpu || 0);
    d.mem += sign * (e.pod.mem || 0);
    sums.set(k, d);
  };
  beforeList.forEach(e => add(e, -1));
  afterList.forEach(e => add(e, 1));
  return [...sums.values()]
    .filter(d => !near(d.cpu, 0) || !near(d.mem, 0))
    .sort((x, y) => Math.abs(y.cpu) - Math.abs(x.cpu) || Math.abs(y.mem) - Math.abs(x.mem));
}

function diffNodes(before, after, keyOf) {
  const kinds = new Map(); // after pod -> kinds
  const removed = [];

  // 1. Same namespace/name.
  const beforeById = new Map(entries(before).map(e => [podId(e), e]));
  const afterLeft = [];
  for (const a of entries(after)) {
    const b = beforeById.get(podId(a));
    if (b) {
      kinds.set(a.pod, pairKinds(b, a));
      beforeById.delete(podId(a));
    } else {
      afterLeft.push(a);
    }
  }

  // 2. Same workload: pairs on the same node first, then in name order.
  const wl = e => keyOf(e.pod) || `pod:${podId(e)}`;
  const beforeGroups = groupBy([...beforeById.values()], wl);
  for (const [key, aList] of groupBy(afterLeft, wl)) {
    const bList = (beforeGroups.get(key) || []).sort(byName);
    aList.sort(byName);
    const pending = [];
    for (const a of aList) {
      const i = bList.findIndex(b => b.node === a.node);
      if (i === -1) pending.push(a);
      else kinds.set(a.pod, pairKinds(bList.splice(i, 1)[0], a));
    }
    for (const a of pending) kinds.set(a.pod, bList.length ? pairKinds(bList.shift(), a) : ["added"]);
    beforeGroups.set(key, bList);
  }
  for (const bList of beforeGroups.values()) removed.push(...bList);

  // 3. Annotate. Pods are copied, so the caller's node lists stay untouched.
  const afterNames = new Set(after.map(n => n.name));
  const beforeNames = new Set(before.map(n => n.name));
  const annotated = after.map(n => ({ ...n, pods: n.pods.map(pod => ({ ...pod, diff: kinds.get(pod) })) }));
  const removedPods = removed.filter(e => afterNames.has(e.node));
  const goneByNode = groupBy(removed.filter(e => !afterNames.has(e.node)), e => e.node);
  const removedNodes = before.filter(n => !afterNames.has(n.name)).map(n => ({
    ...n,
    pods: (goneByNode.get(n.name) || []).map(e => ({ ...e.pod, diff: ["removed"] })),
  }));

  const counts = Object.fromEntries(DIFF_KINDS.map(k => [k, 0]));
  counts.removed = removed.length;
  for (const ks of kinds.values()) ks.forEach(k => { counts[k] += 1; });

  const beforeAll = entries(before);
  const afterAll = entries(after);
  const poolOf = names => e => {
    const n = names.get(e.node);
    return (n && n.topology && n.topology.nodePool) || "(no pool)";
  };
  const beforeNodes = new Map(before.map(n => [n.name, n]));
  const afterNodes = new Map(after.map(n => [n.name, n]));
  const total = deltas(beforeAll, afterAll, () => "total")[0] || { name: "total", cpu: 0, mem: 0 };

  return {
    after: annotated,
    removedNodes,
    removedPods: removedPods.map(e => ({ ...e.pod, node: e.node, diff: ["removed"] })),
    summary: {
      counts,
      changed: [...kinds.values()].filter(ks => ks.length > 0).length + removed.length,
      addedNodes: after.filter(n => !beforeNames.has(n.name)).map(n => n.name),
      removedNodes: removedNodes.map(n => n.name),
      total,
      byNode: deltas(beforeAll, afterAll, e => e.node),
      byPool: deltas(beforeAll.map(e => ({ ...e, pool: poolOf(beforeNodes)(e) })),
        afterAll.map(e => ({ ...e, pool: poolOf(afterNodes)(e) })), e => e.pool),
      byNamespace: deltas(beforeAll, afterAll, e => e.pod.namespace),
    },
  };
}

window.k8sSnapDiff = { DIFF_KINDS, DIFF_HUES, diffHue, diffNodes };
