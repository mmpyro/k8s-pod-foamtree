// View state mirrored into location.hash, so a link reproduces what the sender
// saw: #ctx=prod&view=3d&groupBy=zone&q=ns%3Apayments. Pure string handling —
// validating the values against the views, modes and contexts is app.jsx's job.

// State field → hash key.
const HASH_KEYS = {
  ctx: "ctx",
  view: "view",
  metric: "metric",
  colorBy: "colorBy",
  groupBy: "groupBy",
  memUnit: "memUnit",
  query: "q",
  workload: "wl",
};

// Raw strings per state field; a key absent from the hash is left undefined.
function readHash(hash) {
  const params = new URLSearchParams((hash || "").replace(/^#/, ""));
  const out = {};
  for (const [field, key] of Object.entries(HASH_KEYS)) {
    if (params.has(key)) out[field] = params.get(key);
  }
  return out;
}

// "#…" for `state`. Fields equal to their default are left out to keep links
// short; a field without a default (ctx) is always written when set.
function writeHash(state, defaults) {
  const params = new URLSearchParams();
  for (const [field, key] of Object.entries(HASH_KEYS)) {
    const v = state[field];
    if (v === undefined || v === null || v === "" || v === defaults[field]) continue;
    params.set(key, v);
  }
  const s = params.toString();
  return s ? `#${s}` : "";
}

window.k8sUrlState = { readHash, writeHash };
