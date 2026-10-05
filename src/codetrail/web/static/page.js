// Codetrail's page script: renders diagrams and sends the page's few writes with the session token.
// It is the only script the page runs besides the vendored Mermaid; the Content-Security-Policy allows no inline code.
"use strict";

const token = document.querySelector('meta[name="codetrail-token"]')?.content ?? "";

async function post(url, data) {
  return fetch(url, {
    method: "POST",
    headers: { "X-Codetrail-Token": token, "Content-Type": "application/json" },
    body: JSON.stringify(data),
    credentials: "same-origin",
  });
}

async function renderDiagrams() {
  if (!window.mermaid) return;
  window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });
  const blocks = document.querySelectorAll("pre.diagram");
  for (const [index, block] of blocks.entries()) {
    try {
      const { svg } = await window.mermaid.render(`diagram-${index}`, block.textContent);
      const figure = document.createElement("div");
      figure.className = "diagram-svg";
      figure.innerHTML = svg; // Mermaid's own output, sanitised in strict mode
      block.replaceWith(figure);
    } catch (error) {
      block.classList.add("diagram-failed");
    }
  }
}

function watchLanguage() {
  const form = document.querySelector("[data-language-form]");
  if (!form) return;
  form.addEventListener("change", async () => {
    const response = await post("/settings/language", { language: form.elements.language.value });
    if (response.ok) window.location.reload();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  watchLanguage();
  renderDiagrams();
});
