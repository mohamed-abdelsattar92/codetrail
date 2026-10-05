// Keyboard shortcuts (design section 16.4). None of them spends anything: Ask and Update only open the panel or the
// estimate dialog. Single keys are ignored while typing and whenever Command, Control or Alt is held.
import { closeAsk, isAskOpen, openAsk } from "./ask.js";
import { toggleRead } from "./learning.js";
import { openPalette } from "./palette.js";
import { startUpdate } from "./update.js";

const PLACES = { h: "/", p: "/progress", y: "/system", s: "/answers", d: "/digests", r: "/decisions" };
let awaitingPlace = null;

function typing(element) {
  return element instanceof Element && Boolean(element.closest("input, textarea, select, [contenteditable]"));
}

function follow(selector) {
  const link = document.querySelector(selector);
  if (link instanceof HTMLAnchorElement) window.location.href = link.href;
}

export function closeMenu() {
  document.body.classList.remove("menu-open");
  document.querySelector("[data-menu]")?.setAttribute("aria-expanded", "false");
}

export function setUpShortcuts() {
  const help = document.querySelector("[data-shortcuts]");
  for (const button of document.querySelectorAll("[data-shortcuts-open]")) {
    button.addEventListener("click", () => help?.showModal());
  }
  help?.querySelector("[data-shortcuts-close]")?.addEventListener("click", () => help.close());
  const menu = document.querySelector("[data-menu]");
  menu?.addEventListener("click", () => {
    const open = document.body.classList.toggle("menu-open");
    menu.setAttribute("aria-expanded", String(open));
  });

  document.addEventListener("keydown", (event) => {
    if (event.isComposing || event.defaultPrevented) return;
    const key = event.key;
    if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && key.toLowerCase() === "k") {
      event.preventDefault();
      openPalette();
      return;
    }
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (document.querySelector("dialog[open]")) return; // an open dialog handles its own keys
    if (key === "Escape") {
      if (document.body.classList.contains("menu-open")) closeMenu();
      else if (isAskOpen()) closeAsk();
      return;
    }
    if (typing(event.target)) return;
    if (awaitingPlace) {
      clearTimeout(awaitingPlace);
      awaitingPlace = null;
      const place = PLACES[key.toLowerCase()];
      if (place) {
        event.preventDefault();
        window.location.href = place;
      }
      return;
    }
    const actions = {
      "/": () => openPalette(),
      a: () => openAsk(),
      g: () => {
        awaitingPlace = setTimeout(() => {
          awaitingPlace = null;
        }, 1000);
      },
      "[": () => follow("[data-pager-previous]"),
      "]": () => follow("[data-pager-next]"),
      m: () => toggleRead(),
      u: () => startUpdate(),
      "?": () => help?.showModal(),
    };
    const action = actions[key.length === 1 && key !== "?" ? key.toLowerCase() : key];
    if (action) {
      event.preventDefault();
      action();
    }
  });
}
