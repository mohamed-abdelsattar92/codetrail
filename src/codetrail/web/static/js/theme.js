// The theme switch: system, light or dark, remembered in localStorage when the browser allows it (design 16.2).
import { recall, remember } from "./api.js";

const THEMES = ["system", "light", "dark"];
const button = document.querySelector("[data-theme-toggle]");

function current() {
  const stored = recall("localStorage", "codetrail.theme");
  return THEMES.includes(stored) ? stored : "system";
}

function apply(theme) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  if (button) {
    const label = button.dataset[`label${theme[0].toUpperCase()}${theme.slice(1)}`] ?? theme;
    button.setAttribute("aria-label", label);
    button.title = label;
    button.dataset.theme = theme;
  }
  document.dispatchEvent(new CustomEvent("codetrail:theme"));
}

export function isDark() {
  const chosen = document.documentElement.getAttribute("data-theme");
  if (chosen) return chosen === "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(current()) + 1) % THEMES.length];
  remember("localStorage", "codetrail.theme", next === "system" ? null : next);
  apply(next);
}

export function setUpTheme() {
  apply(current());
  button?.addEventListener("click", cycleTheme);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (current() === "system") apply("system");
  });
}
