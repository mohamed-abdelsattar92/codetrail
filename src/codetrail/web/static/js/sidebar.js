// The sidebar (design section 22): hiding and showing it, peeking at it while hidden, folding its sections and
// pinning pages. Hidden and folded are remembered in this browser; pins are kept by Codetrail. Titles are only ever
// moved with textContent and attributes.
import { post, remember } from "./api.js";

const root = document.documentElement;
const SECTIONS = ["paths", "areas", "concepts", "answers", "more"];
const PEEK_DELAY = 150; // ms the pointer rests on the edge before the sidebar slides out
const LEAVE_DELAY = 300; // ms after the pointer leaves before it slides away
const narrow = window.matchMedia("(max-width: 900px)");
let peekTimer = 0;

const panel = () => document.querySelector("#sidebar");
const edge = () => document.querySelector("[data-sidebar-edge]");
const isHidden = () => root.dataset.sidebar === "hidden";
export const isPeeking = () => document.body.classList.contains("sidebar-peek");

function syncToggle() {
  const button = document.querySelector("[data-sidebar-toggle]");
  if (!button) return;
  button.setAttribute("aria-expanded", String(!isHidden()));
  button.setAttribute("aria-label", isHidden() ? button.dataset.labelShow : button.dataset.labelHide);
}

export function closePeek(returnFocus = false) {
  clearTimeout(peekTimer);
  if (!isPeeking()) return;
  document.body.classList.remove("sidebar-peek");
  edge()?.setAttribute("aria-expanded", "false");
  if (returnFocus) edge()?.focus();
}

function openPeek(focusFirst) {
  clearTimeout(peekTimer);
  if (!isHidden() || narrow.matches) return;
  document.body.classList.add("sidebar-peek");
  edge()?.setAttribute("aria-expanded", "true");
  if (focusFirst) panel()?.querySelector("a[href]")?.focus();
}

function closeSoon() {
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => closePeek(), LEAVE_DELAY);
}

// Esc: the focus goes back to the edge only when it was in the sidebar; elsewhere it stays where the reader is.
export function dismissPeek() {
  closePeek(Boolean(panel()?.contains(document.activeElement)));
}

export function toggleSidebar() {
  if (narrow.matches) return;
  const hide = !isHidden(); // while peeking it is hidden, so this docks it
  const focusWasInside = Boolean(panel()?.contains(document.activeElement));
  if (hide) root.dataset.sidebar = "hidden";
  else delete root.dataset.sidebar;
  remember("localStorage", "codetrail.sidebar", hide ? "hidden" : null);
  closePeek();
  syncToggle();
  if (hide && focusWasInside) document.querySelector("[data-sidebar-toggle]")?.focus();
}

function setUpPeek() {
  const strip = edge();
  const sidebar = panel();
  if (!strip || !sidebar) return;
  // The card slides in under the pointer resting on the edge: moving between the two isn't leaving.
  const inside = (node) => node instanceof Node && (node === strip || sidebar.contains(node));
  strip.addEventListener("pointerenter", (event) => {
    clearTimeout(peekTimer);
    // Only a pointer arriving from the page peeks: the card sliding away under it after Esc mustn't reopen it.
    if (!isPeeking() && !inside(event.relatedTarget)) peekTimer = setTimeout(() => openPeek(false), PEEK_DELAY);
  });
  strip.addEventListener("pointerleave", (event) => {
    if (!isPeeking()) clearTimeout(peekTimer);
    else if (!inside(event.relatedTarget)) closeSoon();
  });
  strip.addEventListener("click", () => openPeek(true)); // open, or already open: either way, into the sidebar
  strip.addEventListener("focusout", (event) => {
    const next = event.relatedTarget;
    if (isPeeking() && next instanceof Node && !sidebar.contains(next)) closePeek(); // tabbed on into the page
  });
  sidebar.addEventListener("pointerenter", () => clearTimeout(peekTimer));
  sidebar.addEventListener("pointerleave", (event) => isPeeking() && !inside(event.relatedTarget) && closeSoon());
  narrow.addEventListener("change", () => closePeek()); // the drawer takes over below 900 px
  sidebar.addEventListener("focusout", (event) => {
    const next = event.relatedTarget;
    if (isPeeking() && next instanceof Node && !sidebar.contains(next) && next !== strip) closePeek();
  });
  sidebar.addEventListener("click", (event) => {
    if (isPeeking() && event.target instanceof Element && event.target.closest("a[href]")) closePeek();
  });
}

function folded() {
  return (root.dataset.folded ?? "").split(" ").filter((name) => SECTIONS.includes(name));
}

function setUpFolding() {
  for (const button of document.querySelectorAll("[data-fold]")) {
    const name = button.dataset.fold;
    button.setAttribute("aria-expanded", String(!folded().includes(name)));
    button.addEventListener("click", () => {
      const now = folded();
      const next = now.includes(name) ? now.filter((other) => other !== name) : [...now, name];
      if (next.length) root.dataset.folded = next.join(" ");
      else delete root.dataset.folded;
      remember("localStorage", "codetrail.sidebar-folded", next.length ? next.join(" ") : null);
      button.setAttribute("aria-expanded", String(!next.includes(name)));
    });
  }
}

const pageHref = (id) => `/pages/${id.split("/").map(encodeURIComponent).join("/")}`;

function setUpPins() {
  const sidebar = panel();
  const list = sidebar?.querySelector("[data-pins]");
  const template = sidebar?.querySelector("[data-pin-template]");
  if (!sidebar || !list || !template) return;
  const limit = Number(sidebar.dataset.maxPins) || 1;
  // The drawn tiles are the list: a pinned page the guide dropped isn't drawn, so it isn't sent back.
  const pinned = () => [...list.querySelectorAll("[data-unpin]")].map((button) => button.dataset.unpin);

  function sync(ids) {
    list.hidden = ids.length === 0;
    for (const button of sidebar.querySelectorAll("[data-pin]")) {
      const on = ids.includes(button.dataset.pin);
      button.setAttribute("aria-pressed", String(on));
      button.disabled = ids.length >= limit && !on;
      if (button.disabled) button.title = sidebar.dataset.labelFull;
      else button.removeAttribute("title");
    }
  }

  function tile(button) {
    const id = button.dataset.pin;
    const title = button.closest("li")?.querySelector(".label")?.textContent.trim() || id;
    const item = template.content.firstElementChild.cloneNode(true);
    const link = item.querySelector("a");
    const href = pageHref(id);
    link.href = href;
    link.title = title;
    link.setAttribute("aria-label", title);
    link.textContent = button.dataset.initials;
    // Compared as written: the template's link has no base to resolve against until it joins the page.
    if (window.location.pathname === href) link.setAttribute("aria-current", "page");
    const remove = item.querySelector("button");
    remove.dataset.unpin = id;
    remove.setAttribute("aria-label", sidebar.dataset.labelUnpin.replace("{title}", () => title)); // no $ patterns
    return item;
  }

  async function save(ids) {
    try {
      return (await post("/settings/pins", { pages: ids })).ok;
    } catch {
      return false; // the sidebar stays as it was
    }
  }

  let saving = false; // one change at a time, so a quick second click can't work from a stale list
  sidebar.addEventListener("click", async (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const pin = target?.closest("[data-pin]");
    const unpin = target?.closest("[data-unpin]");
    if ((!pin && !unpin) || saving) return;
    saving = true;
    try {
      const id = pin ? pin.dataset.pin : unpin.dataset.unpin;
      const ids = pinned();
      const next = ids.includes(id) ? ids.filter((other) => other !== id) : [...ids, id];
      if (!(await save(next))) return;
      if (next.includes(id)) list.append(tile(pin));
      else list.querySelector(`[data-unpin="${CSS.escape(id)}"]`)?.closest("li")?.remove();
      sync(next);
      if (unpin) sidebar.querySelector("a[href]")?.focus();
    } finally {
      saving = false;
    }
  });
  for (const button of sidebar.querySelectorAll("[data-pin], [data-unpin]")) button.hidden = false;
}

export function setUpSidebar() {
  document.querySelector("[data-sidebar-toggle]")?.addEventListener("click", toggleSidebar);
  syncToggle();
  setUpPeek();
  setUpFolding();
  setUpPins();
}
