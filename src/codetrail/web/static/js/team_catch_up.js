// Catching the team up from its page (design section 24.8), as update.js updates one guide: every repository's
// facts are refreshed for free, then the one estimate waits in a dialog with a checkbox per repository. Unticking a
// row recomputes the totals and the overview's row exactly as the server's `CatchUpEstimate.totals` does; only Go
// ahead, with the estimate's single-use id and the ticked targets, lets paid work start. The panel shows each step,
// prefixed by its repository's name, and the page reloads once the catch-up is done.
import { getJSON, post } from "./api.js";

const button = document.querySelector("[data-catch-up-button]");
const dialog = document.querySelector("[data-catch-up-dialog]");
const body = dialog?.querySelector("[data-catch-up-body]");
const panel = document.querySelector("[data-catch-up-panel]");
const log = panel?.querySelector("[data-catch-up-log]");
const stateLine = panel?.querySelector("[data-catch-up-state]");
const ACTIVE = ["preparing", "waiting", "running"];
const POLL_MS = 1500;
const DONE_SHOWN_MS = 1500; // how long "Caught up." stays before the page reloads
let estimateId = null;
let polling = false;
let active = false;
let after = 0; // the number of the last step shown

function say(text, failed = false) {
  stateLine.textContent = text;
  stateLine.classList.toggle("failed", failed);
}

// A token count for people, as the server's `tokens_text` writes it: 950, 12k, 1.4M (halves round to even).
function roundHalfEven(value) {
  const floor = Math.floor(value);
  const rest = value - floor;
  if (rest !== 0.5) return Math.round(value);
  return floor % 2 === 0 ? floor : floor + 1;
}

function tokensText(tokens) {
  if (tokens >= 1_000_000) return (roundHalfEven(tokens / 100_000) / 10).toFixed(1) + "M";
  if (tokens >= 1_000) return `${roundHalfEven(tokens / 1_000)}k`;
  return `${tokens}`;
}

function dollarsText(amount) {
  return amount === null ? "" : `$${amount.toFixed(2)}`;
}

function amount(value) {
  return value === "" || value === undefined ? null : Number(value);
}

// The sum of the amounts in order, or null when any is unknown (a call with no configured price).
function total(amounts) {
  return amounts.includes(null) ? null : amounts.reduce((sum, value) => sum + value, 0);
}

function sum(numbers) {
  return numbers.reduce((running, value) => running + value, 0);
}

// The ticked rows' totals. The overview counts in the maximums while at least two ticked rows may write a digest,
// and in the expected figures while at least two expect one; the dollars at most are capped at the team's budget,
// and nothing expected exceeds its maximum.
function recompute() {
  const rows = [...body.querySelectorAll("[data-catch-up-line]")];
  const ticked = rows.filter((row) => row.querySelector("input[name=target]").checked);
  const overview = body.querySelector("[data-overview]");
  const possible = ticked.filter((row) => row.dataset.digest === "true").length;
  const expecting = ticked.filter((row) => row.dataset.digestExpected === "true").length;
  const atMost = overview && possible >= 2 ? [overview] : [];
  const expected = overview && expecting >= 2 ? [overview] : [];
  if (overview) overview.hidden = atMost.length === 0;
  const maximumTokens =
    sum(ticked.map((row) => Number(row.dataset.maximumTokens))) + sum(atMost.map((call) => Number(call.dataset.tokens)));
  let maximumUsd = total([
    ...ticked.map((row) => amount(row.dataset.maximumUsd)),
    ...atMost.map((call) => amount(call.dataset.costUsd)),
  ]);
  if (maximumUsd !== null) maximumUsd = Math.min(maximumUsd, Number(body.querySelector("[data-budget-usd]").dataset.budgetUsd));
  let expectedUsd = total([
    ...ticked.map((row) => amount(row.dataset.expectedUsd)),
    ...expected.map((call) => amount(call.dataset.costUsd)),
  ]);
  if (expectedUsd !== null && maximumUsd !== null) expectedUsd = Math.min(expectedUsd, maximumUsd);
  const expectedTokens =
    sum(ticked.map((row) => Number(row.dataset.expectedTokens))) + sum(expected.map((call) => Number(call.dataset.tokens)));
  const shown = {
    "expected-tokens": tokensText(Math.min(expectedTokens, maximumTokens)),
    "maximum-tokens": tokensText(maximumTokens),
    "expected-usd": dollarsText(expectedUsd),
    "maximum-usd": dollarsText(maximumUsd),
  };
  for (const [name, text] of Object.entries(shown)) body.querySelector(`[data-total="${name}"]`).textContent = text;
  body.querySelector("[data-total-prices]").hidden = expectedUsd === null || maximumUsd === null;
}

function tickedTargets() {
  return [...body.querySelectorAll("input[name=target]:checked")].map((box) => box.value);
}

async function decide(url, data) {
  if (!estimateId) return;
  const id = estimateId;
  estimateId = null;
  dialog.close();
  const response = await post(url, { estimate_id: id, ...data });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    if (result.error) say(result.error, true);
  }
  poll();
}

async function poll(reloadWhenDone = true) {
  if (polling) return;
  polling = true;
  try {
    const result = await getJSON(`/team/catch-up/status?after=${after}`);
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
      body.innerHTML = result.estimate_html; // rendered, escaped on the server
      dialog.showModal();
    }
    polling = false;
    if (active) setTimeout(() => poll(reloadWhenDone), POLL_MS);
    else if (state === "done") {
      setTimeout(reloadWhenDone ? () => window.location.reload() : () => (panel.hidden = true), DONE_SHOWN_MS);
    }
  } catch {
    polling = false;
  }
}

async function startCatchUp() {
  panel.hidden = false;
  if (active) return; // one runs already: the panel shows it
  log.replaceChildren();
  say(panel.dataset.labelRunning);
  const response = await post("/team/catch-up", {});
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    if (result.message) say(result.message, true);
    if (!ACTIVE.includes(result.state)) return;
  }
  poll();
}

function setUpCatchUp() {
  if (!button || !dialog || !panel) return;
  body.addEventListener("change", recompute);
  dialog.querySelector("[data-catch-up-go]").addEventListener("click", () =>
    decide("/team/catch-up/confirm", { targets: tickedTargets() }),
  );
  dialog.querySelector("[data-catch-up-cancel]").addEventListener("click", () => decide("/team/catch-up/cancel", {}));
  dialog.addEventListener("cancel", () => decide("/team/catch-up/cancel", {})); // Escape cancels too
  button.addEventListener("click", startCatchUp);
  panel.querySelector("[data-catch-up-close]").addEventListener("click", () => {
    panel.hidden = true;
  });
  // A catch-up already running or waiting when the page opens shows in the panel, with its steps so far.
  getJSON("/team/catch-up/status")
    .then((result) => {
      if (ACTIVE.includes(result.state)) {
        panel.hidden = false;
        poll(false);
      } else {
        after = result.next ?? 0; // the steps of a catch-up that has ended aren't shown again
      }
    })
    .catch(() => {});
}

setUpCatchUp();
