// Scheduling simulators: "Can I fit this pod?", "Simulate drain", the
// consolidation finder and "Can I scale this workload?". The backend runs the
// scheduler filter and sends verdicts with reason slugs; this file is the
// single place that decides how they read and which nodes the map outlines,
// so the modals, the drain report and both views agree.

const { useState, useEffect } = React;
const { isBytes } = window.k8sResources;

// Ordered the way the backend's filter evaluates them.
const REASONS = {
  "cordoned":            { label: "cordoned" },
  "not-ready":           { label: "not ready" },
  "node-selector":       { label: "nodeSelector mismatch" },
  "node-affinity":       { label: "node affinity mismatch" },
  "taint":               { label: "untolerated taint" },
  "insufficient-pods":   { label: "pod limit reached" },
  "insufficient-cpu":    { label: "insufficient CPU" },
  "insufficient-memory": { label: "insufficient memory" },
  "insufficient-extended": { label: "insufficient extended resource" },
  "topology-spread":     { label: "topology spread skew" },
  "pod-affinity":        { label: "pod affinity unmet" },
  "pod-anti-affinity":   { label: "pod anti-affinity conflict" },
};

const SKIP_REASONS = {
  daemonset: "DaemonSet — runs on every node, not evicted",
  static: "static pod — owned by the kubelet, not evicted",
};

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// Why a node cannot go even on its own, in the backend's order.
const BLOCKERS = {
  "control-plane": () => "control-plane node — not part of a node group",
  "pending": n => `${plural(n, "pod")} would go Pending`,
  "pdb": n => `${plural(n, "pod")} held by a PodDisruptionBudget`,
  "unmanaged": n => `${plural(n, "naked pod")} — nothing would recreate ${n === 1 ? "it" : "them"}`,
};

function blockerLabel(b) {
  return (BLOCKERS[b.slug] || (() => b.slug))(b.pods);
}

// What the simulator does not model, shown under every result so a green
// verdict is read as "necessary", not "guaranteed".
const CAVEAT = "Filter phase only: CPU, memory, extended resources (GPUs, ephemeral-storage, hugepages), " +
  "pod count, cordon, readiness, nodeSelector, taints, required node and pod (anti-)affinity and " +
  "DoNotSchedule topology spread; drains honour PodDisruptionBudgets. Volume zones are not simulated.";

// Shown when the server could not list PodDisruptionBudgets (RBAC), so drains ignored them.
const PDB_UNCHECKED = "PodDisruptionBudgets not checked: the server may not list them (RBAC), " +
  "so a real drain may still wait on some pods.";

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

// One required node affinity term, its expressions ANDed:
// `zone in (a,b), disk notin (hdd), gpu exists, !spot, cores gt 4`.
// A bare `key` means exists. Commas inside parentheses separate values.
function splitTopLevel(text) {
  const parts = [];
  let depth = 0, current = "";
  for (const ch of text) {
    if (ch === "(") depth++;
    if (ch === ")") depth = Math.max(0, depth - 1);
    if ((ch === "," || ch === "\n") && depth === 0) { parts.push(current); current = ""; continue; }
    current += ch;
  }
  parts.push(current);
  return parts.map(p => p.trim()).filter(Boolean);
}

const SET_OPS = { in: "In", notin: "NotIn" };
const NUM_OPS = { gt: "Gt", lt: "Lt" };
const EXIST_OPS = { exists: "Exists", doesnotexist: "DoesNotExist" };
function parseNodeAffinity(text) {
  const expressions = [], errors = [];
  for (const tok of splitTopLevel(text)) {
    let m;
    if ((m = tok.match(/^(\S+)\s+(in|notin)\s*\(([^)]*)\)$/i))) {
      const values = m[3].split(",").map(v => v.trim()).filter(Boolean);
      if (!values.length) { errors.push(`"${tok}" lists no values`); continue; }
      expressions.push({ key: m[1], operator: SET_OPS[m[2].toLowerCase()], values });
    } else if ((m = tok.match(/^(\S+)\s+(gt|lt)\s+(-?\d+)$/i))) {
      expressions.push({ key: m[1], operator: NUM_OPS[m[2].toLowerCase()], values: [m[3]] });
    } else if ((m = tok.match(/^(\S+)\s+(exists|doesnotexist)$/i))) {
      expressions.push({ key: m[1], operator: EXIST_OPS[m[2].toLowerCase()], values: [] });
    } else if ((m = tok.match(/^!(\S+)$/))) {
      expressions.push({ key: m[1], operator: "DoesNotExist", values: [] });
    } else if (/^[^\s!()]+$/.test(tok)) {
      expressions.push({ key: tok, operator: "Exists", values: [] });
    } else {
      errors.push(`"${tok}" is not key in (a,b), key notin (a), key exists, !key or key gt N`);
    }
  }
  return { nodeAffinity: expressions.length ? [{ matchExpressions: expressions }] : [], errors };
}

// `zone In (a,b), spot DoesNotExist or ...` — the backend's wording.
function fmtNodeAffinity(terms) {
  const fmt = r => ["In", "NotIn"].includes(r.operator)
    ? `${r.key} ${r.operator} (${r.values.join(",")})`
    : r.values && r.values.length ? `${r.key} ${r.operator} ${r.values[0]}` : `${r.key} ${r.operator}`;
  return terms.map(t => [...(t.matchExpressions || []), ...(t.matchFields || [])].map(fmt).join(", ")).join(" or ");
}

// `app=web per kubernetes.io/hostname; ...` — what each pod affinity term selects, and over which domain.
function fmtPodTerms(terms) {
  return terms.map(t => {
    const s = t.labelSelector;
    const parts = s ? [
      ...Object.entries(s.matchLabels || {}).map(([k, v]) => `${k}=${v}`),
      ...(s.matchExpressions || []).map(r => `${r.key} ${r.operator}${r.values && r.values.length ? ` (${r.values.join(",")})` : ""}`),
    ] : [];
    return `${parts.join(", ") || "any pod"} per ${t.topologyKey}`;
  }).join("; ");
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
  const blocked = (result.blocked || []).length;
  const summary = !result.fits
    ? `drain ${result.node}: ${result.pending.length} pending`
    : `drain ${result.node}: ${moved} pod${moved === 1 ? "" : "s"} fit`;
  return {
    kind: "drain",
    summary: blocked ? `${summary}, ${blocked} held by PDB` : summary,
    ok: result.fits && !blocked,
    verdicts,
  };
}

// Removed nodes read "drained", the ones taking their pods "ok", blocked ones
// "fail" — even when they take pods too, since why a node stays is the answer.
function consolidationSimulation(result) {
  const verdicts = new Map();
  for (const step of result.plan) {
    for (const p of step.placements) verdicts.set(p.to, "ok");
  }
  for (const v of result.nodes) if (!v.removable) verdicts.set(v.node, "fail");
  for (const step of result.plan) verdicts.set(step.node, "drained");
  const n = result.plan.length;
  return {
    kind: "consolidation",
    summary: n ? `${n} of ${result.total} nodes could be removed` : "no node could be removed",
    ok: n > 0,
    verdicts,
  };
}

// Nodes taking copies read "ok". When the workload cannot grow by the whole
// count, the nodes that took none could not take one either: "fail".
function replicasSimulation(result) {
  const verdicts = new Map();
  for (const v of result.nodes) {
    if (v.replicas > 0) verdicts.set(v.node, "ok");
    else if (!result.fits) verdicts.set(v.node, "fail");
  }
  const { name } = result.workload;
  return {
    kind: "replicas",
    summary: result.fits
      ? `${name} +${result.requested}: all fit`
      : `${name} +${result.requested}: ${result.placed} fit, ${result.requested - result.placed} pending`,
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
  const [affinityText, setAffinityText] = useState("");
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
  const { nodeAffinity, errors: affErrors } = parseNodeAffinity(affinityText);
  // Same name=quantity syntax as the selector; quantities are validated server-side.
  const { selector: extended, errors: extErrors } = parseSelector(extendedText);
  const formErrors = [...selErrors, ...affErrors, ...tolErrors, ...extErrors];
  const preset = matchPreset(presets, {
    cpu, memory, extended: extendedText, nodeSelector: selectorText, nodeAffinity: affinityText,
    tolerations: tolerationText,
  });
  const customInDemo = window.k8sApi.isStatic && !preset;

  const applyPreset = (p) => {
    setCpu(p.form.cpu);
    setMemory(p.form.memory);
    setExtendedText(p.form.extended);
    setSelectorText(p.form.nodeSelector);
    setAffinityText(p.form.nodeAffinity || "");
    setTolerationText(p.form.tolerations);
    setError(null);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (formErrors.length) return;
    setBusy(true);
    try {
      const spec = { cpu, memory, extended, nodeSelector: selector, nodeAffinity, tolerations };
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
            <span className="ov-label">Node affinity (required)</span>
            <input value={affinityText} onChange={e => setAffinityText(e.target.value)}
              placeholder="topology.kubernetes.io/zone in (us-east-1a,us-east-1b), !spot, cores gt 4" spellCheck="false" />
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
  const blocked = r.blocked || [];
  return (
    <>
      <div className={`sim-summary ${r.fits && !blocked.length ? "sim-summary-ok" : "sim-summary-fail"}`}>
        {moved === 0
          ? (blocked.length ? "Nothing can be evicted yet." : "Nothing to move — this node runs no evictable pods.")
          : r.fits
            ? `All ${moved} evicted pod${moved === 1 ? "" : "s"} fit on the remaining ${r.remainingNodes} node${r.remainingNodes === 1 ? "" : "s"}.`
            : `${r.pending.length} of ${moved} pods would go Pending — the cluster lacks headroom.`}
        {blocked.length > 0 && ` kubectl drain would wait on ${plural(blocked.length, "pod")} held by a PodDisruptionBudget.`}
        {unmanaged > 0 && ` ${unmanaged} naked pod${unmanaged === 1 ? " has" : "s have"} no controller and would not be recreated.`}
      </div>
      {r.pdbsChecked === false && <div className="sim-note">{PDB_UNCHECKED}</div>}

      {blocked.length > 0 && (
        <div className="sim-group">
          <div className="ov-section-title">Blocked by PDB · {blocked.length}</div>
          <div className="sim-skipped">
            {blocked.map(b => (
              <div key={`${b.namespace}/${b.pod}`} className="taint-row">
                <code>{b.namespace}/{b.pod}</code>
                <span className="taint-effect">
                  {b.pdb} allows {plural(b.disruptionsAllowed, "disruption")}
                  {b.disruptionsAllowed > 0 ? ", used up by this drain" : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

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

/* ─────────── Which nodes could be removed? ─────────── */

// `24 CPU / 96 GiB / 2 nvidia.com/gpu` — what removing the planned nodes gives back.
// Byte-sized extended resources (ephemeral-storage, hugepages) are node disk and
// memory carve-outs, not capacity anyone scales down for, so they stay out.
function fmtFreed(freed, memUnit, fmtMem) {
  const parts = [`${+(freed.cpu / 1000).toFixed(1)} CPU`, `${fmtMem(kbToMib(freed.memory), memUnit, true)} ${memUnit}`];
  for (const [name, amount] of Object.entries(freed.extended).sort()) {
    if (amount && !isBytes(name)) parts.push(`${amount} ${name}`);
  }
  return parts.join(" / ");
}

function NodeLine({ v, tone, memUnit, fmtMem, onClick, children }) {
  return (
    <div className={`pod-row sim-row sim-row-${tone} ${onClick ? "sim-row-click" : ""}`} onClick={onClick}>
      <div className="pod-row-name">
        <code>{v.node}</code>
        {children}
      </div>
      <div className="pod-row-stat" title="Requests of the pods a drain would move">
        <span className="pod-row-num">{(v.cpu / 1000).toFixed(2)}</span>
        <span className="pod-row-unit">cores</span>
      </div>
      <div className="pod-row-stat" title="Requests of the pods a drain would move">
        <span className="pod-row-num">{fmtMem(kbToMib(v.memory), memUnit)}</span>
        <span className="pod-row-unit">{memUnit}</span>
      </div>
    </div>
  );
}

function PlanStep({ index, step, v, memUnit, fmtMem }) {
  const [open, setOpen] = useState(false);
  const moves = step.placements.length;
  return (
    <>
      <NodeLine v={v} tone="ok" memUnit={memUnit} fmtMem={fmtMem} onClick={moves ? () => setOpen(o => !o) : null}>
        <div className="sim-reasons sim-reasons-ok">
          <span className="sim-step">{index + 1}</span>
          <span>{moves ? `${plural(moves, "pod")} move ${open ? "▾" : "▸"}` : "nothing to move"}</span>
          {step.skipped.length > 0 && <span className="sim-muted">{step.skipped.length} DaemonSet/static stay</span>}
        </div>
      </NodeLine>
      {open && (
        <div className="pod-rows sim-nested">
          {step.placements.map(p => (
            <PodLine key={`${p.namespace}/${p.pod}`} p={p} memUnit={memUnit} fmtMem={fmtMem}>
              <div className="sim-reasons sim-reasons-ok"><span>→ {p.to}</span></div>
            </PodLine>
          ))}
        </div>
      )}
    </>
  );
}

function ConsolidationModal({ ctx, memUnit, fmtMem, onSimulation, onClose }) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    window.k8sApi.consolidation(ctx)
      .then(res => {
        if (!live) return;
        setResult(res);
        onSimulation(consolidationSimulation(res));
      })
      .catch(err => live && setError(err.message || String(err)));
    return () => { live = false; };
  }, [ctx]);

  const byNode = result ? new Map(result.nodes.map(v => [v.node, v])) : new Map();
  const conflicts = result ? result.nodes.filter(v => v.removable && !v.inPlan) : [];
  const blocked = result ? result.nodes.filter(v => !v.removable).sort((a, b) => a.node.localeCompare(b.node)) : [];

  return (
    <div className="overlay" onClick={onClose}>
      <div className="overlay-card sim-card" onClick={e => e.stopPropagation()}>
        <div className="overlay-head">
          <div>
            <div className="overlay-title">Which nodes could be removed?</div>
            <div className="overlay-sub">Drain every node, then plan a scale-down one node at a time</div>
          </div>
          <button className="icon-btn" onClick={onClose}><CloseIcon /></button>
        </div>

        <div className="overlay-pods">
          {error && <div className="sim-summary sim-summary-fail">Consolidation finder failed: {error}</div>}
          {!result && !error && <div className="sim-note">Simulating a drain of every node…</div>}

          {result && (
            <>
              <div className={`sim-summary ${result.plan.length ? "sim-summary-ok" : "sim-summary-neutral"}`}>
                {result.plan.length
                  ? `${plural(result.plan.length, "node")} of ${result.total} can go, freeing ${fmtFreed(result.freed, memUnit, fmtMem)}.`
                  : `No node can be removed — every one of the ${result.total} is either needed for headroom or blocked.`}
              </div>
              {result.pdbsChecked === false && <div className="sim-note">{PDB_UNCHECKED}</div>}

              {result.plan.length > 0 && (
                <div className="sim-group">
                  <div className="ov-section-title">Removal plan · {result.plan.length}</div>
                  <div className="pod-rows">
                    {result.plan.map((step, i) => (
                      <PlanStep key={step.node} index={i} step={step} v={byNode.get(step.node)}
                        memUnit={memUnit} fmtMem={fmtMem} />
                    ))}
                  </div>
                </div>
              )}

              {conflicts.length > 0 && (
                <div className="sim-group">
                  <div className="ov-section-title">Removable alone, not together · {conflicts.length}</div>
                  <div className="pod-rows">
                    {conflicts.map(v => (
                      <NodeLine key={v.node} v={v} tone="warn" memUnit={memUnit} fmtMem={fmtMem}>
                        <div className="sim-reasons sim-reasons-warn">
                          <span title={v.conflictWith.join(", ")}>
                            its pods no longer fit after removing {plural(v.conflictWith.length, "planned node")} first
                          </span>
                        </div>
                      </NodeLine>
                    ))}
                  </div>
                </div>
              )}

              {blocked.length > 0 && (
                <div className="sim-group">
                  <div className="ov-section-title">Cannot be removed · {blocked.length}</div>
                  <div className="pod-rows">
                    {blocked.map(v => (
                      <NodeLine key={v.node} v={v} tone="fail" memUnit={memUnit} fmtMem={fmtMem}>
                        <div className="sim-reasons">
                          {v.blockers.map(b => <span key={b.slug}>{blockerLabel(b)}</span>)}
                        </div>
                      </NodeLine>
                    ))}
                  </div>
                </div>
              )}
              <SimCaveat />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─────────── Can I scale this workload? ─────────── */

// Matches the backend's MAX_REPLICAS; the static demo stores runs of this many copies.
const MAX_REPLICAS = 100;
// A DaemonSet runs one pod per matching node and a static pod belongs to its kubelet.
const UNSCALABLE = ["DaemonSet", "Node"];

// The workload a pinned key names, or null when it has no replica count to scale.
// Only owned pods have a "namespace/Kind/name" key; a name-only key is a guess.
function scalableWorkload(key) {
  const parts = (key || "").split("/");
  if (parts.length !== 3 || UNSCALABLE.includes(parts[1])) return null;
  const [namespace, kind, name] = parts;
  return { namespace, kind, name };
}

function fmtToleration(t) {
  if (!t.key) return "*";
  const kv = t.operator === "Exists" ? t.key : `${t.key}=${t.value || ""}`;
  return t.effect ? `${kv}:${t.effect}` : kv;
}

function TemplateLine({ t, memUnit, fmtMem }) {
  const ext = Object.entries(t.extended).sort().map(([name, v]) =>
    `${name}=${isBytes(name) ? `${fmtMem(kbToMib(v), memUnit)} ${memUnit}` : v}`);
  const selector = Object.entries(t.nodeSelector).map(([k, v]) => `${k}=${v}`);
  return (
    <div className="sim-template">
      <span>copies of <code>{t.pod}</code></span>
      <span>requests <code>{(t.cpu / 1000).toFixed(2)} cores · {fmtMem(kbToMib(t.memory), memUnit)} {memUnit}</code></span>
      {ext.length > 0 && <span>extended <code>{ext.join(", ")}</code></span>}
      {selector.length > 0 && <span>nodeSelector <code>{selector.join(", ")}</code></span>}
      {(t.nodeAffinity || []).length > 0 && <span>node affinity <code>{fmtNodeAffinity(t.nodeAffinity)}</code></span>}
      {(t.podAffinity || []).length > 0 && <span>pod affinity <code>{fmtPodTerms(t.podAffinity)}</code></span>}
      {(t.podAntiAffinity || []).length > 0 && <span>pod anti-affinity <code>{fmtPodTerms(t.podAntiAffinity)}</code></span>}
      {(t.topologySpread || []).length > 0 && (
        <span>topology spread <code>{t.topologySpread.map(c => `maxSkew ${c.maxSkew} per ${c.topologyKey}`).join("; ")}</code></span>
      )}
      {t.tolerations.length > 0 && <span>tolerations <code>{t.tolerations.map(fmtToleration).join(", ")}</code></span>}
    </div>
  );
}

function replicasHeadline(r, current) {
  const name = r.workload.name;
  if (r.fits) {
    const lead = r.requested === 1 ? "1 more replica fits" : `All ${r.requested} more replicas fit`;
    return `${lead} — ${name} can scale from ${current} to ${current + r.requested}.`;
  }
  if (r.placed === 0) return `No more replicas fit — replica ${current + 1} of ${name} would stay Pending.`;
  return `Only ${r.placed} of ${r.requested} more replicas fit — ${name} can grow from ${current} to ` +
    `${current + r.placed}, and replica ${current + r.placed + 1} would stay Pending.`;
}

function ReplicasModal({ ctx, workload, current, memUnit, fmtMem, onSimulation, onClose }) {
  // Asking to double the workload answers the common question without typing.
  const initial = Math.min(Math.max(current, 1), MAX_REPLICAS);
  const [countText, setCountText] = useState(String(initial));
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const count = Number(countText);
  const valid = Number.isInteger(count) && count >= 1 && count <= MAX_REPLICAS;

  const run = async (n) => {
    setBusy(true);
    try {
      const res = await window.k8sApi.replicas(ctx, workload, n);
      setResult(res);
      setError(null);
      onSimulation(replicasSimulation(res));
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { run(initial); }, []);

  const submit = (e) => {
    e.preventDefault();
    if (valid) run(count);
  };

  const landing = result
    ? result.nodes.filter(v => v.replicas > 0).sort((a, b) => (b.replicas - a.replicas) || a.node.localeCompare(b.node))
    : [];

  return (
    <div className="overlay" onClick={onClose}>
      <div className="overlay-card sim-card" onClick={e => e.stopPropagation()}>
        <div className="overlay-head">
          <div>
            <div className="overlay-title">Can I scale {workload.name}?</div>
            <div className="overlay-sub">
              {workload.namespace}/{workload.kind} · {plural(current, "replica")} now · copy its pod and place each copy in turn
            </div>
          </div>
          <button className="icon-btn" onClick={onClose}><CloseIcon /></button>
        </div>

        <form className="sim-form" onSubmit={submit}>
          <label className="sim-field">
            <span className="ov-label">More replicas</span>
            <input type="number" min="1" max={MAX_REPLICAS} step="1" value={countText}
              onChange={e => setCountText(e.target.value)} />
          </label>
          <div className="sim-actions">
            {!valid && <span className="sim-error">Enter a whole number from 1 to {MAX_REPLICAS}</span>}
            {error && <span className="sim-error">{error}</span>}
            <button type="submit" className={`btn-primary ${busy ? "spinning" : ""}`} disabled={busy || !valid}>
              {busy ? "Simulating…" : "Simulate"}
            </button>
          </div>
        </form>

        {result && (
          <div className="overlay-pods">
            <TemplateLine t={result.template} memUnit={memUnit} fmtMem={fmtMem} />
            <div className={`sim-summary ${result.fits ? "sim-summary-ok" : "sim-summary-fail"}`}>
              {replicasHeadline(result, current)}
            </div>

            {!result.fits && (
              <div className="sim-group">
                <div className="ov-section-title">Where it stops</div>
                <div className="sim-reasons">
                  <span>
                    {result.total === 0
                      ? "the cluster has no nodes"
                      : `0/${result.total} nodes available: ` +
                        result.pending.map(x => `${x.nodes} ${reasonLabel(x.slug)}`).join(", ")}
                  </span>
                </div>
              </div>
            )}

            {landing.length > 0 && (
              <div className="sim-group">
                <div className="ov-section-title">Where they land · {plural(landing.length, "node")}</div>
                <div className="pod-rows">
                  {landing.map(v => (
                    <div key={v.node} className="pod-row sim-row sim-row-ok">
                      <div className="pod-row-name"><code>{v.node}</code></div>
                      <div className="pod-row-stat">
                        <span className="pod-row-num">+{v.replicas}</span>
                        <span className="pod-row-unit">{v.replicas === 1 ? "replica" : "replicas"}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <SimCaveat />
          </div>
        )}
      </div>
    </div>
  );
}

window.k8sSimulate = {
  REASONS, reasonLabel, parseSelector, parseTolerations, fitSimulation, drainSimulation, consolidationSimulation,
  replicasSimulation, scalableWorkload, FitModal, DrainButton, DrainReport, ConsolidationModal, ReplicasModal,
};
