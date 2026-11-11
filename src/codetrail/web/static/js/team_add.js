// The add-repositories screen (design section 24.6, option A): the services' sign-ins, a source picker, the "Follow
// the whole source" switch above the source's repositories, and saving, which shows the free first refresh step by
// step. Every value a service returns (names, accounts, branches, its messages) is set with textContent or as an
// attribute, never parsed as markup; the rows are built with DOM methods.
import { getJSON, post } from "./api.js";
import { Labels } from "./labels.js";

const DAY = 86400000;
const POLL_MS = 1000;
const screen = document.querySelector("[data-team-add]");
const labels = new Labels({
  labels: JSON.parse(document.querySelector("[data-team-add-labels]").textContent),
  lang: document.documentElement.lang,
});
const picker = screen.querySelector("[data-source-picker]");
const organizationForm = screen.querySelector("[data-organization-form]");
const sourceState = screen.querySelector("[data-source-state]");
const list = screen.querySelector("[data-list]");
const rowsList = screen.querySelector("[data-rows]");
const followAll = screen.querySelector("[data-follow-all]");
const followLabel = screen.querySelector("[data-follow-label]");
const filter = screen.querySelector("[data-filter]");
const activeOnly = screen.querySelector("[data-active-only]");
const showArchived = screen.querySelector("[data-show-archived]");
const noMatch = screen.querySelector("[data-no-match]");
const saveButton = screen.querySelector("[data-save]");
const saveState = screen.querySelector("[data-save-state]");
const refreshSection = screen.querySelector("[data-refresh]");
const refreshLog = screen.querySelector("[data-refresh-log]");
const refreshState = screen.querySelector("[data-refresh-state]");

let sources = []; // [{service, name}], in the picker's order
// The chosen source's repositories and the selection being made: `leftOut` while following the whole source,
// `chosen` while picking by hand, and a branch for each repository not taught on its default branch.
let current = null;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// --- Services and sources ---

// A service's fix is Codetrail's own text (the adapters' constants), never the service's: its https address becomes
// a link and the command after "run: " a code span. Signed out, the fix is the command alone.
const ADDRESS = /(https:\/\/[^\s,]+)/;
const RUN = "run: ";

function fixHint(status) {
  const hint = element("span", "faint");
  if (status.installed) {
    hint.append(element("code", "", status.fix));
    return hint;
  }
  const run = status.fix.indexOf(RUN);
  const words = run < 0 ? status.fix : status.fix.slice(0, run + RUN.length);
  for (const part of words.split(ADDRESS)) {
    if (!ADDRESS.test(part)) {
      if (part) hint.append(part);
      continue;
    }
    const link = element("a", "", part);
    link.href = part;
    link.rel = "noreferrer";
    link.target = "_blank";
    hint.append(link);
  }
  if (run >= 0) hint.append(element("code", "", status.fix.slice(run + RUN.length)));
  return hint;
}

function showServices(services) {
  const chips = screen.querySelector("[data-services]");
  chips.replaceChildren();
  for (const status of services) {
    const chip = element("li", `service-chip ${status.signed_in ? "signed-in" : "signed-out"}`);
    chip.append(element("strong", "", labels.labels.services[status.service] ?? status.service));
    let state;
    if (status.signed_in) {
      state = status.account ? labels.text("signedInAs", { account: status.account }) : labels.text("signedIn");
    } else {
      state = status.installed ? labels.text("signedOut") : labels.text("notInstalled");
    }
    chip.append(element("span", "", state));
    if (!status.signed_in && status.fix) chip.append(fixHint(status));
    if (status.sources_failed) chip.append(element("span", "faint", labels.text("sourcesFailed")));
    chips.append(chip);
  }
  // Azure DevOps can't list its organizations, so the reader names one, once it's installed and signed in.
  organizationForm.hidden = !services.some((status) => status.service === "azure_devops" && status.signed_in);
}

function showSources(found, failed) {
  sources = found;
  picker.replaceChildren(element("option", "", labels.text("chooseSource")));
  picker.firstChild.value = "";
  const groups = new Map();
  found.forEach((source, index) => {
    if (!groups.has(source.service)) {
      const group = element("optgroup");
      group.label = labels.labels.services[source.service] ?? source.service;
      groups.set(source.service, group);
      picker.append(group);
    }
    const option = element("option", "", source.name);
    option.value = String(index);
    groups.get(source.service).append(option);
  });
  picker.disabled = found.length === 0;
  if (found.length === 0) sourceState.textContent = labels.text(failed ? "couldNotList" : "noSources");
  else sourceState.textContent = "";
}

async function listSources(organization = "") {
  sourceState.textContent = labels.text("listing");
  const query = organization ? `?organization=${encodeURIComponent(organization)}` : "";
  try {
    const result = await getJSON(`/team/hosting/sources${query}`);
    showServices(result.services);
    showSources(result.sources, result.services.some((status) => status.sources_failed));
  } catch (error) {
    sourceState.textContent = error.body?.error ?? labels.text("couldNotList");
  }
}

// --- The repositories ---

// Whether saving now follows the repository: following the whole source follows the active ones not left out, and
// keeps one it follows already; archived ones are never followed.
function willFollow(row) {
  if (row.archived) return false;
  if (current.follow) return !current.leftOut.has(row.id) && (row.active || row.followed);
  return current.chosen.has(row.id);
}

// While following the whole source, a quiet repository that isn't left out is ticked: it's followed once it's active.
function isTicked(row) {
  if (row.archived) return false;
  return current.follow ? !current.leftOut.has(row.id) : current.chosen.has(row.id);
}

function isShown(row) {
  const text = filter.value.trim().toLowerCase();
  if (text && !row.name.toLowerCase().includes(text)) return false;
  if (row.archived) return showArchived.checked;
  return !activeOnly.checked || row.active || row.followed; // a followed quiet one is still the team's
}

function pushedText(row) {
  if (!row.pushed_at) return "";
  const days = Math.max(0, Math.floor((Date.now() - Date.parse(row.pushed_at)) / DAY));
  return days === 0 ? labels.text("pushedToday") : labels.count("pushedDaysAgo", days);
}

function sizeText(row) {
  if (row.size_kb === null || row.size_kb === undefined) return "";
  if (row.size_kb < 1024) return labels.text("sizeKb", { size: labels.number(row.size_kb) });
  return labels.text("sizeMb", { size: labels.number(Math.round(row.size_kb / 102.4) / 10) });
}

function branchPicker(row) {
  const select = element("select", "branch-picker");
  select.setAttribute("aria-label", labels.text("branchFor", { name: row.name }));
  const chosen = current.branches.get(row.id) ?? row.branch;
  const option = element("option", "", chosen);
  option.value = chosen;
  select.append(option);
  select.disabled = row.followed || row.archived; // a followed repository keeps the branch it's taught on
  let loaded = false;
  // Its branches are listed when it's opened, from the service.
  const load = async () => {
    if (loaded) return;
    loaded = true;
    try {
      const { branches } = await getJSON(`/team/hosting/branches?${query()}&id=${encodeURIComponent(row.id)}`);
      const selected = select.value;
      select.replaceChildren(...branches.map((branch) => {
        const choice = element("option", "", branch);
        choice.value = branch;
        return choice;
      }));
      select.value = branches.includes(selected) ? selected : row.default_branch;
    } catch {
      loaded = false;
    }
  };
  select.addEventListener("focus", load);
  select.addEventListener("pointerdown", load);
  select.addEventListener("change", () => {
    if (select.value === row.default_branch) current.branches.delete(row.id);
    else current.branches.set(row.id, select.value);
  });
  return select;
}

function rowFor(row) {
  const item = element("li", "repository-row");
  item.dataset.repository = row.id;
  const label = element("label", "grow repository-name");
  const box = element("input");
  box.type = "checkbox";
  box.checked = isTicked(row);
  box.disabled = row.archived;
  box.addEventListener("change", () => {
    if (current.follow && box.checked) current.leftOut.delete(row.id);
    else if (current.follow) current.leftOut.add(row.id);
    else if (box.checked) current.chosen.add(row.id);
    else current.chosen.delete(row.id);
    summarize();
  });
  const name = element("bdi", "", row.name);
  label.append(box, " ", name);
  item.append(label);
  for (const text of [pushedText(row), sizeText(row)]) if (text) item.append(element("span", "faint", text));
  const waits = element("span", "faint", labels.text("whenActive"));
  waits.hidden = !(isTicked(row) && !willFollow(row));
  box.addEventListener("change", () => { waits.hidden = !(isTicked(row) && !willFollow(row)); });
  item.append(waits);
  if (row.archived) item.append(element("span", "chip", labels.text("archived")));
  if (row.gone) item.append(element("span", "chip", labels.text("gone")));
  item.append(branchPicker(row));
  return item;
}

function showRows() {
  const shown = current.rows.filter(isShown);
  rowsList.replaceChildren(...shown.map(rowFor));
  rowsList.hidden = shown.length === 0;
  noMatch.hidden = shown.length !== 0;
  noMatch.textContent = labels.text("noMatch");
  summarize();
}

function summarize() {
  const leftOut = current.rows.filter((row) => !row.archived && current.leftOut.has(row.id)).length;
  followLabel.textContent = current.follow && leftOut ? labels.count("leftOut", leftOut) : labels.text("followAll");
  // What saving adds: a repository the team follows already isn't added again, so a revisit that only leaves
  // some out, or flips the switch, just saves.
  const added = current.rows.filter((row) => willFollow(row) && !row.followed).length;
  saveButton.textContent = added ? labels.count("add", added) : labels.text("save");
}

function query() {
  return `service=${encodeURIComponent(current.service)}&source=${encodeURIComponent(current.source)}`;
}

async function chooseSource() {
  const source = sources[Number(picker.value)];
  list.hidden = true;
  if (picker.value === "" || !source) return;
  sourceState.textContent = labels.text("listing");
  saveState.textContent = "";
  await listRepositories(source.service, source.name);
}

// The source's repositories as the service lists them and the team follows them now, and its saved selection.
async function listRepositories(service, name) {
  try {
    const params = `service=${encodeURIComponent(service)}&source=${encodeURIComponent(name)}`;
    const { repositories, following } = await getJSON(`/team/hosting/repositories?${params}`);
    current = {
      service,
      source: name,
      rows: repositories,
      follow: following ? following.follow === "all" : true,
      cloneOver: following?.clone_over ?? "https",
      leftOut: new Set(following?.left_out ?? []),
      chosen: new Set(following?.chosen ?? []),
      branches: new Map(),
    };
    followAll.checked = current.follow;
    sourceState.textContent = "";
    list.hidden = false;
    showRows();
  } catch (error) {
    sourceState.textContent = error.body?.error ?? labels.text("couldNotList");
  }
}

// Turned off, the list becomes a hand-picked selection, starting from what would be followed; turned on again,
// whatever isn't ticked is left out.
function switchFollow() {
  const usable = current.rows.filter((row) => !row.archived);
  if (followAll.checked) {
    current.leftOut = new Set(usable.filter((row) => !current.chosen.has(row.id)).map((row) => row.id));
  } else {
    current.chosen = new Set(usable.filter(willFollow).map((row) => row.id));
  }
  current.follow = followAll.checked;
  showRows();
}

// --- Saving and the refresh ---

function selection() {
  // Only ids the list holds: the server refuses any other.
  const ids = new Set(current.rows.map((row) => row.id));
  const listed = new Set(current.rows.filter((row) => !row.gone).map((row) => row.id));
  return {
    service: current.service,
    source: current.source,
    follow: current.follow ? "all" : "chosen",
    clone_over: current.cloneOver,
    left_out: current.follow ? [...current.leftOut].filter((id) => ids.has(id)) : [],
    chosen: current.follow ? [] : [...current.chosen].filter((id) => listed.has(id)),
    branches: Object.fromEntries([...current.branches].filter(([id]) => listed.has(id))),
  };
}

function stepText(step) {
  if (step.step === "started") return labels.text("started", { repository: step.repository });
  if (step.step === "done") return labels.text("done", { repository: step.repository });
  return labels.text("failed", { repository: step.repository, reason: step.message ?? "" });
}

async function pollRefresh(after = 0) {
  try {
    const status = await getJSON(`/team/refresh/status?after=${after}`);
    for (const step of status.steps) {
      const line = element("li", `update-line${step.step === "done" ? " done" : step.step === "failed" ? " failed" : ""}`);
      line.textContent = stepText(step);
      refreshLog.append(line);
    }
    if (status.state === "running") {
      setTimeout(() => pollRefresh(status.next), POLL_MS);
      return;
    }
    refreshState.textContent = labels.text("finished");
  } catch {
    setTimeout(() => pollRefresh(after), POLL_MS);
  }
}

async function save() {
  saveButton.disabled = true;
  saveState.textContent = labels.text("saving");
  try {
    const response = await post("/team/repositories", selection());
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      saveState.textContent = result.error ?? labels.text("saveFailed");
      return;
    }
    saveState.textContent = "";
    // Listed again, so what was just added shows as followed and the button counts only what's still new.
    await listRepositories(current.service, current.source);
    refreshLog.replaceChildren();
    refreshSection.hidden = false;
    if (result.refresh) {
      refreshState.textContent = labels.text("running");
      pollRefresh();
    } else {
      refreshState.textContent = labels.text(result.added.length ? "notStarted" : "nothingNew");
    }
    refreshSection.scrollIntoView({ block: "nearest" });
  } catch {
    saveState.textContent = labels.text("saveFailed");
  } finally {
    saveButton.disabled = false;
  }
}

picker.addEventListener("change", chooseSource);
followAll.addEventListener("change", switchFollow);
for (const control of [filter, activeOnly, showArchived]) {
  control.addEventListener(control === filter ? "input" : "change", () => current && showRows());
}
saveButton.addEventListener("click", save);
screen.querySelector("[data-organization-form]").addEventListener("submit", (event) => {
  event.preventDefault();
  listSources(screen.querySelector("[data-organization]").value.trim());
});
listSources();
document.documentElement.dataset.ready = "true"; // every handler is in place
