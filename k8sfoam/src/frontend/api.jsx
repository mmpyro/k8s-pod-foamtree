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

const serverApi = {
  isStatic: false,
  contexts: () => fetch("/contexts").then(readJson),
  resources: ctx => fetch(`/resources${contextParam(ctx)}`).then(readJson),
  fit: (ctx, spec) => fetch(`/simulate/fit${contextParam(ctx)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  }).then(readJson),
  drain: (ctx, nodeName) => fetch(`/simulate/drain/${encodeURIComponent(nodeName)}${contextParam(ctx)}`).then(readJson),
  consolidation: ctx => fetch(`/simulate/consolidation${contextParam(ctx)}`).then(readJson),
  // null when the server has no stream; the caller then polls.
  events: ctx => new EventSource(`/events${contextParam(ctx)}`),
  fitPresets: () => Promise.resolve([]),
};

// Shown when a fit is asked for a pod no file holds an answer for.
const CUSTOM_FIT = "The demo only has answers for the presets. To try any pod, run k8sfoams --synthetic locally.";

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
    contexts: () => contexts().then(all => all.map(({ context, active }) => ({ context, active }))),
    resources: ctx => file(ctx, "resources.json"),
    fit: (ctx, spec, presetId) => (presetId
      ? file(ctx, `fit/${encodeURIComponent(presetId)}.json`)
      : Promise.reject(new Error(CUSTOM_FIT))),
    drain: (ctx, nodeName) => file(ctx, `drain/${encodeURIComponent(nodeName)}.json`, `Node ${nodeName} not found`),
    consolidation: ctx => file(ctx, "consolidation.json"),
    events: () => null,
    fitPresets: () => fetch(`${base}fit-presets.json`).then(readJson),
  };
}

const STATIC = window.K8SFOAM_STATIC;

window.k8sApi = STATIC ? staticApi(STATIC.data || "data/") : serverApi;
