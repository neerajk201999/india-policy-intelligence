const state = { data: null, area: "All", query: "", lastFetch: null };
const $ = (selector) => document.querySelector(selector);

function escapeText(value) {
  return String(value ?? "");
}

function formatDate(value, options = { day: "numeric", month: "long", year: "numeric" }) {
  if (!value) return "Not stated";
  const date = new Date(value.length === 10 ? `${value}T12:00:00+05:30` : value);
  return new Intl.DateTimeFormat("en-IN", { ...options, timeZone: "Asia/Kolkata" }).format(date);
}

function relativeTime(value) {
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.max(0, Math.round(diff / 60000));
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} minutes ago`;
  const hours = Math.round(mins / 60);
  if (hours < 36) return `${hours} hours ago`;
  return `${Math.round(hours / 24)} days ago`;
}

async function fetchData() {
  const configured = window.POLICY_DATA_URLS || ["./data/latest.json"];
  const urls = configured.map((url) => `${url}${url.includes("?") ? "&" : "?"}v=${Date.now()}`);
  let lastError;
  for (const url of urls) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } catch (error) { lastError = error; }
  }
  throw lastError || new Error("No data source configured");
}

function renderHeader() {
  const { meta, summary } = state.data;
  $("#edition-date").textContent = formatDate(meta.reportDate);
  $("#freshness").textContent = `Published ${relativeTime(meta.generatedAt)} · ${meta.coverage}`;
  $("#footer-updated").textContent = `Last published ${formatDate(meta.generatedAt, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} IST`;
  $("#stat-developments").textContent = summary.developments;
  $("#stat-primary").textContent = summary.primaryVerified;
  $("#stat-watch").textContent = summary.watching;
  $("#stat-sources").textContent = `${summary.healthySources}/${summary.totalSources}`;
  $("#sync-label").textContent = "Live data";
}

function renderFilters() {
  const filter = $("#area-filter");
  const areas = ["All", ...state.data.areas.filter((area) => area.count > 0).map((area) => area.name)];
  filter.replaceChildren(...areas.map((area) => {
    const button = document.createElement("button");
    button.className = `filter-button${state.area === area ? " active" : ""}`;
    button.textContent = area;
    button.addEventListener("click", () => { state.area = area; renderFilters(); renderEvents(); });
    return button;
  }));
}

function eventMatches(event) {
  const inArea = state.area === "All" || event.area === state.area;
  const haystack = `${event.title} ${event.whatHappened} ${event.whyItMatters} ${event.status} ${event.affectedEntities.join(" ")}`.toLowerCase();
  return inArea && haystack.includes(state.query.toLowerCase());
}

function renderEvents() {
  const list = $("#event-list");
  const events = state.data.events.filter(eventMatches);
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty-brief";
    const filtered = state.area !== "All" || state.query;
    empty.innerHTML = `<h3>${filtered ? "No matching development" : "No weak signal was promoted"}</h3><p>${filtered ? "Try a broader policy area or search term." : "The desk found no additional item that cleared freshness, significance and evidence checks. Source operations remain visible below."}</p>`;
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...events.map((event, index) => {
    const node = $("#event-template").content.firstElementChild.cloneNode(true);
    node.style.animationDelay = `${Math.min(index * 70, 280)}ms`;
    node.querySelector(".event-area").textContent = event.area;
    node.querySelector(".event-status").textContent = event.status;
    node.querySelector(".event-evidence").textContent = event.evidence;
    node.querySelector("h3").textContent = `${event.isUpdate ? "Update — " : ""}${event.title}`;
    node.querySelector(".event-what").textContent = event.whatHappened;
    node.querySelector(".event-why").textContent = event.whyItMatters;
    const dates = [
      `Published ${formatDate(event.publicationDate, { day: "numeric", month: "short", year: "numeric" })}`,
      event.effectiveDate && `Effective ${formatDate(event.effectiveDate, { day: "numeric", month: "short", year: "numeric" })}`,
      event.deadline && `Deadline ${formatDate(event.deadline, { day: "numeric", month: "short", year: "numeric" })}`,
    ].filter(Boolean);
    node.querySelector(".event-dates").textContent = dates.join(" · ");
    const source = node.querySelector(".source-link");
    source.href = event.primarySourceUrl || event.secondarySourceUrls[0];
    source.firstChild.textContent = event.primarySourceUrl ? "Open primary document " : "Open context source ";
    return node;
  }));
}

function watchInstruction(event) {
  if (event.deadline) return `Deadline ${formatDate(event.deadline, { day: "numeric", month: "short", year: "numeric" })}. Watch for the regulator’s next formal step.`;
  const instructions = {
    Draft: "Watch for final text, notification and commencement.",
    Consultation: "Watch for the response window to close and final rules.",
    "Bill introduced": "Watch for scrutiny, amendments and passage.",
    "Cabinet approved": "Watch for the formal text or Gazette notification.",
  };
  return instructions[event.status] || "Watch for an authoritative implementation step.";
}

function renderWatchlist() {
  const container = $("#watch-list");
  if (!state.data.watchlist.length) {
    const empty = document.createElement("p");
    empty.className = "watch-empty";
    empty.textContent = "No evidenced open item is pending today. The radar will populate only when a concrete next step exists.";
    container.replaceChildren(empty);
    return;
  }
  container.replaceChildren(...state.data.watchlist.map((event) => {
    const item = document.createElement("article");
    item.className = "watch-item";
    const label = document.createElement("small"); label.textContent = event.status;
    const title = document.createElement("h3"); title.textContent = event.title;
    const copy = document.createElement("p"); copy.textContent = watchInstruction(event);
    item.append(label, title, copy);
    return item;
  }));
}

function renderCoverage() {
  const max = Math.max(1, ...state.data.areas.map((area) => area.count));
  $("#coverage-map").replaceChildren(...state.data.areas.map((area) => {
    const row = document.createElement("div"); row.className = "coverage-row";
    const name = document.createElement("span"); name.className = "coverage-name"; name.textContent = area.name;
    const track = document.createElement("div"); track.className = "coverage-track";
    const fill = document.createElement("div"); fill.className = "coverage-fill"; track.append(fill);
    const count = document.createElement("span"); count.className = "coverage-count"; count.textContent = area.count;
    row.append(name, track, count);
    requestAnimationFrame(() => { fill.style.width = area.count ? `${Math.max(7, area.count / max * 100)}%` : "0"; });
    return row;
  }));
}

function renderSources() {
  const { sources, summary } = state.data;
  $("#source-summary").textContent = `${summary.healthySources} of ${summary.totalSources} configured sources responded successfully in the latest recorded checks.`;
  $("#source-table").replaceChildren(...sources.map((source) => {
    const row = document.createElement("tr");
    const values = [source.name, source.topic || "All", `Tier ${source.authorityLevel}`, source.lastSuccess ? formatDate(source.lastSuccess, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "No successful fetch", null];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      if (index < 4) cell.textContent = value;
      else {
        const label = document.createElement("span"); label.className = "health-label";
        const dot = document.createElement("i"); dot.className = `health-dot ${source.health}`;
        label.append(dot, document.createTextNode(source.health));
        if (source.lastError) label.title = source.lastError;
        cell.append(label);
      }
      row.append(cell);
    });
    return row;
  }));
}

function updateCountdown() {
  const now = new Date();
  const istParts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(now);
  const parts = Object.fromEntries(istParts.map((part) => [part.type, part.value]));
  const todayEightUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, 2, 30);
  let next = todayEightUtc;
  if (now.getTime() >= next) next += 86400000;
  const diff = next - now.getTime();
  const hours = Math.floor(diff / 3600000);
  const minutes = Math.floor((diff % 3600000) / 60000);
  $("#countdown").textContent = `${hours}h ${String(minutes).padStart(2, "0")}m`;
}

function render() {
  renderHeader(); renderFilters(); renderEvents(); renderWatchlist(); renderCoverage(); renderSources(); updateCountdown();
}

async function refresh(silent = false) {
  try {
    const data = await fetchData();
    const changed = state.data && data.meta.generatedAt !== state.data.meta.generatedAt;
    state.data = data; state.lastFetch = new Date(); render();
    if (changed && !silent) window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    $("#sync-label").textContent = "Data unavailable";
    $("#freshness").textContent = "The published dataset could not be reached. No current claims are being shown.";
    console.error(error);
  }
}

$("#search").addEventListener("input", (event) => { state.query = event.target.value.trim(); renderEvents(); });
window.addEventListener("scroll", () => {
  const max = document.documentElement.scrollHeight - innerHeight;
  $("#reading-progress").style.width = `${max > 0 ? scrollY / max * 100 : 0}%`;
}, { passive: true });

refresh();
setInterval(() => refresh(true), 60000);
setInterval(updateCountdown, 30000);

