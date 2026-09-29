// Main app — sidebar + treemap grid for the k8sfoams dashboard.

const { useState, useEffect, useMemo, useRef } = React;
const { NodeCard } = window.k8sTreemap;
const { Scene3D } = window.k8sScene3D;
const { workloadKey } = window.k8sWorkload;
const { warnInfo, statusOf, WARNING_ORDER } = window.k8sNodeStatus;
const { findingInfo, FINDING_ORDER, PodAuditBadge } = window.k8sPodAudit;
const { QOS_INFO, QOS_ORDER, NEUTRAL_HUE } = window.k8sQos;
const {
  isBytes, isDevice, resourceMeta, nodeCap, nodeUsed, detectResources, fmtMem, fmtValue, unitOf, fragmentation,
} = window.k8sResources;

const COLOR_MODES = [
  { id: "node", label: "Node" },
  { id: "qos", label: "QoS" },
];

// Per-node hue assignment — deterministic from index, evenly spaced around wheel.
function nodeHue(idx, scheme) {
  if (scheme === "monochrome") return 265;
  if (scheme === "status") {
    // returned from utilization later — we'll override at card level
    return [200, 145, 50, 25][idx % 4];
  }
  // spectrum
  return Math.floor((idx * 137.5) % 360);
}

// Always offered; extended resources detected on the cluster are appended.
const BASE_METRICS = [resourceMeta("cpu"), resourceMeta("mem")];
const isBase = metric => metric === "cpu" || metric === "mem";

const VIEWS = [
  { id: "2d", label: "2D Map", icon: "rect" },
  { id: "3d", label: "3D Cubes", icon: "cube" },
];

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "colorScheme": "spectrum",
  "nodeStyle": "gradient",
  "density": "comfortable",
  "showLabels": true,
  "accent": "#7c5cff"
}/*EDITMODE-END*/;

// Backend memory weights are decimal kB (1 kB = 1000 bytes),
// so MiB = kB * 1000 / 1024^2 — not a plain /1024, which would treat kB as KiB.
function kbToMib(kb) {
  return (kb * 1000) / (1024 * 1024);
}

// Extended resource map from the backend, with byte-sized values converted
// from kB to MiB like memory. Devices stay a plain count.
function extOf(extended) {
  const out = {};
  for (const [name, v] of Object.entries(extended || {})) out[name] = isBytes(name) ? kbToMib(v) : v;
  return out;
}

// Merge separate CPU and Memory data structures from backend into rich unified structures.
// Extended resources ride on both payloads, so they are read off the CPU one.
function mergeResources(cpuData, memData) {
  const cpuGroups = cpuData.groups || [];
  const memGroups = memData.groups || [];

  const memNodesMap = new Map();
  for (const mg of memGroups) {
    memNodesMap.set(mg.label, mg);
  }

  return cpuGroups.map((cg, idx) => {
    const mg = memNodesMap.get(cg.label) || { weight: 0, groups: [] };

    // Group pods by label
    const cpuPods = cg.groups || [];
    const memPods = mg.groups || [];

    const memPodsMap = new Map();
    for (const mp of memPods) {
      memPodsMap.set(mp.label, mp);
    }

    const pods = [];
    let cpuUsed = 0;
    let memUsed = 0;
    const extUsed = {};

    for (const cp of cpuPods) {
      if (cp.label === 'empty') continue;

      const mp = memPodsMap.get(cp.label) || { weight: 0, groups: [] };

      // Group containers by label
      const cpuConts = cp.groups || [];
      const memConts = mp.groups || [];
      const memContsMap = new Map();
      for (const mc of memConts) {
        memContsMap.set(mc.label, mc);
      }

      const containers = cpuConts.map(cc => {
        const mc = memContsMap.get(cc.label) || { weight: 0 };
        return {
          name: cc.label,
          // Backend marks init containers with a grey color hint
          init: !!cc.color,
          cpu: cc.weight || 0,
          // Convert memory from kB to MiB
          mem: kbToMib(mc.weight || 0),
          ext: extOf(cc.extended)
        };
      });

      const podCpu = cp.weight || 0;
      const podMem = mp.weight || 0;

      cpuUsed += podCpu;
      memUsed += podMem;
      const podExt = extOf(cp.extended);
      for (const [name, v] of Object.entries(podExt)) extUsed[name] = (extUsed[name] || 0) + v;

      pods.push({
        name: cp.label,
        shortName: cp.label.split('-')[0],
        // Selector metadata for the query bar. Absent on an older backend, so
        // every field falls back to a value that simply never matches.
        namespace: cp.namespace || "",
        labels: cp.labels || {},
        qos: cp.qos || "",
        hasInit: !!cp.hasInitContainers,
        // Best-practice rule slugs, decided by the backend.
        findings: cp.findings || [],
        // Effective request (what the scheduler reserves) — init containers
        // run sequentially, so this is max(sum regular, max init), not a sum.
        cpu: podCpu,
        // Convert memory from kB to MiB
        mem: kbToMib(podMem),
        ext: podExt,
        containers
      });
    }

    // Convert node capacity from kB to MiB
    const memCapacity = kbToMib(mg.weight || 0);
    const convertedMemUsed = kbToMib(memUsed);
    const extCap = extOf(cg.extended);
    const extFree = {};
    for (const [name, cap] of Object.entries(extCap)) extFree[name] = Math.max(0, cap - (extUsed[name] || 0));

    return {
      id: `node-${idx}`,
      name: cg.label,
      region: "us-east-1",
      instanceType: "standard",
      cpuCapacity: cg.weight || 0,
      memCapacity: memCapacity,
      cpuUsed,
      memUsed: convertedMemUsed,
      cpuFree: Math.max(0, (cg.weight || 0) - cpuUsed),
      memFree: Math.max(0, memCapacity - convertedMemUsed),
      extCap,
      extUsed,
      extFree,
      pods,
      // Node health from the backend. Absent on an older backend, so every
      // field falls back to what a plainly healthy node would report.
      warnings: cg.warnings || [],
      taints: cg.taints || [],
      conditions: cg.conditions || {},
      unschedulable: !!cg.unschedulable,
      status: statusOf(cg.warnings || [])
    };
  });
}

function App() {
  const [tw, setTweak] = useTweaks(TWEAK_DEFAULTS);

  const [view, setView] = useState("2d");
  const [zoom, setZoom] = useState(0.7);
  const [metric, setMetric] = useState("cpu");
  const [colorBy, setColorBy] = useState("node");
  const [memUnit, setMemUnit] = useState("GiB");
  const [refreshInterval, setRefreshInterval] = useState(60);
  const [contexts, setContexts] = useState([]);
  const [contextIdx, setContextIdx] = useState(0);
  const [query, setQuery] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(Date.now());
  const [focused, setFocused] = useState(null);
  // Cross-node workload highlighting. A click pins a workload; hover only
  // previews one, so a pinned selection always wins over the pointer.
  const [selectedWorkload, setSelectedWorkload] = useState(null);
  const [hoveredWorkload, setHoveredWorkload] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [nodes, setNodes] = useState([]);
  const [error, setError] = useState(null);
  const [exportError, setExportError] = useState(null);
  const gridWrapRef = useRef(null);
  const captureRef = useRef(null);

  // Load contexts from server
  useEffect(() => {
    fetch('/contexts')
      .then(res => {
        if (!res.ok) throw new Error('API failed');
        return res.json();
      })
      .then(data => {
        if (data && data.length > 0) {
          setContexts(data);
          const activeIdx = data.findIndex(c => c.active);
          setContextIdx(activeIdx !== -1 ? activeIdx : 0);
        } else {
          console.warn("No contexts config found");
          setContexts([]);
          setContextIdx(0);
        }
      })
      .catch(err => {
        console.warn("Failed to fetch contexts:", err);
        setContexts([]);
        setContextIdx(0);
      });
  }, []);

  // Fetch cluster resource data
  const loadData = async () => {
    setRefreshing(true);
    try {
      const currentCtx = contexts[contextIdx];
      const ctxParam = currentCtx ? `?context=${encodeURIComponent(currentCtx.context)}` : '';

      const [cpuRes, memRes] = await Promise.all([
        fetch(`/resources/cpu${ctxParam}`).then(r => {
          if (!r.ok) throw new Error(`CPU resources endpoint returned status ${r.status}`);
          return r.json();
        }),
        fetch(`/resources/memory${ctxParam}`).then(r => {
          if (!r.ok) throw new Error(`Memory resources endpoint returned status ${r.status}`);
          return r.json();
        })
      ]);

      const merged = mergeResources(cpuRes, memRes);
      setNodes(merged);
      setError(null);
      setLastRefresh(Date.now());
    } catch (err) {
      console.error("Error loading resources from live cluster:", err);
      setError(err.message || String(err));
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    if (contexts.length > 0) {
      loadData();
    }
  }, [contextIdx, contexts.length]);

  // Switching context swaps the whole data set, so a workload pinned in the
  // previous cluster is meaningless in the new one: it would match nothing and
  // dim every pod on screen with no pod glowing to explain why.
  useEffect(() => {
    setSelectedWorkload(null);
    setHoveredWorkload(null);
  }, [contextIdx]);

  // Keep a stable ref to the latest loadData so the auto-refresh interval
  // always calls the current closure without re-subscribing each render.
  const loadDataRef = useRef(loadData);
  loadDataRef.current = loadData;

  const parsedQuery = useMemo(() => window.k8sQuery.parseQuery(query), [query]);

  // Matched pods are highlighted and unmatched ones dimmed — nodes are never
  // removed. A malformed query stays inert (and reports itself in the header)
  // rather than dimming everything on a half-typed token.
  const match = useMemo(() => {
    const active = parsedQuery.terms.length > 0 && parsedQuery.errors.length === 0;
    const pods = new Set();
    const dimNodes = new Set();
    let total = 0;
    for (const n of nodes) {
      total += n.pods.length;
      if (!active) continue;
      if (!window.k8sQuery.nodeMatches(n, parsedQuery)) dimNodes.add(n.name);
      for (const p of n.pods) {
        if (window.k8sQuery.podMatches(p, parsedQuery, n)) pods.add(p);
      }
    }
    return { active, pods, dimNodes, count: active ? pods.size : total, total, errors: parsedQuery.errors };
  }, [nodes, parsedQuery]);

  const highlight = selectedWorkload || hoveredWorkload;
  const highlightActive = !!selectedWorkload;

  const toggleWorkload = (wl) => setSelectedWorkload(prev => (prev === wl ? null : wl));

  // Replica spread of the pinned workload. Counted over every node, ignoring
  // the query — the point of the readout is the cluster-wide picture.
  const workloadStats = useMemo(() => {
    if (!selectedWorkload) return null;
    const spread = new Set();
    let replicas = 0;
    for (const n of nodes) {
      for (const p of n.pods) {
        if (workloadKey(p.name) !== selectedWorkload) continue;
        replicas++;
        spread.add(n.name);
      }
    }
    return { key: selectedWorkload, replicas, nodes: spread.size };
  }, [nodes, selectedWorkload]);

  // Resource picker: cpu and memory, then whatever this cluster can allocate.
  const extended = useMemo(() => detectResources(nodes), [nodes]);
  const metrics = useMemo(() => [...BASE_METRICS, ...extended.map(resourceMeta)], [extended]);

  // A context without GPUs cannot show a GPU map; fall back instead of
  // drawing an empty grid. Skipped while nothing is loaded yet.
  useEffect(() => {
    if (nodes.length > 0 && !metrics.some(m => m.id === metric)) setMetric("cpu");
  }, [metrics, nodes.length]);

  // Totals
  const totals = useMemo(() => {
    const t = { cpuCap: 0, cpuUsed: 0, memCap: 0, memUsed: 0, pods: 0, nodes: nodes.length, extCap: {}, extUsed: {} };
    for (const n of nodes) {
      t.cpuCap += n.cpuCapacity;
      t.cpuUsed += n.cpuUsed;
      t.memCap += n.memCapacity;
      t.memUsed += n.memUsed;
      t.pods += n.pods.length;
      for (const [name, v] of Object.entries(n.extCap)) t.extCap[name] = (t.extCap[name] || 0) + v;
      for (const [name, v] of Object.entries(n.extUsed)) t.extUsed[name] = (t.extUsed[name] || 0) + v;
    }
    return t;
  }, [nodes]);

  // Auto-refresh tick — re-fetch live cluster data every refreshInterval seconds.
  useEffect(() => {
    const id = setInterval(() => {
      if (contexts.length > 0) loadDataRef.current();
    }, refreshInterval * 1000);
    return () => clearInterval(id);
  }, [refreshInterval, contexts.length]);

  // React fires no mouseleave when the hovered pod unmounts — a refresh
  // dropping the pod or a view switch both do that — so a stale preview would
  // dim the grid with the cursor over nothing. Drop the preview whenever the
  // rendered set is replaced; the pin is unaffected.
  useEffect(() => { setHoveredWorkload(null); }, [nodes, view]);

  // Escape clears the highlight — pin and hover preview alike.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      setSelectedWorkload(null);
      setHoveredWorkload(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // One row per distinct warning present in the cluster, worst first. Empty on
  // a healthy cluster, which is what hides the legend entirely.
  const health = useMemo(() => {
    const counts = new Map();
    for (const n of nodes) {
      for (const w of n.warnings || []) counts.set(w, (counts.get(w) || 0) + 1);
    }
    return [...counts.entries()]
      .map(([slug, count]) => ({ slug, count, ...warnInfo(slug) }))
      .sort((a, b) => WARNING_ORDER.indexOf(a.slug) - WARNING_ORDER.indexOf(b.slug));
  }, [nodes]);

  // One row per audit rule broken anywhere in the cluster, counted in pods.
  const audit = useMemo(() => {
    const counts = new Map();
    for (const n of nodes) {
      for (const p of n.pods) {
        for (const f of p.findings || []) counts.set(f, (counts.get(f) || 0) + 1);
      }
    }
    return [...counts.entries()]
      .map(([slug, count]) => ({ slug, count, ...findingInfo(slug) }))
      .sort((a, b) => FINDING_ORDER.indexOf(a.slug) - FINDING_ORDER.indexOf(b.slug));
  }, [nodes]);

  // Every class gets a row, even at zero — "no BestEffort pods" is the answer
  // an SRE is usually looking for. Pods with no reported class are not counted.
  const qosBreakdown = useMemo(() => {
    const counts = new Map(QOS_ORDER.map(q => [q, 0]));
    for (const n of nodes) {
      for (const p of n.pods) if (counts.has(p.qos)) counts.set(p.qos, counts.get(p.qos) + 1);
    }
    return QOS_ORDER.map(q => ({ qos: q, count: counts.get(q), ...QOS_INFO[q] }));
  }, [nodes]);

  // In QoS mode node chrome goes neutral, so only the pods carry colour.
  const hueOf = idx => (colorBy === "qos" ? NEUTRAL_HUE : nodeHue(idx, tw.colorScheme));

  // Image and report downloads. Images show what is on screen — current view,
  // metric, colours, query and pinned workload; reports carry every pod.
  const onExport = async (kind) => {
    const k = window.k8sExport;
    const ctx = contexts[contextIdx] ? contexts[contextIdx].context : "";
    try {
      setExportError(null);
      if (kind === "json") {
        const body = k.reportJson({ nodes, totals, context: ctx, metric, query, match });
        k.download(new Blob([body], { type: "application/json" }), k.fileName(ctx, "report", "json"));
        return;
      }
      if (kind === "csv") {
        const body = k.toCsv(k.podRows(nodes, match, ctx), k.csvColumns(nodes));
        k.download(new Blob([body], { type: "text/csv" }), k.fileName(ctx, "report", "csv"));
        return;
      }
      if (view === "3d") {
        if (!captureRef.current) throw new Error("3D view is not ready");
        const blob = await k.canvasBlob(captureRef.current(2));
        k.download(blob, k.fileName(ctx, "3d", "png"));
        return;
      }
      const grid = gridWrapRef.current && gridWrapRef.current.querySelector(".grid");
      const r = grid ? grid.getBoundingClientRect() : { width: 1600, height: 1000 };
      const memPct = Math.round((totals.memUsed / (totals.memCap || 1)) * 100);
      const cpuPct = Math.round((totals.cpuUsed / (totals.cpuCap || 1)) * 100);
      const ext = isBase(metric) ? null : {
        used: totals.extUsed[metric] || 0, cap: totals.extCap[metric] || 0, meta: resourceMeta(metric),
      };
      const svg = k.treemapSvg({
        nodes, match, metric, hueOf, colorBy,
        nodeStyle: tw.nodeStyle, density: tw.density, showLabels: tw.showLabels,
        pinned: selectedWorkload,
        width: r.width, height: r.height,
        meta: {
          title: `${resourceMeta(metric).label} Resources · ${ctx ? shortContext(ctx) : "cluster"}`,
          subtitle: `${totals.nodes} nodes · ${totals.pods} pods` +
            `${match.active ? ` · query: ${query.trim()} (${match.count} matched)` : ""}` +
            `${colorBy === "qos" ? " · color = QoS class" : ""}`,
          stats: `CPU ${(totals.cpuUsed / 1000).toFixed(1)} / ${(totals.cpuCap / 1000).toFixed(0)} cores (${cpuPct}%)` +
            ` · Memory ${fmtMem(totals.memUsed, memUnit)} / ${fmtMem(totals.memCap, memUnit, true)} ${memUnit} (${memPct}%)` +
            (ext ? ` · ${ext.meta.label} ${fmtValue(ext.used, metric, memUnit)} / ${fmtValue(ext.cap, metric, memUnit, true)}` +
              ` ${unitOf(metric, memUnit)} (${Math.round((ext.used / (ext.cap || 1)) * 100)}%)` : ""),
          stamp: `k8sfoams · ${new Date(lastRefresh).toLocaleString()}`,
        },
      });
      if (kind === "svg") {
        k.download(new Blob([svg], { type: "image/svg+xml" }), k.fileName(ctx, "2d", "svg"));
      } else {
        k.download(await k.svgToPng(svg, 2), k.fileName(ctx, "2d", "png"));
      }
    } catch (err) {
      console.error("Export failed:", err);
      setExportError(err.message || String(err));
    }
  };

  // Auto-set accent CSS var
  useEffect(() => {
    document.documentElement.style.setProperty("--accent", tw.accent);
  }, [tw.accent]);

  return (
    <div className={`app ${sidebarOpen ? "sidebar-open" : "sidebar-collapsed"}`}>
      <Sidebar
        open={sidebarOpen}
        onToggle={() => setSidebarOpen(s => !s)}
        view={view} setView={setView}
        zoom={zoom} setZoom={setZoom}
        metric={metric} setMetric={setMetric} metrics={metrics} totals={totals}
        memUnit={memUnit} setMemUnit={setMemUnit}
        refreshInterval={refreshInterval} setRefreshInterval={setRefreshInterval}
        contexts={contexts}
        contextIdx={contextIdx} setContextIdx={setContextIdx}
        doRefresh={loadData} refreshing={refreshing}
        lastRefresh={lastRefresh}
        nodeCount={nodes.length}
        health={health}
        audit={audit}
        qosBreakdown={qosBreakdown}
        colorBy={colorBy} setColorBy={setColorBy}
        query={query} setQuery={setQuery}
      />

      <main className="main">
        {error && (
          <div className="error-banner">
            <span>Failed to connect to cluster: {error}</span>
          </div>
        )}
        {exportError && (
          <div className="error-banner">
            <span>Export failed: {exportError}</span>
          </div>
        )}

        <Header
          metric={metric}
          nodes={nodes}
          view={view} setView={setView}
          totals={totals}
          query={query} setQuery={setQuery}
          match={match}
          memUnit={memUnit}
          contexts={contexts}
          contextIdx={contextIdx}
          onMenu={() => setSidebarOpen(s => !s)}
          onRefresh={loadData}
          refreshing={refreshing}
          workload={workloadStats}
          onClearWorkload={() => setSelectedWorkload(null)}
          onExport={onExport}
          canExport={nodes.length > 0}
        />

        <div className="grid-wrap" ref={gridWrapRef}>
          {view === "3d" ? (
            <Scene3D
              nodes={nodes}
              match={match}
              zoom={zoom}
              hueOf={hueOf}
              colorBy={colorBy}
              memUnit={memUnit}
              fmtMem={fmtMem}
              metric={metric}
              onFocus={setFocused}
              highlight={highlight}
              highlightActive={highlightActive}
              onPodSelect={toggleWorkload}
              onPodHover={setHoveredWorkload}
              captureRef={captureRef}
            />
          ) : (
            <TreemapGrid
              nodes={nodes}
              match={match}
              metric={metric}
              hueOf={hueOf}
              colorBy={colorBy}
              nodeStyle={tw.nodeStyle}
              density={tw.density}
              showLabels={tw.showLabels}
              onFocus={setFocused}
              highlight={highlight}
              highlightActive={highlightActive}
              onPodSelect={toggleWorkload}
              onPodHover={setHoveredWorkload}
            />
          )}
        </div>
      </main>

      {focused && (
        <FocusOverlay node={focused} onClose={() => setFocused(null)} metric={metric} memUnit={memUnit} />
      )}

      <TweaksPanel>
        <TweakSection label="Color">
          <TweakRadio label="Color scheme" value={tw.colorScheme}
            onChange={v => setTweak("colorScheme", v)}
            options={[
              { value: "spectrum", label: "Spectrum" },
              { value: "monochrome", label: "Mono" },
            ]} />
          <TweakColor label="Accent" value={tw.accent}
            onChange={v => setTweak("accent", v)}
            options={["#7c5cff", "#22d3ee", "#f472b6", "#84cc16", "#fb923c"]} />
        </TweakSection>
        <TweakSection label="Cards">
          <TweakRadio label="Node style" value={tw.nodeStyle}
            onChange={v => setTweak("nodeStyle", v)}
            options={[
              { value: "gradient", label: "Gradient" },
              { value: "solid", label: "Solid" },
              { value: "outlined", label: "Outline" },
            ]} />
          <TweakRadio label="Density" value={tw.density}
            onChange={v => setTweak("density", v)}
            options={[
              { value: "comfortable", label: "Comfortable" },
              { value: "compact", label: "Compact" },
            ]} />
          <TweakToggle label="Pod & container labels" value={tw.showLabels}
            onChange={v => setTweak("showLabels", v)} />
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

/* ─────────── Sidebar ─────────── */

function Sidebar({
  open, onToggle, view, setView, zoom, setZoom, metric, setMetric, metrics, totals, memUnit, setMemUnit,
  refreshInterval, setRefreshInterval,
  contexts, contextIdx, setContextIdx, doRefresh, refreshing, lastRefresh, nodeCount, health,
  audit, qosBreakdown, colorBy, setColorBy, query, setQuery
}) {
  const is3d = view === "3d";
  return (
    <aside className="sidebar" data-screen-label="sidebar">
      <div className="brand">
        <div className="brand-mark">
          <svg viewBox="0 0 32 32" width="22" height="22" fill="none">
            <path d="M6 14 L16 6 L26 14 L26 26 L18 26 L18 19 L14 19 L14 26 L6 26 Z"
              stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" fill="none" />
            <circle cx="11" cy="11" r="1.5" fill="currentColor" />
            <circle cx="21" cy="11" r="1.5" fill="currentColor" />
          </svg>
        </div>
        <div className="brand-text">
          <div className="brand-title">k8sfoams</div>
          <div className="brand-sub">cluster topology</div>
        </div>
        <button className="icon-btn brand-collapse" onClick={onToggle} title="Collapse">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M10 4 L6 8 L10 12" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" /></svg>
        </button>
      </div>

      <div className="sidebar-section">
        <div className="section-label">View</div>
        <div className="seg seg-2">
          {VIEWS.map(v => (
            <button key={v.id} className={view === v.id ? "seg-on" : ""}
              onClick={() => setView(v.id)}>
              <ViewIcon kind={v.icon} /> {v.label}
            </button>
          ))}
        </div>
      </div>

      {is3d && (
        <div className="sidebar-section">
          <div className="section-label">
            <span>Zoom</span>
            <span className="section-value">{Math.round(zoom * 100)}%</span>
          </div>
          <input type="range" min="0.4" max="1.6" step="0.05" value={zoom}
            onChange={e => setZoom(+e.target.value)} className="slider" />
        </div>
      )}

      <div className="sidebar-section">
        <div className="section-label">Resource</div>
        {metrics.length > 2 ? (
          // A cluster with extended resources can offer any number of them,
          // so a dropdown replaces the two-button toggle. In 3D it picks which
          // extended resource the cubes highlight.
          <ResourceMenu metrics={metrics} metric={metric} setMetric={setMetric}
            totals={totals} memUnit={memUnit} />
        ) : (
          /* Cubes plot CPU and Memory on separate axes, so there is nothing for
             this control to switch between in 3D. */
          <div className={`seg seg-2 ${is3d ? "seg-disabled" : ""}`}>
            {metrics.map(m => (
              <button key={m.id} className={!is3d && metric === m.id ? "seg-on" : ""}
                disabled={is3d}
                onClick={() => !is3d && setMetric(m.id)}>
                <MetricIcon kind={m.icon} /> {m.label}
              </button>
            ))}
          </div>
        )}
        {is3d && (
          <div className="seg-note">
            Cubes encode both — footprint is CPU, height is Memory.
            {!isBase(metric) && ` Pods requesting ${resourceMeta(metric).label} are highlighted.`}
          </div>
        )}
      </div>

      <div className="sidebar-section">
        <div className="section-label">Color by</div>
        <div className="seg seg-2">
          {COLOR_MODES.map(c => (
            <button key={c.id} className={colorBy === c.id ? "seg-on" : ""}
              onClick={() => setColorBy(c.id)}>
              {c.label}
            </button>
          ))}
        </div>
      </div>

      <div className="sidebar-section">
        <div className="section-label">Context</div>
        <div className="ctx-list">
          {contexts
            .map((c, i) => ({ c, i }))
            .sort((a, b) => (a.i === contextIdx ? -1 : b.i === contextIdx ? 1 : 0))
            .map(o => (
              <button key={o.i}
                className={`ctx-item ${o.i === contextIdx ? "ctx-on" : ""}`}
                onClick={() => setContextIdx(o.i)}>
                <span className="ctx-dot" style={{
                  background: o.i === contextIdx ? "var(--accent)" : "var(--line)"
                }} />
                <span className="ctx-name">{o.c.context.split("/").pop()}</span>
                <span className="ctx-tail">{o.c.context.includes("eks") ? "eks" : o.c.context.includes("gke") ? "gke" : "k8s"}</span>
              </button>
            ))}
        </div>
      </div>

      <div className="sidebar-section">
        <div className="section-label">
          <span>Refresh</span>
          <span className="section-value">{refreshInterval}s</span>
        </div>
        <input type="range" min="5" max="600" step="5" value={refreshInterval}
          onChange={e => setRefreshInterval(+e.target.value)} className="slider" />
        <button className={`btn-primary ${refreshing ? "spinning" : ""}`} onClick={doRefresh}>
          <svg viewBox="0 0 16 16" width="14" height="14" className="refresh-icon">
            <path d="M13.5 8 A5.5 5.5 0 1 1 11.5 4 M13.5 2 V5 H10.5"
              stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Refresh now
        </button>
      </div>

      <div className="sidebar-section">
        <div className="section-label">Memory unit</div>
        <div className="seg seg-3">
          {["MiB", "GiB", "TiB"].map(u => (
            <button key={u} className={memUnit === u ? "seg-on" : ""} onClick={() => setMemUnit(u)}>{u}</button>
          ))}
        </div>
      </div>

      {/* Only rendered when something is actually wrong, so a healthy cluster
          looks exactly as it did before this feature existed. A row toggles its
          health: query, which lights the affected nodes and dims the rest. */}
      {health.length > 0 && (
        <div className="sidebar-section">
          <div className="section-label">Node health</div>
          <div className="health-rows">
            {health.map(h => {
              const token = `health:${h.slug}`;
              const on = query.trim() === token;
              return (
                <button key={h.slug} className={`health-row audit-row ${on ? "audit-on" : ""}`}
                  onClick={() => setQuery(on ? "" : token)}>
                  <span className={`health-swatch sev-${h.sev}`} />
                  <span className="health-name">{h.label}</span>
                  <span className="health-count">{h.count}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Riskiest class first. The swatches double as the legend for Color by →
          QoS, and a row toggles its qos: query, like the audit panel below. */}
      {nodeCount > 0 && (
        <div className="sidebar-section">
          <div className="section-label">QoS &amp; Eviction Risk</div>
          <div className="health-rows">
            {qosBreakdown.map(q => {
              const token = `qos:${q.qos}`;
              const on = query.trim() === token;
              return (
                <button key={q.qos} className={`health-row audit-row ${on ? "audit-on" : ""}`}
                  title={q.risk} onClick={() => setQuery(on ? "" : token)}>
                  <span className="audit-swatch" style={{ background: `hsl(${q.hue} 70% 55%)` }} />
                  <span className="health-name">{q.label}</span>
                  <span className="health-count">{q.count}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Always shown once data is in: "nothing to fix" is an answer too. A row
          toggles its audit: query, which reuses the match highlight in 2D and 3D. */}
      {nodeCount > 0 && (
        <div className="sidebar-section">
          <div className="section-label">Audit &amp; Hygiene</div>
          <div className="health-rows">
            {audit.length === 0 && <div className="audit-clean">No issues found</div>}
            {audit.map(a => {
              const token = `audit:${a.slug}`;
              const on = query.trim() === token;
              return (
                <button key={a.slug} className={`health-row audit-row ${on ? "audit-on" : ""}`}
                  title={a.why} onClick={() => setQuery(on ? "" : token)}>
                  <span className={`audit-swatch sev-${a.sev}`} />
                  <span className="health-name">{a.label}</span>
                  <span className="health-count">{a.count}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="sidebar-footer">
        <div className="footer-row">
          <span className="dot dot-ok" />
          <span>Cluster connected</span>
          <span className="footer-tail">Ready</span>
        </div>
        <div className="footer-row dim">
          <span>{nodeCount} nodes</span>
          <span className="footer-tail">· {timeAgo(lastRefresh)}</span>
        </div>
      </div>
    </aside>
  );
}

// Resource picker once the cluster offers extended resources. A styled menu,
// not a <select>: the native popup is drawn by the OS and ignores the theme.
function ResourceMenu({ metrics, metric, setMetric, totals, memUnit }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const current = resourceMeta(metric);

  useEffect(() => {
    if (!open) return;
    const onDown = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = e => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Cluster-wide allocatable, so the list doubles as an inventory.
  const capOf = id => {
    const cap = id === "cpu" ? totals.cpuCap : id === "mem" ? totals.memCap : totals.extCap[id] || 0;
    return `${fmtValue(cap, id, memUnit, true)} ${unitOf(id, memUnit)}`;
  };
  const item = m => (
    <button key={m.id} role="menuitemradio" aria-checked={m.id === metric}
      className={`res-item ${m.id === metric ? "res-item-on" : ""}`}
      onClick={() => { setMetric(m.id); setOpen(false); }}>
      <MetricIcon kind={m.icon} />
      <span className="res-item-label">{m.label}</span>
      <span className="export-hint">{capOf(m.id)}</span>
    </button>
  );

  return (
    <div className="res-menu" ref={ref}>
      <button className={`res-trigger ${open ? "res-trigger-open" : ""}`}
        onClick={() => setOpen(o => !o)} aria-haspopup="menu" aria-expanded={open}>
        <MetricIcon kind={current.icon} />
        <span className="res-item-label">{current.label}</span>
        <svg className="res-caret" viewBox="0 0 16 16" width="12" height="12" fill="none">
          <path d="M4 6 L8 10 L12 6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="query-pop res-pop" role="menu">
          {metrics.filter(m => isBase(m.id)).map(item)}
          <div className="query-hint-title res-group">Extended</div>
          {metrics.filter(m => !isBase(m.id)).map(item)}
        </div>
      )}
    </div>
  );
}

/* ─────────── Header ─────────── */

function Header({
  metric, nodes, view, setView, totals, query, setQuery, match, memUnit, contexts, contextIdx,
  onMenu, onRefresh, refreshing, workload, onClearWorkload, onExport, canExport,
}) {
  const [hintOpen, setHintOpen] = useState(false);
  const cpuPct = totals.cpuUsed / (totals.cpuCap || 1);
  const memPct = totals.memUsed / (totals.memCap || 1);

  const currentCtx = contexts[contextIdx];
  const contextLabel = currentCtx ? shortContext(currentCtx.context) : "No Context";

  const is3d = view === "3d";
  const extMetric = isBase(metric) ? null : metric;
  const extMeta = extMetric && resourceMeta(extMetric);
  // 3D always plots CPU × Memory; the selected extended resource is named by
  // its stat card, the legend and the sidebar note, so the title stays short.
  const titleMain = is3d ? "CPU + Memory" : resourceMeta(metric).label;
  // Nodes without the selected resource are left out of the 2D map.
  const hidden = extMetric && !is3d ? nodes.filter(n => nodeCap(n, extMetric) <= 0).length : 0;
  const frag = extMetric && isDevice(extMetric) ? fragmentation(nodes, extMetric) : null;

  return (
    <header className="header">
      <button className="icon-btn" onClick={onMenu} aria-label="toggle sidebar">
        <svg viewBox="0 0 20 20" width="16" height="16"><path d="M3 6h14M3 10h14M3 14h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
      </button>

      <div className="header-title">
        <div className="title-row">
          <span className="title-main">{titleMain} Resources</span>
          <span className="title-chip">{contextLabel}</span>
          {/* Replica spread of the pinned workload — the anti-affinity check. */}
          {workload && (
            <span className="title-chip wl-chip" title={workload.key}>
              <span className="wl-chip-name">{workload.key}</span>
              <span className="wl-chip-meta">
                {workload.replicas} replica{workload.replicas === 1 ? "" : "s"} · {workload.nodes} node{workload.nodes === 1 ? "" : "s"}
              </span>
              <button className="wl-chip-clear" onClick={onClearWorkload} title="Clear selection (Esc)">×</button>
            </span>
          )}
        </div>
        <div className="title-sub">
          {totals.nodes} nodes · {totals.pods} pods · {is3d ? "isometric cube topology" : "live foam-tree topology"}
          {hidden > 0 && ` · ${hidden} node${hidden === 1 ? "" : "s"} without ${extMeta.label} hidden`}
          {/* Fragmentation: free devices only help a pod if enough sit on one node. */}
          {frag && (frag.largestNode
            ? ` · ${frag.free} ${extMeta.short} free, largest block ${frag.largest} on ${frag.largestNode}`
            : ` · no ${extMeta.short} free`)}
        </div>
      </div>

      <div className={`header-stats ${extMetric ? "has-ext" : ""}`}>
        <Stat label="CPU" value={`${(totals.cpuUsed / 1000).toFixed(1)} / ${(totals.cpuCap / 1000).toFixed(0)}`} unit="cores" pct={cpuPct} />
        <Stat label="Memory" value={`${fmtMem(totals.memUsed, memUnit)} / ${fmtMem(totals.memCap, memUnit, true)}`} unit={memUnit} pct={memPct} />
        <Stat className="stat-pods" label="Pods" value={totals.pods} unit={`/ ${totals.nodes * 110} cap`} pct={totals.pods / (totals.nodes * 110 || 1)} />
        {extMetric && (
          <Stat label={extMeta.label}
            value={`${fmtValue(totals.extUsed[extMetric] || 0, extMetric, memUnit)} / ${fmtValue(totals.extCap[extMetric] || 0, extMetric, memUnit, true)}`}
            unit={unitOf(extMetric, memUnit)}
            pct={(totals.extUsed[extMetric] || 0) / (totals.extCap[extMetric] || 1)} />
        )}
      </div>

      <div className="header-tools">
        <div className="view-pill">
          {VIEWS.map(v => (
            <button key={v.id}
              className={view === v.id ? `view-on ${v.id === "3d" ? "view-3d" : ""}` : ""}
              onClick={() => setView(v.id)}
              title={v.label}>
              {v.id.toUpperCase()}
            </button>
          ))}
        </div>
        <QueryBar query={query} setQuery={setQuery} match={match}
          hintOpen={hintOpen} setHintOpen={setHintOpen} />
        <ExportMenu is3d={is3d} disabled={!canExport} onExport={onExport} />
        <button className={`icon-btn ${refreshing ? "spinning" : ""}`} onClick={onRefresh} title="Refresh">
          <svg viewBox="0 0 16 16" width="14" height="14" className="refresh-icon">
            <path d="M13.5 8 A5.5 5.5 0 1 1 11.5 4 M13.5 2 V5 H10.5"
              stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
    </header>
  );
}

const EXPORTS = [
  { id: "png", label: "PNG image", hint: "2× resolution" },
  { id: "svg", label: "SVG image", hint: "vector, 2D only", only2d: true },
  { id: "json", label: "JSON report", hint: "nodes, pods, containers" },
  { id: "csv", label: "CSV report", hint: "one row per pod" },
];

// Download menu next to Refresh. Images capture the current view; reports
// are the same in both views.
function ExportMenu({ is3d, disabled, onExport }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = e => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="export-menu" ref={ref}>
      <button className={`icon-btn ${open ? "icon-btn-on" : ""}`} disabled={disabled}
        onClick={() => setOpen(o => !o)} title="Export" aria-haspopup="menu" aria-expanded={open}>
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none">
          <path d="M8 2.5 V10 M4.5 6.5 L8 10 L11.5 6.5 M3 13.5 H13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="query-pop export-pop" role="menu">
          <div className="query-hint-title">Export {is3d ? "3D view" : "2D map"}</div>
          {EXPORTS.map(x => {
            const off = x.only2d && is3d;
            return (
              <button key={x.id} role="menuitem" className="export-item" disabled={off}
                title={off ? "The 3D view is WebGL; switch to 2D for vector SVG" : undefined}
                onClick={() => { setOpen(false); onExport(x.id); }}>
                <span>{x.label}</span>
                <span className="export-hint">{off ? "2D only" : x.hint}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Query bar — search input plus live match count, inline token errors and a
// hint listing the grammar. The grammar itself lives in query.jsx.
function QueryBar({ query, setQuery, match, hintOpen, setHintOpen }) {
  const errors = (match && match.errors) || [];
  const invalid = errors.length > 0;
  const counting = !!query && !invalid && match;

  return (
    <div className="query-bar">
      <div className={`search ${invalid ? "search-invalid" : ""}`}>
        <svg viewBox="0 0 16 16" width="14" height="14"><circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.5" fill="none" /><path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
        <input value={query} onChange={e => setQuery(e.target.value)}
          onFocus={() => setHintOpen(true)} onBlur={() => setHintOpen(false)}
          spellCheck="false"
          placeholder="ns:kube-system app=frontend node:worker-*" />
        {counting && (
          <span className={`query-count ${match.count === 0 ? "query-count-none" : ""}`}>
            {match.count} / {match.total} pods
          </span>
        )}
        {query && <button className="search-clear" onClick={() => setQuery("")}>×</button>}
      </div>

      {invalid && (
        <div className="query-pop query-errors">
          {errors.map((e, i) => (
            <div key={i} className="query-err"><code>{e.token}</code><span>{e.message}</span></div>
          ))}
        </div>
      )}

      {hintOpen && !invalid && (
        <div className="query-pop query-hint">
          <div className="query-hint-title">Filter tokens · combined with AND</div>
          {window.k8sQuery.TOKEN_HINTS.map((h, i) => (
            <div key={i} className="query-hint-row"><code>{h.form}</code><span>{h.desc}</span></div>
          ))}
          <div className="query-hint-foot">Quote values with spaces: app="my app"</div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, unit, pct, className = "" }) {
  const color = pct > 0.85 ? "#ef4444" : pct > 0.6 ? "#f59e0b" : pct > 0.3 ? "#10b981" : "#60a5fa";
  return (
    <div className={`stat ${className}`} title={`${label}: ${value} ${unit}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-val">
        <span className="stat-num">{value}</span>
        <span className="stat-unit">{unit}</span>
      </div>
      <div className="stat-bar">
        <div className="stat-bar-fill" style={{ width: `${Math.min(100, (pct || 0) * 100)}%`, background: color }} />
      </div>
    </div>
  );
}

/* ─────────── Grid ─────────── */

function TreemapGrid({
  nodes, match, metric, hueOf, colorBy, nodeStyle, density, showLabels, onFocus,
  highlight, highlightActive, onPodSelect, onPodHover,
}) {
  const containerRef = useRef(null);
  const [box, setBox] = useState({ w: 0, h: 0 });

  useEffect(() => {
    if (!containerRef.current) return;
    const measure = () => {
      const r = containerRef.current.getBoundingClientRect();
      setBox({ w: r.width, h: r.height });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  // Top-level squarify of nodes themselves, sized by capacity. A node without
  // the selected extended resource has nothing to draw, so it is left out
  // (the header counts it); idx is kept so node hues stay put across metrics.
  const items = nodes
    .map((n, idx) => ({ node: n, value: nodeCap(n, metric), idx }))
    .filter(it => isBase(metric) || it.value > 0);

  const laid = box.w > 0 && box.h > 0
    ? window.k8sTreemap.squarify(items, 0, 0, box.w, box.h)
    : [];

  return (
    <div className="grid" ref={containerRef}>
      {laid.map((it, i) => {
        const hue = hueOf(it.idx);
        return (
          <div key={it.node.id} className="grid-slot"
            style={{
              left: it.x, top: it.y, width: it.w - 6, height: it.h - 6,
            }}>
            <NodeCard
              node={it.node}
              match={match}
              metric={metric}
              hue={hue}
              colorBy={colorBy}
              style={nodeStyle}
              density={density}
              showLabels={showLabels}
              onClick={() => onFocus(it.node)}
              highlight={highlight}
              highlightActive={highlightActive}
              onPodSelect={onPodSelect}
              onPodHover={onPodHover}
            />
          </div>
        );
      })}
    </div>
  );
}

/* ─────────── Focus overlay ─────────── */

function FocusOverlay({ node, onClose, metric, memUnit }) {
  return (
    <div className="overlay" onClick={onClose}>
      <div className="overlay-card" onClick={e => e.stopPropagation()}>
        <div className="overlay-head">
          <div>
            <div className="overlay-title">{node.name}</div>
            <div className="overlay-sub">
              {node.instanceType} · {node.region} ·
              <span className={`status-pill status-${node.status}`}>{node.status}</span>
            </div>
          </div>
          <button className="icon-btn" onClick={onClose}>
            <svg viewBox="0 0 16 16" width="14" height="14"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          </button>
        </div>
        <div className="overlay-stats">
          <div className="ov-stat">
            <div className="ov-label">CPU</div>
            <div className="ov-val">{(node.cpuUsed / 1000).toFixed(2)} / {(node.cpuCapacity / 1000).toFixed(0)} <span>cores</span></div>
            <div className="ov-bar"><div style={{ width: `${(node.cpuUsed / (node.cpuCapacity || 1)) * 100}%` }} /></div>
          </div>
          <div className="ov-stat">
            <div className="ov-label">Memory</div>
            <div className="ov-val">{fmtMem(node.memUsed, memUnit)} / {fmtMem(node.memCapacity, memUnit, true)} <span>{memUnit}</span></div>
            <div className="ov-bar"><div style={{ width: `${(node.memUsed / (node.memCapacity || 1)) * 100}%`, background: "#a78bfa" }} /></div>
          </div>
          {Object.entries(node.extCap).filter(([, cap]) => cap > 0).map(([name, cap]) => (
            <div className="ov-stat" key={name}>
              <div className="ov-label">{resourceMeta(name).label}</div>
              <div className="ov-val">
                {fmtValue(node.extUsed[name] || 0, name, memUnit)} / {fmtValue(cap, name, memUnit, true)} <span>{unitOf(name, memUnit)}</span>
              </div>
              <div className="ov-bar"><div style={{ width: `${((node.extUsed[name] || 0) / cap) * 100}%`, background: "#f472b6" }} /></div>
            </div>
          ))}
          <div className="ov-stat">
            <div className="ov-label">Pods</div>
            <div className="ov-val">{node.pods.length} <span>scheduled</span></div>
            <div className="ov-bar"><div style={{ width: `${(node.pods.length / 110) * 100}%`, background: "#22d3ee" }} /></div>
          </div>
        </div>
        {(node.warnings.length > 0 || node.taints.length > 0) && (
          <div className="overlay-sched">
            <div className="ov-section-title">Scheduling</div>
            <div className="ov-chips">
              {node.warnings.map(w => (
                <span key={w} className={`status-pill status-${warnInfo(w).pill}`}>{warnInfo(w).label}</span>
              ))}
              {node.warnings.length === 0 && (
                <span className="ov-chips-note">Schedulable — the taints below are advisory.</span>
              )}
            </div>
            {node.taints.length > 0 && (
              <div className="taint-rows">
                {node.taints.map((t, i) => (
                  <div key={i} className="taint-row">
                    <code>{t.key}{t.value ? `=${t.value}` : ""}</code>
                    <span className={`taint-effect eff-${t.effect}`}>{t.effect}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="overlay-pods">
          <div className="ov-section-title">Workloads</div>
          {node.pods.length === 0 && <div className="empty-state">Node has no scheduled pods.</div>}
          <div className="pod-rows">
            {node.pods.map((p, i) => {
              // Effective request from the backend, not a container sum —
              // init containers don't add on top of regular ones.
              const cpu = p.cpu;
              const mem = p.mem;
              return (
                <div key={i} className="pod-row">
                  <div className="pod-row-name">
                    <code>{p.name}</code>
                    <div className="pod-row-containers">
                      {p.containers.map((c, j) => (
                        <span key={j} className={`container-pill${c.init ? " init" : ""}`}>{c.name}</span>
                      ))}
                    </div>
                    {Object.keys(p.ext).length > 0 && (
                      <div className="pod-row-containers">
                        {Object.entries(p.ext).map(([name, v]) => (
                          <span key={name} className={`ext-pill${name === metric ? " ext-on" : ""}`}>
                            {resourceMeta(name).short} {fmtValue(v, name, memUnit)}{resourceMeta(name).kind === "bytes" ? ` ${memUnit}` : ""}
                          </span>
                        ))}
                      </div>
                    )}
                    {p.findings.length > 0 && (
                      <div className="pod-row-findings">
                        {p.findings.map(f => (
                          <span key={f} className={`audit-pill sev-${findingInfo(f).sev}`} title={findingInfo(f).why}>
                            <PodAuditBadge findings={[f]} size={10} />
                            {findingInfo(f).label}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="pod-row-stat">
                    <span className="pod-row-num">{(cpu / 1000).toFixed(2)}</span>
                    <span className="pod-row-unit">cores</span>
                  </div>
                  <div className="pod-row-stat">
                    <span className="pod-row-num">{fmtMem(mem, memUnit)}</span>
                    <span className="pod-row-unit">{memUnit}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────── Icons ─────────── */

// View glyphs, sized to sit alongside MetricIcon in the segmented controls:
// a quartered rect for the treemap, an isometric cube for the 3D scene.
function ViewIcon({ kind }) {
  if (kind === "rect") return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
      <rect x="1.5" y="1.5" width="13" height="13" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <path d="M1.5 8 H14.5 M8 1.5 V14.5" stroke="currentColor" strokeWidth="1.1" />
    </svg>
  );
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
      {/* top face, then the left and right walls meeting at the near vertex */}
      <path d="M8 1.6 L14 5 L8 8.4 L2 5 Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M2 5 V11 L8 14.4 V8.4" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M14 5 V11 L8 14.4" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

function MetricIcon({ kind }) {
  if (kind === "chip") return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
      {/* accelerator card: board, die, and the edge connector */}
      <rect x="1.5" y="3.5" width="13" height="8" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <rect x="5" y="5.5" width="4" height="4" stroke="currentColor" strokeWidth="1.1" />
      <path d="M11 6v3M4 11.5v2M6.5 11.5v2M9 11.5v2" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  );
  if (kind === "disk") return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
      <ellipse cx="8" cy="4" rx="5.5" ry="2" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.5 4v8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2V4M2.5 8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
  if (kind === "cpu") return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
      <rect x="3.5" y="3.5" width="9" height="9" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <rect x="5.5" y="5.5" width="5" height="5" stroke="currentColor" strokeWidth="1.3" />
      <path d="M6 1.5v2M8 1.5v2M10 1.5v2M6 12.5v2M8 12.5v2M10 12.5v2M1.5 6h2M1.5 8h2M1.5 10h2M12.5 6h2M12.5 8h2M12.5 10h2" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  );
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
      <rect x="1.5" y="4.5" width="13" height="7" rx="0.8" stroke="currentColor" strokeWidth="1.3" />
      <path d="M4 4.5v7M6.5 4.5v7M9 4.5v7M11.5 4.5v7" stroke="currentColor" strokeWidth="1.1" />
    </svg>
  );
}

/* ─────────── Utils ─────────── */

function shortContext(ctx) {
  const last = ctx.split("/").pop();
  const region = ctx.match(/(us|eu|ap)-[a-z]+-\d+/);
  return region ? `${last} · ${region[0]}` : last;
}

function timeAgo(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
