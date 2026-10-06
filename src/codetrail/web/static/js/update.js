// Updating the guide from the page (design section 15.4): the facts are refreshed for free, then the estimate waits
// in a dialog; only Go ahead, with the estimate's single-use id, lets paid work start. The update panel shows each
// step as the server reports it, closes once the update is done, and stays open with the reason when it fails.
import { getJSON, post } from "./api.js";

const dialog = document.querySelector("[data-estimate-dialog]");
const panel = document.querySelector("[data-update-panel]");
const log = panel?.querySelector("[data-update-log]");
const stateLine = panel?.querySelector("[data-update-state]");
const ACTIVE = ["preparing", "waiting", "running"];
const DONE_SHOWN_MS = 1500; // how long "Updated." stays before the panel closes
let estimateId = null;
let polling = false;
let active = false;
let after = 0; // the number of the last step shown

function say(text, failed = false) {
  stateLine.textContent = text;
  stateLine.classList.toggle("failed", failed);
}

export function showUpdatePanel() {
  if (panel) panel.hidden = false;
}

export function hideUpdatePanel() {
  if (panel) panel.hidden = true;
}

export function isUpdatePanelOpen() {
  return Boolean(panel && !panel.hidden);
}

async function decide(url) {
  if (!estimateId) return;
  const id = estimateId;
  estimateId = null;
  dialog.close();
  await post(url, { estimate_id: id });
  poll();
}

async function poll(reloadWhenDone = true) {
  if (polling) return;
  polling = true;
  try {
    const result = await getJSON(`/update/status?after=${after}`);
    const labels = panel.dataset;
    const { state, message } = result;
    active = ACTIVE.includes(state);
    if (result.log_html) log.insertAdjacentHTML("beforeend", result.log_html); // rendered, escaped on the server
    after = result.next ?? after;
    if (active) say(labels.labelRunning);
    else if (state === "done") say(labels.labelDone);
    else if (state === "failed") say(`${labels.labelFailed} ${message ?? ""}`, true);
    else if (state === "declined") say(message ?? "", true);
    if (state === "waiting" && result.estimate_id && result.estimate_id !== estimateId) {
      estimateId = result.estimate_id;
      dialog.querySelector("[data-estimate-body]").innerHTML = result.estimate_html; // rendered, escaped on the server
      dialog.showModal();
    }
    polling = false;
    if (active) setTimeout(() => poll(reloadWhenDone), 1500);
    else if (state === "done") setTimeout(reloadWhenDone ? () => window.location.reload() : hideUpdatePanel, DONE_SHOWN_MS);
  } catch {
    polling = false;
  }
}

export async function startUpdate() {
  if (!dialog) return;
  showUpdatePanel();
  if (active) return; // one runs already: the panel shows it
  log.replaceChildren();
  say(panel.dataset.labelRunning);
  const response = await post("/update", {});
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    if (response.status !== 409 && result.message) {
      say(result.message, true);
      return;
    }
  }
  poll();
}

export function setUpUpdate() {
  if (!dialog) return;
  dialog.querySelector("[data-estimate-go]").addEventListener("click", () => decide("/update/confirm"));
  dialog.querySelector("[data-estimate-cancel]").addEventListener("click", () => decide("/update/cancel"));
  dialog.addEventListener("cancel", () => decide("/update/cancel")); // Escape cancels too
  for (const button of document.querySelectorAll("[data-update-button]")) {
    button.addEventListener("click", startUpdate);
  }
  panel.querySelector("[data-update-close]").addEventListener("click", hideUpdatePanel);
  // An update already running or waiting when the page opens shows in the panel, with its steps so far.
  getJSON("/update/status")
    .then((result) => {
      if (ACTIVE.includes(result.state)) {
        showUpdatePanel();
        poll(false);
      } else {
        after = result.next ?? 0; // the steps of an update that has ended aren't shown again
      }
    })
    .catch(() => {});
}
