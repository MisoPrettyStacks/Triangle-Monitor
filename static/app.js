/* Triangle Monitor frontend */
const $ = (id) => document.getElementById(id);
const state = { patterns: [], filter: "all", events: [], chart: null, sound: true };
let audioCtx = null;

function beep(freq, dur, when = 0) {
  if (!state.sound) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.connect(g); g.connect(audioCtx.destination);
    o.frequency.value = freq; o.type = "sine";
    const t = audioCtx.currentTime + when;
    g.gain.setValueAtTime(0.25, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + dur);
    o.start(t); o.stop(t + dur);
  } catch (e) { /* audio unavailable */ }
}
document.addEventListener("click", () => {
  try { audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {}
}, { once: true });

const fmtT = (ts) => new Date(ts * 1000).toLocaleString();
const fmtP = (p) => p >= 100 ? p.toFixed(2) : p >= 1 ? p.toFixed(4) : p.toFixed(6);

function badgeClass(type, status) {
  if (status === "breakout") return "b-breakout";
  if (status === "aborted") return "b-abort";
  if (type.includes("descending")) return "b-desc";
  if (type.includes("ascending")) return "b-asc";
  if (type.includes("symmetrical")) return "b-sym";
  if (type.includes("flag")) return "b-flag";
  if (type.includes("double")) return "b-double";
  return "b-forming";
}

function matchFilter(r) {
  const f = state.filter, t = r.type;
  if (f === "all") return true;
  if (f === "breakout") return r.status === "breakout";
  if (f === "flag") return t.includes("flag");
  if (f === "double") return t.includes("double");
  return t === f;
}

function renderCards() {
  const el = $("cards");
  const list = state.patterns.filter(matchFilter);
  $("stat-patterns").textContent = state.patterns.length;
  $("stat-breakouts").textContent = state.patterns.filter(r => r.status === "breakout").length;
  if (!list.length) {
    el.innerHTML = `<div class="empty">No patterns match right now.<br>The scanner labels them the moment they form.</div>`;
    return;
  }
  el.innerHTML = list.map(r => {
    const p = r.pattern;
    const statusBadge = r.status === "breakout"
      ? `<span class="badge b-breakout">⚡ Breakout confirmed</span>`
      : r.status === "aborted"
        ? `<span class="badge b-abort">Aborted</span>`
        : `<span class="badge b-forming">Forming</span>`;
    const apex = p.apex_compression ? `<div>◈ Apex compression — coiling in final third</div>` : "";
    return `<div class="card ${r.status === "breakout" ? "breakout" : ""} ${r.status === "aborted" ? "aborted" : ""}" data-pair="${r.pair}">
      <h3>${r.pair.replace("_", " / ")}</h3>
      <div><span class="badge ${badgeClass(r.type, "")}">${p.label}</span>${statusBadge}</div>
      <div class="price">$${fmtP(p.price)}</div>
      <div class="meta">
        ${apex}
        <div>Breakout watch: $${fmtP(p.breakout_level)} · Invalidation: $${fmtP(p.invalidation_level)}</div>
        <div>Quality ${p.quality}% · first seen ${new Date(r.first_seen * 1000).toLocaleTimeString()}</div>
      </div>
    </div>`;
  }).join("");
  el.querySelectorAll(".card").forEach(c =>
    c.addEventListener("click", () => openDetail(c.dataset.pair)));
}

function renderEvents() {
  $("events").innerHTML = state.events.slice(0, 40).map(ev => {
    const when = new Date(ev.ts * 1000).toLocaleTimeString();
    const label = { identified: "Tagged", breakout_confirmed: "⚡ BREAKOUT", abort: "🛑 ABORT", target_hit: "🎯 Target", completed: "✅ Done", dissolved: "dissolved" }[ev.kind] || ev.kind;
    return `<div class="ev ${ev.kind}"><span class="t">${when}</span><br><b>${label}</b> — ${ev.pair.replace("_", "/")} <span style="color:var(--muted)">${ev.label || ""}</span>${ev.price ? ` @ $${fmtP(ev.price)}` : ""}</div>`;
  }).join("") || `<div class="ev">Waiting for first signals…</div>`;
}

function showFlash(kind, pair, detail) {
  const ov = $("flash-overlay");
  ov.className = kind === "abort" ? "abort" : "breakout";
  $("flash-kind").textContent = kind === "abort" ? "ABORT ABORT" : "⚡ BREAKOUT CONFIRMED";
  $("flash-pair").textContent = pair.replace("_", " / ");
  $("flash-detail").textContent = detail;
  ov.classList.remove("hidden");
  if (kind === "abort") { beep(220, .5); beep(220, .5, .6); beep(220, .8, 1.2); }
  else { beep(660, .25); beep(880, .25, .3); beep(1320, .5, .6); }
}
$("flash-dismiss").addEventListener("click", () => $("flash-overlay").classList.add("hidden"));

function handleEvent(ev) {
  state.events.unshift(ev);
  if (state.events.length > 100) state.events.pop();
  if (ev.kind === "breakout_confirmed")
    showFlash("breakout", ev.pair, `${ev.label} — close beyond $${fmtP(ev.breakout_level)}. Paper-signal alert, not financial advice.`);
  if (ev.kind === "abort")
    showFlash("abort", ev.pair, `${ev.label} — invalidated before exits.`);
  refreshPatterns();
  renderEvents();
}

/* ---------------- detail chart ---------------- */

function toSec(ms) { return Math.floor(ms / 1000); }

function drawChart(pair, data) {
  const el = $("chart");
  el.innerHTML = "";
  if (state.chart) { try { state.chart.remove(); } catch (e) {} }
  const chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "#0d1117" }, textColor: "#e6edf3" },
    grid: { vertLines: { color: "#1c2330" }, horzLines: { color: "#1c2330" } },
    timeScale: { timeVisible: true },
  });
  state.chart = chart;
  const candles = chart.addCandlestickSeries({ upColor: "#3fb950", downColor: "#f85149", wickUpColor: "#3fb950", wickDownColor: "#f85149" });
  candles.setData(data.candles.map(c => ({ time: toSec(c[0]), open: c[1], high: c[2], low: c[3], close: c[4] })));

  const markers = [];
  const t0 = toSec(data.candles[0][0]);
  const tEnd = toSec(data.candles[data.candles.length - 1][0]);

  const addLine = (pts, color, width = 2, dashed = false) => {
    const s = chart.addLineSeries({
      color, lineWidth: width,
      lineStyle: dashed ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    s.setData(pts.map(p => ({ time: toSec(p[0]), value: p[1] })));
    return s;
  };
  const flatLine = (price, color, label) => {
    const s = addLine([[data.candles[0][0], price], [data.candles[data.candles.length - 1][0], price]], color, 2);
    s.createPriceLine({ price, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: label });
  };

  data.patterns.filter(r => r.status !== "dissolved").forEach(r => {
    const p = r.pattern;
    // structural lines per pattern type
    if (p.resistance_line) addLine(p.resistance_line, "#f85149", 2);
    if (p.support_line) addLine(p.support_line, "#3fb950", 2);
    if (p.support_level) flatLine(p.support_level, "#3fb950", "BASE");
    if (p.resistance_level) flatLine(p.resistance_level, "#f85149", "RESIST");
    if (p.flag_top) flatLine(p.flag_top, "#58a6ff", "FLAG TOP");
    if (p.flag_bottom) flatLine(p.flag_bottom, "#58a6ff", "FLAG BOT");
    if (p.neckline) flatLine(p.neckline, "#d29922", "NECKLINE");

    // breakout / invalidation / exits as price lines on candle series
    const pl = (price, color, title) => candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title });
    if (p.breakout_level) pl(p.breakout_level, "#58a6ff", r.status === "breakout" ? "BROKE OUT" : "BREAKOUT?");
    if (p.invalidation_level) pl(p.invalidation_level, "#f85149", "ABORT IF CROSSED");
    (p.exits || []).forEach((ex, i) => pl(ex.price, "#3fb950", ex.name));

    // Active Pattern Markup annotations
    if (p.type === "descending_triangle") {
      const a = (p.resistance_anchors || [])[0];
      if (a) markers.push({ time: toSec(a[0]), position: "aboveBar", color: "#f85149", shape: "arrowDown", text: "Upper Resistance — sellers suppressing each recovery" });
      let lowT = data.candles[0][0], lowV = Infinity;
      data.candles.forEach(c => { if (Math.abs(c[3] - p.support_level) / p.support_level < 0.012 && c[3] < lowV) { lowV = c[3]; lowT = c[0]; } });
      markers.push({ time: toSec(lowT), position: "belowBar", color: "#3fb950", shape: "arrowUp", text: "Lower Base — accumulation holding the floor" });
      if (p.apex_compression) markers.push({ time: tEnd, position: "aboveBar", color: "#bc8cff", shape: "circle", text: "APEX COMPRESSION — coiling in final third" });
    }
    if (p.type === "ascending_triangle" && p.apex_compression)
      markers.push({ time: tEnd, position: "belowBar", color: "#bc8cff", shape: "circle", text: "APEX COMPRESSION — coiling in final third" });
    if (p.type === "symmetrical_triangle" && p.apex_compression)
      markers.push({ time: tEnd, position: "aboveBar", color: "#bc8cff", shape: "circle", text: "APEX COMPRESSION — volatility coiling" });
  });
  if (markers.length) LightweightCharts.createSeriesMarkers(candles, markers.sort((a, b) => a.time - b.time));
  chart.timeScale().fitContent();

  // meta panel
  const r0 = data.patterns.find(r => r.status !== "dissolved");
  if (r0) {
    const p = r0.pattern;
    let html = `<b>${p.label}</b> · ${r0.status.toUpperCase()} · quality ${p.quality}%<br>`;
    if (p.type === "descending_triangle") html += `
      <b>Upper Resistance Line:</b> downward-sloping trendline through sequential lower highs (R² ${p.resistance_r2}) — dynamic distribution pressure; sellers suppress each recovery attempt at lower prices.<br>
      <b>Lower Base Line:</b> flat structural floor at $${fmtP(p.support_level)} across ${p.support_touches} touches — accumulation orders neutralizing selling pressure.<br>
      <b>Apex Compression:</b> ${p.apex_compression ? "YES — price sits in the final third; trendlines closing in, volatility coiling ahead of expansion." : "not yet — coil ratio " + p.coil_ratio}<br>`;
    html += `<b>Breakout watch:</b> hourly close ${p.direction === "long" ? "above" : "below"} $${fmtP(p.breakout_level)}<br>`;
    html += `<b>Exits (Trend Extensions):</b> ${(p.exits || []).map(e => `${e.name} $${fmtP(e.price)}`).join(" · ")}<br>`;
    html += `<b>ABORT trigger:</b> hourly close ${p.direction === "long" ? "below" : "above"} $${fmtP(p.invalidation_level)} before exits are reached.`;
    $("detail-meta").innerHTML = html;
    $("detail-badges").innerHTML = `<span class="badge ${badgeClass(p.type, "")}">${p.label}</span>
      <span class="badge ${r0.status === "breakout" ? "b-breakout" : r0.status === "aborted" ? "b-abort" : "b-forming"}">${r0.status}</span>`;
  } else {
    $("detail-meta").innerHTML = "No active pattern on this pair right now.";
    $("detail-badges").innerHTML = "";
  }
}

async function openDetail(pair) {
  $("detail-title").textContent = pair.replace("_", " / ");
  $("detail").classList.remove("hidden");
  $("chart").innerHTML = "<div style='padding:40px;color:var(--muted)'>Loading chart…</div>";
  try {
    const r = await fetch(`/api/patterns/${pair}`);
    if (!r.ok) { $("chart").innerHTML = "<div style='padding:40px;color:var(--muted)'>No candle data yet — try again in a minute.</div>"; return; }
    drawChart(pair, await r.json());
  } catch (e) {
    $("chart").innerHTML = "<div style='padding:40px;color:var(--muted)'>Failed to load chart.</div>";
  }
}
$("detail-close").addEventListener("click", () => {
  $("detail").classList.add("hidden");
  if (state.chart) { try { state.chart.remove(); } catch (e) {} state.chart = null; }
});

/* ---------------- data + stream ---------------- */

async function refreshPatterns() {
  try {
    const r = await fetch("/api/patterns");
    state.patterns = (await r.json()).patterns || [];
    renderCards();
  } catch (e) {}
}
async function refreshUniverse() {
  try {
    const r = await fetch("/api/universe");
    const u = await r.json();
    $("stat-universe").textContent = u.pairs;
    if (u.last_refresh) $("stat-updated").textContent = new Date(u.last_refresh * 1000).toLocaleTimeString();
    const hour = Date.now() / 1000 - 3600;
    const adds = (u.recently_added || []).filter(x => x.ts > hour);
    const rems = (u.recently_removed || []).filter(x => x.ts > hour);
    const box = $("universe-events");
    if (adds.length || rems.length) {
      box.classList.remove("hidden");
      box.textContent = `Universe update: +${adds.map(x => x.pair).join(", ") || "—"}  −${rems.map(x => x.pair).join(", ") || "—"}`;
    } else box.classList.add("hidden");
  } catch (e) {}
}
async function refreshEvents() {
  try {
    const r = await fetch("/api/events?n=40");
    state.events = (await r.json()).events || [];
    state.events.sort((a, b) => b.ts - a.ts);
    renderEvents();
  } catch (e) {}
}
async function checkFlashes() {
  try {
    const r = await fetch("/api/flashes");
    const f = (await r.json()).flashes || [];
    if (f.length) {
      const ev = f[0];
      showFlash(ev.kind === "abort" ? "abort" : "breakout", ev.pair,
        ev.kind === "abort" ? `${ev.label} — invalidated before exits.` : `${ev.label} — breakout confirmed.`);
    }
  } catch (e) {}
}

function connectStream() {
  try {
    const es = new EventSource("/api/stream");
    es.addEventListener("lifecycle", (e) => handleEvent(JSON.parse(e.data)));
    es.onerror = () => { es.close(); setTimeout(connectStream, 8000); };
  } catch (e) { setTimeout(connectStream, 8000); }
}

document.querySelectorAll("#filters button").forEach(b =>
  b.addEventListener("click", () => {
    document.querySelectorAll("#filters button").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    state.filter = b.dataset.f;
    renderCards();
  }));

(async function init() {
  await Promise.all([refreshUniverse(), refreshPatterns(), refreshEvents()]);
  checkFlashes();
  connectStream();
  setInterval(refreshPatterns, 30000);
  setInterval(refreshUniverse, 60000);
  setInterval(refreshEvents, 30000);
})();
