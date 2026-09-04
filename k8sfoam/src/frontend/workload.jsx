// Workload identity derived from a pod name — no backend call needed.
//
// Kubernetes builds pod names by appending generated suffixes to the owner's
// name, so the owner can be recovered by stripping them back off:
//
//   Deployment   web-api-66b6c48dd5-x2vpq  ->  web-api   (rs hash + pod suffix)
//   DaemonSet    kube-proxy-x9j2v          ->  kube-proxy
//   Job          backup-tk4wq              ->  backup
//   CronJob      report-28234560-9jm2t     ->  report    (schedule stamp + suffix)
//   StatefulSet  web-0                     ->  web       (ordinal)
//   static pod   kube-apiserver-cp-node    ->  unchanged
//
// Deliberately conservative: merging two unrelated workloads is worse than
// failing to merge replicas, so anything that is not clearly generated is kept.

// Alphabet used by k8s.io/apimachinery/pkg/util/rand for pod suffixes and
// ReplicaSet hashes. It omits every vowel plus 0, 1 and 3, which is what makes
// "is this token generated?" answerable at all — real English name fragments
// ("redis", "nginx", "proxy") almost always contain a character from outside it.
const RAND_ALPHABET = /^[bcdfghjklmnpqrstvwxz2456789]+$/;

// Pod suffixes are always rand.String(5); ReplicaSet hashes are longer.
function isRandToken(token, minLen, maxLen) {
  return token.length >= minLen && token.length <= maxLen && RAND_ALPHABET.test(token);
}

// StatefulSet ordinals count up from 0 and stay small, so cap the width and
// reject leading zeros — that keeps node names ("cp-01") and cronjob schedule
// stamps ("28234560") out of this branch.
function isOrdinal(token) {
  return /^(0|[1-9]\d{0,2})$/.test(token);
}

function workloadKey(podName) {
  if (!podName) return "";
  const parts = podName.split("-");
  if (parts.length < 2) return podName;

  const last = parts[parts.length - 1];
  if (isOrdinal(last)) return parts.slice(0, -1).join("-");

  if (isRandToken(last, 5, 5)) {
    const prev = parts[parts.length - 2];
    // ReplicaSet hash, or a CronJob schedule stamp (minutes since epoch, 6+
    // digits) — both are generated, so the real owner sits one token further up.
    const generatedMid = isRandToken(prev, 5, 10) || /^\d{6,}$/.test(prev);
    if (parts.length > 2 && generatedMid) return parts.slice(0, -2).join("-");
    return parts.slice(0, -1).join("-");
  }

  return podName;
}

window.k8sWorkload = { workloadKey };
