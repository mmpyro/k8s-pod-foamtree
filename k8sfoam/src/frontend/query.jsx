// Query-bar grammar: parser + matchers. Pure data in, pure data out — no React,
// no DOM — so the filter semantics can be reasoned about on their own.
//
// Whitespace-separated tokens, all ANDed together:
//   ns:<name>              pod namespace
//   node:<glob>            node name, "*" matches any run of characters
//   qos:<class>            Guaranteed | Burstable | BestEffort (case-insensitive)
//   has:init-containers    pod declares at least one init container
//   key=value              label equality
//   key!=value             label inequality (a missing label counts as unequal)
//   <text>                 case-insensitive substring of the pod name
//
// Double quotes keep whitespace inside a value (app="my app") and force a whole
// token to be read as literal text ("web:1"). A malformed token is reported in
// `errors` instead of throwing, so a half-typed query can never blank the view.

const QOS_CLASSES = ["guaranteed", "burstable", "besteffort"];
const HAS_FIELDS = ["init-containers"];
const FILTER_PREFIXES = ["ns", "node", "qos", "has"];

// Shown by the query bar's hint popover — kept next to the grammar it documents.
const TOKEN_HINTS = [
  { form: "ns:kube-system", desc: "namespace" },
  { form: "node:worker-*", desc: "node name glob" },
  { form: "qos:BestEffort", desc: "QoS class" },
  { form: "has:init-containers", desc: "pods with init containers" },
  { form: "app=frontend", desc: "label equals" },
  { form: "env!=prod", desc: "label differs" },
  { form: "nginx", desc: "pod name contains" },
];

// Split on whitespace, honouring double-quoted runs. `quoted` marks tokens that
// opened with a quote — those bypass the grammar and match as literal text.
function tokenize(input) {
  const tokens = [];
  let value = "";
  let started = false;
  let quoted = false;
  let inQuotes = false;

  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (ch === '"') {
      if (!started) quoted = true;
      started = true;
      inQuotes = !inQuotes;
      continue;
    }
    if (!inQuotes && /\s/.test(ch)) {
      if (started) tokens.push({ text: value, quoted });
      value = ""; started = false; quoted = false;
      continue;
    }
    value += ch;
    started = true;
  }
  if (started) tokens.push({ text: value, quoted });
  return { tokens, unterminated: inQuotes };
}

// Glob → anchored regexp. Everything but "*" is escaped, so a node name with a
// dot (ip-10-0-1-5.ec2.internal) can't act as a wildcard.
function globToRegExp(glob) {
  const escaped = glob.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`, "i");
}

function parseFilter(prefix, value, raw) {
  if (!value) return { error: { token: raw, message: `${prefix}: needs a value` } };
  if (prefix === "ns") return { term: { kind: "ns", value: value.toLowerCase() } };
  if (prefix === "node") return { term: { kind: "node", value, regex: globToRegExp(value) } };
  if (prefix === "qos") {
    const qos = value.toLowerCase();
    if (QOS_CLASSES.indexOf(qos) === -1) {
      return { error: { token: raw, message: "unknown QoS class — use Guaranteed, Burstable or BestEffort" } };
    }
    return { term: { kind: "qos", value: qos } };
  }
  const field = value.toLowerCase();
  if (HAS_FIELDS.indexOf(field) === -1) {
    return { error: { token: raw, message: `unknown has: field — use ${HAS_FIELDS.join(", ")}` } };
  }
  return { term: { kind: "has", value: field } };
}

function parseToken(token) {
  const raw = token.text;
  if (token.quoted) return { term: { kind: "text", value: raw.toLowerCase() } };

  // "!=" first: it also contains "=", so a later "=" split would mangle it.
  const neq = raw.indexOf("!=");
  if (neq !== -1) return parseLabel(raw.slice(0, neq), raw.slice(neq + 2), raw, true);

  // A filter prefix is a bare word before ":" — "app=ns:x" can't match it.
  const filter = raw.match(/^([A-Za-z]+):([\s\S]*)$/);
  if (filter && FILTER_PREFIXES.indexOf(filter[1].toLowerCase()) !== -1) {
    return parseFilter(filter[1].toLowerCase(), filter[2], raw);
  }

  const eq = raw.indexOf("=");
  if (eq !== -1) return parseLabel(raw.slice(0, eq), raw.slice(eq + 1), raw, false);

  if (raw.indexOf(":") !== -1) {
    return { error: { token: raw, message: `unknown filter — use ${FILTER_PREFIXES.map(p => `${p}:`).join(", ")}` } };
  }
  return { term: { kind: "text", value: raw.toLowerCase() } };
}

function parseLabel(key, value, raw, negate) {
  if (!key) return { error: { token: raw, message: "label selector needs a key" } };
  if (!value) return { error: { token: raw, message: "label selector needs a value" } };
  return { term: { kind: "label", key, value, negate } };
}

// → { terms, errors }. Both are always arrays; errors never stops parsing the
// remaining tokens, so every problem in a query is reported at once.
function parseQuery(input) {
  const terms = [];
  const errors = [];
  const scanned = tokenize(input || "");

  if (scanned.unterminated) errors.push({ token: '"', message: "unterminated quoted value" });
  for (const token of scanned.tokens) {
    const parsed = parseToken(token);
    if (parsed.error) errors.push(parsed.error);
    else terms.push(parsed.term);
  }
  return { terms, errors };
}

function labelValue(pod, key) {
  const labels = pod.labels || {};
  return Object.prototype.hasOwnProperty.call(labels, key) ? labels[key] : undefined;
}

function termMatchesPod(term, pod, nodeName) {
  if (term.kind === "ns") return String(pod.namespace || "").toLowerCase() === term.value;
  if (term.kind === "node") return term.regex.test(nodeName || "");
  if (term.kind === "qos") return String(pod.qos || "").toLowerCase() === term.value;
  if (term.kind === "has") return !!pod.hasInit;
  if (term.kind === "label") {
    const value = labelValue(pod, term.key);
    return term.negate ? value !== term.value : value === term.value;
  }
  if (term.kind === "text") return String(pod.name || "").toLowerCase().indexOf(term.value) !== -1;
  return false;
}

// Every term must hold — an empty term list matches everything.
function podMatches(pod, parsed, nodeName) {
  return parsed.terms.every(term => termMatchesPod(term, pod, nodeName));
}

// Node-scoped view of the same query: only node: globs can rule a node out, so
// a node keeps rendering (dimmed) rather than disappearing.
function nodeMatches(nodeName, parsed) {
  return parsed.terms.every(term => term.kind !== "node" || term.regex.test(nodeName || ""));
}

window.k8sQuery = { parseQuery, podMatches, nodeMatches, tokenize, globToRegExp, TOKEN_HINTS };
