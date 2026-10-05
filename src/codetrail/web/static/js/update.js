// Updating the guide from the page (design section 15.4): the facts are refreshed for free, then the estimate waits
// in a dialog; only Go ahead, with the estimate's single-use id, lets paid work start.
import { getJSON, post } from "./api.js";

const dialog = document.querySelector("[data-estimate-dialog]");
const toast = document.querySelector("[data-update-status]");
const ACTIVE = ["preparing", "waiting", "running"];
let estimateId = null;
let polling = false;

function say(text) {
  if (toast) toast.textContent = text;
}

function buttons(disabled) {
  for (const button of document.querySelectorAll("[data-update-button]")) button.disabled = disabled;
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
    const result = await getJSON("/update/status");
    const labels = toast?.dataset ?? {};
    const { state, message } = result;
    const prefix = { preparing: labels.labelPreparing, running: labels.labelRunning, done: labels.labelDone,
                     failed: `${labels.labelFailed ?? ""} ` }[state] ?? ""; // prettier-ignore
    say(state === "idle" || state === "waiting" ? "" : prefix + (message ?? ""));
    buttons(ACTIVE.includes(state));
    if (state === "waiting" && result.estimate_id && result.estimate_id !== estimateId) {
      estimateId = result.estimate_id;
      dialog.querySelector("[data-estimate-body]").innerHTML = result.estimate_html; // rendered, escaped on the server
      dialog.showModal();
    }
    polling = false;
    if (ACTIVE.includes(state)) setTimeout(() => poll(reloadWhenDone), 1500);
    else if (state === "done" && reloadWhenDone) window.location.reload();
  } catch {
    polling = false;
  }
}

export async function startUpdate() {
  if (!dialog) return;
  const response = await post("/update", {});
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    if (result.message) say(result.message);
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
  poll(false); // show an update that is already running or waiting
}
