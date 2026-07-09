import { DATA_ENDPOINTS, POLL_MS } from "./config.js";

const $ = (sel) => document.querySelector(sel);
const pct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");
const shortHk = (hk) => (hk && hk.length > 12 ? hk.slice(0, 6) + "…" + hk.slice(-4) : hk || "—");

async function fetchDashboard() {
  for (const url of DATA_ENDPOINTS) {
    try {
      const r = await fetch(url + (url.includes("?") ? "" : "?t=" + Date.now()), { cache: "no-store" });
      if (r.ok) return await r.json();
    } catch (_) { /* try next */ }
  }
  return null;
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

function renderStats(d) {
  const s = d.stats || {};
  const cards = [
    ["Court", (d.reign || []).length + " / " + (d.spec?.court_size ?? 5)],
    ["Eval runs", s.eval_runs ?? 0],
    ["Coronations", s.coronations ?? 0],
    ["Queue", s.queue_depth ?? (d.queue || []).length],
    ["Win margin", pct(d.spec?.win_margin)],
  ];
  const wrap = $("#stats"); wrap.innerHTML = "";
  for (const [k, v] of cards) {
    const c = el("div", "card");
    c.append(el("div", "k", k), el("div", "v", String(v)));
    wrap.append(c);
  }
}

function renderReign(d) {
  const wrap = $("#reign"); wrap.innerHTML = "";
  const reign = d.reign || [];
  if (!reign.length) { wrap.append(el("div", "empty", "No active reign — emissions burn until the first coronation.")); return; }
  for (const m of reign) {
    const k = el("div", "king" + (m.slot === 1 ? " lead" : ""));
    k.append(el("div", "slot", (m.slot === 1 ? "👑 " : "#") + m.slot));
    k.append(el("div", "uid", "uid " + m.uid));
    k.append(el("div", "hk", shortHk(m.hotkey)));
    k.append(el("div", "repo", m.repo || "—"));
    const wt = el("div", "wt"); wt.append(el("i", null, ""));
    wt.firstChild.style.width = ((m.weight || 0) * 100).toFixed(1) + "%";
    k.append(wt, el("div", "wtn", pct(m.weight) + " emissions"));
    wrap.append(k);
  }
}

function facetCell(f) {
  if (!f) return "<td>—</td>";
  const k = Math.round((f.king || 0) * 100), c = Math.round((f.challenger || 0) * 100);
  return `<td><div class="facet mono" title="king ${k}% · challenger ${c}%">
    <div class="bar"><i class="c" style="width:${c}%"></i></div>${c}%</div></td>`;
}

function renderLeaderboard(d) {
  const body = $("#leaderboard tbody"); body.innerHTML = "";
  const rows = d.leaderboard || [];
  if (!rows.length) { body.append(el("tr", null, `<td colspan="8" class="empty">No participants yet.</td>`)); return; }
  for (const e of rows) {
    const status = e.status === "king"
      ? `<span class="badge win">👑 slot ${e.slot}</span>`
      : `<span class="badge">challenger</span>`;
    const best = e.best_composite != null ? e.best_composite.toFixed(3) : "—";
    body.append(el("tr", null,
      `<td class="mono">${e.rank}</td>
       <td class="mono">${e.uid}</td>
       <td class="mono" title="${e.hotkey || ""}">${shortHk(e.hotkey)}</td>
       <td>${status}</td>
       <td class="mono">${e.status === "king" ? pct(e.weight) : "—"}</td>
       <td class="mono">${best}</td>
       <td class="mono">${e.coronations ?? 0}</td>
       <td class="mono">${e.duels ?? 0}</td>`));
  }
}

function renderDuels(d) {
  const body = $("#duels tbody"); body.innerHTML = "";
  const runs = d.eval_runs || [];
  if (!runs.length) { body.append(el("tr", null, `<td colspan="8" class="empty">No duels yet.</td>`)); return; }
  for (const r of runs) {
    const won = r.challenger_won;
    const comp = r.composite_challenger != null
      ? `${r.composite_challenger?.toFixed(3)} <span class="muted">vs</span> ${r.composite_king?.toFixed(3)}`
      : "—";
    const badge = r.state === "failed"
      ? `<span class="badge lose">failed</span>`
      : (won ? `<span class="badge win">CROWNED</span>` : `<span class="badge lose">held</span>`);
    const tr = el("tr", null,
      `<td class="mono">${r.block ?? "—"}</td>
       <td class="mono">uid ${r.challenger_uid ?? "—"}</td>
       <td class="mono">uid ${r.king_uid ?? "—"}</td>
       <td class="mono">${comp}</td>
       ${facetCell(r.facets?.intelligibility)}
       ${facetCell(r.facets?.adherence)}
       ${facetCell(r.facets?.naturalness)}
       <td>${badge}</td>`);
    body.append(tr);
  }
}

function renderQueue(d) {
  const wrap = $("#queue"); wrap.innerHTML = "";
  const q = d.queue || [];
  if (!q.length) { wrap.append(el("div", "empty", "Queue empty.")); return; }
  for (const c of q) {
    const row = el("div", "qrow");
    row.append(el("div", null, `<div class="mono">uid ${c.uid}</div><div class="repo">${c.repo || "—"}</div>`));
    row.append(el("div", "mono muted", "block " + (c.block ?? "—")));
    wrap.append(row);
  }
}

function render(d) {
  if (!d) { $("#subtitle").textContent = "Could not load dashboard data."; return; }
  $("#subtitle").textContent = (d.spec?.name || "Vocence") + " · netuid " + (d.spec?.netuid ?? "—");
  $("#block").textContent = "block " + (d.chain?.block ?? "—");
  $("#updated").textContent = "updated " + (d.updated_at ? d.updated_at.replace("T", " ").replace("Z", " UTC") : "—");
  renderStats(d); renderReign(d); renderLeaderboard(d); renderDuels(d); renderQueue(d);
}

async function tick() { render(await fetchDashboard()); }

$("#theme").addEventListener("click", () => {
  const root = document.documentElement;
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
});

tick();
setInterval(tick, POLL_MS);
