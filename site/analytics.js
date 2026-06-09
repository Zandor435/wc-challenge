/* WC Challenge — Analytics page.
   Self-contained (does not depend on app.js). Reads the same static JSON the
   home page does, plus two served reference CSVs (team_strength.csv, matches.csv),
   and computes the analytics client-side. The engine still computes the truth;
   this page only re-presents what the data already says.

   Sections:
     1 THE SCOREBOARD     — win prob, luck score, max points, elimination watch
     2 DRAFT REPORT CARD  — draft value index, points-by-tier, dependency
     3 RIVALRIES          — head-to-head differential, schedule difficulty
     4 THE MODEL          — prediction accuracy (Brier + winner call)
   Each section's Rome pull-quote comes from commentary.json.rome_analytics_quotes
   when present, else a hardcoded placeholder. */

/* ---------- shared constants / helpers (mirrors app.js) ---------- */
const OWNER_COLORS = { Zach: "#f4c430", Gunner: "#2f6dff", Gayden: "#28c060", Devin: "#f0743a", Rafe: "#a855f7" };
const OWNER_ORDER = Object.keys(OWNER_COLORS);
const ownerColor = (o) => OWNER_COLORS[o] || "#8b919c";
const TIER_COLORS = { 1: "#6f42c1", 2: "#1f6feb", 3: "#2d8a5a", 4: "#5a626d" };

const el = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const fmtNum = (n) => {
  const v = Math.round(Number(n) * 100) / 100;
  return Number.isFinite(v) ? (v === Math.trunc(v) ? String(Math.trunc(v)) : String(v)) : "0";
};
const signed = (n) => (Number(n) > 0 ? "+" : "") + fmtNum(n);
const pct = (n, d = 0) => `${(Number(n) * 100).toFixed(d)}%`;

const TEAM_FLAGS = {
  Argentina: "ar", Brazil: "br", France: "fr", Spain: "es", Portugal: "pt",
  Germany: "de", Netherlands: "nl", Belgium: "be", Uruguay: "uy",
  Colombia: "co", Croatia: "hr", Morocco: "ma", Japan: "jp",
  Switzerland: "ch", Austria: "at", Norway: "no", Sweden: "se",
  Senegal: "sn", "Ivory Coast": "ci", Mexico: "mx", USA: "us", Ecuador: "ec",
  Turkey: "tr", Czechia: "cz", Bosnia: "ba", Egypt: "eg", Tunisia: "tn",
  Algeria: "dz", "South Africa": "za", Ghana: "gh", Australia: "au",
  "Korea Republic": "kr", Canada: "ca", Paraguay: "py", "Cape Verde": "cv",
  "DR Congo": "cd", Iran: "ir", Uzbekistan: "uz", Jordan: "jo", Qatar: "qa",
  "Saudi Arabia": "sa", Iraq: "iq", "New Zealand": "nz", Panama: "pa",
  Haiti: "ht", Curacao: "cw",
  England: "gb-eng", Scotland: "gb-sct", Wales: "gb-wls",
};
function flag(team) {
  const code = TEAM_FLAGS[team];
  if (!code) return "";
  return `<img class="flag" src="https://flagcdn.com/24x18/${code}.png" ` +
         `srcset="https://flagcdn.com/48x36/${code}.png 2x" width="24" height="18" alt="" loading="lazy" />`;
}

/* team_strength.csv uses a few non-canonical names; map ours -> theirs. */
const STRENGTH_ALIASES = {
  Turkey: "Türkiye", "Korea Republic": "South Korea", USA: "United States",
  Bosnia: "Bosnia and Herzegovina",
};

async function loadJSON(path) {
  const res = await fetch(path + "?v=" + Date.now());
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}
async function loadText(path) {
  const res = await fetch(path + "?v=" + Date.now());
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.text();
}
/* Minimal CSV parser (handles quoted fields with commas). */
function parseCSV(text) {
  const rows = [];
  const lines = text.replace(/\r/g, "").split("\n").filter((l) => l.length);
  if (!lines.length) return rows;
  const split = (line) => {
    const out = []; let cur = ""; let q = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (q) { if (ch === '"') { if (line[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
      else if (ch === '"') q = true;
      else if (ch === ",") { out.push(cur); cur = ""; }
      else cur += ch;
    }
    out.push(cur); return out;
  };
  const header = split(lines[0]);
  for (let i = 1; i < lines.length; i++) {
    const cells = split(lines[i]);
    const row = {};
    header.forEach((h, j) => { row[h.trim()] = (cells[j] || "").trim(); });
    rows.push(row);
  }
  return rows;
}

/* ---------- group helpers over the shared data ---------- */
function teamsByOwner(teamTable) {
  const by = {};
  (teamTable.teams || []).forEach((t) => { (by[t.owner] = by[t.owner] || []).push(t); });
  return by;
}
function ownersFrom(standings, teamTable) {
  const fromStandings = (standings.standings || []).map((s) => s.owner);
  const set = new Set([...fromStandings, ...Object.keys(teamsByOwner(teamTable))]);
  // canonical color order first, then any extras
  return [...OWNER_ORDER.filter((o) => set.has(o)), ...[...set].filter((o) => !OWNER_ORDER.includes(o))];
}
function latestTimeline(timeline) {
  const e = (Array.isArray(timeline) ? [...timeline] : [])
    .sort((a, b) => String(a.date).localeCompare(String(b.date)) || (a.matchday || 0) - (b.matchday || 0));
  return e[e.length - 1] || null;
}
function lastResultsDate(daily) {
  const days = (daily && daily.days) || [];
  return days.length ? days[days.length - 1].date : null;
}

/* ======================================================================
   SECTION 1A — WIN PROBABILITY
   ====================================================================== */
let winprobChart = null;
function renderWinProb(timeline) {
  const canvas = el("winprob-chart");
  if (!canvas) return;
  const entries = (Array.isArray(timeline) ? [...timeline] : [])
    .sort((a, b) => String(a.date).localeCompare(String(b.date)) || (a.matchday || 0) - (b.matchday || 0));
  const wrap = canvas.parentElement;
  if (!entries.length) { wrap.innerHTML = `<div class="news-empty"><p>Win probability populates once the engine runs.</p></div>`; return; }
  if (typeof Chart === "undefined") { wrap.innerHTML = `<div class="news-empty"><p>Chart library unavailable.</p></div>`; return; }

  const seen = new Set();
  entries.forEach((e) => Object.keys(e.win_probability || {}).forEach((o) => seen.add(o)));
  const owners = [...OWNER_ORDER.filter((o) => seen.has(o)), ...[...seen].filter((o) => !OWNER_ORDER.includes(o))];
  const xlabel = (e) => (e.label === "preseason" || e.matchday === 0) ? "Preseason" : (e.date ? e.date.slice(5) : `MD ${e.matchday}`);
  const labels = entries.map(xlabel);
  const datasets = owners.map((o) => ({
    label: o,
    data: entries.map((e) => (e.win_probability && e.win_probability[o] != null) ? +(e.win_probability[o] * 100).toFixed(1) : null),
    borderColor: ownerColor(o), backgroundColor: ownerColor(o),
    borderWidth: 2.5, pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: ownerColor(o),
    tension: 0.25, spanGaps: true,
  }));
  if (el("winprob-meta")) el("winprob-meta").textContent = entries.length === 1 ? "PRESEASON BASELINE" : `THROUGH ${labels[labels.length - 1]}`;
  if (winprobChart) winprobChart.destroy();
  winprobChart = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#cfd3da", usePointStyle: true, pointStyleWidth: 10, boxHeight: 7, font: { family: "Inter", weight: "600" } } },
        tooltip: {
          backgroundColor: "#101218", borderColor: "#262a34", borderWidth: 1, titleColor: "#fff", bodyColor: "#cfd3da",
          callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y}%` },
        },
      },
      scales: {
        x: { grid: { color: "#1d212a" }, ticks: { color: "#8b919c", font: { family: "Inter" } } },
        y: { min: 0, suggestedMax: 50, grid: { color: "#1d212a" }, ticks: { color: "#8b919c", font: { family: "Inter" }, callback: (v) => v + "%" } },
      },
    },
  });
}

/* ======================================================================
   SECTION 1B — OWNER LUCK SCORE
   ====================================================================== */
function renderLuck(owners, standings, timeline) {
  const box = el("luck-table");
  const pointsOf = Object.fromEntries((standings.standings || []).map((s) => [s.owner, s.total_points]));
  const last = latestTimeline(timeline);
  const proj = (last && last.projected_points) || {};
  const rows = owners.map((o) => {
    const actual = Number(pointsOf[o] || 0);
    const p = proj[o];
    if (!p) return { o, actual, median: null };
    const delta = actual - p.median;
    let label = "On Pace ✅", cls = "pace";
    if (actual > p.p90) { label = "Running Hot 🔥"; cls = "hot"; }
    else if (actual < p.p10) { label = "Cursed 💀"; cls = "cursed"; }
    return { o, actual, median: p.median, p10: p.p10, p90: p.p90, delta, label, cls };
  });
  const body = rows.map((r) => {
    const c = ownerColor(r.o);
    if (r.median == null) {
      return `<tr><td><span class="owner-pill"><span class="owner-dot" style="background:${c}"></span><span style="color:${c}">${esc(r.o)}</span></span></td>
        <td class="num pts-strong">${fmtNum(r.actual)}</td><td class="num">—</td><td class="num">—</td><td><span class="luck-tag pace">No projection</span></td></tr>`;
    }
    return `<tr>
      <td><span class="owner-pill"><span class="owner-dot" style="background:${c}"></span><span style="color:${c}">${esc(r.o)}</span></span></td>
      <td class="num pts-strong">${fmtNum(r.actual)}</td>
      <td class="num">${fmtNum(r.median)} <span class="muted-range">(${fmtNum(r.p10)}–${fmtNum(r.p90)})</span></td>
      <td class="num ${r.delta >= 0 ? "pos" : "neg"}">${signed(r.delta)}</td>
      <td><span class="luck-tag ${r.cls}">${esc(r.label)}</span></td>
    </tr>`;
  }).join("");
  box.outerHTML = `<table id="luck-table">
    <thead><tr><th>Owner</th><th class="num">Actual</th><th class="num">Projected median</th><th class="num">Δ</th><th>Verdict</th></tr></thead>
    <tbody>${body}</tbody></table>`;
}

/* ======================================================================
   SECTION 1C — MAX POINTS REMAINING (theoretical ceiling)
   ====================================================================== */
// Max a single team could still bank on a full run from here:
//   group win = 3 + best-case upset bonus (2 x gap to Tier 1), per remaining group game
//   full knockout run = 5 wins x3 (15) + advancement bonuses (2+5+10+18+30 = 65) = 80
const KO_MAX = 80;
function maxGroupWin(tier) { return 3 + 2 * Math.max(0, (Number(tier) || 4) - 1); }

function remainingGroupGames(team, matchesMeta, lastDate, played) {
  // Prefer the fixture list; fall back to 3 minus games already played.
  if (matchesMeta && matchesMeta.length) {
    return matchesMeta.filter((m) =>
      m.phase === "group" && (!lastDate || m.date > lastDate) &&
      (m.team1 === team || m.team2 === team)).length;
  }
  return Math.max(0, 3 - played);
}

function renderMaxPoints(owners, standings, byOwner, eliminated, matchesMeta, lastDate) {
  const box = el("maxpts-list");
  const pointsOf = Object.fromEntries((standings.standings || []).map((s) => [s.owner, Number(s.total_points) || 0]));
  const leaderNow = Math.max(0, ...owners.map((o) => pointsOf[o] || 0));
  const rows = owners.map((o) => {
    const cur = pointsOf[o] || 0;
    const teams = byOwner[o] || [];
    let ceiling = cur, alive = 0;
    teams.forEach((t) => {
      if (eliminated.has(t.team)) return;
      alive++;
      const played = (t.W || 0) + (t.D || 0) + (t.L || 0);
      const rg = remainingGroupGames(t.team, matchesMeta, lastDate, played);
      ceiling += rg * maxGroupWin(t.tier) + KO_MAX;
    });
    return { o, cur, alive, ceiling, couch: ceiling < leaderNow };
  });
  const maxCeil = Math.max(1, ...rows.map((r) => r.ceiling));
  box.classList.remove("loading");
  box.innerHTML = rows.map((r) => {
    const c = ownerColor(r.o);
    const curW = (r.cur / maxCeil) * 100;
    const ceilW = (r.ceiling / maxCeil) * 100;
    const leadW = (leaderNow / maxCeil) * 100;
    return `<div class="bar-row ${r.couch ? "couch" : ""}">
      <div class="bar-label"><span style="color:${c}">${esc(r.o)}</span>
        <span class="bar-sub">${r.alive}/6 alive${r.couch ? ` · <b class="couch-flag">☠️ COUCH WATCH</b>` : ""}</span></div>
      <div class="bar-track">
        <div class="bar-fill ceil" style="width:${ceilW}%;"></div>
        <div class="bar-fill cur" style="width:${curW}%;background:${c}"></div>
        <div class="bar-leader" style="left:${leadW}%" title="Leader now: ${fmtNum(leaderNow)}"></div>
      </div>
      <div class="bar-val">${fmtNum(r.cur)} <span class="muted-range">→ ${fmtNum(r.ceiling)} max</span></div>
    </div>`;
  }).join("") + `<p class="sub-note tiny">Vertical line = leader's current total (${fmtNum(leaderNow)} pts). A bar whose ceiling falls short of it can no longer catch the lead.</p>`;
}

/* ======================================================================
   SECTION 1D — ELIMINATION WATCH
   ====================================================================== */
// Eliminated = knockout losers (from narrative_state) + last-place finishers in
// a COMPLETED group (4th of 4 can never advance, so this is conservative/safe).
function computeEliminated(daily, matchesMeta, narrative) {
  const out = new Set();
  try {
    ((narrative && narrative.phase && narrative.phase.eliminated_teams) || []).forEach((e) => {
      if (e && e.team) out.add(e.team);
    });
  } catch (_) { /* ignore */ }

  try {
    if (!matchesMeta || !matchesMeta.length) return out;
    // team -> group, and group -> set of teams
    const teamGroup = {}; const groupTeams = {};
    matchesMeta.forEach((m) => {
      if (m.phase !== "group" || !m.group) return;
      [m.team1, m.team2].forEach((t) => {
        if (!t || t === "TBD") return;
        teamGroup[t] = m.group;
        (groupTeams[m.group] = groupTeams[m.group] || new Set()).add(t);
      });
    });
    // accumulate group-stage results from played matches
    const tbl = {}; // team -> {pts, gd, gf, played}
    const ck = (t) => (tbl[t] = tbl[t] || { pts: 0, gd: 0, gf: 0, played: 0 });
    ((daily && daily.days) || []).forEach((day) => (day.matches || []).forEach((m) => {
      if (String(m.stage || "").toLowerCase() !== "group") return;
      const h = ck(m.home), a = ck(m.away);
      const hs = m.home_score, as = m.away_score;
      h.played++; a.played++; h.gf += hs; a.gf += as; h.gd += hs - as; a.gd += as - hs;
      if (hs > as) h.pts += 3; else if (as > hs) a.pts += 3; else { h.pts += 1; a.pts += 1; }
    }));
    // for each fully-played group (each of 4 teams has 3 games), mark the last team out
    Object.entries(groupTeams).forEach(([, teams]) => {
      const ts = [...teams];
      if (ts.length < 4) return;
      const complete = ts.every((t) => (tbl[t] && tbl[t].played >= 3));
      if (!complete) return;
      const ranked = ts.slice().sort((x, y) => {
        const X = tbl[x], Y = tbl[y];
        return (Y.pts - X.pts) || (Y.gd - X.gd) || (Y.gf - X.gf) || x.localeCompare(y);
      });
      out.add(ranked[ranked.length - 1]); // 4th place — definitively eliminated
    });
  } catch (_) { /* degrade to KO-only */ }
  return out;
}
function renderElimination(owners, byOwner, eliminated) {
  const box = el("elim-grid");
  box.classList.remove("loading");
  box.innerHTML = owners.map((o) => {
    const c = ownerColor(o);
    const teams = (byOwner[o] || []).slice().sort((a, b) => (a.tier || 9) - (b.tier || 9));
    const aliveN = teams.filter((t) => !eliminated.has(t.team)).length;
    const dots = teams.map((t) => {
      const dead = eliminated.has(t.team);
      return `<span class="elim-team ${dead ? "dead" : ""}" title="${esc(t.team)}${dead ? " — eliminated" : ""}">${flag(t.team)}<span class="elim-name">${esc(t.team)}</span></span>`;
    }).join("");
    return `<div class="elim-row" style="--c:${c}">
      <div class="elim-owner"><span style="color:${c}">${esc(o)}</span><span class="elim-count">${aliveN}/6 alive</span></div>
      <div class="elim-teams">${dots}</div>
    </div>`;
  }).join("");
}

/* ======================================================================
   SECTION 2A — DRAFT VALUE INDEX (points per tier slot)
   ====================================================================== */
function tierSlots(teams) {
  const byTier = { 1: [], 2: [], 3: [], 4: [] };
  teams.forEach((t) => { if (byTier[t.tier]) byTier[t.tier].push(Number(t.points) || 0); });
  const avg = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
  return { 1: avg(byTier[1]), 2: avg(byTier[2]), 3: avg(byTier[3]), 4: avg(byTier[4]) };
}
function renderDVI(owners, byOwner) {
  const box = el("dvi-table");
  const slots = Object.fromEntries(owners.map((o) => [o, tierSlots(byOwner[o] || [])]));
  const tierAvg = {};
  [1, 2, 3, 4].forEach((t) => { tierAvg[t] = owners.reduce((s, o) => s + slots[o][t], 0) / (owners.length || 1); });
  // STEAL = best in tier (and above avg); BUST = worst in tier (and below avg)
  const best = {}, worst = {};
  [1, 2, 3, 4].forEach((t) => {
    const vals = owners.map((o) => ({ o, v: slots[o][t] }));
    best[t] = vals.slice().sort((a, b) => b.v - a.v)[0];
    worst[t] = vals.slice().sort((a, b) => a.v - b.v)[0];
  });
  const cell = (o, t) => {
    const v = slots[o][t];
    const above = v > tierAvg[t] + 1e-9, below = v < tierAvg[t] - 1e-9;
    let tag = "";
    if (best[t].o === o && above) tag = `<span class="dvi-tag steal">STEAL</span>`;
    else if (worst[t].o === o && below) tag = `<span class="dvi-tag bust">BUST</span>`;
    return `<td class="num ${above ? "pos" : below ? "neg" : ""}">${fmtNum(v)}${tag}</td>`;
  };
  const body = owners.map((o) => {
    const c = ownerColor(o);
    return `<tr><td><span class="owner-pill"><span class="owner-dot" style="background:${c}"></span><span style="color:${c}">${esc(o)}</span></span></td>
      ${cell(o, 1)}${cell(o, 2)}${cell(o, 3)}${cell(o, 4)}</tr>`;
  }).join("");
  const avgRow = `<tr class="dvi-avg"><td>Field average</td>${[1, 2, 3, 4].map((t) => `<td class="num">${fmtNum(tierAvg[t])}</td>`).join("")}</tr>`;
  box.outerHTML = `<table id="dvi-table">
    <thead><tr><th>Owner</th><th class="num">T1</th><th class="num">T2 avg</th><th class="num">T3 avg</th><th class="num">T4</th></tr></thead>
    <tbody>${body}${avgRow}</tbody></table>`;
}

/* ======================================================================
   SECTION 2B — POINTS BREAKDOWN BY TIER (stacked bar)
   ====================================================================== */
let tierChart = null;
function renderTierChart(owners, byOwner) {
  const canvas = el("tier-chart");
  if (!canvas) return;
  const wrap = canvas.parentElement;
  if (typeof Chart === "undefined") { wrap.innerHTML = `<div class="news-empty"><p>Chart library unavailable.</p></div>`; return; }
  const sumTier = (teams, tier) => teams.filter((t) => t.tier === tier).reduce((s, t) => s + (Number(t.points) || 0), 0);
  const datasets = [1, 2, 3, 4].map((t) => ({
    label: `T${t}`,
    data: owners.map((o) => sumTier(byOwner[o] || [], t)),
    backgroundColor: TIER_COLORS[t], borderColor: "#0a0a0b", borderWidth: 1,
    stack: "pts",
  }));
  if (tierChart) tierChart.destroy();
  tierChart = new Chart(canvas, {
    type: "bar",
    data: { labels: owners, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: "#cfd3da", usePointStyle: true, pointStyleWidth: 10, boxHeight: 7, font: { family: "Inter", weight: "600" } } },
        tooltip: { backgroundColor: "#101218", borderColor: "#262a34", borderWidth: 1, titleColor: "#fff", bodyColor: "#cfd3da",
          callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtNum(ctx.parsed.y)} pts` } },
      },
      scales: {
        x: { stacked: true, grid: { color: "#1d212a" }, ticks: { color: "#cfd3da", font: { family: "Inter", weight: "600" } } },
        y: { stacked: true, beginAtZero: true, grid: { color: "#1d212a" }, ticks: { color: "#8b919c", font: { family: "Inter" } } },
      },
    },
  });
}

/* ======================================================================
   SECTION 2C — DEPENDENCY INDEX (% of points from T1 team)
   ====================================================================== */
function renderDependency(owners, byOwner, narrative) {
  const box = el("dep-list");
  box.classList.remove("loading");
  const fromState = (narrative && narrative.dependency_index) || null;
  box.innerHTML = owners.map((o) => {
    const teams = byOwner[o] || [];
    const total = teams.reduce((s, t) => s + (Number(t.points) || 0), 0);
    const t1 = teams.find((t) => t.tier === 1);
    const t1pts = t1 ? (Number(t1.points) || 0) : 0;
    let dep = (fromState && fromState[o] != null) ? Number(fromState[o]) : (total > 0 ? t1pts / total : 0);
    if (dep > 1) dep = dep / 100; // tolerate a 0–100 representation
    const c = ownerColor(o);
    const w = Math.max(0, Math.min(100, dep * 100));
    const t1name = t1 ? t1.team : "—";
    const note = total === 0 ? "no points yet" : (dep >= 0.6 ? "fragile — leaning hard on T1" : dep === 0 ? `0% from ${t1name} — T1 is dead weight` : "balanced");
    return `<div class="dep-row">
      <div class="dep-label"><span style="color:${c}">${esc(o)}</span> <span class="dep-sub">${flag(t1name)}${esc(t1name)}</span></div>
      <div class="dep-track"><div class="dep-fill" style="width:${w}%;background:${c}"></div></div>
      <div class="dep-val">${pct(dep, 0)} <span class="dep-note">${esc(note)}</span></div>
    </div>`;
  }).join("");
}

/* ======================================================================
   SECTION 3A — HEAD-TO-HEAD POINT DIFFERENTIAL
   ====================================================================== */
function renderH2H(owners, narrative) {
  const box = el("h2h-grid");
  box.classList.remove("loading");
  const matrix = (narrative && narrative.head_to_head_matrix) || {};
  const diff = (narrative && narrative.h2h_differential) || null;
  const pairs = [];
  for (let i = 0; i < owners.length; i++)
    for (let j = i + 1; j < owners.length; j++) pairs.push([owners[i], owners[j]]);

  const cards = pairs.map(([a, b]) => {
    const rec = (matrix[a] && matrix[a][b]) || { W: 0, D: 0, L: 0 };
    const games = (rec.W || 0) + (rec.D || 0) + (rec.L || 0);
    const d = (diff && diff[a] && diff[a][b] != null) ? Number(diff[a][b]) : null;
    const ca = ownerColor(a), cb = ownerColor(b);
    if (!games) {
      return `<div class="h2h-card empty">
        <div class="h2h-pair"><span style="color:${ca}">${esc(a)}</span><span class="vs">vs</span><span style="color:${cb}">${esc(b)}</span></div>
        <div class="h2h-rec muted">Not yet played</div></div>`;
    }
    const dominant = (rec.L === 0 && rec.W > 0);
    const leader = d == null ? null : (d > 0 ? a : d < 0 ? b : null);
    const lc = leader === a ? ca : leader === b ? cb : "var(--muted)";
    const tagline = dominant
      ? `<span style="color:${ca}">${esc(a)}</span> owns this matchup`
      : (leader ? `<span style="color:${lc}">${esc(leader)}</span> ahead on points` : "Dead even");
    return `<div class="h2h-card ${dominant ? "dominant" : ""}">
      <div class="h2h-pair"><span style="color:${ca}">${esc(a)}</span><span class="vs">vs</span><span style="color:${cb}">${esc(b)}</span></div>
      <div class="h2h-rec">${rec.W}-${rec.D}-${rec.L} <span class="muted-range">(${games} mtg${games === 1 ? "" : "s"})</span></div>
      <div class="h2h-diff">${d == null ? "" : `<span class="h2h-swing ${d >= 0 ? "pos" : "neg"}">${signed(d)} pts</span> `}${tagline}</div>
    </div>`;
  }).join("");
  box.innerHTML = pairs.length ? `<div class="h2h-cards">${cards}</div>` : `<div class="news-empty"><p>No head-to-head matchups yet.</p></div>`;
}

/* ======================================================================
   SECTION 3B — SCHEDULE DIFFICULTY REMAINING
   ====================================================================== */
function buildStrengthMap(strengthRows) {
  const m = {};
  strengthRows.forEach((r) => { if (r.team) m[r.team] = Number(r.strength_rating) || null; });
  return m;
}
function strengthOf(team, strengthMap) {
  const key = STRENGTH_ALIASES[team] || team;
  return strengthMap[key] != null ? strengthMap[key] : null;
}
function renderSchedule(owners, byOwner, eliminated, matchesMeta, lastDate, strengthMap) {
  const box = el("sched-list");
  box.classList.remove("loading");
  if (!matchesMeta.length || !Object.keys(strengthMap).length) {
    box.innerHTML = `<div class="news-empty"><p>Schedule difficulty needs the fixture + strength reference data.</p></div>`;
    return;
  }
  const rows = owners.map((o) => {
    const myTeams = new Set((byOwner[o] || []).filter((t) => !eliminated.has(t.team)).map((t) => t.team));
    const opps = [];
    matchesMeta.forEach((m) => {
      if (m.phase !== "group") return;
      if (lastDate && m.date <= lastDate) return;
      let opp = null;
      if (myTeams.has(m.team1)) opp = m.team2;
      else if (myTeams.has(m.team2)) opp = m.team1;
      if (opp && opp !== "TBD") opps.push(opp);
    });
    const rated = opps.map((t) => strengthOf(t, strengthMap)).filter((v) => v != null);
    const avg = rated.length ? rated.reduce((a, b) => a + b, 0) / rated.length : null;
    const hardest = opps.slice().sort((x, y) => (strengthOf(y, strengthMap) || 0) - (strengthOf(x, strengthMap) || 0))[0] || null;
    return { o, count: opps.length, avg, hardest };
  });
  const ranked = rows.slice().sort((a, b) => (b.avg || 0) - (a.avg || 0));
  const maxAvg = Math.max(1, ...ranked.map((r) => r.avg || 0));
  const minAvg = Math.min(...ranked.filter((r) => r.avg != null).map((r) => r.avg), maxAvg);
  box.innerHTML = ranked.map((r, i) => {
    const c = ownerColor(r.o);
    if (r.avg == null) {
      return `<div class="sched-row"><div class="sched-rank">${i + 1}</div>
        <div class="sched-owner"><span style="color:${c}">${esc(r.o)}</span><span class="sched-sub">no remaining group games</span></div>
        <div class="sched-bar"><div class="sched-fill" style="width:0%"></div></div>
        <div class="sched-val">—</div></div>`;
    }
    const w = ((r.avg - minAvg) / (maxAvg - minAvg || 1)) * 80 + 20; // 20–100% so bars stay visible
    return `<div class="sched-row">
      <div class="sched-rank">${i + 1}</div>
      <div class="sched-owner"><span style="color:${c}">${esc(r.o)}</span>
        <span class="sched-sub">${r.count} game${r.count === 1 ? "" : "s"} left${r.hardest ? ` · toughest: ${flag(r.hardest)}${esc(r.hardest)}` : ""}</span></div>
      <div class="sched-bar"><div class="sched-fill" style="width:${w}%;background:${c}"></div></div>
      <div class="sched-val">${Math.round(r.avg)}</div>
    </div>`;
  }).join("") + `<p class="sub-note tiny">Strength ratings from team_strength.csv (Elo-style, provisional). Higher = harder remaining slate.</p>`;
}

/* ======================================================================
   SECTION 4A — PREDICTION ACCURACY (Brier + winner call)
   ====================================================================== */
// Build an actual-result index from the played matches so predictions can be
// scored live even before the prediction pipeline back-fills `actual`.
function playedIndex(daily) {
  const idx = {};
  ((daily && daily.days) || []).forEach((day) => (day.matches || []).forEach((m) => {
    const key = [m.home, m.away].sort().join(" :: ");
    idx[key] = { home: m.home, away: m.away, hs: m.home_score, as: m.away_score };
  }));
  return idx;
}
// Resolve a prediction's actual outcome relative to its (team1, team2) order.
function resolveActual(p, idx) {
  // explicit actual on the prediction wins, if present and parseable
  if (p.actual && typeof p.actual === "object") {
    const w = p.actual.winner || p.actual.result;
    if (w === "team1" || w === "team2" || w === "draw") return { outcome: w, score: p.actual.score || null };
  }
  const rec = idx[[p.team1, p.team2].sort().join(" :: ")];
  if (!rec) return null;
  // orient scores to (team1, team2)
  const s1 = rec.home === p.team1 ? rec.hs : rec.as;
  const s2 = rec.home === p.team1 ? rec.as : rec.hs;
  const outcome = s1 > s2 ? "team1" : s2 > s1 ? "team2" : "draw";
  return { outcome, score: `${s1}-${s2}` };
}
function renderPredictions(predictions, daily) {
  const statBox = el("pred-stats");
  const tableBox = el("pred-table");
  const preds = (predictions && predictions.predictions) || [];
  const idx = playedIndex(daily);

  let brierSum = 0, scored = 0, correct = 0;
  const rows = preds.map((p) => {
    const pr = p.predicted || {};
    const probs = { team1: Number(pr.team1_win) || 0, draw: Number(pr.draw) || 0, team2: Number(pr.team2_win) || 0 };
    const pick = ["team1", "draw", "team2"].reduce((a, b) => (probs[b] > probs[a] ? b : a), "team1");
    const act = resolveActual(p, idx);
    let cls = "pending", mark = "", actualLabel = "—";
    if (act) {
      scored++;
      const oneHot = { team1: act.outcome === "team1" ? 1 : 0, draw: act.outcome === "draw" ? 1 : 0, team2: act.outcome === "team2" ? 1 : 0 };
      brierSum += ["team1", "draw", "team2"].reduce((s, k) => s + (probs[k] - oneHot[k]) ** 2, 0);
      const hit = pick === act.outcome;
      if (hit) correct++;
      cls = hit ? "hit" : "miss";
      mark = hit ? "✅" : "❌";
      const winnerName = act.outcome === "team1" ? p.team1 : act.outcome === "team2" ? p.team2 : "Draw";
      actualLabel = `${esc(winnerName)} <span class="muted-range">${esc(act.score || "")}</span>`;
    }
    const probCell = (k, name) => `<span class="prob ${pick === k ? "pick" : ""}">${name} ${pct(probs[k], 0)}</span>`;
    return `<tr class="pred-${cls}">
      <td class="pred-date">${esc((p.date || "").slice(5))}</td>
      <td class="pred-match">${flag(p.team1)}${esc(p.team1)} <span class="vs">v</span> ${flag(p.team2)}${esc(p.team2)}</td>
      <td class="pred-probs">${probCell("team1", "1")} ${probCell("draw", "X")} ${probCell("team2", "2")}</td>
      <td class="pred-actual">${actualLabel}</td>
      <td class="pred-mark">${mark}</td>
    </tr>`;
  }).join("");

  // prefer the engine's scored values when present; else use the live computation
  const meta = (predictions && predictions.meta) || {};
  const brier = (meta.brier_score != null) ? Number(meta.brier_score) : (scored ? brierSum / scored : null);
  const winPct = (meta.correct_winner_pct != null) ? Number(meta.correct_winner_pct) : (scored ? correct / scored : null);

  statBox.innerHTML = `
    <div class="stat-card"><span class="stat-num">${brier == null ? "—" : brier.toFixed(3)}</span><span class="stat-lbl">Brier score</span></div>
    <div class="stat-card"><span class="stat-num">${winPct == null ? "—" : pct(winPct <= 1 ? winPct : winPct / 100, 0)}</span><span class="stat-lbl">Correct winner call</span></div>
    <div class="stat-card"><span class="stat-num">${scored}<span class="stat-of">/${preds.length}</span></span><span class="stat-lbl">Predictions scored</span></div>`;

  tableBox.outerHTML = `<table id="pred-table" class="pred-table">
    <thead><tr><th>Date</th><th>Match</th><th>Predicted (1 / X / 2)</th><th>Result</th><th>Call</th></tr></thead>
    <tbody>${rows || `<tr><td colspan="5" class="muted">No predictions yet.</td></tr>`}</tbody></table>`;
}

/* ======================================================================
   ROME PULL-QUOTES
   ====================================================================== */
const PLACEHOLDER_QUOTES = [
  "The numbers don't lie, but your draft does.",                    // Scoreboard
  "You spent a first-round pick on THAT? Bold strategy, Cotton.",   // Draft Report Card
  "Somebody in this pool is getting bullied. Check the tape.",      // Rivalries
  "The sim called it. You're just here for the receipts.",          // The Model
];
const QUOTE_IDS = ["quote-scoreboard", "quote-draft", "quote-rivalries", "quote-model"];
function renderQuotes(commentary) {
  const arr = (commentary && Array.isArray(commentary.rome_analytics_quotes)) ? commentary.rome_analytics_quotes : null;
  QUOTE_IDS.forEach((id, i) => {
    const node = el(id);
    if (!node) return;
    const q = (arr && arr[i]) ? arr[i] : PLACEHOLDER_QUOTES[i];
    node.innerHTML = `<span class="rome-mark">📻</span><span class="rome-text">${esc(q)}</span><cite>— Jim Rome</cite>`;
  });
}

/* ======================================================================
   GOLDEN BOOT (moved from the main page — same markup + data source)
   ====================================================================== */
function renderGoldenBoot(doc) {
  const leaders = (doc && doc.leaders) || [];
  const src = el("goals-src");
  if (src) src.textContent = doc && doc.source ? `SOURCE: ${doc.source}` : "";
  const box = el("player-goals");
  if (!box) return;
  box.classList.remove("loading");
  if (!leaders.length) {
    box.innerHTML = `<div class="news-empty"><p>No goals tracked yet — populates once matches are played.</p></div>`;
    return;
  }
  box.innerHTML = `<div class="boot-list">${leaders.map((r) => {
    const c = ownerColor(r.owner);
    const pens = r.penalties ? `<span class="boot-pens">${r.penalties} PEN</span>` : "";
    return `
      <div class="boot-row ${r.rank === 1 ? "top" : ""}">
        <div class="boot-rk">${r.rank}</div>
        <div class="boot-who">
          <span class="boot-player">${esc(r.player)}</span>
          <span class="boot-meta">${flag(r.team)}${esc(r.team)} · <b style="color:${c}">${esc(r.owner)}</b></span>
        </div>
        <div class="boot-goals"><span class="ball">⚽</span>${r.goals} ${pens}</div>
      </div>`;
  }).join("")}</div>`;
}

/* ======================================================================
   BOOT
   ====================================================================== */
function fail(id, msg) {
  const node = el(id);
  if (node) { node.classList && node.classList.remove("loading"); node.innerHTML = `<div class="news-empty"><p>${esc(msg)}</p></div>`; }
}
async function main() {
  // Quotes first (independent of the heavy data).
  loadJSON("data/commentary.json").then(renderQuotes).catch(() => renderQuotes(null));

  let standings, teamTable, daily, timeline, narrative, predictions;
  try {
    [standings, teamTable, daily] = await Promise.all([
      loadJSON("data/owner_standings.json"),
      loadJSON("data/team_table.json"),
      loadJSON("data/daily_results.json"),
    ]);
  } catch (e) {
    console.error(e);
    ["luck-table", "maxpts-list", "elim-grid", "dvi-table", "dep-list", "h2h-grid", "sched-list", "pred-table"]
      .forEach((id) => fail(id, `Failed to load data: ${e.message}`));
    return;
  }

  if (standings.rules_version) el("foot-rules").textContent = standings.rules_version;
  if (el("leaguebar-meta")) el("leaguebar-meta").textContent = `analytics · ${standings.rules_version || "rebalanced_v3"}`;

  const owners = ownersFrom(standings, teamTable);
  const byOwner = teamsByOwner(teamTable);
  const lastDate = lastResultsDate(daily);

  // Optional reference / context loads — each section degrades on its own.
  const [timelineR, narrativeR, predictionsR, strengthR, matchesR] = await Promise.all([
    loadJSON("data/timeline.json").catch(() => null),
    loadJSON("data/narrative_state.json").catch(() => null),
    loadJSON("data/predictions.json").catch(() => null),
    loadText("data/team_strength.csv").then(parseCSV).catch(() => []),
    loadText("data/matches.csv").then(parseCSV).catch(() => []),
  ]);
  timeline = timelineR; narrative = narrativeR; predictions = predictionsR;
  const strengthMap = buildStrengthMap(strengthR);
  const matchesMeta = matchesR;
  const eliminated = computeEliminated(daily, matchesMeta, narrative);

  // Section 1
  renderWinProb(timeline);
  renderLuck(owners, standings, timeline);
  renderMaxPoints(owners, standings, byOwner, eliminated, matchesMeta, lastDate);
  renderElimination(owners, byOwner, eliminated);
  // Section 2
  renderDVI(owners, byOwner);
  renderTierChart(owners, byOwner);
  renderDependency(owners, byOwner, narrative);
  // Section 3
  renderH2H(owners, narrative);
  renderSchedule(owners, byOwner, eliminated, matchesMeta, lastDate, strengthMap);
  // Section 4
  renderPredictions(predictions, daily);
  // Golden Boot (own data source; degrades independently)
  loadJSON("data/player_goals.json").then(renderGoldenBoot).catch(() => {
    const box = el("player-goals");
    if (box) { box.classList.remove("loading"); box.innerHTML = `<div class="news-empty"><p>No goals tracked yet.</p></div>`; }
  });
}
main();
