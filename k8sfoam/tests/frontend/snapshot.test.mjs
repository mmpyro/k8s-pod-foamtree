// Run with `make js_tests` (node --test). The frontend modules register on
// window.*, so a bare object stands in for it.
import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.window = {};
await import("../../src/frontend/snapshot.js");
const { parseReport, SCHEMA_VERSION } = window.k8sSnapshot;

const isBytes = name => name.endsWith("memory") || name === "hugepages-2Mi";
// What app.jsx's kbToMib does to payload memory before reportJson writes it.
const kbToMib = kb => (kb * 1000) / (1024 * 1024);

function report(extra = {}) {
  return {
    schemaVersion: SCHEMA_VERSION, k8sfoamsVersion: "1.16.0", generatedAt: "2026-11-07T10:00:00.000Z",
    context: "prod", pendingPods: 2, pricing: { source: "snapshot" },
    nodes: [{
      name: "n1", topology: { zone: "a" }, cpuCapacity: 4000, memCapacity: kbToMib(16384000),
      extended: { capacity: { "nvidia.com/gpu": 2, "hugepages-2Mi": kbToMib(2048000) }, used: {} },
      unschedulable: false, warnings: [], taints: [], conditions: {}, price: { hourly: 0.2 },
      pods: [{
        name: "web-1", namespace: "shop", owner: { kind: "Deployment", name: "web" }, qos: "Burstable", labels: {},
        cpu: 250, mem: kbToMib(524288), extended: { "nvidia.com/gpu": 1 }, findings: [],
        containers: [{ name: "init", init: true, cpu: 10, mem: kbToMib(1000), ext: {} },
          { name: "app", init: false, cpu: 250, mem: kbToMib(524288), ext: { "nvidia.com/gpu": 1 } }],
      }],
    }],
    ...extra,
  };
}

test("a current report converts back to the /resources payload exactly", () => {
  const snap = parseReport(JSON.stringify(report()), isBytes);
  assert.equal(snap.context, "prod");
  assert.equal(snap.version, "1.16.0");
  assert.equal(snap.payload.pendingPods, 2);
  const n = snap.payload.nodes[0];
  assert.equal(n.cpu, 4000);
  assert.equal(n.memory, 16384000);
  assert.deepEqual(n.extended, { "nvidia.com/gpu": 2, "hugepages-2Mi": 2048000 });
  assert.deepEqual(n.price, { hourly: 0.2 });
  const p = n.pods[0];
  assert.equal(p.memory, 524288);
  assert.equal(p.hasInitContainers, true);
  assert.deepEqual(p.extended, { "nvidia.com/gpu": 1 });
  assert.deepEqual(p.containers.map(c => c.memory), [1000, 524288]);
});

test("MiB to kB round trip is exact for integer kB", () => {
  const { mibToKb } = window.k8sSnapshot;
  for (const kb of [0, 1, 999, 1024, 524288, 16384000, 987654321]) assert.equal(mibToKb(kbToMib(kb)), kb);
});

test("a report from before schema versioning reads as version 1", () => {
  const r = report();
  delete r.schemaVersion;
  delete r.k8sfoamsVersion;
  const snap = parseReport(JSON.stringify(r), isBytes);
  assert.equal(snap.version, null);
  assert.equal(snap.payload.nodes.length, 1);
});

test("a newer schema is refused with a message naming the versions", () => {
  const r = report({ schemaVersion: SCHEMA_VERSION + 1, k8sfoamsVersion: "9.0.0" });
  assert.throws(() => parseReport(JSON.stringify(r), isBytes), /k8sfoams 9\.0\.0 .*please upgrade/);
});

test("files that are not reports are refused", () => {
  assert.throws(() => parseReport("{oops", isBytes), /not a JSON file/);
  assert.throws(() => parseReport('{"rows": []}', isBytes), /not a k8sfoams JSON report/);
  assert.throws(() => parseReport(JSON.stringify(report({ schemaVersion: "x" })), isBytes), /unknown report schemaVersion/);
});
