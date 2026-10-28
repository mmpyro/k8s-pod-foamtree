// Scheduling simulators: "Can I fit this pod?" and "Simulate drain". The
// backend runs the scheduler filter and sends verdicts with reason slugs; this
// file is the single place that decides how they read and which nodes the map
// outlines, so the modal, the drain report and both views agree.

const { useState, useEffect } = React;

// Ordered the way the backend's filter evaluates them.
const REASONS = {
  "cordoned":            { label: "cordoned" },
  "not-ready":           { label: "not ready" },
  "node-selector":       { label: "nodeSelector mismatch" },
  "taint":               { label: "untolerated taint" },
  "insufficient-pods":   { label: "pod limit reached" },
  "insufficient-cpu":    { label: "insufficient CPU" },
  "insufficient-memory": { label: "insufficient memory" },
  "insufficient-extended": { label: "insufficient extended resource" },
};

const SKIP_REASONS = {
  daemonset: "DaemonSet — runs on every node, not evicted",
  static: "static pod — owned by the kubelet, not evicted",
};

// What the simulator does not model, shown under every result so a green
// verdict is read as "necessary", not "guaranteed".
const CAVEAT = "Filter phase only: CPU, memory, extended resources (GPUs, ephemeral-storage, hugepages), " +
  "pod count, cordon, readiness, nodeSelector and taints. " +
  "Affinity, topology spread, PDBs and volume zones are not simulated.";

function reasonLabel(slug) {
  return (REASONS[slug] || { label: slug }).label;
}

// Backend memory is decimal kB, like the /resources payload.
const kbToMib = kb => (kb * 1000) / (1024 * 1024);

// `zone=a, disk=ssd` or one per line.
function parseSelector(text) {
  const selector = {}, errors = [];
  for (const raw of text.split(/[\n,]/)) {
    const tok = raw.trim();
    if (!tok) continue;
    const eq = tok.indexOf("=");
    if (eq <= 0) { errors.push(`"${tok}" is not key=value`); continue; }
    selector[tok.slice(0, eq).trim()] = tok.slice(eq + 1).trim();
  }
  return { selector, errors };
}

// `key=value:Effect`, `key:Effect`, `key` (any value, any effect) or `*` (everything).
const EFFECTS = ["NoSchedule", "PreferNoSchedule", "NoExecute"];
function parseTolerations(text) {
  const tolerations = [], errors = [];
  for (const raw of text.split(/[\n,]/)) {
    const tok = raw.trim();
    if (!tok) continue;
    if (tok === "*") { tolerations.push({ operator: "Exists" }); continue; }
    const [kv, effect] = tok.split(":");
    if (effect !== undefined && !EFFECTS.includes(effect.trim())) {
      errors.push(`"${tok}": effect must be one of ${EFFECTS.join(", ")}`);
      continue;
    }
    const eq = kv.indexOf("=");
    const key = (eq === -1 ? kv : kv.slice(0, eq)).trim();
    if (!key) { errors.push(`"${tok}" has no key`); continue; }
    tolerations.push(eq === -1
      ? { key, operator: "Exists", effect: effect && effect.trim() }
      : { key, operator: "Equal", value: kv.slice(eq + 1).trim(), effect: effect && effect.trim() });
  }
  return { tolerations, errors };
}

// The preset whose fields the form holds, or null. Whitespace around the
// commas of a list field does not make it a different pod.
function matchPreset(presets, form) {
  const norm = s => (s || "").split(",").map(t => t.trim()).filter(Boolean).join(",");
  return presets.find(p => Object.keys(p.form).every(k => norm(p.form[k]) === norm(form[k]))) || null;
}

// Map verdicts: node name → "ok" | "fail" | "drained".
function fitSimulation(result) {
  return {
    kind: "fit",
    summary: `fits ${result.fits} / ${result.total} nodes`,
    ok: result.fits > 0,
    verdicts: new Map(result.nodes.map(n => [n.node, n.fits ? "ok" : "fail"])),
  };
}

function drainSimulation(result) {
  const verdicts = new Map([[result.node, "drained"]]);
  for (const p of result.placements) verdicts.set(p.to, "ok");
  const moved = result.placements.length + result.pending.length;
  return {
    kind: "drain",
    summary: result.fits
      ? `drain ${result.node}: ${moved} pod${moved === 1 ? "" : "s"} fit`
      : `drain ${result.node}: ${result.pending.length} pending`,
    ok: result.fits,
    verdicts,
  };
}

function SimCaveat() {
  return <div className="sim-caveat">{CAVEAT}</div>;
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14"><path d="M4 4 L12 12 M12 4 L4 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
  );
}

/* ─────────── Can I fit this pod? ─────────── */

function FitModal({ ctx, memUnit, fmtMem, onSimulation, onClose }) {
  const [cpu, setCpu] = useState("500m");
  const [memory, setMemory] = useState("1Gi");
  const [selectorText, setSelectorText] = useState("");
  const [tolerationText, setTolerationText] = useState("");
  const [extendedText, setExtendedText] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // The static demo can only answer for these pods; the server answers any.
  const [presets, setPresets] = useState([]);

  useEffect(() => {
    window.k8sApi.fitPresets().then(setPresets).catch(err => setError(err.message || String(err)));
  }, []);

  const { selector, errors: selErrors } = parseSelector(selectorText);
  const { tolerations, errors: tolErrors } = parseTolerations(tolerationText);
  // Same name=quantity syntax as the selector; quantities are validated server-side.
  const { selector: extended, errors: extErrors } = parseSelector(extendedText);
  const formErrors = [...selErrors, ...tolErrors, ...extErrors];
  const preset = matchPreset(presets, {
    cpu, memory, extended: extendedText, nodeSelector: selectorText, tolerations: tolerationText,
  });
  const customInDemo = window.k8sApi.isStatic && !preset;

  const applyPreset = (p) => {
    setCpu(p.form.cpu);
    setMemory(p.form.memory);
    setExtendedText(p.form.extended);
    setSelectorText(p.form.nodeSelector);
    setTolerationText(p.form.tolerations);
    setError(null);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (formErrors.length) return;
    setBusy(true);
    try {
      const spec = { cpu, memory, extended, nodeSelector: selector, tolerations };
      const res = await window.k8sApi.fit(ctx, spec, preset && preset.id);
      setResult(res);
      setError(null);
      onSimulation(fitSimulation(res));
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  // Schedulable nodes first, then the rest by name, so the answer is on top.
  const rows = result ? [...result.nodes].sort((a, b) => (b.fits - a.fits) || a.node.localeCompare(b.node)) : [];

  return (
    <div className="overlay" onClick={onClose}>
      <div className="overlay-card sim-card" onClick={e => e.stopPropagation()}>
        <div className="overlay-head">
          <div>
            <div className="overlay-title">Can I fit this pod?</div>
            <div className="overlay-sub">Dry-run the scheduler against free allocatable capacity</div>
          </div>
          <button className="icon-btn" onClick={onClose}><CloseIcon /></button>
        </div>

        <form className="sim-form" onSubmit={submit}>
          {presets.length > 0 && (
            <div className="sim-field sim-wide">
              <span className="ov-label">Demo presets</span>
              <div className="sim-presets">
                {presets.map(p => (
                  <button key={p.id} type="button" className={`sim-preset ${preset && preset.id === p.id ? "sim-preset-on" : ""}`}
                    onClick={() => applyPreset(p)}>
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          <label className="sim-field">
            <span className="ov-label">CPU request</span>
            <input value={cpu} onChange={e => setCpu(e.target.value)} placeholder="4000m or 4" spellCheck="false" />
          </label>
          <label className="sim-field">
            <span className="ov-label">Memory request</span>
            <input value={memory} onChange={e => setMemory(e.target.value)} placeholder="16Gi" spellCheck="false" />
          </label>
          <label className="sim-field sim-wide">
            <span className="ov-label">Extended resources</span>
            <input value={extendedText} onChange={e => setExtendedText(e.target.value)}
              placeholder="nvidia.com/gpu=1, ephemeral-storage=10Gi" spellCheck="false" />
          </label>
          <label className="sim-field sim-wide">
            <span className="ov-label">nodeSelector</span>
            <input value={selectorText} onChange={e => setSelectorText(e.target.value)}
              placeholder="topology.kubernetes.io/zone=us-east-1a, disk=ssd" spellCheck="false" />
          </label>
          <label className="sim-field sim-wide">
            <span className="ov-label">Tolerations</span>
            <input value={tolerationText} onChange={e => setTolerationText(e.target.value)}
              placeholder="dedicated=gpu:NoSchedule, spot  ( * tolerates all )" spellCheck="false" />
          </label>
          <div className="sim-actions sim-wide">
            {formErrors.map((m, i) => <span key={i} className="sim-error">{m}</span>)}
            {error && <span className="sim-error">{error}</span>}
            {customInDemo && !error && (
              <span className="sim-note">The demo only has answers for the presets — run <code>k8sfoams --synthetic</code> locally to try any pod.</span>
            )}
            <button type="submit" className={`btn-primary ${busy ? "spinning" : ""}`}
              disabled={busy || formErrors.length > 0 || customInDemo}>
              {busy ? "Simulating…" : "Simulate"}
            </button>
          </div>
        </form>

        {result && (
          <div className="overlay-pods">
            <div className={`sim-summary ${result.fits ? "sim-summary-ok" : "sim-summary-fail"}`}>
              {result.fits
                ? `${result.fits} of ${result.total} nodes can schedule it — no scale-up needed.`
                : `No node can schedule it — this pod would stay Pending (or trigger a scale-up).`}
            </div>
            <div className="pod-rows">
              {rows.map(n => (
                <div key={n.node} className={`pod-row sim-row ${n.fits ? "sim-row-ok" : "sim-row-fail"}`}>
                  <div className="pod-row-name">
                    <code>{n.node}</code>
                    {n.reasons.length > 0 && (
                      <div className="sim-reasons">
                        {n.reasons.map((r, i) => <span key={i} title={reasonLabel(r.slug)}>{r.message}</span>)}
                      </div>
                    )}
                  </div>
                  <div className="pod-row-stat">
                    <span className="pod-row-num">{(n.free.cpu / 1000).toFixed(2)}</span>
                    <span className="pod-row-unit">cores free</span>
                  </div>
                  <div className="pod-row-stat">
                    <span className="pod-row-num">{fmtMem(kbToMib(n.free.memory), memUnit)}</span>
                    <span className="pod-row-unit">{memUnit} free</span>
                  </div>
                </div>
              ))}
            </div>
            <SimCaveat />
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────── Simulate drain ─────────── */

function DrainButton({ ctx, nodeName, onReport }) {
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      onReport({ result: await window.k8sApi.drain(ctx, nodeName) });
    } catch (err) {
      onReport({ error: err.message || String(err) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <button className={`btn-primary sim-drain-btn ${busy ? "spinning" : ""}`} onClick={run} disabled={busy}
      title="What happens to this node's pods if it is drained or fails?">
      {busy ? "Simulating…" : "Simulate drain"}
    </button>
  );
}

function PodLine({ p, memUnit, fmtMem, children }) {
  return (
    <div className={`pod-row sim-row ${p.reasons ? "sim-row-fail" : "sim-row-ok"}`}>
      <div className="pod-row-name">
        <code>{p.namespace}/{p.pod}</code>
        {children}
      </div>
      <div className="pod-row-stat">
        <span className="pod-row-num">{(p.cpu / 1000).toFixed(2)}</span>
        <span className="pod-row-unit">cores</span>
      </div>
      <div className="pod-row-stat">
        <span className="pod-row-num">{fmtMem(kbToMib(p.memory), memUnit)}</span>
        <span className="pod-row-unit">{memUnit}</span>
      </div>
    </div>
  );
}

function DrainReport({ report, memUnit, fmtMem }) {
  if (report.error) return <div className="sim-summary sim-summary-fail">Drain simulation failed: {report.error}</div>;
  const r = report.result;
  const moved = r.placements.length + r.pending.length;
  const unmanaged = [...r.placements, ...r.pending].filter(p => p.unmanaged).length;
  return (
    <>
      <div className={`sim-summary ${r.fits ? "sim-summary-ok" : "sim-summary-fail"}`}>
        {moved === 0
          ? "Nothing to move — this node runs no evictable pods."
          : r.fits
            ? `All ${moved} evictable pod${moved === 1 ? "" : "s"} fit on the remaining ${r.remainingNodes} node${r.remainingNodes === 1 ? "" : "s"}.`
            : `${r.pending.length} of ${moved} pods would go Pending — the cluster lacks headroom.`}
        {unmanaged > 0 && ` ${unmanaged} naked pod${unmanaged === 1 ? " has" : "s have"} no controller and would not be recreated.`}
      </div>

      {r.pending.length > 0 && (
        <div className="sim-group">
          <div className="ov-section-title">Would go Pending · {r.pending.length}</div>
          <div className="pod-rows">
            {r.pending.map(p => (
              <PodLine key={`${p.namespace}/${p.pod}`} p={p} memUnit={memUnit} fmtMem={fmtMem}>
                <div className="sim-reasons">
                  <span>
                    {r.remainingNodes === 0
                      ? "no other nodes in the cluster"
                      : `0/${r.remainingNodes} nodes available: ` +
                        p.reasons.map(x => `${x.nodes} ${reasonLabel(x.slug)}`).join(", ")}
                  </span>
                  {p.unmanaged && <span className="sim-tag">naked pod</span>}
                </div>
              </PodLine>
            ))}
          </div>
        </div>
      )}

      {r.placements.length > 0 && (
        <div className="sim-group">
          <div className="ov-section-title">Rescheduled · {r.placements.length}</div>
          <div className="pod-rows">
            {r.placements.map(p => (
              <PodLine key={`${p.namespace}/${p.pod}`} p={p} memUnit={memUnit} fmtMem={fmtMem}>
                <div className="sim-reasons sim-reasons-ok">
                  <span>→ {p.to}</span>
                  {p.unmanaged && <span className="sim-tag" title="No controller owns it: the drain deletes it for good">naked pod</span>}
                </div>
              </PodLine>
            ))}
          </div>
        </div>
      )}

      {r.skipped.length > 0 && (
        <div className="sim-group">
          <div className="ov-section-title">Stays on the node · {r.skipped.length}</div>
          <div className="sim-skipped">
            {r.skipped.map(s => (
              <div key={`${s.namespace}/${s.pod}`} className="taint-row">
                <code>{s.namespace}/{s.pod}</code>
                <span className="taint-effect">{SKIP_REASONS[s.reason] || s.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <SimCaveat />
    </>
  );
}

window.k8sSimulate = {
  REASONS, reasonLabel, parseSelector, parseTolerations, fitSimulation, drainSimulation,
  FitModal, DrainButton, DrainReport,
};
