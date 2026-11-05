// Node cost vocabulary. The backend hands each node its on-demand list price
// (`price: {hourly, source}` in USD, null when unknown); this file turns it into
// monthly and idle cost, so the focus overlay, the sidebar and the topology
// groups agree.

const HOURS_PER_MONTH = 730;

// Idle is the unrequested share of the node's busier resource: a node full on
// memory is not idle just because its CPU is free.
function nodeCost(node) {
  if (!node.price) return null;
  const monthly = node.price.hourly * HOURS_PER_MONTH;
  const used = Math.min(1, Math.max(node.cpuUsed / (node.cpuCapacity || 1), node.memUsed / (node.memCapacity || 1)));
  return { hourly: node.price.hourly, monthly, idle: monthly * (1 - used), source: node.price.source };
}

function fmtUsd(v, digits = 0) {
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

window.k8sCost = { HOURS_PER_MONTH, nodeCost, fmtUsd };
