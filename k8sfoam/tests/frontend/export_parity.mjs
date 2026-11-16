// The browser's JSON / CSV export, run in Node on a /resources payload, for test_report.py's parity test.
// usage: node export_parity.mjs <payload.json> <context> <k8sfoams version> <cli report.json>
// Prints {json, csv, reloaded}; `reloaded` is the CLI report opened as a snapshot and exported again.
// The .jsx files aren't modules, so the export functions are cut out of the sources verbatim.
import { readFileSync } from "node:fs";

const [payloadPath, context, version, cliJson] = process.argv.slice(2);
const src = f => readFileSync(new URL(`../../src/frontend/${f}`, import.meta.url), "utf8");

function extractFn(text, file, name) {
  const start = text.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`no function ${name} in ${file}`);
  let i = text.indexOf("{", text.indexOf(")", start)), depth = 0;
  for (; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return text.slice(start, i + 1);
  }
}
function extract(text, file, re) {
  const m = text.match(re);
  if (!m) throw new Error(`no ${re} in ${file}`);
  return m;
}

globalThis.window = {};
await import("../../src/frontend/snapshot.js");
const app = src("app.jsx"), exp = src("export.jsx"), res = src("resources.jsx");
const fns = (text, file, names) => names.map(name => extractFn(text, file, name));
const code = [
  ...fns(res, "resources.jsx", ["isBytes", "detectResources"]),
  "function statusOf() { return null; }",  // only feeds n.status, which the exports never read
  ...fns(app, "app.jsx", ["kbToMib", "extOf", "toNode"]),
  `function computeTotals(nodes) {${extract(app, "app.jsx", /const totals = useMemo\(\(\) => \{([\s\S]*?)\}, \[nodes\]\);/)[1]}}`,
  extract(exp, "export.jsx", /const CSV_COLUMNS = \[[\s\S]*?\];/)[0],
  extract(exp, "export.jsx", /const topo = .*;/)[0],
  ...fns(exp, "export.jsx", ["extColumn", "csvColumns", "podRows", "csvCell", "toCsv", "reportJson"]),
  "return { isBytes, toNode, computeTotals, podRows, csvColumns, toCsv, reportJson };",
].join("\n");
const k = new Function("window", "APP_VERSION", code)(window, version);

// app.jsx: payload -> nodes -> export with the UI defaults (CPU metric, no query).
function exportOf(payload) {
  const nodes = (payload.nodes || []).map(k.toNode);
  const json = k.reportJson({ nodes, totals: k.computeTotals(nodes), context, metric: "cpu", query: "", match: null,
                              pendingPods: payload.pendingPods, pricing: payload.pricing });
  return { nodes, json: json + "\n" };
}

const { nodes, json } = exportOf(JSON.parse(readFileSync(payloadPath, "utf8")));
const reloaded = exportOf(window.k8sSnapshot.parseReport(readFileSync(cliJson, "utf8"), k.isBytes).payload).json;
process.stdout.write(JSON.stringify({ json, csv: k.toCsv(k.podRows(nodes, null, context), k.csvColumns(nodes)), reloaded }));
