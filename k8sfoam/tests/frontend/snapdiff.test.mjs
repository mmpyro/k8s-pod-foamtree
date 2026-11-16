// Run with `make js_tests` (node --test).
import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.window = {};
await import("../../src/frontend/snapdiff.js");
const { diffNodes } = window.k8sSnapDiff;

const keyOf = p => (p.owner ? `${p.namespace}/${p.owner.kind}/${p.owner.name}` : null);
const pod = (name, extra = {}) => ({
  name, namespace: "shop", owner: { kind: "Deployment", name: name.split("-")[0] }, cpu: 100, mem: 128, ext: {}, ...extra,
});
const node = (name, pods, pool = "general") => ({ name, topology: { nodePool: pool }, pods });
const kindsOf = d => Object.fromEntries(d.after.flatMap(n => n.pods.map(p => [p.name, p.diff])));
// structuredClone: the "after" side must be separate objects, as a second file or a refresh gives.
const cluster = () => [
  node("n1", [pod("web-aaaaa"), pod("web-bbbbb"), pod("db-0", { cpu: 500, mem: 1024 })]),
  node("n2", [pod("api-ccccc"), pod("naked", { owner: null })], "gpu"),
];

test("a snapshot compared with itself shows no changes", () => {
  const d = diffNodes(cluster(), structuredClone(cluster()), keyOf);
  assert.deepEqual(d.summary.counts, { added: 0, removed: 0, resized: 0, moved: 0 });
  assert.equal(d.summary.changed, 0);
  assert.deepEqual(d.summary.byNode, []);
  assert.deepEqual(d.summary.byPool, []);
  assert.deepEqual(d.summary.byNamespace, []);
  assert.deepEqual(d.removedNodes, []);
  assert.deepEqual(d.removedPods, []);
  assert.ok(d.after.every(n => n.pods.every(p => Array.isArray(p.diff) && p.diff.length === 0)));
});

test("added, removed, resized and moved pods", () => {
  const after = structuredClone(cluster());
  after[0].pods = after[0].pods.filter(p => p.name !== "db-0");      // db-0 moves to n2...
  after[1].pods.push(pod("db-0", { cpu: 500, mem: 2048 }));          // ...with more memory
  after[1].pods = after[1].pods.filter(p => p.name !== "naked");      // naked pod gone
  after[1].pods.push(pod("cache-ddddd"));                            // a new workload
  after[0].pods[0].cpu = 200;                                        // web-aaaaa resized in place
  const d = diffNodes(cluster(), after, keyOf);
  const k = kindsOf(d);
  assert.deepEqual(k["web-aaaaa"], ["resized"]);
  assert.deepEqual(k["web-bbbbb"], []);
  assert.deepEqual(k["db-0"], ["resized", "moved"]);
  assert.deepEqual(k["cache-ddddd"], ["added"]);
  assert.deepEqual(d.removedPods.map(p => [p.name, p.node]), [["naked", "n2"]]);
  assert.deepEqual(d.summary.counts, { added: 1, removed: 1, resized: 2, moved: 1 });
  assert.equal(d.summary.changed, 4);
});

test("a rollout renames pods without counting them as added and removed", () => {
  const after = structuredClone(cluster());
  after[0].pods[0].name = "web-xxxxx";
  after[0].pods[1].name = "web-zzzzz";
  const d = diffNodes(cluster(), after, keyOf);
  assert.deepEqual(d.summary.counts, { added: 0, removed: 0, resized: 0, moved: 0 });
});

test("a rollout that changes requests reads as resized; a scale-up adds the extra replica", () => {
  const after = structuredClone(cluster());
  after[0].pods[0] = pod("web-xxxxx", { cpu: 300 });
  after[0].pods[1] = pod("web-zzzzz", { cpu: 300 });
  after[1].pods.push(pod("web-yyyyy", { cpu: 300 }));
  const d = diffNodes(cluster(), after, keyOf);
  const k = kindsOf(d);
  // The two replicas on n1 pair with the old ones on n1; the third one is new.
  assert.deepEqual([k["web-xxxxx"], k["web-zzzzz"], k["web-yyyyy"]], [["resized"], ["resized"], ["added"]]);
});

test("a removed node becomes a ghost carrying its removed pods", () => {
  const after = structuredClone(cluster()).slice(0, 1);
  const d = diffNodes(cluster(), after, keyOf);
  assert.deepEqual(d.summary.removedNodes, ["n2"]);
  assert.deepEqual(d.removedNodes[0].pods.map(p => [p.name, p.diff]), [["api-ccccc", ["removed"]], ["naked", ["removed"]]]);
  assert.deepEqual(d.removedPods, []); // only pods of surviving nodes are listed apart
  assert.equal(d.summary.counts.removed, 2);
});

test("deltas per node, pool and namespace add up", () => {
  const after = structuredClone(cluster());
  after[1].pods.push(pod("job-eeeee", { namespace: "batch", cpu: 1000, mem: 512 }));
  after.push(node("n3", [pod("api-fffff")], "gpu"));
  const d = diffNodes(cluster(), after, keyOf);
  assert.deepEqual(d.summary.addedNodes, ["n3"]);
  assert.deepEqual(d.summary.total, { name: "total", cpu: 1100, mem: 640 });
  assert.deepEqual(d.summary.byNode, [{ name: "n2", cpu: 1000, mem: 512 }, { name: "n3", cpu: 100, mem: 128 }]);
  assert.deepEqual(d.summary.byPool, [{ name: "gpu", cpu: 1100, mem: 640 }]);
  assert.deepEqual(d.summary.byNamespace, [{ name: "batch", cpu: 1000, mem: 512 }, { name: "shop", cpu: 100, mem: 128 }]);
});

test("the input node lists are not modified", () => {
  const before = cluster();
  const after = structuredClone(cluster());
  const copy = structuredClone(after);
  diffNodes(before, after, keyOf);
  assert.deepEqual(after, copy);
  assert.deepEqual(before, cluster());
});
