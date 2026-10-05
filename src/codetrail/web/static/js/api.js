// Talking to Codetrail's server: every write and every JSON read carries the session's token (design 7.4 and 16.3).

const token = document.querySelector('meta[name="codetrail-token"]')?.content ?? "";

export async function post(url, data) {
  return fetch(url, {
    method: "POST",
    headers: { "X-Codetrail-Token": token, "Content-Type": "application/json" },
    body: JSON.stringify(data),
    credentials: "same-origin",
  });
}

export async function getJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { "X-Codetrail-Token": token, Accept: "application/json" },
    credentials: "same-origin",
    cache: "no-store",
    signal: options.signal,
  });
  if (!response.ok) throw new Error(`${response.status}`);
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
export function remember(storage, key, value) {
  try {
    if (value === null) window[storage].removeItem(key);
    else window[storage].setItem(key, value);
  } catch {
    // nothing to do: the choice just isn't remembered
  }
}

export function recall(storage, key) {
  try {
    return window[storage].getItem(key);
  } catch {
    return null;
  }
}
