/* WC Challenge — Analytics dashboard.
   Self-contained (does not depend on app.js). Reads the same static JSON the
   home page does, plus two served reference CSVs (team_strength.csv, matches.csv),
   and computes the analytics client-side. The engine still computes the truth;
   this page only re-presents what the data already says.

   Layout: a dense tiled grid (Bloomberg/Geckoboard feel). Each section is a CSS
   grid whose container colour shows through 1px gaps as gap-lines; tiles paint
   over it. JS builds each section's tiles (incl. the spanning header) as innerHTML,
   then initialises any Chart.js canvases inside them.

   Sections:  THE RACE · ELIMINATION WATCH · THE DRAFT · RIVALRIES · DEEP STATS · WIN PROB TREND
*/

/* ---------- shared constants / helpers (mirrors app.js) ---------- */
const OWNER_COLORS = { Zach: "#f4c430", Gunner: "#2f6dff", Gayden: "#28c060", Devin: "#f0743a", Rafe: "#a855f7" };
const OWNER_ORDER = Object.keys(OWNER_COLORS);
const ownerColor = (o) => OWNER_COLORS[o] || "#5a6070";
const TIER_COLORS = { 1: "#6f42c1", 2: "#1f6feb", 3: "#2d8a5a", 4: "#5a626d" };

/* canvas can't read CSS vars — hardcode the dashboard palette for charts */
const C = {
  num: "#e8eaf0", label: "#5a6070", label2: "#8890a4",
  grid: "#1c2030", gridSoft: "#171a26", tip: "#0c0e14", tipLine: "#262a34",
  whiteFaint: "rgba(255,255,255,0.06)",
};
const fill25 = (hex) => hex + "40"; // ~25% alpha for chart fills

const el = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const fmtNum = (n) => {
  const v = Math.round(Number(n) * 100) / 100;
  return Number.isFinite(v) ? (v === Math.trunc(v) ? String(Math.trunc(v)) : String(v)) : "0";
};
const signed = (n) => (Number(n) > 0 ? "+" : "") + fmtNum(n);
const pct = (n, d = 0) => `${(Number(n) * 100).toFixed(d)}%`;
const ordinal = (n) => {
  const v = Number(n) || 0, t = v % 100;
  if (t >= 11 && t <= 13) return v + "th";
  return v + (["th", "st", "nd", "rd"][v % 10] || "th");
};
const abbr = (o) => String(o).slice(0, 3).toUpperCase();

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
/* Sum of per-owner points banked on the most recent results day. */
function lastDayDeltas(daily) {
  const days = (daily && daily.days) || [];
  if (!days.length) return { date: null, deltas: {} };
  const last = days[days.length - 1];
  const deltas = {};
  (last.matches || []).forEach((m) => {
    const pts = m.points || {};
    Object.keys(pts).forEach((o) => { deltas[o] = (deltas[o] || 0) + (Number(pts[o]) || 0); });
  });
  return { date: last.date, deltas };
}

/* ---------- section scaffolding ---------- */
function headTile(label, sub) {
  return `<div class="tile tile-head"><span class="sec-head">${esc(label)}</span>` +
         (sub ? `<span class="sec-sub">${esc(sub)}</span>` : "") + `</div>`;
}
function section(id, label, sub, tilesHTML) {
  const node = el(id);
  if (node) node.innerHTML = headTile(label, sub) + tilesHTML;
}

/* ======================================================================
   THE RACE — KPI cards + win prob + gap to leader + biggest mover
   ====================================================================== */
function renderRace(owners, standings, timeline, daily, narrative) {
  const std = (standings.standings || []).slice();
  const byOwner = Object.fromEntries(std.map((s) => [s.owner, s]));
  const ranked = std.slice().sort((a, b) => (a.rank || 99) - (b.rank || 99));
  const { date: lastDate, deltas } = lastDayDeltas(daily);

  /* 5 KPI tiles, in standings/rank order */
  const kpis = ranked.map((s) => {
    const c = ownerColor(s.owner);
    const d = deltas[s.owner];
    const dLabel = (d == null || !lastDate)
      ? `${ordinal(s.rank)} · preseason`
      : `${ordinal(s.rank)} · <span class="${d > 0 ? "pos" : d < 0 ? "neg" : "neu"}">${signed(d)}</span> today`;
    return `<div class="tile kpi span3" style="--c:${c}">
      <div class="t-label">${esc(s.owner)}</div>
      <div class="t-big">${fmtNum(s.total_points)}</div>
      <div class="t-sub">${dLabel}</div>
    </div>`;
  }).join("");

  /* win probability — horizontal bars (init after innerHTML set) */
  const winTile = `<div class="tile span5">
    <div class="t-cap">Win probability</div>
    <div class="chart-sm"><canvas id="winprob-chart"></canvas></div>
  </div>`;

  /* gap to leader */
  const lead = ranked[0];
  const leadPts = lead ? Number(lead.total_points) || 0 : 0;
  const gapRows = ranked.map((s, i) => {
    const c = ownerColor(s.owner);
    const gap = (Number(s.total_points) || 0) - leadPts;
    const val = i === 0 ? `<span class="neu">leader</span>` : `<span class="num">${signed(gap)}</span>`;
    return `<div class="row"><span class="dot" style="--c:${c}"></span><span class="nm">${esc(s.owner)}</span><span class="val">${val}</span></div>`;
  }).join("");
  const gapTile = `<div class="tile span5">
    <div class="t-cap">Gap to leader</div>
    <div class="rows">${gapRows}</div>
  </div>`;

  /* biggest mover + upset of the day */
  let moverBody;
  const moverEntries = Object.entries(deltas).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const events = (narrative && Array.isArray(narrative.notable_events)) ? narrative.notable_events : [];
  const upset = events.filter((e) => e && e.type === "upset")
    .sort((a, b) => (Number(b.tier_gap || b.gap || 0)) - (Number(a.tier_gap || a.gap || 0)))[0] || null;
  if (!lastDate && !upset) {
    moverBody = `<p class="empty-note">No matches played yet — movers appear after the first results day.</p>`;
  } else {
    const mv = moverEntries[0];
    const moverLine = mv
      ? `<div class="t-mid"><span class="dot" style="--c:${ownerColor(mv[0])}"></span>${esc(mv[0])} <span class="pos">${signed(mv[1])}</span></div>
         <div class="t-sub">most points banked${lastDate ? ` · ${esc(lastDate.slice(5))}` : ""}</div>`
      : `<div class="t-sub">No owner gained points on the last day.</div>`;
    const upsetLine = upset
      ? `<div class="draft-meta" style="margin-top:11px"><span class="badge good">Upset</span> ${esc(upset.description || upset.summary || upset.team || "")}</div>`
      : ``;
    moverBody = moverLine + upsetLine;
  }
  const moverTile = `<div class="tile span5">
    <div class="t-cap">Biggest mover</div>
    ${moverBody}
  </div>`;

  section("sec-race", "The race", lead ? `LEADER ${esc(lead.owner)}` : "", kpis + winTile + gapTile + moverTile);

  /* charts after the DOM exists */
  initWinProbBar(timeline);
}

let winprobBar = null;
function initWinProbBar(timeline) {
  const canvas = el("winprob-chart");
  if (!canvas) return;
  const last = latestTimeline(timeline);
  const wp = (last && last.win_probability) || {};
  const owners = [...OWNER_ORDER.filter((o) => o in wp), ...Object.keys(wp).filter((o) => !OWNER_ORDER.includes(o))];
  if (!owners.length || typeof Chart === "undefined") {
    canvas.parentElement.innerHTML = `<p class="empty-note">Win probability populates once the engine runs.</p>`;
    return;
  }
  const data = owners.map((o) => +(wp[o] * 100).toFixed(1));
  const colors = owners.map(ownerColor);
  if (winprobBar) winprobBar.destroy();
  winprobBar = new Chart(canvas, {
    type: "bar",
    data: {
      labels: owners,
      datasets: [{
        data,
        backgroundColor: colors.map(fill25),
        borderColor: colors,
        borderWidth: 1,
        barThickness: "flex",
        maxBarThickness: 18,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: C.tip, borderColor: C.tipLine, borderWidth: 1, titleColor: C.num, bodyColor: C.label2,
          callbacks: { label: (ctx) => ` ${ctx.parsed.x}%` },
        },
      },
      scales: {
        x: { min: 0, suggestedMax: 45, grid: { color: C.gridSoft }, ticks: { color: C.label, font: { family: "Inter", size: 10 }, callback: (v) => v + "%" } },
        y: { grid: { display: false }, ticks: { color: C.label2, font: { family: "Inter", size: 11, weight: "600" } } },
      },
    },
  });
}

/* ======================================================================
   ELIMINATION WATCH — N/6 alive + flag chips per owner
   ====================================================================== */
// Eliminated = knockout losers (from narrative_state) + last-place finishers in
// a COMPLETED group (4th of 4 can never advance, so this is conservative/safe).
function computeEliminated(daily, matchesMeta, narrative) {
  const out = new Set();
  try {
    ((narrative && narrative.phase && narrative.phase.eliminated_teams) || []).forEach((e) => {
      const t = (e && e.team) || (typeof e === "string" ? e : null);
      if (t) out.add(t);
    });
  } catch (_) { /* ignore */ }

  try {
    if (!matchesMeta || !matchesMeta.length) return out;
    const teamGroup = {}; const groupTeams = {};
    matchesMeta.forEach((m) => {
      if (m.phase !== "group" || !m.group) return;
      [m.team1, m.team2].forEach((t) => {
        if (!t || t === "TBD") return;
        teamGroup[t] = m.group;
        (groupTeams[m.group] = groupTeams[m.group] || new Set()).add(t);
      });
    });
    const tbl = {};
    const ck = (t) => (tbl[t] = tbl[t] || { pts: 0, gd: 0, gf: 0, played: 0 });
    ((daily && daily.days) || []).forEach((day) => (day.matches || []).forEach((m) => {
      if (String(m.stage || "").toLowerCase() !== "group") return;
      const h = ck(m.home), a = ck(m.away);
      const hs = m.home_score, as = m.away_score;
      h.played++; a.played++; h.gf += hs; a.gf += as; h.gd += hs - as; a.gd += as - hs;
      if (hs > as) h.pts += 3; else if (as > hs) a.pts += 3; else { h.pts += 1; a.pts += 1; }
    }));
    Object.entries(groupTeams).forEach(([, teams]) => {
      const ts = [...teams];
      if (ts.length < 4) return;
      const complete = ts.every((t) => (tbl[t] && tbl[t].played >= 3));
      if (!complete) return;
      const ranked = ts.slice().sort((x, y) => {
        const X = tbl[x], Y = tbl[y];
        return (Y.pts - X.pts) || (Y.gd - X.gd) || (Y.gf - X.gf) || x.localeCompare(y);
      });
      out.add(ranked[ranked.length - 1]);
    });
  } catch (_) { /* degrade to KO-only */ }
  return out;
}
function renderElimination(owners, byOwner, eliminated) {
  const tiles = owners.map((o) => {
    const c = ownerColor(o);
    const teams = (byOwner[o] || []).slice().sort((a, b) => (a.tier || 9) - (b.tier || 9));
    const aliveN = teams.filter((t) => !eliminated.has(t.team)).length;
    const chips = teams.map((t) => {
      const dead = eliminated.has(t.team);
      return `<span class="chip ${dead ? "dead" : ""}" title="${esc(t.team)}${dead ? " — out" : ""}">${flag(t.team)}</span>`;
    }).join("");
    return `<div class="tile" style="--c:${c}">
      <div class="t-label"><span class="dot" style="--c:${c}"></span>${esc(o)}</div>
      <div class="t-big">${aliveN}<span class="t-sub" style="margin:0 0 0 2px">/${teams.length}</span></div>
      <div class="chips">${chips}</div>
    </div>`;
  }).join("");
  section("sec-elim", "Elimination watch", "TEAMS STILL ALIVE", tiles);
}

/* ======================================================================
   THE DRAFT — best steal · worst bust · most dependent · most balanced
   ====================================================================== */
function tierSlots(teams) {
  const byTier = { 1: [], 2: [], 3: [], 4: [] };
  teams.forEach((t) => { if (byTier[t.tier]) byTier[t.tier].push(Number(t.points) || 0); });
  const avg = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
  return { 1: avg(byTier[1]), 2: avg(byTier[2]), 3: avg(byTier[3]), 4: avg(byTier[4]) };
}
function renderDraft(owners, byOwner, narrative) {
  const slots = Object.fromEntries(owners.map((o) => [o, tierSlots(byOwner[o] || [])]));
  const tierAvg = {};
  [1, 2, 3, 4].forEach((t) => { tierAvg[t] = owners.reduce((s, o) => s + slots[o][t], 0) / (owners.length || 1); });

  // best steal = biggest positive (owner-tier value − field avg) that is above avg;
  // worst bust = biggest negative that is below avg.
  let steal = null, bust = null;
  owners.forEach((o) => [1, 2, 3, 4].forEach((t) => {
    const v = slots[o][t], d = v - tierAvg[t];
    if (d > 1e-9 && (!steal || d > steal.d)) steal = { o, t, v, d };
    if (d < -1e-9 && (!bust || d < bust.d)) bust = { o, t, v, d };
  }));
  const teamsInSlot = (o, t) => (byOwner[o] || []).filter((x) => x.tier === t).map((x) => x.team);

  const draftCard = (cap, pick, badgeCls, badgeText) => {
    if (!pick) return `<div class="tile"><div class="t-cap">${esc(cap)}</div><p class="empty-note">No separation yet — awaiting results.</p></div>`;
    const c = ownerColor(pick.o);
    const names = teamsInSlot(pick.o, pick.t);
    const namesHTML = names.map((n) => `${flag(n)}${esc(n)}`).join(", ");
    return `<div class="tile" style="--c:${c}">
      <div class="t-cap">${esc(cap)}</div>
      <div class="t-mid"><span class="dot" style="--c:${c}"></span>${esc(pick.o)} <span class="badge ${badgeCls}">${esc(badgeText)}</span></div>
      <div class="draft-meta">Tier ${pick.t} slot · ${fmtNum(pick.v)} pts (${signed(pick.d)} vs field)</div>
      <div class="draft-team">${namesHTML || "—"}</div>
    </div>`;
  };

  // dependency: % of an owner's points from their Tier-1 team
  const fromState = (narrative && narrative.dependency_index) || null;
  const deps = owners.map((o) => {
    const teams = byOwner[o] || [];
    const total = teams.reduce((s, t) => s + (Number(t.points) || 0), 0);
    const t1 = teams.find((t) => t.tier === 1);
    const t1pts = t1 ? (Number(t1.points) || 0) : 0;
    let dep = (fromState && fromState[o] != null) ? Number(fromState[o]) : (total > 0 ? t1pts / total : 0);
    if (dep > 1) dep = dep / 100;
    return { o, dep, total, t1: t1 ? t1.team : "—" };
  });
  const withPts = deps.filter((d) => d.total > 0);
  const mostDep = withPts.slice().sort((a, b) => b.dep - a.dep)[0] || null;
  const mostBal = withPts.slice().sort((a, b) => a.dep - b.dep)[0] || null;

  const depCard = (cap, pick, note) => {
    if (!pick) return `<div class="tile"><div class="t-cap">${esc(cap)}</div><p class="empty-note">No points banked yet.</p></div>`;
    const c = ownerColor(pick.o);
    const w = Math.max(0, Math.min(100, pick.dep * 100));
    return `<div class="tile" style="--c:${c}">
      <div class="t-cap">${esc(cap)}</div>
      <div class="t-big">${pct(pick.dep, 0)}</div>
      <div class="t-sub"><span class="dot" style="--c:${c}"></span>${esc(pick.o)} · ${esc(note)} ${flag(pick.t1)}${esc(pick.t1)}</div>
      <div class="pbar" style="--c:${c}"><span style="width:${w}%"></span></div>
    </div>`;
  };

  const tiles = draftCard("Best steal", steal, "good", "Steal")
    + draftCard("Worst bust", bust, "bad", "Bust")
    + depCard("Most dependent", mostDep, "from")
    + depCard("Most balanced", mostBal, "from");
  section("sec-draft", "The draft", "VALUE & FRAGILITY", tiles);
}

/* ======================================================================
   RIVALRIES — 3 H2H matchups + max points remaining chart
   ====================================================================== */
function renderRivalries(owners, narrative, standings, byOwner, eliminated, matchesMeta, lastDate) {
  const matrix = (narrative && narrative.head_to_head_matrix) || {};
  const diff = (narrative && narrative.h2h_differential) || null;

  // build candidate pairs with games played + |differential|
  const pairs = [];
  for (let i = 0; i < owners.length; i++)
    for (let j = i + 1; j < owners.length; j++) {
      const a = owners[i], b = owners[j];
      const rec = (matrix[a] && matrix[a][b]) || { W: 0, D: 0, L: 0 };
      const games = (rec.W || 0) + (rec.D || 0) + (rec.L || 0);
      const d = (diff && diff[a] && diff[a][b] != null) ? Number(diff[a][b]) : null;
      pairs.push({ a, b, rec, games, d });
    }
  // most interesting = most games played, then closest differential
  const played = pairs.filter((p) => p.games > 0)
    .sort((a, b) => (b.games - a.games) || (Math.abs(a.d || 0) - Math.abs(b.d || 0)));
  const chosen = played.slice(0, 3);
  while (chosen.length < 3 && pairs[chosen.length]) chosen.push(pairs[chosen.length]); // fill layout in preseason

  const h2hTiles = chosen.map((p) => {
    const ca = ownerColor(p.a), cb = ownerColor(p.b);
    const head = `<div class="h2h-vs"><span class="dot" style="--c:${ca}"></span>${esc(p.a)} <span class="neu">vs</span> ${esc(p.b)} <span class="dot" style="--c:${cb}"></span></div>`;
    if (!p.games) {
      return `<div class="tile"><div class="t-cap">Head to head</div>${head}<p class="empty-note">Not yet played.</p></div>`;
    }
    const leader = p.d == null ? null : (p.d > 0 ? p.a : p.d < 0 ? p.b : null);
    const lc = leader === p.a ? ca : leader === p.b ? cb : C.label;
    const line = leader
      ? `<span style="color:${lc}">${esc(leader)}</span> ahead`
      : `dead even`;
    const swing = p.d == null ? "" : `<span class="${p.d >= 0 ? "pos" : "neg"}">${signed(p.d)} pts</span> · `;
    return `<div class="tile"><div class="t-cap">Head to head</div>
      ${head}
      <div class="h2h-rec">${p.rec.W}–${p.rec.D}–${p.rec.L}</div>
      <div class="h2h-line">${swing}${line}</div>
    </div>`;
  }).join("");

  // max points tile = current vs ceiling chart
  const maxTile = `<div class="tile">
    <div class="t-cap">Max points remaining</div>
    <div class="chart-sm"><canvas id="maxpts-chart"></canvas></div>
  </div>`;

  section("sec-rivalries", "Rivalries", "OWNER VS OWNER", h2hTiles + maxTile);
  initMaxPoints(owners, standings, byOwner, eliminated, matchesMeta, lastDate);
}

/* ceiling model: group win = 3 + best-case upset bonus per remaining group game;
   full knockout run = 5×3 + advancement bonuses (2+5+10+18+30) = 80 */
const KO_MAX = 80;
function maxGroupWin(tier) { return 3 + 2 * Math.max(0, (Number(tier) || 4) - 1); }
function remainingGroupGames(team, matchesMeta, lastDate, played) {
  if (matchesMeta && matchesMeta.length) {
    return matchesMeta.filter((m) =>
      m.phase === "group" && (!lastDate || m.date > lastDate) &&
      (m.team1 === team || m.team2 === team)).length;
  }
  return Math.max(0, 3 - played);
}
let maxptsChart = null;
function initMaxPoints(owners, standings, byOwner, eliminated, matchesMeta, lastDate) {
  const canvas = el("maxpts-chart");
  if (!canvas || typeof Chart === "undefined") { if (canvas) canvas.parentElement.innerHTML = `<p class="empty-note">Chart library unavailable.</p>`; return; }
  const pointsOf = Object.fromEntries((standings.standings || []).map((s) => [s.owner, Number(s.total_points) || 0]));
  const rows = owners.map((o) => {
    const cur = pointsOf[o] || 0;
    let ceiling = cur;
    (byOwner[o] || []).forEach((t) => {
      if (eliminated.has(t.team)) return;
      const played = (t.W || 0) + (t.D || 0) + (t.L || 0);
      const rg = remainingGroupGames(t.team, matchesMeta, lastDate, played);
      ceiling += rg * maxGroupWin(t.tier) + KO_MAX;
    });
    return { o, cur, ceiling };
  });
  const colors = owners.map(ownerColor);
  if (maxptsChart) maxptsChart.destroy();
  maxptsChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: owners,
      datasets: [
        { label: "Now", data: rows.map((r) => r.cur), backgroundColor: colors, borderWidth: 0, stack: "s", barThickness: "flex", maxBarThickness: 18 },
        { label: "Headroom", data: rows.map((r) => Math.max(0, r.ceiling - r.cur)), backgroundColor: C.whiteFaint, borderWidth: 0, stack: "s", barThickness: "flex", maxBarThickness: 18 },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: C.tip, borderColor: C.tipLine, borderWidth: 1, titleColor: C.num, bodyColor: C.label2,
          callbacks: { label: (ctx) => ctx.datasetIndex === 0 ? ` now ${fmtNum(rows[ctx.dataIndex].cur)}` : ` ceiling ${fmtNum(rows[ctx.dataIndex].ceiling)}` },
        },
      },
      scales: {
        x: { stacked: true, beginAtZero: true, grid: { color: C.gridSoft }, ticks: { color: C.label, font: { family: "Inter", size: 10 } } },
        y: { stacked: true, grid: { display: false }, ticks: { color: C.label2, font: { family: "Inter", size: 11, weight: "600" } } },
      },
    },
  });
}

/* ======================================================================
   DEEP STATS — luck · model accuracy · schedule difficulty · golden boot
   ====================================================================== */
function renderLuck(owners, standings, timeline) {
  const pointsOf = Object.fromEntries((standings.standings || []).map((s) => [s.owner, s.total_points]));
  const last = latestTimeline(timeline);
  const proj = (last && last.projected_points) || {};
  const rows = owners.map((o) => {
    const c = ownerColor(o);
    const actual = Number(pointsOf[o] || 0);
    const p = proj[o];
    if (!p) return `<div class="row"><span class="dot" style="--c:${c}"></span><span class="nm">${esc(o)}</span><span class="badge flat">No proj</span></div>`;
    const delta = actual - p.median;
    let cls = "flat", text = "On pace";
    if (actual > p.p90) { cls = "good"; text = "Hot"; }
    else if (actual < p.p10) { cls = "bad"; text = "Cursed"; }
    return `<div class="row"><span class="dot" style="--c:${c}"></span><span class="nm">${esc(o)}</span>
      <span class="val ${delta >= 0 ? "pos" : "neg"}">${signed(delta)}</span><span class="badge ${cls}">${text}</span></div>`;
  }).join("");
  return `<div class="tile">
    <div class="t-cap">Luck score</div>
    <div class="rows">${rows}</div>
  </div>`;
}
function renderModelTile(predictions, daily) {
  const preds = (predictions && predictions.predictions) || [];
  const idx = playedIndex(daily);
  let brierSum = 0, scored = 0, correct = 0;
  preds.forEach((p) => {
    const pr = p.predicted || {};
    const probs = { team1: Number(pr.team1_win) || 0, draw: Number(pr.draw) || 0, team2: Number(pr.team2_win) || 0 };
    const pick = ["team1", "draw", "team2"].reduce((a, b) => (probs[b] > probs[a] ? b : a), "team1");
    const act = resolveActual(p, idx);
    if (!act) return;
    scored++;
    const oneHot = { team1: act.outcome === "team1" ? 1 : 0, draw: act.outcome === "draw" ? 1 : 0, team2: act.outcome === "team2" ? 1 : 0 };
    brierSum += ["team1", "draw", "team2"].reduce((s, k) => s + (probs[k] - oneHot[k]) ** 2, 0);
    if (pick === act.outcome) correct++;
  });
  const meta = (predictions && predictions.meta) || {};
  const brier = (meta.brier_score != null) ? Number(meta.brier_score) : (scored ? brierSum / scored : null);
  const winPct = (meta.correct_winner_pct != null) ? Number(meta.correct_winner_pct) : (scored ? correct / scored : null);
  const total = preds.length || meta.total_predictions || 0;
  return `<div class="tile">
    <div class="t-cap">Model accuracy</div>
    <div class="stat3">
      <div><div class="s-num">${brier == null ? "—" : brier.toFixed(3)}</div><div class="s-lbl">Brier score (lower better)</div></div>
      <div><div class="s-num">${winPct == null ? "—" : pct(winPct <= 1 ? winPct : winPct / 100, 0)}</div><div class="s-lbl">Correct winner call</div></div>
      <div><div class="s-num">${scored}<span class="neu" style="font-size:14px"> / ${total}</span></div><div class="s-lbl">Predictions scored</div></div>
    </div>
  </div>`;
}
function playedIndex(daily) {
  const idx = {};
  ((daily && daily.days) || []).forEach((day) => (day.matches || []).forEach((m) => {
    const key = [m.home, m.away].sort().join(" :: ");
    idx[key] = { home: m.home, away: m.away, hs: m.home_score, as: m.away_score };
  }));
  return idx;
}
function resolveActual(p, idx) {
  if (p.actual && typeof p.actual === "object") {
    const w = p.actual.winner || p.actual.result;
    if (w === "team1" || w === "team2" || w === "draw") return { outcome: w, score: p.actual.score || null };
  }
  const rec = idx[[p.team1, p.team2].sort().join(" :: ")];
  if (!rec) return null;
  const s1 = rec.home === p.team1 ? rec.hs : rec.as;
  const s2 = rec.home === p.team1 ? rec.as : rec.hs;
  const outcome = s1 > s2 ? "team1" : s2 > s1 ? "team2" : "draw";
  return { outcome, score: `${s1}-${s2}` };
}
function buildStrengthMap(strengthRows) {
  const m = {};
  strengthRows.forEach((r) => { if (r.team) m[r.team] = Number(r.strength_rating) || null; });
  return m;
}
function strengthOf(team, strengthMap) {
  const key = STRENGTH_ALIASES[team] || team;
  return strengthMap[key] != null ? strengthMap[key] : null;
}
function renderScheduleTile(owners, byOwner, eliminated, matchesMeta, lastDate, strengthMap) {
  if (!matchesMeta.length || !Object.keys(strengthMap).length) {
    return `<div class="tile"><div class="t-cap">Schedule difficulty</div><p class="empty-note">Needs fixture + strength reference data.</p></div>`;
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
    return { o, count: opps.length, avg };
  });
  const ranked = rows.slice().sort((a, b) => (b.avg || 0) - (a.avg || 0));
  const body = ranked.map((r, i) => {
    const c = ownerColor(r.o);
    const val = r.avg == null ? "—" : Math.round(r.avg);
    return `<div class="row"><span class="rk">${i + 1}</span><span class="dot" style="--c:${c}"></span>
      <span class="nm">${esc(r.o)}</span><span class="val">${val}</span></div>`;
  }).join("");
  return `<div class="tile"><div class="t-cap">Schedule difficulty</div><div class="rows">${body}</div></div>`;
}
function renderBootTile(doc) {
  const leaders = ((doc && doc.leaders) || []).slice(0, 4);
  if (!leaders.length) {
    return `<div class="tile"><div class="t-cap">Golden boot</div><p class="empty-note">No goals tracked yet — populates once matches are played.</p></div>`;
  }
  const body = leaders.map((r) => {
    const c = ownerColor(r.owner);
    return `<div class="boot-row"><span class="rk">${r.rank}</span>
      <span class="nm">${flag(r.team)}${esc(r.player)} <span class="neu" style="font-size:11px">${esc(r.owner)}</span></span>
      <span class="g">${r.goals}</span></div>`;
  }).join("");
  return `<div class="tile" style="--c:${ownerColor((leaders[0] || {}).owner)}"><div class="t-cap">Golden boot</div><div class="boot">${body}</div></div>`;
}
function renderDeep(owners, standings, timeline, predictions, daily, byOwner, eliminated, matchesMeta, lastDate, strengthMap, goalsDoc) {
  const tiles = renderLuck(owners, standings, timeline)
    + renderModelTile(predictions, daily)
    + renderScheduleTile(owners, byOwner, eliminated, matchesMeta, lastDate, strengthMap)
    + renderBootTile(goalsDoc);
  section("sec-deep", "Deep stats", "UNDER THE HOOD", tiles);
}

/* ======================================================================
   WIN PROB TREND — full-width line chart with accent-dot legend
   ====================================================================== */
let trendChart = null;
function renderTrend(timeline) {
  const entries = (Array.isArray(timeline) ? [...timeline] : [])
    .sort((a, b) => String(a.date).localeCompare(String(b.date)) || (a.matchday || 0) - (b.matchday || 0));
  const seen = new Set();
  entries.forEach((e) => Object.keys(e.win_probability || {}).forEach((o) => seen.add(o)));
  const owners = [...OWNER_ORDER.filter((o) => seen.has(o)), ...[...seen].filter((o) => !OWNER_ORDER.includes(o))];
  const legend = owners.map((o) => `<span><span class="dot" style="--c:${ownerColor(o)}"></span>${esc(abbr(o))}</span>`).join("");

  let body;
  if (entries.length < 2) {
    body = `<p class="empty-note">A single preseason snapshot so far — the trend line draws once results accumulate.</p>`;
  } else {
    body = `<div class="legend" style="margin-bottom:6px">${legend}</div><div class="chart-xs"><canvas id="trend-chart"></canvas></div>`;
  }
  section("sec-trend", "Win prob trend", "CHANCE OF FINISHING #1", `<div class="tile">${body}</div>`);
  if (entries.length >= 2) initTrend(entries, owners);
}
function initTrend(entries, owners) {
  const canvas = el("trend-chart");
  if (!canvas || typeof Chart === "undefined") return;
  const xlabel = (e) => (e.label === "preseason" || e.matchday === 0) ? "Pre" : (e.date ? e.date.slice(5) : `MD ${e.matchday}`);
  const labels = entries.map(xlabel);
  const datasets = owners.map((o) => ({
    label: o,
    data: entries.map((e) => (e.win_probability && e.win_probability[o] != null) ? +(e.win_probability[o] * 100).toFixed(1) : null),
    borderColor: ownerColor(o), backgroundColor: ownerColor(o),
    borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 3, tension: 0.25, spanGaps: true,
  }));
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: C.tip, borderColor: C.tipLine, borderWidth: 1, titleColor: C.num, bodyColor: C.label2,
          callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y}%` } },
      },
      scales: {
        x: { grid: { color: C.gridSoft }, ticks: { color: C.label, font: { family: "Inter", size: 10 } } },
        y: { min: 0, suggestedMax: 50, grid: { color: C.gridSoft }, ticks: { color: C.label, font: { family: "Inter", size: 10 }, callback: (v) => v + "%" } },
      },
    },
  });
}

/* ======================================================================
   ROME PULL-QUOTES — two floating quotes (Race↓Elimination, Rivalries↓Deep)
   ====================================================================== */
const PLACEHOLDER_QUOTES = [
  "The numbers don't lie, but your draft might.",
  "Somebody in this pool is getting bullied. Check the tape.",
];
const QUOTE_IDS = ["rome-1", "rome-2"];
function renderQuotes(commentary) {
  const arr = (commentary && Array.isArray(commentary.rome_analytics_quotes)) ? commentary.rome_analytics_quotes : null;
  QUOTE_IDS.forEach((id, i) => {
    const node = el(id);
    if (!node) return;
    const q = (arr && arr[i]) ? arr[i] : PLACEHOLDER_QUOTES[i];
    if (!q) { node.hidden = true; return; }
    node.hidden = false;
    node.innerHTML = `${esc(q)} <cite>— Jim Rome</cite>`;
  });
}

/* ======================================================================
   BOOT
   ====================================================================== */
function failAll(msg) {
  ["sec-race", "sec-elim", "sec-draft", "sec-rivalries", "sec-deep", "sec-trend"].forEach((id) => {
    const node = el(id);
    if (node) node.innerHTML = headTile("Analytics", "") + `<div class="tile" style="grid-column:1/-1"><p class="empty-note">${esc(msg)}</p></div>`;
  });
}
async function main() {
  loadJSON("data/commentary.json").then(renderQuotes).catch(() => renderQuotes(null));

  let standings, teamTable, daily;
  try {
    [standings, teamTable, daily] = await Promise.all([
      loadJSON("data/owner_standings.json"),
      loadJSON("data/team_table.json"),
      loadJSON("data/daily_results.json"),
    ]);
  } catch (e) {
    console.error(e);
    failAll(`Failed to load data: ${e.message}`);
    return;
  }

  if (standings.rules_version && el("foot-rules")) el("foot-rules").textContent = standings.rules_version;
  if (el("leaguebar-meta")) el("leaguebar-meta").textContent = `analytics · ${standings.rules_version || "rebalanced_v3"}`;

  const owners = ownersFrom(standings, teamTable);
  const byOwner = teamsByOwner(teamTable);
  const lastDate = lastResultsDate(daily);

  const [timeline, narrative, predictions, strengthR, matchesR, goalsDoc] = await Promise.all([
    loadJSON("data/timeline.json").catch(() => null),
    loadJSON("data/narrative_state.json").catch(() => null),
    loadJSON("data/predictions.json").catch(() => null),
    loadText("data/team_strength.csv").then(parseCSV).catch(() => []),
    loadText("data/matches.csv").then(parseCSV).catch(() => []),
    loadJSON("data/player_goals.json").catch(() => null),
  ]);
  const strengthMap = buildStrengthMap(strengthR);
  const matchesMeta = matchesR;
  const eliminated = computeEliminated(daily, matchesMeta, narrative);

  renderRace(owners, standings, timeline, daily, narrative);
  renderElimination(owners, byOwner, eliminated);
  renderDraft(owners, byOwner, narrative);
  renderRivalries(owners, narrative, standings, byOwner, eliminated, matchesMeta, lastDate);
  renderDeep(owners, standings, timeline, predictions, daily, byOwner, eliminated, matchesMeta, lastDate, strengthMap, goalsDoc);
  renderTrend(timeline);
}
main();
