// Codetrail's page script. Each module wires one part of the page; the security policy allows no inline code,
// so this file, the theme's first-paint script and the vendored Mermaid are all the page runs.
import { post } from "./api.js";
import { setUpAsk } from "./ask.js";
import { setUpDiagrams } from "./diagram.js";
import { setUpLearning } from "./learning.js";
import { setUpOutline } from "./outline.js";
import { setUpPalette } from "./palette.js";
import { setUpResize } from "./resize.js";
import { setUpShortcuts } from "./shortcuts.js";
import { setUpTheme } from "./theme.js";
import { setUpUpdate } from "./update.js";

function setUpLanguage() {
  const form = document.querySelector("[data-language-form]");
  form?.addEventListener("change", async () => {
    const response = await post("/settings/language", { language: form.elements.language.value });
    if (response.ok) window.location.reload();
  });
}

setUpTheme();
setUpLanguage();
setUpLearning();
setUpAsk();
setUpPalette();
setUpUpdate();
setUpShortcuts();
setUpOutline();
setUpResize();
setUpDiagrams();
document.documentElement.dataset.ready = "true"; // every handler is in place
