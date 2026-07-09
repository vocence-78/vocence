// Dashboard data sources. The dashboard is a static site: it reads JSON snapshots
// published to Hippius by validators (no dashboard API server). Endpoints are tried
// in order; the first that responds wins. Override window.VOCENCE_DATA_URL for the
// main snapshot, or window.VOCENCE_HOSTS (array) for the Hippius mirrors.
export const POLL_MS = 8000;

const HOSTS = (window.VOCENCE_HOSTS || [
  "https://s3.hippius.com/vocence",
  "https://us-east-1.hippius.com/vocence",
  "https://eu-central-1.hippius.com/vocence",
]);

export const DATA_ENDPOINTS = (window.VOCENCE_DATA_URL ? [window.VOCENCE_DATA_URL] : [])
  .concat(HOSTS.map(h => h + "/data/dashboard.json"))
  .concat(["./sample-dashboard.json"]);  // local dev fallback

// Per-duel detail: data/runs/<run_id>.json
export const runDetailEndpoints = (runId) =>
  HOSTS.map(h => h + "/data/runs/" + runId + ".json").concat(["./sample-run.json"]);
