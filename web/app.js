const state = {
  data: null,
  area: "All",
  query: "",
  status: "All",
  sort: "newest",
};

const $ = (selector, root = document) => root.querySelector(selector);
const AREAS = [
  ["All", "All"],
  ["Deregulation & Ease of Doing Business", "Deregulation"],
  ["Digital Economy & AI", "Digital & AI"],
  ["Financial & Banking", "Financial"],
  ["Competition", "Competition"],
  ["Education", "Education"],
  ["Land, Housing & Governance Reform", "Land & Governance"],
  ["Corporate Governance / ESG", "Corporate / ESG"],
];

function safeDate(value) {
  if (!value) return null;
  const date = new Date(String(value).length === 10 ? `${value}T12:00:00+05:30` : value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value, options = { day: "numeric", month: "long", year: "numeric" }) {
  const date = safeDate(value);
  return date ? new Intl.DateTimeFormat("en-IN", { ...options, timeZone: "Asia/Kolkata" }).format(date) : null;
}

function formatUpdated(value) {
  const date = safeDate(value);
  if (!date) return "Update time unavailable";
  return `Updated ${new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" }).format(date)} IST`;
}

function eventPath(event) {
  return `/tracker/${encodeURIComponent(event.slug || "policy-update")}`;
}

function routeName() {
  if (location.pathname.startsWith("/tracker/")) return "detail";
  if (location.pathname === "/tracker" || location.pathname === "/tracker/") return "tracker";
  if (location.pathname === "/watchlist" || location.pathname === "/watchlist/") return "watchlist";
  return "briefing";
}

function setIntro(label, title, description, edition = null) {
  $("#page-label").textContent = label;
  $("#page-title").textContent = title;
  $("#page-description").textContent = description;
  $("#edition").textContent = edition || "";
}

function setMeta(title, description) {
  document.title = title === "India Policy Intelligence" ? title : `${title} · India Policy Intelligence`;
  const meta = document.querySelector('meta[name="description"]');
  if (meta) meta.content = description || "Verified Indian policy and regulatory developments.";
}

function setNavigation() {
  const current = routeName() === "detail" ? "tracker" : routeName();
  document.querySelectorAll("[data-nav]").forEach((link) => {
    const active = link.dataset.nav === current;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

async function fetchData() {
  const urls = window.POLICY_DATA_URLS || ["/data/latest.json"];
  let lastError;
  for (const configured of urls) {
    try {
      const separator = configured.includes("?") ? "&" : "?";
      const response = await fetch(`${configured}${separator}v=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Data request failed with HTTP ${response.status}`);
      const payload = await response.json();
      if (!payload || !payload.meta || !Array.isArray(payload.events)) throw new Error("Published data is malformed");
      payload.tracker = Array.isArray(payload.tracker) ? payload.tracker : payload.events;
      payload.watchlist = Array.isArray(payload.watchlist) ? payload.watchlist : [];
      return payload;
    } catch (error) { lastError = error; }
  }
  throw lastError || new Error("No publication data source is configured");
}

function searchableText(event) {
  return [event.title, event.whatHappened, event.whyItMatters, event.institution, event.area,
    event.status, event.primarySourceTitle, ...(event.affectedEntities || [])].filter(Boolean).join(" ").toLowerCase();
}

function matches(event) {
  const areaMatch = state.area === "All" || event.area === state.area;
  const statusMatch = state.status === "All" || event.status === state.status;
  return areaMatch && statusMatch && searchableText(event).includes(state.query.toLowerCase());
}

function createControls(events, tracker = false) {
  const controls = $("#controls-template").content.firstElementChild.cloneNode(true);
  const filters = $(".category-filters", controls);
  filters.replaceChildren(...AREAS.map(([value, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `filter-button${state.area === value ? " active" : ""}`;
    button.textContent = label;
    button.setAttribute("aria-pressed", String(state.area === value));
    button.addEventListener("click", () => { state.area = value; render(); });
    return button;
  }));
  const search = $("input", controls);
  search.value = state.query;
  search.addEventListener("input", (event) => { state.query = event.target.value.trim(); renderListOnly(); });
  const secondary = $(".secondary-controls", controls);
  const count = document.createElement("span");
  count.className = "results-count";
  count.dataset.resultsCount = "";
  secondary.append(count);
  if (tracker) {
    const statuses = ["All", ...new Set(events.map((event) => event.status).filter(Boolean))];
    const statusSelect = document.createElement("select");
    statusSelect.className = "select-control";
    statusSelect.setAttribute("aria-label", "Filter by legal status");
    statusSelect.replaceChildren(...statuses.map((status) => {
      const option = document.createElement("option"); option.value = status; option.textContent = status === "All" ? "All statuses" : status; option.selected = state.status === status; return option;
    }));
    statusSelect.addEventListener("change", (event) => { state.status = event.target.value; renderListOnly(); });
    const sort = document.createElement("select");
    sort.className = "select-control";
    sort.setAttribute("aria-label", "Sort policy developments");
    [["newest", "Newest"], ["updated", "Recently updated"]].forEach(([value, label]) => {
      const option = document.createElement("option"); option.value = value; option.textContent = label; option.selected = state.sort === value; sort.append(option);
    });
    sort.addEventListener("change", (event) => { state.sort = event.target.value; renderListOnly(); });
    secondary.prepend(statusSelect, sort);
  }
  return controls;
}

function metadataItem(label, value) {
  if (!value) return null;
  const span = document.createElement("span");
  const strong = document.createElement("strong"); strong.textContent = `${label}: `;
  span.append(strong, document.createTextNode(value));
  return span;
}

function createSourceLink(prefix, label, url, detail = null) {
  if (!url) return null;
  const link = document.createElement("a");
  link.className = "source-link";
  link.href = url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = `${prefix} → ${label || "Authoritative document"}`;
  if (detail) {
    const small = document.createElement("span"); small.className = "source-detail"; small.textContent = detail; link.append(small);
  }
  return link;
}

function createPolicyEvent(event, detailView = false) {
  const node = $("#event-template").content.firstElementChild.cloneNode(true);
  node.classList.toggle("detail-event", detailView);
  $(".event-area", node).textContent = areaLabel(event.area);
  $(".event-status", node).textContent = event.status || "Status pending";
  const verification = $(".verification", node);
  $("span", verification).textContent = event.evidence || (event.isVerified ? "Primary source verified" : "Official source");
  if (!event.primarySourceUrl) verification.hidden = true;
  const title = $(".event-title a", node);
  title.textContent = event.title || "Untitled policy development";
  title.href = eventPath(event);
  $(".event-summary", node).textContent = event.whatHappened || "A verified summary is not available for this development.";
  $(".event-why", node).textContent = event.whyItMatters || "Practical implications have not yet been established.";
  const metadata = $(".event-metadata", node);
  const affected = (event.affectedEntities || []).join(" · ");
  const items = [
    metadataItem("Affects", affected),
    metadataItem("Published", formatDate(event.publicationDate, { day: "numeric", month: "short", year: "numeric" })),
    metadataItem("Effective", formatDate(event.effectiveDate, { day: "numeric", month: "short", year: "numeric" }) || event.effectiveDateText),
    metadataItem("Consultation closes", formatDate(event.deadline, { day: "numeric", month: "short", year: "numeric" })),
    metadataItem("Next expected step", event.nextStep),
  ].filter(Boolean);
  metadata.replaceChildren(...items);
  if (!items.length) metadata.hidden = true;
  const sources = $(".event-sources", node);
  const institution = event.institution || "Official authority";
  const sourceTitle = event.primarySourceTitle || event.sourceDocumentTitle || "Authoritative document";
  const sourcePrefix = event.isVerified ? "Primary source" : "Official source";
  const links = [createSourceLink(sourcePrefix, `${institution} — ${sourceTitle}`, event.primarySourceUrl, formatDate(event.publicationDate))];
  (event.secondarySourceUrls || []).slice(0, 1).forEach((url) => links.push(createSourceLink("News context", "Context source", url)));
  sources.replaceChildren(...links.filter(Boolean));
  if (!sources.children.length) sources.hidden = true;
  return node;
}

function areaLabel(area) {
  return AREAS.find(([value]) => value === area)?.[1] || area || "Policy";
}

function emptyState(title, copy) {
  const section = document.createElement("section"); section.className = "empty-state";
  const heading = document.createElement("h2"); heading.textContent = title;
  const paragraph = document.createElement("p"); paragraph.textContent = copy;
  section.append(heading, paragraph); return section;
}

function renderBriefingList(container) {
  const events = state.data.events.filter(matches);
  const count = document.querySelector("[data-results-count]");
  if (count) count.textContent = `${events.length} ${events.length === 1 ? "development" : "developments"}`;
  if (!events.length) {
    const hasFilters = state.query || state.area !== "All";
    container.replaceChildren(emptyState(
      hasFilters ? "No matching developments" : "No developments today",
      hasFilters ? "Clear a filter or use a broader search term." : "No meaningful regulatory developments were verified in the current window."
    ));
    return;
  }
  container.replaceChildren(...events.map((event) => createPolicyEvent(event)));
}

function sortedTracker() {
  return state.data.tracker.filter(matches).sort((a, b) => {
    const field = state.sort === "updated" ? "lastUpdated" : "publicationDate";
    return (safeDate(b[field])?.getTime() || 0) - (safeDate(a[field])?.getTime() || 0);
  });
}

function renderTrackerList(container) {
  const events = sortedTracker();
  const count = document.querySelector("[data-results-count]");
  if (count) count.textContent = `${events.length} ${events.length === 1 ? "development" : "developments"}`;
  if (!events.length) { container.replaceChildren(emptyState("No policy developments match your search", "Clear a filter or use a broader search term.")); return; }
  container.replaceChildren(...events.map((event) => {
    const node = $("#tracker-template").content.firstElementChild.cloneNode(true);
    $(".tracker-date", node).textContent = formatDate(event.publicationDate, { day: "numeric", month: "short", year: "numeric" }) || "Date unavailable";
    $(".event-area", node).textContent = areaLabel(event.area);
    $(".event-status", node).textContent = event.status || "Status pending";
    node.querySelectorAll("a[data-route]").forEach((link) => { link.href = eventPath(event); });
    $("h2 a", node).textContent = event.title || "Untitled policy development";
    $(".tracker-summary", node).textContent = event.whatHappened || "Summary unavailable.";
    $(".tracker-institution", node).textContent = event.institution || "Institution unavailable";
    return node;
  }));
}

function renderListOnly() {
  const list = $("#content-list");
  if (!list) return;
  if (routeName() === "briefing") renderBriefingList(list);
  if (routeName() === "tracker") renderTrackerList(list);
}

function renderBriefing() {
  const date = formatDate(state.data.meta.reportDate) || "Date unavailable";
  setIntro("Daily briefing", "Today’s verified developments", "Verified regulatory and policy developments from the last 24 hours.", `${date} · ${formatUpdated(state.data.meta.generatedAt)}`);
  setMeta("India Policy Intelligence", "Today’s verified Indian policy and regulatory developments.");
  const view = $("#route-view");
  const controls = createControls(state.data.events);
  const list = document.createElement("section"); list.id = "content-list"; list.setAttribute("aria-label", "Today’s policy developments");
  view.replaceChildren(controls, list);
  renderBriefingList(list);
}

function renderTracker() {
  setIntro("Policy tracker", "Policy developments over time", "Browse verified developments, legal status changes and related updates.", `${state.data.tracker.length} recorded developments`);
  setMeta("Policy Tracker", "Search and browse the history of verified Indian policy and regulatory developments.");
  const view = $("#route-view");
  const controls = createControls(state.data.tracker, true);
  const list = document.createElement("section"); list.id = "content-list"; list.className = "tracker-list"; list.setAttribute("aria-label", "Policy tracker results");
  view.replaceChildren(controls, list);
  renderTrackerList(list);
}

function renderWatchlist() {
  setIntro("Watchlist", "Open policy developments", "Only pending developments with a concrete next step or deadline.", `${state.data.watchlist.length} currently open`);
  setMeta("Policy Watchlist", "Pending Indian policy developments likely to move next.");
  const view = $("#route-view");
  if (!state.data.watchlist.length) { view.replaceChildren(emptyState("Nothing currently requires monitoring", "The watchlist will populate when a verified draft, consultation, Bill, deadline or other concrete next step remains open.")); return; }
  const list = document.createElement("section"); list.className = "watch-list";
  list.replaceChildren(...state.data.watchlist.map((event) => {
    const node = $("#watch-template").content.firstElementChild.cloneNode(true);
    $(".watch-state", node).textContent = `Pending · ${event.status || "Next step"}`;
    $(".event-area", node).textContent = areaLabel(event.area);
    $("h2 a", node).textContent = event.title || "Untitled policy development";
    $(".watch-next", node).textContent = event.nextStep || (event.deadline ? `Deadline ${formatDate(event.deadline)}` : "Next formal step pending.");
    $(".watch-last", node).textContent = `Last update: ${formatDate(event.lastUpdated, { day: "numeric", month: "short", year: "numeric" }) || "Date unavailable"}`;
    node.querySelectorAll("a[data-route]").forEach((link) => { link.href = eventPath(event); });
    return node;
  }));
  view.replaceChildren(list);
}

function findHistory(event) {
  const result = [];
  let previousId = event.previousEventId;
  const seen = new Set([event.id]);
  while (previousId && !seen.has(previousId)) {
    const previous = state.data.tracker.find((item) => item.id === previousId);
    if (!previous) break;
    result.push(previous); seen.add(previous.id); previousId = previous.previousEventId;
  }
  return result;
}

function renderDetail() {
  const slug = decodeURIComponent(location.pathname.split("/").filter(Boolean)[1] || "");
  const event = state.data.tracker.find((item) => item.slug === slug);
  if (!event) {
    setIntro("Policy tracker", "Development not found", "The requested policy development is not present in the published tracker.");
    setMeta("Development not found", "The requested policy development could not be found.");
    $("#route-view").replaceChildren(emptyState("This policy update is unavailable", "It may have been superseded or the link may be incorrect. Return to the Policy Tracker to continue browsing."));
    return;
  }
  setIntro(areaLabel(event.area), event.title, `${event.status || "Policy update"}${event.institution ? ` · ${event.institution}` : ""}`, `${formatDate(event.publicationDate) || "Date unavailable"}`);
  setMeta(event.title, event.whatHappened);
  const back = document.createElement("a"); back.className = "detail-back"; back.href = "/tracker"; back.dataset.route = ""; back.textContent = "← Back to Policy Tracker";
  const detail = createPolicyEvent(event, true);
  const history = findHistory(event);
  const children = [back, detail];
  if (history.length) {
    const section = document.createElement("section"); section.className = "event-history";
    const heading = document.createElement("h2"); heading.textContent = "Development history"; section.append(heading);
    history.forEach((item) => {
      const row = document.createElement("div"); row.className = "history-item";
      const time = document.createElement("time"); time.textContent = formatDate(item.publicationDate, { day: "numeric", month: "short", year: "numeric" }) || "Date unavailable";
      const title = document.createElement("h3"); title.textContent = `${item.status || "Update"} — ${item.title}`;
      row.append(time, title); section.append(row);
    });
    children.push(section);
  }
  $("#route-view").replaceChildren(...children);
}

function render() {
  if (!state.data) return;
  setNavigation();
  $("#header-updated").textContent = formatUpdated(state.data.meta.generatedAt);
  const route = routeName();
  if (route === "briefing") renderBriefing();
  if (route === "tracker") renderTracker();
  if (route === "watchlist") renderWatchlist();
  if (route === "detail") renderDetail();
}

function renderError(error) {
  setIntro("Daily briefing", "The briefing is temporarily unavailable", "No policy claims are shown while the published dataset cannot be verified.");
  setMeta("Briefing unavailable", "The latest policy briefing could not be loaded.");
  const section = document.createElement("section"); section.className = "error-state";
  const heading = document.createElement("h2"); heading.textContent = "Unable to load verified updates";
  const copy = document.createElement("p"); copy.textContent = "The publication data could not be reached. Try again shortly; the interface will not substitute unverified content.";
  const retry = document.createElement("button"); retry.type = "button"; retry.textContent = "Try again"; retry.addEventListener("click", load);
  section.append(heading, copy, retry); $("#route-view").replaceChildren(section);
  console.error(error);
}

async function load() {
  try { state.data = await fetchData(); render(); }
  catch (error) { renderError(error); }
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-route]");
  if (!link || link.target || link.origin !== location.origin) return;
  event.preventDefault();
  if (link.dataset.nav || link.classList.contains("wordmark")) {
    state.area = "All";
    state.query = "";
    state.status = "All";
    state.sort = "newest";
  }
  history.pushState({}, "", link.pathname);
  window.scrollTo({ top: 0, behavior: "smooth" });
  render();
});
window.addEventListener("popstate", render);
window.addEventListener("scroll", () => {
  const distance = document.documentElement.scrollHeight - innerHeight;
  $("#reading-progress").style.width = `${distance > 0 ? Math.min(100, scrollY / distance * 100) : 0}%`;
}, { passive: true });

load();
setInterval(async () => {
  try {
    const next = await fetchData();
    if (!state.data || next.meta.generatedAt !== state.data.meta.generatedAt) { state.data = next; render(); }
  } catch (_) { /* preserve the last verified edition during a transient refresh error */ }
}, 60000);
