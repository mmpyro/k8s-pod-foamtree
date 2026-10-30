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

// `workload` is { namespace, kind, name }, the owner the backend resolves pods to.
const workloadPath = w => [w.namespace, w.kind, w.name].map(encodeURIComponent).join("/");

const postJson = (url, body) => fetch(url, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
}).then(readJson);

// `extraNodes` is the hypothetical nodes' [{ template | shape, count }]; empty runs on the cluster as it is.
const serverApi = {
  isStatic: false,
  contexts: () => fetch("/contexts").then(readJson),
  resources: ctx => fetch(`/resources${contextParam(ctx)}`).then(readJson),
  fit: (ctx, spec) => postJson(`/simulate/fit${contextParam(ctx)}`, spec),
  drain: (ctx, nodeName, extraNodes = []) => {
    const url = `/simulate/drain/${encodeURIComponent(nodeName)}${contextParam(ctx)}`;
    return extraNodes.length ? postJson(url, { extraNodes }) : fetch(url).then(readJson);
  },
  pending: (ctx, extraNodes = []) => postJson(`/simulate/pending${contextParam(ctx)}`, { extraNodes }),
  consolidation: ctx => fetch(`/simulate/consolidation${contextParam(ctx)}`).then(readJson),
  replicas: (ctx, workload, count) => fetch(
    `/simulate/replicas/${workloadPath(workload)}?count=${count}${ctx ? `&context=${encodeURIComponent(ctx)}` : ""}`,
  ).then(readJson),
  // null when the server has no stream; the caller then polls.
  events: ctx => new EventSource(`/events${contextParam(ctx)}`),
  fitPresets: () => Promise.resolve([]),
};

// Shown when a fit is asked for a pod no file holds an answer for.
const CUSTOM_FIT = "The demo only has answers for the presets. To try any pod, run k8sfoams --synthetic locally.";
// Shown when a simulation asks for nodes the cluster does not have.
const EXTRA_NODES = "The demo has no answers with hypothetical nodes. To add nodes, run k8sfoams --synthetic locally.";
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
  };
}

const STATIC = window.K8SFOAM_STATIC;

window.k8sApi = STATIC ? staticApi(STATIC.data || "data/") : serverApi;
