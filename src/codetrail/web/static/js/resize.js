// Resizing the sidebar and the Ask panel (design section 16.1): drag a handle, or focus it and use the arrow keys,
// Home and End; double-click resets. Widths stay within each panel's limits and never squeeze the reading column
// below MIN_READING; they are remembered in localStorage when the browser allows it.
import { recall, remember } from "./api.js";

const MIN_READING = 480;
const STEP = 16;
const NARROW = 900; // below this the sidebar is a menu and the panel is full width, so nothing resizes
const PANELS = {
  sidebar: { variable: "--sidebar-width", key: "codetrail.sidebar-width", fallback: 272, min: 200, max: 420, share: 0.3 },
  panel: { variable: "--panel-width", key: "codetrail.panel-width", fallback: 420, min: 320, max: 720, share: 0.45 },
};
const root = document.documentElement;

function rightToLeft() {
  return getComputedStyle(root).direction === "rtl";
}

function current(name) {
  return parseFloat(getComputedStyle(root).getPropertyValue(PANELS[name].variable)) || PANELS[name].fallback;
}

function limits(name) {
  const panel = PANELS[name];
  return { min: panel.min, max: Math.max(panel.min, Math.min(panel.max, Math.floor(window.innerWidth * panel.share))) };
}

function squeezesReading(name) {
  // The panel takes a column of the page only on wide screens; narrower, it floats over the page.
  return name === "sidebar" || (document.body.classList.contains("ask-open") && window.innerWidth > 1200);
}

function readingWidth() {
  return document.querySelector("#content")?.getBoundingClientRect().width ?? Infinity;
}

function set(name, width) {
  root.style.setProperty(PANELS[name].variable, `${width}px`);
}

function apply(name, wanted) {
  const { min, max } = limits(name);
  let width = Math.round(Math.min(max, Math.max(min, wanted)));
  set(name, width);
  const short = MIN_READING - readingWidth();
  if (short > 0 && squeezesReading(name)) {
    width = Math.max(min, width - Math.ceil(short));
    set(name, width);
  }
  const handle = document.querySelector(`[data-resize="${name}"]`);
  handle?.setAttribute("aria-valuemin", String(min));
  handle?.setAttribute("aria-valuemax", String(max));
  handle?.setAttribute("aria-valuenow", String(width));
  return width;
}

function saved(name) {
  const value = Number(recall("localStorage", PANELS[name].key));
  return Number.isFinite(value) && value > 0 ? value : PANELS[name].fallback;
}

function save(name) {
  remember("localStorage", PANELS[name].key, String(Math.round(current(name))));
}

function fit() {
  if (window.innerWidth <= NARROW) return;
  apply("sidebar", saved("sidebar"));
  apply("panel", saved("panel"));
}

function widthAt(name, x) {
  const fromStart = rightToLeft() ? window.innerWidth - x : x;
  return name === "sidebar" ? fromStart : window.innerWidth - fromStart;
}

function setUpHandle(handle) {
  const name = handle.dataset.resize;
  if (!PANELS[name]) return;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");
  });
  handle.addEventListener("pointermove", (event) => {
    if (handle.hasPointerCapture(event.pointerId)) apply(name, widthAt(name, event.clientX));
  });
  const stop = (event) => {
    if (!handle.hasPointerCapture(event.pointerId)) return;
    handle.releasePointerCapture(event.pointerId);
    handle.classList.remove("dragging");
    document.body.classList.remove("resizing");
    save(name);
  };
  handle.addEventListener("pointerup", stop);
  handle.addEventListener("pointercancel", stop);
  handle.addEventListener("dblclick", () => {
    remember("localStorage", PANELS[name].key, null);
    apply(name, PANELS[name].fallback);
  });
  handle.addEventListener("keydown", (event) => {
    const { min, max } = limits(name);
    // The sidebar grows toward the page's end, the panel toward its start; right-to-left mirrors both.
    const growKey = (name === "sidebar") !== rightToLeft() ? "ArrowRight" : "ArrowLeft";
    const shrinkKey = growKey === "ArrowRight" ? "ArrowLeft" : "ArrowRight";
    const next = { [growKey]: current(name) + STEP, [shrinkKey]: current(name) - STEP, Home: min, End: max }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    apply(name, next);
    save(name);
  });
}

export function setUpResize() {
  for (const handle of document.querySelectorAll("[data-resize]")) setUpHandle(handle);
  fit();
  let frame = 0;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(fit);
  });
  // Opening or closing the Ask panel changes how much room the page has.
  new MutationObserver(fit).observe(document.body, { attributes: true, attributeFilter: ["class"] });
}
