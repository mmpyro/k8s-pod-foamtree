// Main app — sidebar + treemap grid for the k8sfoams dashboard.

const { useState, useEffect, useMemo, useRef } = React;
const { NodeCard } = window.k8sTreemap;

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

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "colorScheme": "spectrum",
  "nodeStyle": "gradient",
  "density": "comfortable",
  "showLabels": true,
  "accent": "#7c5cff"
}/*EDITMODE-END*/;

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
          cpu: cc.weight || 0,
          // Convert memory from kB to MiB
          mem: (mc.weight || 0) / 1024
        };
      });

      const podCpu = cp.weight || 0;
      const podMem = mp.weight || 0;

      cpuUsed += podCpu;
      memUsed += podMem;

      pods.push({
        name: cp.label,
        shortName: cp.label.replace(/^h2oai-/, '').split('-')[0],
        containers
      });
    }

    // Convert node capacity from kB to MiB
    const memCapacity = (mg.weight || 0) / 1024;
    const convertedMemUsed = memUsed / 1024;

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
      status: "ready"
    };
  });
}

function App() {
  const [tw, setTweak] = useTweaks(TWEAK_DEFAULTS);

  const [metric, setMetric] = useState("cpu");
  const [memUnit, setMemUnit] = useState("GiB");
  const [refreshInterval, setRefreshInterval] = useState(60);
  const [contexts, setContexts] = useState([]);
  const [contextIdx, setContextIdx] = useState(0);
  const [query, setQuery] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(Date.now());
  const [focused, setFocused] = useState(null);
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

  // Keep a stable ref to the latest loadData so the auto-refresh interval
  // always calls the current closure without re-subscribing each render.
  const loadDataRef = useRef(loadData);
  loadDataRef.current = loadData;

  const filtered = useMemo(
    () => nodes.filter(n => !query || n.name.toLowerCase().includes(query.toLowerCase())),
    [nodes, query]
  );

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

  // Auto-set accent CSS var
  useEffect(() => {
    document.documentElement.style.setProperty("--accent", tw.accent);
  }, [tw.accent]);

  return (
    <div className={`app ${sidebarOpen ? "sidebar-open" : "sidebar-collapsed"}`}>
       <Sidebar
         open={sidebarOpen}
         onToggle={() => setSidebarOpen(s => !s)}
         metric={metric} setMetric={setMetric}
         memUnit={memUnit} setMemUnit={setMemUnit}
         refreshInterval={refreshInterval} setRefreshInterval={setRefreshInterval}
         contexts={contexts}
         contextIdx={contextIdx} setContextIdx={setContextIdx}
         doRefresh={loadData} refreshing={refreshing}
         lastRefresh={lastRefresh}
         nodeCount={nodes.length}
       />

      <main className="main">
        {error && (
          <div className="error-banner">
            <span>Failed to connect to cluster: {error}</span>
          </div>
        )}

        <Header
          metric={metric}
          totals={totals}
          query={query} setQuery={setQuery}
          memUnit={memUnit}
          contexts={contexts}
          contextIdx={contextIdx}
          onMenu={() => setSidebarOpen(s => !s)}
          onRefresh={loadData}
          refreshing={refreshing}
        />

        <div className="grid-wrap">
          <TreemapGrid
            nodes={filtered}
            metric={metric}
            colorScheme={tw.colorScheme}
            nodeStyle={tw.nodeStyle}
            density={tw.density}
            showLabels={tw.showLabels}
            onFocus={setFocused}
          />
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
  open, onToggle, metric, setMetric, memUnit, setMemUnit,
  refreshInterval, setRefreshInterval,
  contexts, contextIdx, setContextIdx, doRefresh, refreshing, lastRefresh, nodeCount
}) {
  return (
    <aside className="sidebar" data-screen-label="sidebar">
      <div className="brand">
        <div className="brand-mark">
          <svg viewBox="0 0 32 32" width="22" height="22" fill="none">
            <path d="M6 14 L16 6 L26 14 L26 26 L18 26 L18 19 L14 19 L14 26 L6 26 Z"
              stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" fill="none"/>
            <circle cx="11" cy="11" r="1.5" fill="currentColor"/>
            <circle cx="21" cy="11" r="1.5" fill="currentColor"/>
          </svg>
        </div>
        <div className="brand-text">
          <div className="brand-title">k8sfoams</div>
          <div className="brand-sub">cluster topology</div>
        </div>
        <button className="icon-btn brand-collapse" onClick={onToggle} title="Collapse">
          <svg viewBox="0 0 16 16" width="14" height="14"><path d="M10 4 L6 8 L10 12" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round"/></svg>
        </button>
      </div>

      <div className="sidebar-section">
        <div className="section-label">Resource</div>
        <div className="seg seg-2">
          {METRICS.map(m => (
            <button key={m.id} className={metric === m.id ? "seg-on" : ""}
              onClick={() => setMetric(m.id)}>
              <MetricIcon kind={m.icon}/> {m.label}
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
                }}/>
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
          onChange={e => setRefreshInterval(+e.target.value)} className="slider"/>
        <button className={`btn-primary ${refreshing ? "spinning" : ""}`} onClick={doRefresh}>
          <svg viewBox="0 0 16 16" width="14" height="14" className="refresh-icon">
            <path d="M13.5 8 A5.5 5.5 0 1 1 11.5 4 M13.5 2 V5 H10.5"
              stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
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

      <div className="sidebar-footer">
        <div className="footer-row">
          <span className="dot dot-ok"/>
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

function Header({ metric, totals, query, setQuery, memUnit, contexts, contextIdx, onMenu, onRefresh, refreshing }) {
  const cpuPct = totals.cpuUsed / (totals.cpuCap || 1);
  const memPct = totals.memUsed / (totals.memCap || 1);

  const currentCtx = contexts[contextIdx];
  const contextLabel = currentCtx ? shortContext(currentCtx.context) : "No Context";

  return (
    <header className="header">
      <button className="icon-btn" onClick={onMenu} aria-label="toggle sidebar">
        <svg viewBox="0 0 20 20" width="16" height="16"><path d="M3 6h14M3 10h14M3 14h14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
      </button>

      <div className="header-title">
        <div className="title-row">
          <span className="title-main">{metric === "cpu" ? "CPU" : "Memory"} Resources</span>
          <span className="title-chip">{contextLabel}</span>
        </div>
        <div className="title-sub">
          {totals.nodes} nodes · {totals.pods} pods · live foam-tree topology
        </div>
      </div>

      <div className="header-stats">
        <Stat label="CPU" value={`${(totals.cpuUsed / 1000).toFixed(1)} / ${(totals.cpuCap / 1000).toFixed(0)}`} unit="cores" pct={cpuPct}/>
        <Stat label="Memory" value={`${fmtMem(totals.memUsed, memUnit)} / ${fmtMem(totals.memCap, memUnit, true)}`} unit={memUnit} pct={memPct}/>
        <Stat label="Pods" value={totals.pods} unit={`/ ${totals.nodes * 110} cap`} pct={totals.pods / (totals.nodes * 110 || 1)}/>
      </div>

      <div className="header-tools">
        <div className="search">
          <svg viewBox="0 0 16 16" width="14" height="14"><circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.5" fill="none"/><path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Filter nodes…"/>
          {query && <button className="search-clear" onClick={() => setQuery("")}>×</button>}
        </div>
        <button className={`icon-btn ${refreshing ? "spinning" : ""}`} onClick={onRefresh} title="Refresh">
          <svg viewBox="0 0 16 16" width="14" height="14" className="refresh-icon">
            <path d="M13.5 8 A5.5 5.5 0 1 1 11.5 4 M13.5 2 V5 H10.5"
              stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
        </button>
      </div>
    </header>
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
        <div className="stat-bar-fill" style={{ width: `${Math.min(100, (pct || 0) * 100)}%`, background: color }}/>
      </div>
    </div>
  );
}

/* ─────────── Grid ─────────── */

function TreemapGrid({ nodes, metric, colorScheme, nodeStyle, density, showLabels, onFocus }) {
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
              metric={metric}
              hue={hue}
              style={nodeStyle}
              density={density}
              showLabels={showLabels}
              onClick={() => onFocus(it.node)}
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
            <svg viewBox="0 0 16 16" width="14" height="14"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
          </button>
        </div>
        <div className="overlay-stats">
          <div className="ov-stat">
            <div className="ov-label">CPU</div>
            <div className="ov-val">{(node.cpuUsed / 1000).toFixed(2)} / {(node.cpuCapacity / 1000).toFixed(0)} <span>cores</span></div>
            <div className="ov-bar"><div style={{ width: `${(node.cpuUsed/(node.cpuCapacity || 1))*100}%` }}/></div>
          </div>
          <div className="ov-stat">
            <div className="ov-label">Memory</div>
            <div className="ov-val">{fmtMem(node.memUsed, memUnit)} / {fmtMem(node.memCapacity, memUnit, true)} <span>{memUnit}</span></div>
            <div className="ov-bar"><div style={{ width: `${(node.memUsed/(node.memCapacity || 1))*100}%`, background: "#a78bfa" }}/></div>
          </div>
          <div className="ov-stat">
            <div className="ov-label">Pods</div>
            <div className="ov-val">{node.pods.length} <span>scheduled</span></div>
            <div className="ov-bar"><div style={{ width: `${(node.pods.length/110)*100}%`, background: "#22d3ee" }}/></div>
          </div>
        </div>
        <div className="overlay-pods">
          <div className="ov-section-title">Workloads</div>
          {node.pods.length === 0 && <div className="empty-state">Node has no scheduled pods.</div>}
          <div className="pod-rows">
            {node.pods.map((p, i) => {
              const cpu = p.containers.reduce((s, c) => s + c.cpu, 0);
              const mem = p.containers.reduce((s, c) => s + c.mem, 0);
              return (
                <div key={i} className="pod-row">
                  <div className="pod-row-name">
                    <code>{p.name}</code>
                    <div className="pod-row-containers">
                      {p.containers.map((c, j) => (
                        <span key={j} className="container-pill">{c.name}</span>
                      ))}
                    </div>
                  </div>
                  <div className="pod-row-stat">
                    <span className="pod-row-num">{(cpu/1000).toFixed(2)}</span>
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

function MetricIcon({ kind }) {
  if (kind === "cpu") return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
      <rect x="3.5" y="3.5" width="9" height="9" rx="1" stroke="currentColor" strokeWidth="1.3"/>
      <rect x="5.5" y="5.5" width="5" height="5" stroke="currentColor" strokeWidth="1.3"/>
      <path d="M6 1.5v2M8 1.5v2M10 1.5v2M6 12.5v2M8 12.5v2M10 12.5v2M1.5 6h2M1.5 8h2M1.5 10h2M12.5 6h2M12.5 8h2M12.5 10h2" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round"/>
    </svg>
  );
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none">
      <rect x="1.5" y="4.5" width="13" height="7" rx="0.8" stroke="currentColor" strokeWidth="1.3"/>
      <path d="M4 4.5v7M6.5 4.5v7M9 4.5v7M11.5 4.5v7" stroke="currentColor" strokeWidth="1.1"/>
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
  // turn "arn:aws:eks:us-east-1:905…:cluster/h2oai-prod" → "h2oai-prod · us-east-1"
  const last = ctx.split("/").pop();
  const region = ctx.match(/(us|eu|ap)-[a-z]+-\d+/);
  return region ? `${last} · ${region[0]}` : last;
}

function timeAgo(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  return `${Math.floor(s/3600)}h ago`;
}

ReactDOM.createRoot(document.getElementById("root")).render(<App/>);
