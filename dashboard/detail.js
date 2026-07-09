import { runDetailEndpoints } from "./config.js";

const $ = (s) => document.querySelector(s);
const pct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");
const runId = new URLSearchParams(location.search).get("run");

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

async function fetchDetail(id) {
  for (const url of runDetailEndpoints(id)) {
    try {
      const r = await fetch(url + "?t=" + Date.now(), { cache: "no-store" });
      if (r.ok) return await r.json();
    } catch (_) { /* try next */ }
  }
  return null;
}

function renderSummary(run) {
  const won = run.challenger_won;
  const cards = [
    ["Challenger", "uid " + (run.challenger_uid ?? "—")],
    ["King", "uid " + (run.king_uid ?? "—")],
    ["Composite", `${(run.composite_challenger ?? 0).toFixed(3)} vs ${(run.composite_king ?? 0).toFixed(3)}`],
    ["Margin", pct(run.win_margin)],
    ["Scored", `${run.scored_samples ?? "—"}/${run.total_samples ?? "—"}`],
    ["Result", run.state === "failed" ? "failed" : (won ? "CROWNED" : "held")],
  ];
  const w = $("#summary"); w.innerHTML = "";
  for (const [k, v] of cards) {
    const c = el("div", "card");
    c.append(el("div", "k", k), el("div", "v", String(v)));
    if (k === "Result") c.querySelector(".v").style.color = won ? "var(--win)" : "var(--lose)";
    w.append(c);
  }
}

function facetPair(f) {
  if (!f) return "—";
  const k = (f.king ?? 0).toFixed(2), c = (f.challenger ?? 0).toFixed(2);
  const winCls = (f.challenger ?? 0) > (f.king ?? 0) ? ' style="color:var(--win)"' : "";
  return `<span class="muted">${k}</span> / <b${winCls}>${c}</b>`;
}

function traitChips(traits) {
  const keys = Object.keys(traits || {});
  if (!keys.length) return "—";
  return keys.map(k => `<span class="chip">${k}: ${traits[k]}</span>`).join(" ");
}

function renderSamples(detail) {
  const body = $("#samples tbody"); body.innerHTML = "";
  const samples = detail.samples || [];
  if (!samples.length) { body.append(el("tr", null, `<td colspan="7" class="empty">No per-sample records.</td>`)); return; }
  for (const s of samples) {
    const gate = s.challenger_intelligible
      ? `<span class="badge win">pass</span>` : `<span class="badge lose">fail</span>`;
    body.append(el("tr", null,
      `<td class="mono">${s.sample_id}</td>
       <td class="text">${s.target_text || "—"}</td>
       <td class="chips">${traitChips(s.traits)}</td>
       <td class="mono">${facetPair(s.facets?.intelligibility)}</td>
       <td class="mono">${facetPair(s.facets?.adherence)}</td>
       <td class="mono">${facetPair(s.facets?.naturalness)}</td>
       <td>${gate}</td>`));
  }
}

async function load() {
  if (!runId) { $("#subtitle").textContent = "No run id. Open from a duel row."; return; }
  $("#runid").textContent = "run " + runId;
  const detail = await fetchDetail(runId);
  if (!detail) { $("#subtitle").textContent = "Could not load run " + runId; return; }
  renderSummary(detail.run || {});
  renderSamples(detail);
}

$("#theme").addEventListener("click", () => {
  const r = document.documentElement;
  r.dataset.theme = r.dataset.theme === "light" ? "dark" : "light";
});

load();
