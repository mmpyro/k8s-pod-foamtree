// Main app — sidebar + treemap grid for the k8sfoams dashboard.

const { useState, useEffect, useMemo, useRef } = React;
const { NodeCard } = window.k8sTreemap;
const { Scene3D } = window.k8sCube3D;
const { workloadKey } = window.k8sWorkload;
const { warnInfo, statusOf, WARNING_ORDER } = window.k8sNodeStatus;

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

const METRICS = [
  { id: "cpu", label: "CPU", icon: "cpu" },
  { id: "mem", label: "Memory", icon: "mem" },
];

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

// Backend memory weights are decimal kB (bitmath .kB, 1 kB = 1000 bytes),
// so MiB = kB * 1000 / 1024^2 — not a plain /1024, which would treat kB as KiB.
function kbToMib(kb) {
  return (kb * 1000) / (1024 * 1024);
}

// Merge separate CPU and Memory data structures from backend into rich unified structures.
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
          mem: kbToMib(mc.weight || 0)
        };
      });

      const podCpu = cp.weight || 0;
      const podMem = mp.weight || 0;

      cpuUsed += podCpu;
      memUsed += podMem;

      pods.push({
        name: cp.label,
        shortName: cp.label.split('-')[0],
        // Selector metadata for the query bar. Absent on an older backend, so
        // every field falls back to a value that simply never matches.
        namespace: cp.namespace || "",
        labels: cp.labels || {},
        qos: cp.qos || "",
        hasInit: !!cp.hasInitContainers,
        // Effective request (what the scheduler reserves) — init containers
        // run sequentially, so this is max(sum regular, max init), not a sum.
        cpu: podCpu,
        // Convert memory from kB to MiB
        mem: kbToMib(podMem),
        containers
      });
    }

    // Convert node capacity from kB to MiB
    const memCapacity = kbToMib(mg.weight || 0);
    const convertedMemUsed = kbToMib(memUsed);

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
      if (!window.k8sQuery.nodeMatches(n.name, parsedQuery)) dimNodes.add(n.name);
      for (const p of n.pods) {
        if (window.k8sQuery.podMatches(p, parsedQuery, n.name)) pods.add(p);
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

  // Totals
  const totals = useMemo(() => {
    const t = { cpuCap: 0, cpuUsed: 0, memCap: 0, memUsed: 0, pods: 0, nodes: nodes.length };
    for (const n of nodes) {
      t.cpuCap += n.cpuCapacity;
      t.cpuUsed += n.cpuUsed;
      t.memCap += n.memCapacity;
      t.memUsed += n.memUsed;
      t.pods += n.pods.length;
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
        metric={metric} setMetric={setMetric}
        memUnit={memUnit} setMemUnit={setMemUnit}
        refreshInterval={refreshInterval} setRefreshInterval={setRefreshInterval}
        contexts={contexts}
        contextIdx={contextIdx} setContextIdx={setContextIdx}
        doRefresh={loadData} refreshing={refreshing}
        lastRefresh={lastRefresh}
        nodeCount={nodes.length}
        health={health}
      />

      <main className="main">
        {error && (
          <div className="error-banner">
            <span>Failed to connect to cluster: {error}</span>
          </div>
        )}

        <Header
          metric={metric}
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
        />

        <div className="grid-wrap">
          {view === "3d" ? (
            <Scene3D
              nodes={nodes}
              match={match}
              zoom={zoom}
              hueOf={idx => nodeHue(idx, tw.colorScheme)}
              memUnit={memUnit}
              fmtMem={fmtMem}
              onFocus={setFocused}
              highlight={highlight}
              highlightActive={highlightActive}
              onPodSelect={toggleWorkload}
              onPodHover={setHoveredWorkload}
            />
          ) : (
            <TreemapGrid
              nodes={nodes}
              match={match}
              metric={metric}
              colorScheme={tw.colorScheme}
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
  open, onToggle, view, setView, zoom, setZoom, metric, setMetric, memUnit, setMemUnit,
  refreshInterval, setRefreshInterval,
  contexts, contextIdx, setContextIdx, doRefresh, refreshing, lastRefresh, nodeCount, health
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
        {/* Cubes plot CPU and Memory on separate axes, so there is nothing for
            this control to switch between in 3D. */}
        <div className={`seg seg-2 ${is3d ? "seg-disabled" : ""}`}>
          {METRICS.map(m => (
            <button key={m.id} className={!is3d && metric === m.id ? "seg-on" : ""}
              disabled={is3d}
              onClick={() => !is3d && setMetric(m.id)}>
              <MetricIcon kind={m.icon} /> {m.label}
            </button>
          ))}
        </div>
        {is3d && (
          <div className="seg-note">Cubes encode both — footprint is CPU, height is Memory.</div>
        )}
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
          looks exactly as it did before this feature existed. */}
      {health.length > 0 && (
        <div className="sidebar-section">
          <div className="section-label">Node health</div>
          <div className="health-rows">
            {health.map(h => (
              <div key={h.slug} className="health-row">
                <span className={`health-swatch sev-${h.sev}`} />
                <span className="health-name">{h.label}</span>
                <span className="health-count">{h.count}</span>
              </div>
            ))}
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

/* ─────────── Header ─────────── */

function Header({
  metric, view, setView, totals, query, setQuery, match, memUnit, contexts, contextIdx,
  onMenu, onRefresh, refreshing, workload, onClearWorkload,
}) {
  const [hintOpen, setHintOpen] = useState(false);
  const cpuPct = totals.cpuUsed / (totals.cpuCap || 1);
  const memPct = totals.memUsed / (totals.memCap || 1);

  const currentCtx = contexts[contextIdx];
  const contextLabel = currentCtx ? shortContext(currentCtx.context) : "No Context";

  const is3d = view === "3d";
  const titleMain = is3d ? "CPU + Memory" : metric === "cpu" ? "CPU" : "Memory";

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
        </div>
      </div>

      <div className="header-stats">
        <Stat label="CPU" value={`${(totals.cpuUsed / 1000).toFixed(1)} / ${(totals.cpuCap / 1000).toFixed(0)}`} unit="cores" pct={cpuPct} />
        <Stat label="Memory" value={`${fmtMem(totals.memUsed, memUnit)} / ${fmtMem(totals.memCap, memUnit, true)}`} unit={memUnit} pct={memPct} />
        <Stat label="Pods" value={totals.pods} unit={`/ ${totals.nodes * 110} cap`} pct={totals.pods / (totals.nodes * 110 || 1)} />
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

function Stat({ label, value, unit, pct }) {
  const color = pct > 0.85 ? "#ef4444" : pct > 0.6 ? "#f59e0b" : pct > 0.3 ? "#10b981" : "#60a5fa";
  return (
    <div className="stat">
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
  nodes, match, metric, colorScheme, nodeStyle, density, showLabels, onFocus,
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

  // Top-level squarify of nodes themselves, sized by capacity
  const items = nodes.map((n, idx) => ({
    node: n,
    value: metric === "cpu" ? n.cpuCapacity : n.memCapacity,
    idx,
  }));

  const laid = box.w > 0 && box.h > 0
    ? window.k8sTreemap.squarify(items, 0, 0, box.w, box.h)
    : [];

  return (
    <div className="grid" ref={containerRef}>
      {laid.map((it, i) => {
        const hue = nodeHue(it.idx, colorScheme);
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
