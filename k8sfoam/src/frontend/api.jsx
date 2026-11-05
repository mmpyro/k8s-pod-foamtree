// Where the dashboard's data comes from. Normally the k8sfoams server's HTTP
// API. On the docs site the exported index.html sets window.K8SFOAM_STATIC,
// and the same calls read JSON files holding what each endpoint answered for
// the synthetic clusters (python -m k8sfoam.src.synthetic.export).

async function readJson(res) {
  if (res.ok) return res.json();
  // Flask returns the reason as plain text on 400/404/500.
  throw new Error((await res.text()) || `status ${res.status}`);
}

const contextParam = ctx => (ctx ? `?context=${encodeURIComponent(ctx)}` : "");

// A server on a non-loopback host prints http://host:port/#token=…. A fragment
// never reaches the server or its logs; it is moved to sessionStorage and taken
// out of the address bar, so urlstate and shared links never see it.
const TOKEN_KEY = "k8sfoamToken";
const TOKEN = (() => {
  const params = new URLSearchParams(location.hash.slice(1));
  const fromUrl = params.get("token");
  try {
    if (!fromUrl) return sessionStorage.getItem(TOKEN_KEY);
    sessionStorage.setItem(TOKEN_KEY, fromUrl);
  } catch (e) { /* storage blocked: the token lasts until a reload */ }
  params.delete("token");
  const rest = params.toString();
  history.replaceState(null, "", `${location.pathname}${location.search}${rest ? `#${rest}` : ""}`);
  return fromUrl;
})();

const apiFetch = (url, options = {}) => fetch(url, TOKEN
  ? { ...options, headers: { ...options.headers, Authorization: `Bearer ${TOKEN}` } }
  : options);

// `workload` is { namespace, kind, name }, the owner the backend resolves pods to.
const workloadPath = w => [w.namespace, w.kind, w.name].map(encodeURIComponent).join("/");

const postJson = (url, body) => apiFetch(url, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
}).then(readJson);

// `extraNodes` is the hypothetical nodes' [{ template | shape, count }]; empty runs on the cluster as it is.
const serverApi = {
  isStatic: false,
  contexts: () => apiFetch("/contexts").then(readJson),
  resources: ctx => apiFetch(`/resources${contextParam(ctx)}`).then(readJson),
  fit: (ctx, spec) => postJson(`/simulate/fit${contextParam(ctx)}`, spec),
  drain: (ctx, nodeName, extraNodes = []) => {
    const url = `/simulate/drain/${encodeURIComponent(nodeName)}${contextParam(ctx)}`;
    return extraNodes.length ? postJson(url, { extraNodes }) : apiFetch(url).then(readJson);
  },
  pending: (ctx, extraNodes = []) => postJson(`/simulate/pending${contextParam(ctx)}`, { extraNodes }),
  consolidation: ctx => apiFetch(`/simulate/consolidation${contextParam(ctx)}`).then(readJson),
  replicas: (ctx, workload, count) => apiFetch(
    `/simulate/replicas/${workloadPath(workload)}?count=${count}${ctx ? `&context=${encodeURIComponent(ctx)}` : ""}`,
  ).then(readJson),
  // null when the server has no stream; the caller then polls.
  // EventSource cannot send headers, so the stream takes the token as a query parameter.
  events: ctx => new EventSource(`/events${contextParam(ctx)}${TOKEN ? `${ctx ? "&" : "?"}token=${encodeURIComponent(TOKEN)}` : ""}`),
  fitPresets: () => Promise.resolve([]),
  // {enabled, model}; the API key never leaves the server.
  advisorStatus: () => apiFetch("/advisor/status").then(readJson),
  advisorPreview: (ctx, redact) => apiFetch(
    `/advisor/preview${contextParam(ctx)}${ctx ? "&" : "?"}redact=${redact}`,
  ).then(readJson),
  advisor: (ctx, redact) => postJson(`/advisor${contextParam(ctx)}`, { redact }),
  // body: { question, history: [{ role, content }], redact } -> { model, answer, sources }
  assistant: (ctx, body) => postJson(`/assistant${contextParam(ctx)}`, body),
};

// Shown when a fit is asked for a pod no file holds an answer for.
const CUSTOM_FIT = "The demo only has answers for the presets. To try any pod, run k8sfoams --synthetic locally.";
// Shown when a simulation asks for nodes the cluster does not have.
const EXTRA_NODES = "The demo has no answers with hypothetical nodes. To add nodes, run k8sfoams --synthetic locally.";
const ADVISOR_OFF = "The demo has no AI advisor. Run k8sfoams with OPENAI_API_KEY set to try it.";
const withoutExtra = (extraNodes, answer) => ((extraNodes || []).length
  ? Promise.reject(new Error(EXTRA_NODES)) : answer());

// The demo stores one replica run with the most copies the server allows. Each
// copy only sees the ones placed before it, so the run for `count` is its first
// `count` placements. Per-node reasons describe the end of the stored run; they
// are only read when the result does not fit, and then both runs end alike.
function firstReplicas(result, count) {
  if (count >= result.requested) return result;
  const placements = result.placements.slice(0, count);
  const fits = placements.length === count;
  const placed = {};
  for (const node of placements) placed[node] = (placed[node] || 0) + 1;
  return {
    ...result,
    requested: count,
    placed: placements.length,
    fits,
    placements,
    nodes: result.nodes.map(v => ({ ...v, replicas: placed[v.node] || 0 })),
    pending: fits ? [] : result.pending,
  };
}

// Relative URLs, so the site works under any path (/k8s-pod-foamtree/demo/ on GitHub Pages).
function staticApi(base) {
  let list = null;
  const contexts = () => list || (list = fetch(`${base}contexts.json`).then(readJson));
  const file = async (ctx, name, notFound) => {
    const all = await contexts();
    const c = all.find(x => x.context === ctx) || all.find(x => x.active) || all[0];
    if (!c) throw new Error("The demo has no clusters");
    const res = await fetch(`${base}${c.path}/${name}`);
    // A static host answers a missing file with its own HTML page, not a reason.
    if (!res.ok) throw new Error(notFound || `status ${res.status}`);
    return res.json();
  };
  return {
    isStatic: true,
    noExtraNodes: EXTRA_NODES,
    contexts: () => contexts().then(all => all.map(({ context, active }) => ({ context, active }))),
    resources: ctx => file(ctx, "resources.json"),
    fit: (ctx, spec, presetId) => withoutExtra(spec.extraNodes, () => (presetId
      ? file(ctx, `fit/${encodeURIComponent(presetId)}.json`)
      : Promise.reject(new Error(CUSTOM_FIT)))),
    drain: (ctx, nodeName, extraNodes) => withoutExtra(extraNodes,
      () => file(ctx, `drain/${encodeURIComponent(nodeName)}.json`, `Node ${nodeName} not found`)),
    pending: (ctx, extraNodes) => withoutExtra(extraNodes, () => file(ctx, "pending.json")),
    consolidation: ctx => file(ctx, "consolidation.json"),
    replicas: (ctx, workload, count) => file(ctx, `replicas/${workloadPath(workload)}.json`,
      `Workload ${workload.namespace}/${workload.kind}/${workload.name} not found`)
      .then(result => firstReplicas(result, count)),
    events: () => null,
    fitPresets: () => fetch(`${base}fit-presets.json`).then(readJson),
    // No server, no LLM: the advisor panel stays hidden in the demo.
    advisorStatus: () => Promise.resolve({ enabled: false, model: null }),
    advisorPreview: () => Promise.reject(new Error(ADVISOR_OFF)),
    advisor: () => Promise.reject(new Error(ADVISOR_OFF)),
    assistant: () => Promise.reject(new Error(ADVISOR_OFF)),
  };
}

const STATIC = window.K8SFOAM_STATIC;

window.k8sApi = STATIC ? staticApi(STATIC.data || "data/") : serverApi;
