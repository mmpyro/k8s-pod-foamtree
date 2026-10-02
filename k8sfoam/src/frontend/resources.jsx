// Resource vocabulary. cpu and mem are fixed fields on every pod, container and
// node; everything else (nvidia.com/gpu, ephemeral-storage, hugepages-2Mi) is
// discovered from the node `extended` maps the backend sends. This file is the
// single place that decides how a resource is named, read and formatted, so the
// 2D map, the 3D cubes, the overlay and the exports agree.

// Byte-sized resources arrive in decimal kB like memory and are kept in MiB.
function isBytes(name) {
  return name === "ephemeral-storage" || name.startsWith("hugepages-");
}

// Device plugins always advertise vendor-prefixed names.
function isDevice(name) {
  return name.includes("/");
}

const VENDOR_LABELS = { "nvidia.com": "NVIDIA", "amd.com": "AMD", "intel.com": "Intel" };

function resourceMeta(name) {
  if (name === "cpu") return { id: "cpu", label: "CPU", short: "CPU", kind: "cores", icon: "cpu" };
  if (name === "mem") return { id: "mem", label: "Memory", short: "Mem", kind: "bytes", icon: "mem" };
  if (name === "ephemeral-storage") {
    return { id: name, label: "Ephemeral Storage", short: "Disk", kind: "bytes", icon: "disk" };
  }
  if (name.startsWith("hugepages-")) {
    const size = name.slice("hugepages-".length);
    return { id: name, label: `HugePages ${size}`, short: `HP ${size}`, kind: "bytes", icon: "mem" };
  }
  if (isDevice(name)) {
    const [vendor, device] = name.split("/");
    const upper = device.length <= 4 ? device.toUpperCase() : device;
    const label = VENDOR_LABELS[vendor] ? `${VENDOR_LABELS[vendor]} ${upper}` : name;
    return { id: name, label, short: upper, kind: "count", icon: "chip" };
  }
  return { id: name, label: name, short: name, kind: "count", icon: "chip" };
}

function metricValue(obj, metric) {
  if (metric === "cpu") return obj.cpu || 0;
  if (metric === "mem") return obj.mem || 0;
  return (obj.ext && obj.ext[metric]) || 0;
}

function nodeCap(node, metric) {
  if (metric === "cpu") return node.cpuCapacity || 0;
  if (metric === "mem") return node.memCapacity || 0;
  return (node.extCap && node.extCap[metric]) || 0;
}

function nodeUsed(node, metric) {
  if (metric === "cpu") return node.cpuUsed || 0;
  if (metric === "mem") return node.memUsed || 0;
  return (node.extUsed && node.extUsed[metric]) || 0;
}

// Every extended resource some node can allocate, sorted like the backend's list.
function detectResources(nodes) {
  const names = new Set();
  for (const n of nodes) {
    for (const [name, cap] of Object.entries(n.extCap || {})) if (cap > 0) names.add(name);
  }
  return [...names].sort();
}

// Format a MiB memory value into the active unit. MiB/GiB keep their original
// precision (and integer capacity); TiB uses adaptive decimals so a non-zero
// quantity never renders as a flat "0" (TiB is coarse for node/pod memory).
function fmtMem(mib, unit, capacity = false) {
  const div = unit === "TiB" ? 1024 * 1024 : unit === "GiB" ? 1024 : 1;
  const v = mib / div;
  if (unit === "MiB") return v.toFixed(0);
  if (unit === "GiB") return v.toFixed(capacity ? 0 : 1);
  // TiB: grow decimals (2 → max 6) until the rounded value is non-zero.
  if (v === 0) return "0";
  let d = 2;
  while (d < 6 && Number(v.toFixed(d)) === 0) d++;
  return v.toFixed(d);
}

// Number and unit for one value of a resource, formatted separately so the
// header and overlay can style the unit on its own.
function fmtValue(value, metric, memUnit, capacity = false) {
  const kind = resourceMeta(metric).kind;
  if (kind === "cores") return (value / 1000).toFixed(capacity ? 0 : 2);
  if (kind === "bytes") return fmtMem(value, memUnit, capacity);
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function unitOf(metric, memUnit) {
  const meta = resourceMeta(metric);
  if (meta.kind === "cores") return "cores";
  if (meta.kind === "bytes") return memUnit;
  return meta.short;
}

// Fragmentation readout for devices: free units cluster-wide, and the largest
// block a single pod can still get. 6 free GPUs as 2+2+2 cannot run a 4-GPU job.
function fragmentation(nodes, metric) {
  let free = 0, largest = 0, largestNode = null;
  for (const n of nodes) {
    const f = Math.max(0, nodeCap(n, metric) - nodeUsed(n, metric));
    free += f;
    if (f > largest) { largest = f; largestNode = n.name; }
  }
  return { free, largest, largestNode };
}

window.k8sResources = {
  isBytes, isDevice, resourceMeta, metricValue, nodeCap, nodeUsed, detectResources,
  fmtMem, fmtValue, unitOf, fragmentation,
};
