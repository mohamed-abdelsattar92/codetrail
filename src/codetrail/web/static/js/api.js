// Talking to Codetrail's server: every write and every JSON read carries the session's token (design 7.4 and 16.3).

const token = document.querySelector('meta[name="codetrail-token"]')?.content ?? "";
// The prefix the page is served under: empty at `/`, `/repos/<target>` on the team page (design section 24.7).
const base = document.querySelector('meta[name="codetrail-base"]')?.content ?? "";

// One of the page's own routes, under its prefix. The shared static files stay at the root and never go through it.
export function sitePath(path) {
  return base + path;
}

export async function post(url, data) {
  return fetch(sitePath(url), {
    method: "POST",
    headers: { "X-Codetrail-Token": token, "Content-Type": "application/json" },
    body: JSON.stringify(data),
    credentials: "same-origin",
  });
}

export async function getJSON(url, options = {}) {
  const response = await fetch(sitePath(url), {
    headers: { "X-Codetrail-Token": token, Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
    signal: options.signal,
  });
  if (!response.ok) {
    const error = new Error(`${response.status}`);
    error.body = await response.json().catch(() => null); // a JSON refusal's message, for the script to show
    throw error;
  }
  return response.json();
}

// What a call actually used, beside its estimate on the button (design section 15.4).
export function usedText(labels, usage) {
  if (!usage) return "";
  const tokens = usage.tokens >= 1000 ? `${Math.round(usage.tokens / 1000)}k` : `${usage.tokens}`;
  const cost = usage.cost_usd ? ` · $${usage.cost_usd.toFixed(2)}` : "";
  return `${labels.dataset.labelUsed} ${tokens} ${labels.dataset.labelTokens}${cost}`;
}

// Session and local storage can be missing or refuse (private windows, blocked site data); the page works without.
// Every guide on the team's server shares one origin, so each keeps what it remembers under its prefix; at `/` the
// keys are as they always were. The theme is the reader's choice for every page, so its key is shared.
const SHARED_KEYS = ["codetrail.theme"];

function storageKey(key) {
  return SHARED_KEYS.includes(key) ? key : base + key;
}

export function remember(storage, key, value) {
  try {
    if (value === null) window[storage].removeItem(storageKey(key));
    else window[storage].setItem(storageKey(key), value);
  } catch {
    // nothing to do: the choice just isn't remembered
  }
}

export function recall(storage, key) {
  try {
    return window[storage].getItem(storageKey(key));
  } catch {
    return null;
  }
}
