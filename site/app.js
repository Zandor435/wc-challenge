// WC Challenge — renders static JSON into the page. Computes nothing.
const OWNER_COLORS = { Zach: "#f0c040", Gunner: "#1f6feb", Gayden: "#4cc38a", Devin: "#e0603e" };

async function loadJSON(path) {
  const res = await fetch(path + "?v=" + Date.now()); // cache-bust so JSON updates show
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function renderLeaderboard(standings) {
  const el = document.getElementById("leaderboard");
  el.innerHTML = standings.map((r, i) => {
    const b = r.breakdown || {};
    const parts = [];
    if (b.match) parts.push(`match ${b.match}`);
    if (b.upset) parts.push(`upset ${b.upset}`);
    if (b.advancement) parts.push(`advancement ${b.advancement}`);
    const detail = parts.length ? parts.join(" · ") : "no points yet";
    return `
      <div class="lb-row ${i === 0 ? "first" : ""}">
        <div class="rank">${r.rank}</div>
        <div class="owner" style="color:${OWNER_COLORS[r.owner] || "inherit"}">${r.owner}</div>
        <div class="pts">${r.total_points}</div>
        <div class="breakdown">${detail}</div>
      </div>`;
  }).join("");
}

function renderTeamTable(teams) {
  const el = document.getElementById("team-table");
  const rows = teams.map(t => `
    <tr>
      <td>${t.team}</td>
      <td><span class="owner-tag" style="color:${OWNER_COLORS[t.owner] || "inherit"}">${t.owner}</span></td>
      <td><span class="tier tier-${t.tier}">T${t.tier}</span></td>
      <td class="num">${t.W}</td>
      <td class="num">${t.D}</td>
      <td class="num">${t.L}</td>
      <td class="num">${t.points}</td>
    </tr>`).join("");
  el.innerHTML = `
    <table>
      <thead>
        <tr><th>Team</th><th>Owner</th><th>Tier</th>
            <th class="num">W</th><th class="num">D</th><th class="num">L</th><th class="num">Pts</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderPlayerGoals(doc) {
  const el = document.getElementById("player-goals");
  const leaders = (doc && doc.leaders) || [];
  if (!leaders.length) {
    el.className = "empty";
    el.textContent = "No goals tracked yet — populates once matches are played.";
    return;
  }
  const rows = leaders.map(r => `
    <tr>
      <td class="num">${r.rank}</td>
      <td>${r.player}</td>
      <td>${r.team}</td>
      <td><span class="owner-tag" style="color:${OWNER_COLORS[r.owner] || "inherit"}">${r.owner}</span></td>
      <td class="num">${r.goals}</td>
      <td class="num">${r.penalties || 0}</td>
    </tr>`).join("");
  el.innerHTML = `
    <table>
      <thead><tr><th class="num">#</th><th>Player</th><th>Team</th><th>Owner</th>
                 <th class="num">Goals</th><th class="num">Pens</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="src-note">source: ${doc.source || "—"}</p>`;
}

async function main() {
  try {
    const [standings, teamTable] = await Promise.all([
      loadJSON("data/owner_standings.json"),
      loadJSON("data/team_table.json"),
    ]);
    renderLeaderboard(standings.standings);
    renderTeamTable(teamTable.teams);
    loadJSON("data/player_goals.json").then(renderPlayerGoals).catch(() => {
      const el = document.getElementById("player-goals");
      el.className = "empty";
      el.textContent = "No goals tracked yet — populates once matches are played.";
    });
    document.getElementById("rules-version").textContent = standings.rules_version || "—";
    document.getElementById("meta").textContent =
      `${standings.tournament || "World Cup 2026"} · rules ${standings.rules_version} · source: ${standings.source}`;
  } catch (e) {
    document.getElementById("leaderboard").textContent = "Failed to load data: " + e.message;
    console.error(e);
  }
}

main();
