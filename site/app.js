/* WC Challenge — renders static JSON into a Fox Sports–style layout.
   The site computes nothing; all numbers come from the JSON files. */

const OWNER_COLORS = {
  Zach:   "#f4c430",
  Gunner: "#2f6dff",
  Gayden: "#28c060",
  Devin:  "#f0743a",
};
const ownerColor = (o) => OWNER_COLORS[o] || "#8b919c";

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
          <span class="tick-team">${esc(m.home)}</span>
          <span class="tick-score">${m.home_score}</span>
        </div>
        <div class="tick-row ${awayWin ? "win" : ""}">
          <span class="tick-team">${esc(m.away)}</span>
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
        <td class="team-name">${esc(t.team)}</td>
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
          <div class="match-line ${homeWin ? "win" : ""}"><span class="mt">${esc(m.home)}</span><span class="ms">${m.home_score}</span></div>
          <div class="match-line ${awayWin ? "win" : ""}"><span class="mt">${esc(m.away)}</span><span class="ms">${m.away_score}</span></div>
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
          <span class="boot-meta">${esc(r.team)} · <b style="color:${c}">${esc(r.owner)}</b></span>
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
    renderTeams(teams);
    renderResults(daily);
    renderTicker(flattenMatches(daily));

    const v = standings.rules_version || "rebalanced_v3";
    el("leaguebar-meta").textContent = `scoring · ${v}`;
    el("foot-rules").textContent = v;
    el("foot-src").textContent = standings.source || "—";

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
