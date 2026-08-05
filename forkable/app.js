"use strict";

const CFG = window.ER_INTEL_CONFIG;
const $ = (s) => document.querySelector(s);
const E = {
  connection: $("#connection"), progress: $("#progress"), pct: $("#pct"), clock: $("#clock"), windowLabel: $("#windowLabel"),
  strip: $("#strip"), cards: $("#cards"), matrixNote: $("#matrixNote"), signals: $("#signals"), legend: $("#legend"), chart: $("#chart"),
  events: $("#events"), eventCount: $("#eventCount"), forensics: $("#forensics"), summary: $("#summary"), error: $("#error"), reset: $("#resetWindow")
};
const IDS = Object.keys(CFG.hospitals);
const ID_MAP = { "credit-valley": "cvh", "milton-district": "milton", "oakville-trafalgar": "otmh", cvh: "cvh", milton: "milton", otmh: "otmh" };
const CIRC = 2 * Math.PI * 43;
E.progress.style.strokeDasharray = CIRC;

let start = new Date(localStorage.getItem("erIntelStart") || Date.now());
if (Number.isNaN(start.getTime())) start = new Date();
let end = new Date(start.getTime() + 24 * 3600_000);
const state = { raw: [], attempts: {}, series: {}, latest: {}, events: [], loadedAt: null };

const fmtTime = (d) => new Intl.DateTimeFormat("en-CA", { timeZone: CFG.timezone, hour: "numeric", minute: "2-digit", hour12: true }).format(d);
const fmtDate = (d) => new Intl.DateTimeFormat("en-CA", { timeZone: CFG.timezone, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true }).format(d);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const median = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const quantile = (a, q) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), p = (s.length - 1) * q, b = Math.floor(p), r = p - b; return s[b + 1] == null ? s[b] : s[b] + r * (s[b + 1] - s[b]); };
const mad = (a) => { const m = median(a); return m == null ? null : median(a.map((x) => Math.abs(x - m))); };
const duration = (m) => { if (!Number.isFinite(m)) return "—"; m = Math.round(m); const h = Math.floor(m / 60), r = m % 60; return h ? (r ? `${h}h ${r}m` : `${h}h`) : `${r}m`; };
const deltaText = (v) => v == null ? "—" : `${v > 0 ? "+" : ""}${Math.round(v)}m`;
const deltaClass = (v) => v == null || Math.abs(v) < 5 ? "flat" : v > 0 ? "up" : "down";
const ageMinutes = (d) => d ? Math.max(0, (Date.now() - d.getTime()) / 60000) : Infinity;

function missionClock() {
  const now = new Date();
  const total = end - start;
  const elapsed = clamp(now - start, 0, total);
  const pct = elapsed / total * 100;
  E.progress.style.strokeDashoffset = CIRC * (1 - pct / 100);
  E.pct.textContent = `${Math.round(pct)}%`;
  const remaining = Math.max(0, end - now);
  const s = Math.floor(remaining / 1000);
  E.clock.textContent = `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  E.windowLabel.textContent = `${fmtDate(start)} → ${fmtDate(end)} · Toronto`;
}

async function fetchRows() {
  const from = new Date(Math.min(start.getTime(), Date.now() - 48 * 3600_000)).toISOString();
  const select = "id,hospital_id,wait_minutes,total_patients,waiting_patients,source_reported_at,source_time_label,retrieved_at,source_tier,source_url,is_valid,invalid_reason";
  const url = `${CFG.supabaseUrl}/rest/v1/${CFG.table}?select=${select}&retrieved_at=gte.${encodeURIComponent(from)}&order=retrieved_at.asc`;
  const rows = [];
  for (let page = 0; page < 10; page++) {
    const lo = page * 1000, hi = lo + 999;
    const r = await fetch(url, { headers: { apikey: CFG.supabaseAnonKey, Authorization: `Bearer ${CFG.supabaseAnonKey}`, Range: `${lo}-${hi}` }, cache: "no-store" });
    if (!r.ok) throw new Error(`History API returned ${r.status}`);
    const batch = await r.json();
    rows.push(...batch);
    if (batch.length < 1000) break;
  }
  return rows;
}

function process(rows) {
  state.raw = rows.map((r) => ({ ...r, hospital_id: ID_MAP[r.hospital_id] || r.hospital_id, t: new Date(r.retrieved_at), sourceT: r.source_reported_at ? new Date(r.source_reported_at) : null })).filter((r) => CFG.hospitals[r.hospital_id] && !Number.isNaN(r.t.getTime()));
  for (const id of IDS) {
    state.attempts[id] = state.raw.filter((r) => r.hospital_id === id).sort((a, b) => a.t - b.t);
    const seen = new Set();
    const valid = state.attempts[id].filter((r) => r.is_valid !== false && Number.isFinite(r.wait_minutes)).map((r) => ({ ...r, obsT: r.sourceT || r.t })).filter((r) => {
      const key = `${r.source_reported_at || r.retrieved_at}|${r.wait_minutes}|${r.total_patients ?? ""}|${r.waiting_patients ?? ""}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    }).sort((a, b) => a.obsT - b.obsT);
    state.latest[id] = valid.at(-1) || null;
    state.series[id] = valid.filter((r) => r.obsT >= start && r.obsT <= end);
  }
  state.events = buildEvents();
  state.loadedAt = new Date();
  render();
}

function prior(arr, target, toleranceMinutes) {
  let hit = null;
  for (const x of arr) { if (x.obsT > target) break; hit = x; }
  return hit && target - hit.obsT <= toleranceMinutes * 60000 ? hit : null;
}
function deltaFor(id, minutes) {
  const arr = state.series[id]; const current = arr.at(-1) || state.latest[id];
  if (!current) return null;
  const p = prior(arr, new Date(current.obsT - minutes * 60000), Math.max(15, minutes * .7));
  return p ? current.wait_minutes - p.wait_minutes : null;
}
function freshness(id, row) {
  if (!row) return "unavailable";
  const age = ageMinutes(row.sourceT || row.t), [current, stale] = CFG.hospitals[id].fresh;
  return age <= current ? "current" : age <= stale ? "delayed" : "stale";
}
function confidence(id, row) {
  if (!row) return { score: 0, text: "No valid official reading." };
  const recent = state.attempts[id].slice(-20);
  const success = recent.length ? recent.filter((r) => r.is_valid !== false && Number.isFinite(r.wait_minutes)).length / recent.length : 0;
  const [current, stale] = CFG.hospitals[id].fresh;
  const age = ageMinutes(row.sourceT || row.t);
  let score = 72 + 22 * success;
  if (age > current) score -= Math.min(38, ((age - current) / Math.max(1, stale - current)) * 28);
  if (row.total_patients == null) score -= 5;
  if (row.waiting_patients == null) score -= 3;
  return { score: Math.round(clamp(score, 0, 99)), text: `${Math.round(success * 100)}% recent source success · source age ${Math.round(age)}m${row.total_patients == null ? " · volume unavailable" : ""}` };
}
function stats(id) {
  const arr = state.series[id], vals = arr.map((r) => r.wait_minutes), elapsed = clamp((Math.min(Date.now(), end) - start) / 60000, 1, 1440);
  const expected = Math.max(1, Math.floor(elapsed / CFG.hospitals[id].cadence) + 1);
  const gaps = arr.slice(1).map((r, i) => (r.obsT - arr[i].obsT) / 60000);
  return { n: arr.length, med: median(vals), min: vals.length ? Math.min(...vals) : null, max: vals.length ? Math.max(...vals) : null, p25: quantile(vals, .25), p75: quantile(vals, .75), mad: mad(vals), coverage: Math.round(clamp(arr.length / expected * 100, 0, 100)), maxGap: gaps.length ? Math.max(...gaps) : null };
}

function synchronized() {
  const times = [...new Set(IDS.flatMap((id) => state.series[id].map((r) => r.obsT.getTime())))].sort((a, b) => a - b);
  const last = {};
  return times.map((t) => {
    for (const id of IDS) { const candidates = state.series[id].filter((r) => r.obsT.getTime() <= t); last[id] = candidates.at(-1) || last[id] || null; }
    const complete = IDS.every((id) => last[id]);
    const leader = complete ? IDS.reduce((a, b) => last[a].wait_minutes <= last[b].wait_minutes ? a : b) : null;
    return { t: new Date(t), readings: { ...last }, leader };
  });
}
function leadAnalysis() {
  const tl = synchronized(); let switches = 0, previous = null; const ms = Object.fromEntries(IDS.map((id) => [id, 0]));
  for (let i = 0; i < tl.length; i++) {
    const x = tl[i]; if (x.leader && previous && x.leader !== previous) switches++; if (x.leader) previous = x.leader;
    if (i < tl.length - 1 && x.leader) ms[x.leader] += Math.max(0, tl[i + 1].t - x.t);
  }
  return { switches, ms, current: tl.at(-1)?.leader || null };
}
function buildEvents() {
  const out = [];
  for (const id of IDS) {
    const h = CFG.hospitals[id], arr = state.series[id];
    for (let i = 1; i < arr.length; i++) {
      const d = arr[i].wait_minutes - arr[i - 1].wait_minutes;
      const gap = (arr[i].obsT - arr[i - 1].obsT) / 60000;
      if (Math.abs(d) >= 30 && gap <= 90) out.push({ t: arr[i].obsT, title: `${h.short} moved ${d > 0 ? "up" : "down"} ${Math.abs(d)} minutes`, text: `${duration(arr[i - 1].wait_minutes)} → ${duration(arr[i].wait_minutes)} across a ${Math.round(gap)}-minute source interval.` });
    }
    for (const r of state.attempts[id].filter((x) => x.t >= start && x.is_valid === false)) out.push({ t: r.t, title: `${h.short} source failure`, text: r.invalid_reason || "The source attempt did not produce a valid reading." });
  }
  const tl = synchronized(); let priorLeader = null;
  for (const x of tl) if (x.leader) { if (priorLeader && x.leader !== priorLeader) out.push({ t: x.t, title: `Lowest displayed estimate changed`, text: `${CFG.hospitals[priorLeader].short} → ${CFG.hospitals[x.leader].short}.` }); priorLeader = x.leader; }
  return out.sort((a, b) => b.t - a.t).slice(0, 30);
}

function render() {
  E.connection.className = "pill ok"; E.connection.innerHTML = "<i></i>Live database";
  const all = IDS.flatMap((id) => state.series[id]);
  const coverage = Math.round(IDS.reduce((s, id) => s + stats(id).coverage, 0) / IDS.length);
  const latestRetrieval = state.raw.length ? new Date(Math.max(...state.raw.map((r) => r.t.getTime()))) : null;
  E.strip.innerHTML = [
    ["Experiment elapsed", duration(clamp((Date.now() - start) / 60000, 0, 1440))], ["Distinct observations", all.length], ["Average coverage", `${coverage}%`],
    ["Latest collection", latestRetrieval ? fmtTime(latestRetrieval) : "—"], ["Acquisition", "GitHub Actions · 5m"]
  ].map(([a, b]) => `<div class="metric"><span>${a}</span><strong>${b}</strong></div>`).join("");

  const waits = IDS.map((id) => state.latest[id]?.wait_minutes).filter(Number.isFinite);
  const leader = waits.length === 3 ? IDS.reduce((a, b) => state.latest[a].wait_minutes <= state.latest[b].wait_minutes ? a : b) : null;
  E.matrixNote.textContent = leader ? `${CFG.hospitals[leader].short} currently has the shortest displayed estimate.` : "Waiting for a complete synchronized state.";
  E.cards.innerHTML = IDS.map((id) => {
    const h = CFG.hospitals[id], r = state.latest[id], f = freshness(id, r), c = confidence(id, r);
    const d15 = deltaFor(id, 15), d60 = deltaFor(id, 60), d180 = deltaFor(id, 180);
    return `<article class="card" style="--hospital:${h.color}"><div class="cardhead"><div><h3>${h.name}</h3><div class="city">${h.city}</div></div><span class="fresh ${f}">${f}</span></div>
      <div class="wait">${r ? duration(r.wait_minutes) : "—"}</div><div class="subrow"><span>${r?.waiting_patients ?? "—"} waiting · ${r?.total_patients ?? "—"} total</span><span>${r ? fmtTime(r.sourceT || r.t) : "—"}</span></div>
      <div class="deltas">${[["15m", d15], ["1h", d60], ["3h", d180]].map(([l, v]) => `<div class="delta"><span class="smalllabel">${l}</span><b class="${deltaClass(v)}">${deltaText(v)}</b></div>`).join("")}</div>
      <div class="confidence"><b>${c.score}/100 confidence</b><br>${c.text}</div></article>`;
  }).join("");

  const lead = leadAnalysis();
  const spread = waits.length ? Math.max(...waits) - Math.min(...waits) : null;
  const fastestMove = IDS.map((id) => ({ id, d: deltaFor(id, 60) })).filter((x) => x.d != null).sort((a, b) => Math.abs(b.d) - Math.abs(a.d))[0];
  const mostVolatile = IDS.map((id) => ({ id, v: stats(id).mad })).filter((x) => x.v != null).sort((a, b) => b.v - a.v)[0];
  E.signals.innerHTML = [
    ["Current leader", lead.current ? CFG.hospitals[lead.current].short : "Incomplete", "Lowest displayed estimate in the latest synchronized state."],
    ["Cross-hospital spread", spread == null ? "—" : duration(spread), "Difference between the highest and lowest current estimates."],
    ["Lead changes", lead.switches, "How often the lowest displayed estimate changed during this test."],
    ["Strongest 1h move", fastestMove ? `${CFG.hospitals[fastestMove.id].short} ${deltaText(fastestMove.d)}` : "—", "Largest absolute one-hour change currently measurable."],
    ["Most volatile", mostVolatile ? CFG.hospitals[mostVolatile.id].short : "—", mostVolatile ? `Median absolute deviation: ${Math.round(mostVolatile.v)} minutes.` : "Awaiting enough distinct values."],
    ["Anomalies logged", state.events.length, "Large jumps, source failures and lead changes."],
    ["Window observations", all.length, "Distinct source-published states after deduplication."],
    ["Collection health", `${coverage}%`, "Observed distinct states versus each source's expected cadence."]
  ].map(([l, v, p]) => `<div class="signal"><span class="smalllabel">${l}</span><strong>${v}</strong><p>${p}</p></div>`).join("");

  renderChart();
  E.eventCount.textContent = `${state.events.length} events`;
  E.events.innerHTML = state.events.length ? state.events.map((x) => `<div class="event"><time>${fmtTime(x.t)}</time><div><b>${esc(x.title)}</b><p>${esc(x.text)}</p></div></div>`).join("") : `<div class="loading">No significant events yet.</div>`;
  E.forensics.innerHTML = IDS.map((id) => { const s = stats(id), attempts = state.attempts[id].filter((r) => r.t >= start), failed = attempts.filter((r) => r.is_valid === false).length; return `<div class="forensic"><b>${CFG.hospitals[id].short}</b><span>${s.n} distinct · ${failed} failures · max gap ${s.maxGap == null ? "—" : `${Math.round(s.maxGap)}m`}</span><strong>${s.coverage}%</strong></div>`; }).join("");

  const bestLead = Object.entries(lead.ms).sort((a, b) => b[1] - a[1])[0];
  E.summary.innerHTML = `<div class="summarygrid">
    <div class="summaryitem"><span class="smalllabel">Most time in lead</span><strong>${bestLead && bestLead[1] ? CFG.hospitals[bestLead[0]].short : "—"}</strong><p>Hospital holding the lowest displayed estimate for the longest measured duration.</p></div>
    <div class="summaryitem"><span class="smalllabel">Observed range</span><strong>${all.length ? `${duration(Math.min(...all.map((r) => r.wait_minutes)))}–${duration(Math.max(...all.map((r) => r.wait_minutes)))}` : "—"}</strong><p>Minimum and maximum across all distinct hospital readings in the window.</p></div>
    <div class="summaryitem"><span class="smalllabel">System spread now</span><strong>${spread == null ? "—" : duration(spread)}</strong><p>Current difference between the highest and lowest displayed estimates.</p></div>
    <div class="summaryitem"><span class="smalllabel">Evidence quality</span><strong>${coverage}%</strong><p>Cadence-adjusted collection coverage. This is data quality, not forecast confidence.</p></div>
  </div>`;
}

function renderChart() {
  E.legend.innerHTML = IDS.map((id) => `<span><i style="background:${CFG.hospitals[id].color}"></i>${CFG.hospitals[id].short}</span>`).join("");
  const points = IDS.flatMap((id) => state.series[id]);
  if (points.length < 2) { E.chart.innerHTML = `<div class="loading">Trend calibration requires at least two distinct readings after ${fmtTime(start)}.</div>`; return; }
  const width = 1000, height = 330, pad = { l: 52, r: 20, t: 18, b: 34 };
  const minT = start.getTime(), maxT = Math.min(Date.now(), end.getTime());
  const values = points.map((r) => r.wait_minutes), minV = Math.max(0, Math.floor(Math.min(...values) / 30) * 30), maxV = Math.max(minV + 60, Math.ceil(Math.max(...values) / 30) * 30);
  const x = (t) => pad.l + ((t - minT) / Math.max(1, maxT - minT)) * (width - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - minV) / (maxV - minV)) * (height - pad.t - pad.b);
  let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Emergency wait estimate timeline">`;
  for (let i = 0; i <= 4; i++) { const v = minV + (maxV - minV) * i / 4, yy = y(v); svg += `<line class="gridline" x1="${pad.l}" x2="${width - pad.r}" y1="${yy}" y2="${yy}"/><text class="axis" x="4" y="${yy + 4}">${duration(v)}</text>`; }
  for (let i = 0; i <= 4; i++) { const t = minT + (maxT - minT) * i / 4, xx = x(t); svg += `<line class="gridline" x1="${xx}" x2="${xx}" y1="${pad.t}" y2="${height - pad.b}"/><text class="axis" text-anchor="middle" x="${xx}" y="${height - 8}">${fmtTime(new Date(t))}</text>`; }
  for (const id of IDS) { const arr = state.series[id]; if (!arr.length) continue; const poly = arr.map((r) => `${x(r.obsT.getTime()).toFixed(1)},${y(r.wait_minutes).toFixed(1)}`).join(" "); svg += `<polyline class="series" stroke="${CFG.hospitals[id].color}" points="${poly}"/>`; for (const r of arr.slice(-20)) svg += `<circle class="dot" fill="${CFG.hospitals[id].color}" cx="${x(r.obsT.getTime())}" cy="${y(r.wait_minutes)}" r="4"/>`; }
  E.chart.innerHTML = svg + "</svg>";
}

async function refresh() {
  try { const rows = await fetchRows(); process(rows); E.error.classList.add("hidden"); }
  catch (err) { E.connection.className = "pill bad"; E.connection.innerHTML = "<i></i>Data error"; E.error.textContent = `Could not load history: ${err.message}`; E.error.classList.remove("hidden"); }
}

E.reset.addEventListener("click", () => { localStorage.setItem("erIntelStart", new Date().toISOString()); location.reload(); });
missionClock(); setInterval(missionClock, 1000); refresh(); setInterval(refresh, CFG.pollMs || 60000);
