/* WC Challenge — renders static JSON into a Fox Sports–style layout.
   The site computes nothing; all numbers come from the JSON files. */

const OWNER_COLORS = {
  Zach:   "#f4c430",
  Gunner: "#2f6dff",
  Gayden: "#28c060",
  Devin:  "#f0743a",
};
const ownerColor = (o) => OWNER_COLORS[o] || "#8b919c";

// AI manager portraits (Nano Banana). Owners without one fall back to an
// initials circle; add the file + an entry here once their reference is generated.
const OWNER_PORTRAITS = {
  Zach:   "assets/portraits/zach_1.jpg",   // José Mourinho
  Devin:  "assets/portraits/devin_1.jpg",  // Ted Lasso
  Gunner: "assets/portraits/gunner_1.jpg", // Jesse Marsch
  Gayden: "assets/portraits/gayden_1.jpg", // Pep Guardiola
};
function avatar(owner, cls = "") {
  const c = ownerColor(owner);
  const src = OWNER_PORTRAITS[owner];
  const img = src ? `<img src="${src}" alt="${esc(owner)}" loading="lazy"
                       onerror="this.style.display='none'">` : "";
  return `<span class="owner-avatar ${cls}" style="--c:${c}" data-initial="${esc(owner[0])}">${img}</span>`;
}

/* ---------- TEAM FLAGS ----------
   Canonical team name -> ISO 3166-1 alpha-2 code (flagcdn). Keys match the
   canonical forms produced by team_aliases.json / tiers.json. England, Scotland,
   and Wales use GB sub-region codes. An unmapped name yields no flag. */
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
  // GB sub-flags
  England: "gb-eng", Scotland: "gb-sct", Wales: "gb-wls",
};
function flag(team) {
  const code = TEAM_FLAGS[team];
  if (!code) return "";
  return `<img class="flag" src="https://flagcdn.com/24x18/${code}.png" ` +
         `srcset="https://flagcdn.com/48x36/${code}.png 2x" ` +
         `width="24" height="18" alt="" loading="lazy" />`;
}

async function loadJSON(path) {
  const res = await fetch(path + "?v=" + Date.now());
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}
const el = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

function ptsPill(owner, pts) {
  const c = ownerColor(owner);
  return `<span class="pts-pill"><span class="pd" style="background:${c}"></span>${esc(owner)} ${pts > 0 ? "+" : ""}${pts}</span>`;
}

/* ---------- flatten daily_results into match objects (newest first) ---------- */
function flattenMatches(daily) {
  const out = [];
  (daily.days || []).forEach((day) => {
    (day.matches || []).forEach((m) => {
      const winner = m.home_score > m.away_score ? "home"
                   : m.away_score > m.home_score ? "away" : "draw";
      out.push({ ...m, winner });
    });
  });
  return out.reverse(); // newest day/match first for the ticker
}

/* ---------- SCORES TICKER ---------- */
function renderTicker(matches) {
  const box = el("ticker-track");
  if (!matches.length) { box.innerHTML = `<div class="ticker-loading">No matches yet.</div>`; return; }
  box.innerHTML = matches.map((m) => {
    const homeWin = m.winner === "home", awayWin = m.winner === "away";
    const pts = Object.entries(m.points || {});
    const ptsHTML = pts.length
      ? `<div class="tick-pts">${pts.map(([o, p]) => ptsPill(o, p)).join("")}</div>`
      : "";
    const status = m.round ? String(m.round).toUpperCase() : "FINAL";
    return `
      <div class="tick">
        <div class="tick-status">${esc(status)} · ${esc(m.date.slice(5))}</div>
        <div class="tick-row ${homeWin ? "win" : ""}">
          <span class="tick-team">${flag(m.home)}${esc(m.home)}</span>
          <span class="tick-score">${m.home_score}</span>
        </div>
        <div class="tick-row ${awayWin ? "win" : ""}">
          <span class="tick-team">${flag(m.away)}${esc(m.away)}</span>
          <span class="tick-score">${m.away_score}</span>
        </div>
        ${ptsHTML}
      </div>`;
  }).join("");
}

/* ---------- PUNDIT ROUNDTABLE ---------- */
const PUNDIT_FALLBACK_COLORS = {
  "Eric Wynalda": "#e2231a",
  "Landon Donovan": "#2f6dff",
  "Clint Dempsey": "#28c060",
  "Alexi Lalas": "#f4a423",
};
function warmingUp() {
  el("roundtable-cards").classList.remove("loading");
  el("roundtable-cards").innerHTML = `<div class="roundtable-warming">Pundits are warming up…</div>`;
}
function renderRoundtable(doc) {
  const box = el("roundtable-cards");
  box.classList.remove("loading");
  const pundits = (doc && doc.pundits) || [];
  const live = pundits.filter((p) => p.take && p.take.trim() && p.take.trim() !== "Pundits are warming up...");
  if (!live.length) { warmingUp(); return; }
  if (doc.source) el("roundtable-meta").textContent = `SOURCE: ${doc.source}`;
  box.innerHTML = `<div class="roundtable-grid">${pundits.map((p) => {
    const color = p.color || PUNDIT_FALLBACK_COLORS[p.name] || "#2f6dff";
    return `
      <div class="pundit-card" style="--pundit:${color}">
        <div class="pundit-head">
          <span class="pundit-name">${esc(p.name)}</span>
          ${p.tone ? `<span class="pundit-tone">${esc(p.tone)}</span>` : ""}
        </div>
        <p class="pundit-take">${esc(p.take)}</p>
      </div>`;
  }).join("")}</div>`;
}

/* ---------- HERO + STANDINGS BOARD ---------- */
function renderStandings(doc) {
  const s = doc.standings || [];
  const leader = s[0];
  if (leader) {
    el("hero-leader").innerHTML = `
      <span class="crown">👑</span>
      <span>
        <span class="who" style="color:${ownerColor(leader.owner)}">${esc(leader.owner)}</span>
        <span class="lead-pts">${leader.total_points} PTS</span><br/>
        <span class="lead-cap">leads the pool</span>
      </span>`;
  }
  const avg = s.length ? s.reduce((a, r) => a + r.total_points, 0) / s.length : 0;
  el("hero-board").innerHTML = s.map((r) => {
    const c = ownerColor(r.owner);
    const trend = r.total_points > avg ? "up" : r.total_points < avg ? "down" : "flat";
    const arrow = trend === "up" ? "▲" : trend === "down" ? "▼" : "—";
    const b = r.breakdown || {};
    const sub = [
      b.match ? `${b.match} match` : null,
      b.upset ? `${b.upset} upset` : null,
      b.advancement ? `${b.advancement} adv` : null,
    ].filter(Boolean).join(" · ") || "no points yet";
    return `
      <div class="rank-row ${r.rank === 1 ? "first" : ""}">
        <div class="rk">${r.rank}</div>
        <div class="arrow ${trend}">${arrow}</div>
        ${avatar(r.owner)}
        <div class="owner-cell">
          <span class="owner-name" style="color:${c}">${esc(r.owner)}</span>
          <span class="owner-sub">${esc(sub)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          <span class="owner-pts">${r.total_points}</span>
          <span class="bar-tab" style="background:${c}"></span>
        </div>
      </div>`;
  }).join("");
}

/* ---------- WIN PROBABILITY CHART ---------- */
let winprobChart = null;
function winprobEmpty(msg) {
  const c = el("winprob-chart");
  if (c && c.parentElement) c.parentElement.innerHTML = `<div class="news-empty"><p>${esc(msg)}</p></div>`;
}
function renderWinProb(timeline) {
  const canvas = el("winprob-chart");
  if (!canvas) return;
  const entries = (Array.isArray(timeline) ? [...timeline] : [])
    .sort((a, b) => String(a.date).localeCompare(String(b.date)) || (a.matchday || 0) - (b.matchday || 0));
  if (!entries.length) { winprobEmpty("Win probability populates once the engine runs."); return; }
  if (typeof Chart === "undefined") { winprobEmpty("Chart library unavailable."); return; }

  // owners: canonical color order first, then any extras seen in the data
  const seen = new Set();
  entries.forEach((e) => Object.keys(e.win_probability || {}).forEach((o) => seen.add(o)));
  const owners = [
    ...Object.keys(OWNER_COLORS).filter((o) => seen.has(o)),
    ...[...seen].filter((o) => !(o in OWNER_COLORS)),
  ];
  const xlabel = (e) => (e.label === "preseason" || e.matchday === 0)
    ? "Preseason" : (e.date ? e.date.slice(5) : `MD ${e.matchday}`);
  const labels = entries.map(xlabel);
  const datasets = owners.map((o) => ({
    label: o,
    data: entries.map((e) => (e.win_probability && e.win_probability[o] != null)
      ? +(e.win_probability[o] * 100).toFixed(1) : null),
    borderColor: ownerColor(o),
    backgroundColor: ownerColor(o),
    borderWidth: 2.5,
    pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: ownerColor(o),
    tension: 0.25, spanGaps: true,
  }));

  if (el("winprob-meta")) {
    el("winprob-meta").textContent = entries.length === 1
      ? "PRESEASON BASELINE" : `THROUGH ${labels[labels.length - 1]}`;
  }
  if (winprobChart) winprobChart.destroy();
  winprobChart = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#cfd3da", usePointStyle: true, pointStyleWidth: 10,
                            boxHeight: 7, font: { family: "Inter", weight: "600" } } },
        tooltip: {
          backgroundColor: "#101218", borderColor: "#262a34", borderWidth: 1,
          titleColor: "#fff", bodyColor: "#cfd3da",
          callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y}%` },
        },
      },
      scales: {
        x: { grid: { color: "#1d212a" }, ticks: { color: "#8b919c", font: { family: "Inter" } } },
        y: { min: 0, suggestedMax: 50, grid: { color: "#1d212a" },
             ticks: { color: "#8b919c", font: { family: "Inter" }, callback: (v) => v + "%" } },
      },
    },
  });
}

/* ---------- OWNER PORTFOLIOS ---------- */
function renderPortfolios(standings, teamTable) {
  const box = el("owner-portfolios");
  box.classList.remove("loading");
  const byOwner = {};
  (teamTable.teams || []).forEach((t) => { (byOwner[t.owner] = byOwner[t.owner] || []).push(t); });
  const ranked = (standings.standings || []);
  box.innerHTML = `<div class="portfolio-grid">${ranked.map((r) => {
    const c = ownerColor(r.owner);
    const teams = (byOwner[r.owner] || []).sort((a, b) => (a.tier || 9) - (b.tier || 9));
    const rows = teams.map((t) => `
      <div class="pf-team">
        <span class="tier tier-${t.tier}">T${t.tier}</span>
        <span class="pf-team-name">${flag(t.team)}${esc(t.team)}</span>
        <span class="pf-team-rec">${t.W}-${t.D}-${t.L}</span>
        <span class="pf-team-pts">${t.points}</span>
      </div>`).join("");
    return `
      <a class="portfolio-card" href="bios.html#${encodeURIComponent(r.owner.toLowerCase())}" style="--c:${c}">
        <div class="pf-head">
          ${avatar(r.owner, "lg")}
          <div class="pf-id">
            <span class="pf-name" style="color:${c}">${esc(r.owner)}</span>
            <span class="pf-sub">Rank #${r.rank} · ${r.total_points} pts</span>
          </div>
          <span class="pf-biolink">BIO →</span>
        </div>
        <div class="pf-teams">${rows}</div>
      </a>`;
  }).join("")}</div>`;
}

/* ---------- DRAFTED TEAMS TABLE ---------- */
function renderTeams(doc) {
  const teams = [...(doc.teams || [])].sort(
    (a, b) => b.points - a.points || b.W - a.W || (a.tier || 9) - (b.tier || 9) || a.team.localeCompare(b.team)
  );
  el("teams-count").textContent = `${teams.length} TEAMS`;
  const rows = teams.map((t, i) => {
    const c = ownerColor(t.owner);
    return `
      <tr>
        <td class="rk-col">${i + 1}</td>
        <td class="team-name">${flag(t.team)}${esc(t.team)}</td>
        <td><span class="owner-pill"><span class="owner-dot" style="background:${c}"></span><span style="color:${c}">${esc(t.owner)}</span></span></td>
        <td><span class="tier tier-${t.tier}">T${t.tier}</span></td>
        <td class="num">${t.W}</td>
        <td class="num">${t.D}</td>
        <td class="num">${t.L}</td>
        <td class="num pts-strong">${t.points}</td>
      </tr>`;
  }).join("");
  el("team-table").outerHTML = `
    <table id="team-table">
      <thead><tr>
        <th class="rk-col">#</th><th>Team</th><th>Owner</th><th>Tier</th>
        <th class="num">W</th><th class="num">D</th><th class="num">L</th><th class="num">Pts</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

/* ---------- LATEST RESULTS ---------- */
function renderResults(daily) {
  const days = [...(daily.days || [])].reverse();
  el("results-meta").textContent = daily.source ? `SOURCE: ${daily.source}` : "";
  el("results-list").classList.remove("loading");
  el("results-list").innerHTML = days.map((day) => {
    const cards = (day.matches || []).map((m) => {
      const homeWin = m.home_score > m.away_score, awayWin = m.away_score > m.home_score;
      const pts = Object.entries(m.points || {});
      const ptsHTML = pts.length
        ? `<div class="match-pts">${pts.map(([o, p]) => ptsPill(o, p)).join("")}</div>`
        : `<div class="match-pts none">No drafted team scored.</div>`;
      const stage = m.stage === "group" ? "GROUP STAGE" : (m.round || "KNOCKOUT");
      return `
        <div class="match-card">
          <div class="match-stage">${esc(stage)} · ${esc(m.date.slice(5))}</div>
          <div class="match-line ${homeWin ? "win" : ""}"><span class="mt">${flag(m.home)}${esc(m.home)}</span><span class="ms">${m.home_score}</span></div>
          <div class="match-line ${awayWin ? "win" : ""}"><span class="mt">${flag(m.away)}${esc(m.away)}</span><span class="ms">${m.away_score}</span></div>
          ${ptsHTML}
        </div>`;
    }).join("");
    return `
      <div class="result-day">
        <p class="result-day-label">${esc(day.date)}</p>
        <div class="result-grid">${cards}</div>
      </div>`;
  }).join("");
}

/* ---------- GOLDEN BOOT ---------- */
function renderGoldenBoot(doc) {
  const leaders = (doc && doc.leaders) || [];
  el("goals-src").textContent = doc && doc.source ? `SOURCE: ${doc.source}` : "";
  const box = el("player-goals");
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

/* ---------- BOOT ---------- */
async function main() {
  try {
    const [standings, teams, daily] = await Promise.all([
      loadJSON("data/owner_standings.json"),
      loadJSON("data/team_table.json"),
      loadJSON("data/daily_results.json"),
    ]);

    renderStandings(standings);
    renderPortfolios(standings, teams);
    renderTeams(teams);
    renderResults(daily);
    renderTicker(flattenMatches(daily));

    const v = standings.rules_version || "rebalanced_v3";
    el("leaguebar-meta").textContent = `scoring · ${v}`;
    el("foot-rules").textContent = v;
    el("foot-src").textContent = standings.source || "—";

    loadJSON("data/timeline.json").then(renderWinProb)
      .catch(() => winprobEmpty("Win probability populates once the engine runs."));

    loadJSON("data/commentary.json").then(renderRoundtable).catch(warmingUp);

    loadJSON("data/player_goals.json").then(renderGoldenBoot).catch(() => {
      el("player-goals").classList.remove("loading");
      el("player-goals").innerHTML = `<div class="news-empty"><p>No goals tracked yet.</p></div>`;
    });
  } catch (e) {
    console.error(e);
    el("hero-board").innerHTML = `<div class="loading">Failed to load data: ${esc(e.message)}</div>`;
  }
}
main();
