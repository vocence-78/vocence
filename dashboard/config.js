// Dashboard data sources. The dashboard is a static site: it reads a JSON snapshot
// published to Hippius by validators (no dashboard API server). Endpoints are tried
// in order; the first that responds wins. Override window.VOCENCE_DATA_URL to point
// at a custom location.
export const POLL_MS = 8000;

export const DATA_ENDPOINTS = (window.VOCENCE_DATA_URL ? [window.VOCENCE_DATA_URL] : []).concat([
  "https://s3.hippius.com/vocence/data/dashboard.json",
  "https://us-east-1.hippius.com/vocence/data/dashboard.json",
  "https://eu-central-1.hippius.com/vocence/data/dashboard.json",
  // local dev fallback
  "./sample-dashboard.json",
]);
