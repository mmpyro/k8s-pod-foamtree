// AI cluster advisor: a consent step showing exactly what would be sent, then
// the model's recommendations as cards. App owns the request (it outlives the
// modal, local models take minutes); this file only renders.

const { CloseIcon } = window.k8sSimulate;

// Model severities onto the dashboard's danger / warn / info scale.
const SEVERITY_INFO = {
  high: { sev: "danger", label: "High" },
  medium: { sev: "warn", label: "Medium" },
  low: { sev: "info", label: "Low" },
};
const WORKLOAD_KINDS = new Set(["Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob"]);

// What Highlight does for an affected object: pin a workload (workload.jsx key) or set a query.
function highlightOf(a) {
  if (WORKLOAD_KINDS.has(a.kind) && a.namespace) return { workload: `${a.namespace}/${a.kind}/${a.name}` };
  if (a.kind === "Node") return { query: `node:${a.name}` };
  if (a.kind === "NodePool") return { query: `pool:${a.name}` };
  if (a.kind === "Namespace") return { query: `ns:${a.name}` };
  if (a.namespace) return { query: `ns:${a.namespace}` };
  return null;
}

// The user message is "Cluster summary:\n<json>"; pretty-print the JSON so the preview is readable.
function messageText(m) {
  const [lead, json] = m.content.split(/\n(.*)/s);
  try { return `${lead}\n${JSON.stringify(JSON.parse(json), null, 2)}`; } catch { return m.content; }
}

function RedactToggle({ redact, setRedact, disabled }) {
  return (
    <label className="advisor-redact" title="Replace node, pool, namespace and workload names with placeholders; answers are mapped back">
      <input type="checkbox" checked={redact} disabled={disabled} onChange={e => setRedact(e.target.checked)} />
      Redact names
    </label>
  );
}

function Consent({ ctx, model, redact, setRedact, onAsk, onClose }) {
  const [preview, setPreview] = React.useState(null);
  const [error, setError] = React.useState(null);
  React.useEffect(() => {
    let live = true;
    setPreview(null);
    window.k8sApi.advisorPreview(ctx, redact)
      .then(p => live && (setPreview(p), setError(null)))
      .catch(err => live && setError(err.message || String(err)));
    return () => { live = false; };
  }, [ctx, redact]);

  return (
    <>
      <div className="advisor-intro">
        Nothing has been sent yet. <b>Send</b> posts the messages below to <b>{model}</b>, the model the server is
        configured with. They summarise this cluster's requests, health and audit findings; labels are never included.
      </div>
      <div className="advisor-pre">
        {!preview && !error && <div className="sim-note">Building the summary…</div>}
        {preview && preview.messages.map((m, i) => (
          <div key={i}>
            <div className="advisor-role">{m.role}</div>
            <pre>{messageText(m)}</pre>
          </div>
        ))}
      </div>
      <div className="sim-actions advisor-actions">
        {error && <span className="sim-error">{error}</span>}
        <RedactToggle redact={redact} setRedact={setRedact} />
        <button className="sim-preset" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!preview} onClick={onAsk}>Send to {model}</button>
      </div>
    </>
  );
}

function RecommendationCard({ r, onCopy, onHighlight }) {
  const [copied, setCopied] = React.useState(null);
  const info = SEVERITY_INFO[r.severity] || SEVERITY_INFO.low;
  const copy = () => onCopy(r.suggestedChange).then(() => setCopied("Copied"), err => setCopied(err.message));
  return (
    <div className="advisor-card">
      <div className="advisor-card-head">
        <span className={`advisor-sev sev-${info.sev}`}>{info.label}</span>
        <span className="advisor-title">{r.title}</span>
        <span className="advisor-cat">{r.category}</span>
      </div>
      <div className="advisor-why">{r.rationale}</div>
      {r.affected.length > 0 && (
        <div className="advisor-affected">
          {r.affected.map((a, i) => {
            const target = highlightOf(a);
            const label = `${a.kind} ${a.namespace ? `${a.namespace}/` : ""}${a.name}`;
            return target
              ? <button key={i} className="advisor-chip" title="Highlight on the map" onClick={() => onHighlight(target)}>{label}</button>
              : <span key={i} className="advisor-chip">{label}</span>;
          })}
        </div>
      )}
      {r.suggestedChange && (
        <div className="advisor-change">
          <pre>{r.suggestedChange}</pre>
          <button className="sim-preset" onClick={copy}>{copied || "Copy"}</button>
        </div>
      )}
    </div>
  );
}

// `advice` is App's { ctx, busy, error, result } for the current context, or null before the first ask.
function AdvisorModal({ ctx, model, consented, redact, setRedact, advice, onAsk, onCopy, onHighlight, onClose }) {
  const result = advice && advice.result;
  const busy = !!(advice && advice.busy);
  return (
    <div className="overlay" onClick={onClose}>
      <div className="overlay-card sim-card advisor-modal" onClick={e => e.stopPropagation()}>
        <div className="overlay-head">
          <div>
            <div className="overlay-title">AI Advisor</div>
            <div className="overlay-sub">Recommendations from {model}, based on this cluster's requests and findings</div>
          </div>
          <button className="icon-btn" onClick={onClose}><CloseIcon /></button>
        </div>
        {!consented ? (
          <Consent ctx={ctx} model={model} redact={redact} setRedact={setRedact} onAsk={onAsk} onClose={onClose} />
        ) : (
          <>
            <div className="advisor-banner">AI-generated — verify before applying. The audit and health data stay the source of truth.</div>
            <div className="sim-actions advisor-actions">
              {busy && (
                <span className="sim-note advisor-busy" role="status">
                  <span className="advisor-spinner" aria-hidden="true" />
                  Asking {model}… a local model can take several minutes.
                </span>
              )}
              {advice && advice.error && <span className="sim-error">{advice.error}</span>}
              {result && !busy && (
                <span className="sim-note">{result.recommendations.length} recommendations{result.cached ? " · cached answer" : ""}</span>
              )}
              <RedactToggle redact={redact} setRedact={setRedact} disabled={busy} />
              <button className={`btn-primary ${busy ? "spinning" : ""}`} disabled={busy} onClick={onAsk}>Ask again</button>
            </div>
            {result && !busy && (
              <div className="advisor-cards">
                {result.recommendations.length === 0 && <div className="audit-clean">The model had no recommendations.</div>}
                {result.recommendations.map((r, i) => (
                  <RecommendationCard key={i} r={r} onCopy={onCopy} onHighlight={onHighlight} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// Model answers are markdown; without a markdown library, fenced code goes in <pre> and the rest stays as text.
function AnswerText({ text }) {
  return text.split(/```[^\n]*\n?/).map((part, i) => (i % 2
    ? <pre key={i} className="assistant-code">{part.replace(/\n$/, "")}</pre>
    : part.trim() && <div key={i} className="assistant-text">{part.trim()}</div>));
}

// `chat` is App's { ctx, turns: [{ role, content, sources }], busy, error } for the current context, or null.
function AssistantModal({ model, redact, setRedact, chat, onSend, onClear, onClose }) {
  const [draft, setDraft] = React.useState("");
  const endRef = React.useRef(null);
  const turns = chat ? chat.turns : [];
  const busy = !!(chat && chat.busy);
  React.useEffect(() => { endRef.current && endRef.current.scrollIntoView({ block: "end" }); }, [turns.length, busy]);
  const send = () => {
    if (!draft.trim() || busy) return;
    onSend(draft.trim());
    setDraft("");
  };
  return (
    <div className="overlay" onClick={onClose}>
      <div className="overlay-card sim-card advisor-modal" onClick={e => e.stopPropagation()}>
        <div className="overlay-head">
          <div>
            <div className="overlay-title">Ask a question</div>
            <div className="overlay-sub">About using k8sfoams or about this cluster · answered by {model}</div>
          </div>
          <button className="icon-btn" onClick={onClose}><CloseIcon /></button>
        </div>
        <div className="assistant-log" aria-live="polite">
          {turns.length === 0 && (
            <div className="sim-note">Try "How do I simulate draining a node?" or "Which namespace requests the most CPU?"</div>
          )}
          {turns.map((t, i) => (
            <div key={i} className={`assistant-turn assistant-${t.role}`}>
              {t.role === "user" ? <div className="assistant-text">{t.content}</div> : <AnswerText text={t.content} />}
              {t.sources && t.sources.length > 0 && <div className="assistant-sources">Docs: {t.sources.join(" · ")}</div>}
            </div>
          ))}
          {busy && (
            <span className="sim-note advisor-busy" role="status">
              <span className="advisor-spinner" aria-hidden="true" />
              Asking {model}… a local model can take a few minutes.
            </span>
          )}
          {chat && chat.error && <span className="sim-error">{chat.error}</span>}
          <div ref={endRef} />
        </div>
        <div className="advisor-intro">
          AI-generated — verify before applying. <b>Send</b> posts your question, the earlier turns, the matching docs
          sections and this cluster's summary to <b>{model}</b>; labels are never included.
        </div>
        <div className="assistant-input">
          <textarea value={draft} rows={2} maxLength={2000} placeholder="Ask about k8sfoams or this cluster… (Enter sends, Shift+Enter new line)"
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        </div>
        <div className="sim-actions advisor-actions">
          <RedactToggle redact={redact} setRedact={setRedact} disabled={busy} />
          <button className="sim-preset" disabled={busy || turns.length === 0} onClick={onClear}>Clear</button>
          <button className="btn-primary" disabled={busy || !draft.trim()} onClick={send}>
            {busy ? <><span className="advisor-spinner" aria-hidden="true" />Asking…</> : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}

window.k8sAdvisor = { AdvisorModal, AssistantModal, SEVERITY_INFO, highlightOf };
