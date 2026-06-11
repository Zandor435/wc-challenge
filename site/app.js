/* WC Challenge — renders static JSON into a Fox Sports–style layout.
   The site computes nothing; all numbers come from the JSON files. */

const OWNER_COLORS = {
  Zach:   "#f4c430",
  Gunner: "#2f6dff",
  Gayden: "#28c060",
  Devin:  "#f0743a",
  Rafe:   "#a855f7",
};
const ownerColor = (o) => OWNER_COLORS[o] || "#8b919c";

// AI manager portraits (Nano Banana). Owners without one fall back to an
// initials circle; add the file + an entry here once their reference is generated.
const OWNER_PORTRAITS = {
  Zach:   "assets/portraits/zach_1.jpg",   // José Mourinho
  Devin:  "assets/portraits/devin_1.jpg",  // Ted Lasso
  Gunner: "assets/portraits/gunner_1.jpg", // Jesse Marsch
  Gayden: "assets/portraits/gayden_1.jpg", // Pep Guardiola
  Rafe:   "assets/portraits/fox/rafe_fox.jpg",
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
  Rafe: "The Noisemaker",
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

/* ============================================================
   TODAY'S MATCHES (schedule hero) — the page's lead.
   Fixtures + times come from data/matches.csv; ownership from
   team_table.json (the site-served draft mapping); tiers from
   team_tiers.json; scored results from daily_results.json.
   All point math here is DISPLAY-ONLY — the values are hardcoded
   to the rebalanced_v3 rules, not imported from scoring.py.
   ============================================================ */

// Tiers loaded at boot from team_tiers.json (1 strongest … 4 weakest).
let TEAM_TIERS = {};

// Group-stage scoring (rebalanced_v3): Win 3 / Draw 1 / Loss 0, plus an upset
// bonus of +2 per tier gap when a lower-tier team beats a higher-tier one.
const GROUP_WIN = 3, GROUP_DRAW = 1, UPSET_PER_TIER = 2;
// Knockout advancement bonus by round.
const ADV_BONUS = { round_of_32: 2, round_of_16: 2, quarterfinal: 5, semifinal: 10, final: 18, third_place: 0 };
const ADV_LABEL = { round_of_32: "R32", round_of_16: "R16", quarterfinal: "QF", semifinal: "SF", final: "Final", third_place: "3rd" };
const KO_GROUP_LABEL = { round_of_32: "Round of 32", round_of_16: "Round of 16", quarterfinal: "Quarterfinal", semifinal: "Semifinal", final: "Final", third_place: "Third Place" };

/* Minimal CSV parser: handles quoted fields containing commas (venues like
   "Zapopan, Mexico"). Returns an array of row objects keyed by the header. */
function parseCSV(text) {
  const rows = [];
  let row = [], field = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQ) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else inQ = false; }
      else field += c;
    } else if (c === '"') { inQ = true; }
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      if (field.length || row.length) { row.push(field); rows.push(row); row = []; field = ""; }
    } else field += c;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  const header = (rows.shift() || []).map((h) => h.trim());
  return rows.map((r) => Object.fromEntries(header.map((h, i) => [h, (r[i] ?? "").trim()])));
}

/* Local calendar date as YYYY-MM-DD, matching the CSV's `date` column. */
function todayLocalISO() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function prettyDate(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  if (!y) return iso;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

// team -> owner, from team_table.json (the served draft board).
function buildOwnerIndex(teamTable) {
  const idx = {};
  (teamTable.teams || []).forEach((t) => { idx[t.team] = t.owner; });
  return idx;
}
// tier lookup: team_tiers.json first, team_table as fallback for drafted sides.
function makeTierLookup(teamTable) {
  const tbl = {};
  (teamTable.teams || []).forEach((t) => { if (t.tier != null) tbl[t.team] = t.tier; });
  return (team) => (TEAM_TIERS[team] != null ? TEAM_TIERS[team] : (tbl[team] != null ? tbl[team] : null));
}
// scored match lookup keyed by date + unordered team pair.
function buildResultIndex(daily) {
  const idx = {};
  (daily.days || []).forEach((day) => (day.matches || []).forEach((m) => {
    idx[`${m.date}|${[m.home, m.away].sort().join("~")}`] = m;
  }));
  return idx;
}

// Points an owner banks for a win, given their team's tier vs the opponent's.
// Upset bonus only applies in the group stage (per the display rules).
function winPoints(myTier, oppTier, isGroup) {
  let bonus = 0;
  if (isGroup && myTier != null && oppTier != null && myTier > oppTier) {
    bonus = UPSET_PER_TIER * (myTier - oppTier);
  }
  return { win: GROUP_WIN + bonus, bonus };
}

// Owner tag: ring name + draft color accent. `none` => undrafted, dimmed.
function tmOwnerTag(owner) {
  if (!owner) return `<span class="tm-owner none">Undrafted</span>`;
  const ring = WWE_NAMES[owner] || owner;
  return `<span class="tm-owner" style="--c:${ownerColor(owner)}">` +
         `<span class="tm-owner-dot"></span>${esc(owner)} · ${esc(ring)}</span>`;
}

// One stakes line per drafted owner in the match.
function tmStakes(t1, o1, tier1, t2, o2, tier2, isGroup, phase) {
  const adv = !isGroup ? ADV_BONUS[phase] : null;
  const line = (owner, myTier, oppTier) => {
    if (!owner) return null;
    const ring = WWE_NAMES[owner] || owner;
    const { win, bonus } = winPoints(myTier, oppTier, isGroup);
    let s = `<b>${esc(ring)}</b>: W = ${win}`;
    if (bonus > 0) s += ` <span class="tm-bonus">(includes +${bonus} upset bonus)</span>`;
    if (isGroup) s += ` · D = 1`;
    if (adv) s += `<span class="tm-adv">Advance = +${adv} pts</span>`;
    return `<div class="tm-stake" style="--c:${ownerColor(owner)}">${s}</div>`;
  };
  const a = line(o1, tier1, tier2), b = line(o2, tier2, tier1);
  if (!a && !b) return `<div class="tm-stake none">No fantasy points at stake</div>`;
  return [a, b].filter(Boolean).join("");
}

// Render one fixture card. `result` is the scored daily_results match if final.
function tmCard(fx, ownerIdx, tierOf, result) {
  const t1 = fx.team1, t2 = fx.team2;
  const o1 = ownerIdx[t1] || "", o2 = ownerIdx[t2] || "";
  const tier1 = tierOf(t1), tier2 = tierOf(t2);
  const isGroup = fx.phase === "group";
  const undrafted = !o1 && !o2;

  // OWNER CLASH — two different owners' teams meet (highest drama).
  const clash = o1 && o2 && o1 !== o2;
  const clashBanner = clash
    ? `<div class="tm-clash"><span class="swords">⚔️</span>` +
      `${esc((WWE_NAMES[o1] || o1).toUpperCase())} vs ${esc((WWE_NAMES[o2] || o2).toUpperCase())}</div>`
    : "";

  // Upset Watch — both drafted, different tiers (underdog tier shown first).
  const upset = o1 && o2 && tier1 != null && tier2 != null && tier1 !== tier2;
  const upsetChip = upset
    ? `<span class="tm-upset">Upset Watch: T${Math.max(tier1, tier2)} vs T${Math.min(tier1, tier2)}</span>`
    : "";

  // State: scored => FINAL w/ score + points; else the kickoff time.
  const final = !!result;
  let s1 = "", s2 = "", w1 = false, w2 = false, ptsHTML = "";
  if (final) {
    s1 = result.home === t1 ? result.home_score : result.away_score;
    s2 = result.home === t1 ? result.away_score : result.home_score;
    w1 = s1 > s2; w2 = s2 > s1;
    const pts = Object.entries(result.points || {});
    ptsHTML = pts.length
      ? `<div class="tm-result-pts">${pts.map(([o, p]) => ptsPill(o, p)).join("")}</div>`
      : "";
  }
  const stateHTML = final
    ? `<span class="tm-state final">FINAL</span>`
    : `<span class="tm-state upcoming">${esc(fx.time_et || "TBD")}</span>`;

  const groupLabel = isGroup
    ? `Group ${esc(fx.group || "—")}`
    : esc(KO_GROUP_LABEL[fx.phase] || fx.phase || "Knockout");

  const teamRow = (team, owner, score, win) => `
    <div class="tm-team ${win ? "win" : ""} ${owner ? "" : "undrafted"}">
      ${flag(team)}
      <span class="tm-name">${esc(team)}</span>
      ${tmOwnerTag(owner)}
      ${final ? `<span class="tm-score">${score}</span>` : ""}
    </div>`;

  return `
    <article class="tm-card ${undrafted ? "muted" : ""} ${clash ? "clash" : ""}">
      ${clashBanner}
      <div class="tm-body">
        <div class="tm-teams">
          ${teamRow(t1, o1, s1, w1)}
          ${teamRow(t2, o2, s2, w2)}
        </div>
        <div class="tm-info">
          <span class="tm-group">${groupLabel}</span>
          <span class="sep">·</span>
          ${stateHTML}
        </div>
        ${upsetChip}
        <div class="tm-stakes">
          ${tmStakes(t1, o1, tier1, t2, o2, tier2, isGroup, fx.phase)}
          ${ptsHTML}
        </div>
      </div>
    </article>`;
}

function renderTodaysMatches(rows, ownerIdx, tierOf, resultIdx) {
  const box = el("todays-matches");
  if (!box) return;
  box.classList.remove("loading");
  const today = todayLocalISO();
  let fixtures = rows.filter((r) => r.date === today);
  let targetDate = today, isToday = true;

  if (!fixtures.length) {
    // No slate today — preview the next date that has fixtures.
    const future = rows.filter((r) => r.date > today).map((r) => r.date).sort();
    targetDate = future[0];
    isToday = false;
    if (!targetDate) {
      el("tm-title").textContent = "⚽ MATCHES";
      el("tm-meta").textContent = "";
      box.innerHTML = `<div class="news-empty"><div class="news-empty-badge">⚽ SCHEDULE</div>
        <p>The tournament schedule is complete.</p></div>`;
      return;
    }
    fixtures = rows.filter((r) => r.date === targetDate);
  }

  el("tm-title").textContent = isToday ? "⚽ TODAY'S MATCHES" : "⚽ NEXT MATCH DAY";
  const n = fixtures.length;
  el("tm-meta").textContent = `${prettyDate(targetDate).toUpperCase()} · ${n} ${n === 1 ? "MATCH" : "MATCHES"}`;

  const intro = isToday ? "" :
    `<p class="tm-next-note">No matches today — next up <b>${esc(prettyDate(targetDate))}</b>.</p>`;
  const cards = fixtures.map((fx) => {
    const result = isToday ? resultIdx[`${fx.date}|${[fx.team1, fx.team2].sort().join("~")}`] : null;
    return tmCard(fx, ownerIdx, tierOf, result);
  }).join("");
  box.innerHTML = intro + `<div class="tm-cards">${cards}</div>`;
}

/* ---------- TODAY'S PUNDIT (single rotating voice) ---------- */
const PUNDIT_FALLBACK_COLORS = {
  "Eric Wynalda": "#e2231a",
  "Landon Donovan": "#2f6dff",
  "Clint Dempsey": "#28c060",
  "Alexi Lalas": "#f4a423",
};
// circular headshots shown next to the pundit name (default variant of each;
// more variants live alongside in assets/portraits/pundits/<slug>/ for rotation).
const PUNDIT_AVATARS = {
  "Eric Wynalda":   "assets/portraits/pundits/wynalda/wynalda.png",
  "Landon Donovan": "assets/portraits/pundits/donovan/donovan.png",
  "Clint Dempsey":  "assets/portraits/pundits/dempsey/dempsey.png",
  "Alexi Lalas":    "assets/portraits/pundits/lalas/lalas.png",
};
// pundit_takes entries identify a pundit by slug; map slug -> display name +
// accent so the Pundit Takes strip can show the right byline, color, and avatar.
const PUNDIT_SLUGS = {
  wynalda:  { name: "Eric Wynalda",   color: "#e2231a" },
  donovan:  { name: "Landon Donovan", color: "#2f6dff" },
  dempsey:  { name: "Clint Dempsey",  color: "#28c060" },
  lalas:    { name: "Alexi Lalas",    color: "#f4a423" },
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

/* ---------- PUNDIT TAKES (satirical news-ticker strip) ----------
   Reads doc.pundit_takes: deadpan headline + subtitle + byline, no body.
   Each take names its pundit by slug (wynalda/donovan/dempsey/lalas). */
function renderPunditTakes(doc) {
  const box = el("pundit-takes");
  if (!box) return;
  box.classList.remove("loading");
  const takes = (doc && Array.isArray(doc.pundit_takes)) ? doc.pundit_takes : [];
  if (!takes.length) {
    box.innerHTML = `<div class="news-empty">
        <div class="news-empty-badge">🗞️ THE WIRE</div>
        <p>The pundits go live once the slate begins.</p>
      </div>`;
    return;
  }
  box.innerHTML = takes.map((t) => {
    const slug = String(t.pundit || "").toLowerCase();
    const info = PUNDIT_SLUGS[slug] || {};
    const name = info.name || t.pundit || "";
    const color = info.color || "#2f6dff";
    const avSrc = `assets/portraits/pundits/${slug}/${slug}.png`;
    const av = slug
      ? `<img class="take-avatar" src="${esc(avSrc)}" alt="" width="34" height="34" loading="lazy" onerror="this.style.display='none'" />`
      : "";
    const meta = [t.match, t.date].filter(Boolean).join(" · ");
    return `
      <article class="take-card" style="--pundit:${color}">
        <h3 class="take-headline">${esc(t.headline || "")}</h3>
        ${t.subtitle ? `<p class="take-subtitle">${esc(t.subtitle)}</p>` : ""}
        <div class="take-byline">
          ${av}
          <span class="take-pundit">${esc(name)}</span>
          ${meta ? `<span class="take-meta">${esc(meta)}</span>` : ""}
        </div>
      </article>`;
  }).join("");
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
  const avSrc = p.avatar || PUNDIT_AVATARS[p.name] || "";
  const av = avSrc
    ? `<img class="pundit-avatar" src="${esc(avSrc)}" alt="" width="44" height="44" loading="lazy" />`
    : "";
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
        ${av}
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
/* First N sentences of a plain-text blob — used for the collapsed teaser. */
function firstSentences(text, n) {
  const clean = String(text).replace(/\s+/g, " ").trim();
  const m = clean.match(/[^.!?]+[.!?]+(?:["')\]]+)?/g);
  return m ? m.slice(0, n).join(" ").trim() : clean;
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
    const fullHTML = (typeof marked !== "undefined")
      ? marked.parse(md)
      : "<p>" + esc(md).replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br/>") + "</p>";
    // Lead with the first 2–3 sentences; the rest expands in place (READ MORE)
    // so the column is present but doesn't crowd out the match cards above it.
    const tmp = document.createElement("div");
    tmp.innerHTML = fullHTML;
    const plain = (tmp.textContent || "").trim();
    const preview = firstSentences(plain, 3);
    if (!preview || preview.length >= plain.length - 1) {
      box.innerHTML = `<div class="jimrome-body">${fullHTML}</div>`;
      return;
    }
    box.innerHTML = `
      <div class="jimrome-body">
        <p class="jimrome-preview">${esc(preview)}</p>
        <div class="jimrome-full" hidden>${fullHTML}</div>
        <button class="take-toggle jimrome-toggle" type="button" aria-expanded="false">READ MORE ▾</button>
      </div>`;
    const btn = box.querySelector(".jimrome-toggle");
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") === "true";
      box.querySelector(".jimrome-preview").hidden = !open;
      box.querySelector(".jimrome-full").hidden = open;
      btn.setAttribute("aria-expanded", String(!open));
      btn.textContent = open ? "READ MORE ▾" : "READ LESS ▴";
    });
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

    // Today's Matches hero: schedule (matches.csv) + ownership (team_table) +
    // tiers (team_tiers.json) + scored results (daily). Failure is non-fatal —
    // the rest of the page still renders.
    Promise.all([
      fetch("data/matches.csv?v=" + Date.now()).then((r) => r.ok ? r.text() : Promise.reject(new Error("matches.csv " + r.status))),
      loadJSON("data/team_tiers.json").catch(() => ({})),
    ]).then(([csvText, tiers]) => {
      TEAM_TIERS = tiers || {};
      renderTodaysMatches(parseCSV(csvText), buildOwnerIndex(teams), makeTierLookup(teams), buildResultIndex(daily));
    }).catch((e) => {
      console.error(e);
      const b = el("todays-matches");
      if (b) { b.classList.remove("loading"); b.innerHTML = `<div class="loading">Schedule unavailable.</div>`; }
    });

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

    loadJSON("data/commentary.json")
      .then((doc) => { renderPunditTakes(doc); renderPundit(doc); })
      .catch(() => { renderPunditTakes(null); warmingUp(); });

    renderJimRome();
  } catch (e) {
    console.error(e);
    const sb = el("sidebar-board");
    if (sb) sb.innerHTML = `<div class="loading">Failed to load data: ${esc(e.message)}</div>`;
  }
}
main();
