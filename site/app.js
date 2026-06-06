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
/* Trim a numeric value to a clean string: 4.0 -> "4", 2.5 -> "2.5". */
const fmtNum = (n) => {
  const v = Math.round(Number(n) * 100) / 100;
  return Number.isFinite(v) ? (v === Math.trunc(v) ? String(Math.trunc(v)) : String(v)) : "0";
};

/* Owner -> WWE ring name (matches site/bios.html + generate_commentary.py).
   Used so the upset banner credits the manager by persona. */
const WWE_NAMES = {
  Zach: "Mustard Boy",
  Gunner: "Bubba G",
  Gayden: "The Backpass Assassin",
  Devin: "Ghost Pepper",
};

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

/* ---------- TODAY'S PUNDIT (single rotating voice) ---------- */
const PUNDIT_FALLBACK_COLORS = {
  "Eric Wynalda": "#e2231a",
  "Landon Donovan": "#2f6dff",
  "Clint Dempsey": "#28c060",
  "Alexi Lalas": "#f4a423",
};
// daily rotation order, used only as a fallback when an older (four-up)
// commentary.json is encountered so the page still shows one voice.
const PUNDIT_ROTATION = ["Eric Wynalda", "Landon Donovan", "Clint Dempsey", "Alexi Lalas"];
const TONE_BADGE = { arrogant: "ARROGANT", hedging: "HEDGING", chill: "CHILL", bombastic: "BOMBASTIC" };
function warmingUp() {
  const box = el("pundit-card");
  if (!box) return;
  box.classList.remove("loading");
  box.innerHTML = `<div class="roundtable-warming">Today's pundit is warming up…</div>`;
}

/* Format a take: bold the opening sentence (the hot take) so the eye catches it
   even when collapsed, and truncate long takes to ~80 words behind READ MORE. */
const TAKE_WORD_LIMIT = 80;
function formatTake(raw) {
  const text = String(raw || "").trim().replace(/\s+/g, " ");
  const fm = text.match(/^(.*?[.!?])(?=\s|$)/);     // first sentence
  const first = fm ? fm[1] : "";
  const emph = (s) => {                               // bold the first-sentence portion of s
    if (first && s.startsWith(first)) return `<strong>${esc(first)}</strong>${esc(s.slice(first.length))}`;
    if (first && first.startsWith(s)) return `<strong>${esc(s)}</strong>`;
    return esc(s);
  };
  const words = text.split(" ");
  if (words.length <= TAKE_WORD_LIMIT) return { truncated: false, full: emph(text) };
  return {
    truncated: true,
    preview: emph(words.slice(0, TAKE_WORD_LIMIT).join(" ")) + "…",
    full: emph(text),
  };
}

/* Pick the single pundit to render. New format is a single {pundit} object;
   if we hit a legacy {pundits:[...]} file, rotate by day so one voice still shows. */
function pickPundit(doc) {
  if (!doc) return null;
  if (doc.pundit) return doc.pundit;
  if (Array.isArray(doc.pundits) && doc.pundits.length) {
    const day = Math.floor(Date.now() / 86400000);          // days since epoch
    const name = PUNDIT_ROTATION[day % PUNDIT_ROTATION.length];
    return doc.pundits.find((p) => p.name === name) || doc.pundits[0];
  }
  return null;
}

function renderPundit(doc) {
  const box = el("pundit-card");
  if (!box) return;
  box.classList.remove("loading");
  const p = pickPundit(doc);
  if (!p || !p.take || !p.take.trim() || p.take.trim() === "Pundits are warming up...") {
    warmingUp();
    return;
  }
  if (doc.source && el("pundit-meta")) el("pundit-meta").textContent = `SOURCE: ${doc.source}`;
  const color = p.color || PUNDIT_FALLBACK_COLORS[p.name] || "#2f6dff";
  const badge = p.tone ? (TONE_BADGE[String(p.tone).toLowerCase()] || p.tone) : "";
  const t = formatTake(p.take);
  const takeBody = t.truncated
    ? `<p class="pundit-take">
         <span class="take-preview">${t.preview}</span>
         <span class="take-full" hidden>${t.full}</span>
       </p>
       <button class="take-toggle" type="button" aria-expanded="false">READ MORE ▼</button>`
    : `<p class="pundit-take">${t.full}</p>`;
  box.innerHTML = `
    <div class="pundit-card solo" style="--pundit:${color}">
      <div class="pundit-head">
        <span class="pundit-name">${esc(p.name)}</span>
        ${badge ? `<span class="pundit-tone">${esc(badge)}</span>` : ""}
      </div>
      ${takeBody}
    </div>`;

  const btn = box.querySelector(".take-toggle");
  if (btn) {
    btn.addEventListener("click", () => {
      const card = btn.closest(".pundit-card");
      const prev = card.querySelector(".take-preview");
      const full = card.querySelector(".take-full");
      const open = btn.getAttribute("aria-expanded") === "true";
      prev.hidden = !open;
      full.hidden = open;
      btn.setAttribute("aria-expanded", String(!open));
      btn.textContent = open ? "READ MORE ▼" : "READ LESS ▲";
    });
  }
}

/* ---------- JIM ROME'S TAKE (rolling narrative from tournament_recap.md) ---------- */
const RECAP_PLACEHOLDER_RE = /column drops once the next slate/i;
function jimRomePre(box) {
  box.classList.remove("loading");
  box.classList.remove("jimrome-card");
  box.innerHTML = `<div class="news-empty">
      <div class="news-empty-badge">📻 JIM ROME</div>
      <p>Jim Rome's tournament coverage begins June 11.</p>
    </div>`;
}
async function renderJimRome() {
  const box = el("jim-rome");
  if (!box) return;
  try {
    const res = await fetch("data/tournament_recap.md?v=" + Date.now());
    if (!res.ok) throw new Error(`recap: ${res.status}`);
    const md = (await res.text()).trim();
    if (!md || RECAP_PLACEHOLDER_RE.test(md)) { jimRomePre(box); return; }
    box.classList.remove("loading");
    const html = (typeof marked !== "undefined")
      ? marked.parse(md)
      : "<p>" + esc(md).replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br/>") + "</p>";
    box.innerHTML = `<div class="jimrome-body">${html}</div>`;
  } catch (e) {
    jimRomePre(box);
  }
}

/* ---------- STANDINGS SIDEBAR ---------- */
/* The latest timeline entry holds each owner's current win probability. */
function latestWinProb(timeline) {
  const entries = (Array.isArray(timeline) ? [...timeline] : [])
    .sort((a, b) => String(a.date).localeCompare(String(b.date)) || (a.matchday || 0) - (b.matchday || 0));
  const last = entries[entries.length - 1];
  return (last && last.win_probability) || {};
}
function renderSidebar(standingsDoc, timeline) {
  const box = el("sidebar-board");
  if (!box) return;
  box.classList.remove("loading");
  const s = (standingsDoc && standingsDoc.standings) || [];
  const wp = latestWinProb(timeline);
  box.innerHTML = s.map((r) => {
    const c = ownerColor(r.owner);
    const prob = wp[r.owner] != null ? `${(wp[r.owner] * 100).toFixed(0)}% to win` : "—";
    return `
      <div class="sb-row ${r.rank === 1 ? "first" : ""}">
        <div class="sb-rk">${r.rank}</div>
        ${avatar(r.owner, "sm")}
        <div class="sb-id">
          <span class="sb-name" style="color:${c}">${esc(r.owner)}</span>
          <span class="sb-prob">${prob}</span>
        </div>
        <div class="sb-pts">${r.total_points}</div>
      </div>`;
  }).join("");
}

/* ---------- OWNER MOMENTUM (Rome inline badges) ----------
   One badge per owner showing points banked on the most recent matchday (the
   last day block in daily_results). 3+ = 🔥 hot, 0 = ❄️ cold, else ➡️ steady.
   No matchdays played yet -> everyone steady. */
function lastMatchdayPoints(daily, owners) {
  const days = (daily && daily.days) || [];
  const last = days[days.length - 1];
  const pts = Object.fromEntries(owners.map((o) => [o, 0]));
  if (last) {
    (last.matches || []).forEach((m) => {
      Object.entries(m.points || {}).forEach(([o, p]) => { if (o in pts) pts[o] += Number(p) || 0; });
    });
  }
  return pts;
}
function renderMomentum(daily, standings) {
  const box = el("owner-momentum");
  if (!box) return;
  const owners = ((standings && standings.standings) || []).map((s) => s.owner);
  const list = owners.length ? owners : Object.keys(OWNER_COLORS);
  const hasData = ((daily && daily.days) || []).length > 0;
  const pts = lastMatchdayPoints(daily, list);
  box.innerHTML = list.map((o) => {
    const p = pts[o] || 0;
    let icon = "➡️", cls = "neutral", note = hasData ? `+${fmtNum(p)} last MD` : "no games yet";
    if (hasData && p >= 3) { icon = "🔥"; cls = "hot"; }
    else if (hasData && p === 0) { icon = "❄️"; cls = "cold"; note = "0 last MD"; }
    return `
      <div class="mom-badge ${cls}" style="--c:${ownerColor(o)}">
        <span class="mom-icon">${icon}</span>
        <span class="mom-owner">${esc(o)}</span>
        <span class="mom-note">${esc(note)}</span>
      </div>`;
  }).join("");
}

/* ---------- UPSET OF THE DAY (Rome inline banner) ----------
   If a tier-gap upset is logged in narrative_state.notable_events on the most
   recent matchday, surface the biggest one below Rome's take. Else show nothing. */
function renderUpset(daily, narrative) {
  const box = el("upset-of-day");
  if (!box) return;
  const days = (daily && daily.days) || [];
  const lastDate = days.length ? days[days.length - 1].date : null;
  const events = (narrative && narrative.notable_events) || [];
  const upsets = events.filter((e) => e.type === "upset" && e.date === lastDate);
  if (!lastDate || !upsets.length) { box.innerHTML = ""; return; }
  const u = upsets.slice().sort((a, b) => (b.bonus || 0) - (a.bonus || 0))[0];
  const persona = WWE_NAMES[u.owner] || u.owner || "the owner";
  box.innerHTML = `
    <div class="upset-banner">
      <span class="upset-tag">🔥 UPSET</span>
      <span class="upset-text"><b>${esc(u.team)}</b> (+${fmtNum(u.bonus)}) over ${esc(u.beat)} — ${esc(persona)} cashes in.</span>
    </div>`;
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

/* ---------- BOOT ---------- */
async function main() {
  try {
    const [standings, teams, daily] = await Promise.all([
      loadJSON("data/owner_standings.json"),
      loadJSON("data/team_table.json"),
      loadJSON("data/daily_results.json"),
    ]);

    renderPortfolios(standings, teams);
    renderResults(daily);
    renderTicker(flattenMatches(daily));
    renderMomentum(daily, standings);

    const v = standings.rules_version || "rebalanced_v3";
    el("leaguebar-meta").textContent = `scoring · ${v}`;
    el("foot-rules").textContent = v;
    el("foot-src").textContent = standings.source || "—";

    // The sidebar shows points (standings) + win probability (latest timeline
    // entry). The win-probability chart itself now lives on the analytics page.
    loadJSON("data/timeline.json")
      .then((timeline) => renderSidebar(standings, timeline))
      .catch(() => renderSidebar(standings, null));

    // The upset-of-the-day banner reads the rolling narrative state.
    loadJSON("data/narrative_state.json")
      .then((narrative) => renderUpset(daily, narrative))
      .catch(() => renderUpset(daily, null));

    loadJSON("data/commentary.json").then(renderPundit).catch(warmingUp);

    renderJimRome();
  } catch (e) {
    console.error(e);
    const sb = el("sidebar-board");
    if (sb) sb.innerHTML = `<div class="loading">Failed to load data: ${esc(e.message)}</div>`;
  }
}
main();
