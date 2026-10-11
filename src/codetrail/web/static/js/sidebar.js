// The sidebar (design section 22): hiding and showing it, peeking at it while hidden, folding its sections and
// pinning pages. Hidden and folded are remembered in this browser; pins are kept by Codetrail. Titles are only ever
// moved with textContent and attributes.
import { remember } from "./api.js";

const root = document.documentElement;
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

export function toggleSidebar() {
  if (narrow.matches) return;
  const hide = !isHidden(); // while peeking it is hidden, so this docks it
  if (hide) root.dataset.sidebar = "hidden";
  else delete root.dataset.sidebar;
  remember("localStorage", "codetrail.sidebar", hide ? "hidden" : null);
  closePeek();
  syncToggle();
}

function setUpPeek() {
  const strip = edge();
  const sidebar = panel();
  if (!strip || !sidebar) return;
  strip.addEventListener("pointerenter", () => {
    clearTimeout(peekTimer);
    peekTimer = setTimeout(() => openPeek(false), PEEK_DELAY);
  });
  strip.addEventListener("pointerleave", () => (isPeeking() ? closeSoon() : clearTimeout(peekTimer)));
  strip.addEventListener("click", () => (isPeeking() ? closePeek(true) : openPeek(true)));
  sidebar.addEventListener("pointerenter", () => clearTimeout(peekTimer));
  sidebar.addEventListener("pointerleave", () => isPeeking() && closeSoon());
  sidebar.addEventListener("focusout", (event) => {
    const next = event.relatedTarget;
    if (isPeeking() && next instanceof Node && !sidebar.contains(next) && next !== strip) closePeek();
  });
  sidebar.addEventListener("click", (event) => {
    if (isPeeking() && event.target instanceof Element && event.target.closest("a[href]")) closePeek();
  });
}

export function setUpSidebar() {
  document.querySelector("[data-sidebar-toggle]")?.addEventListener("click", toggleSidebar);
  syncToggle();
  setUpPeek();
}
