// Diagrams drawn from facts, rendered by the vendored Mermaid in strict mode, in the page's light or dark theme.
import { isDark } from "./theme.js";

let counter = 0;

async function draw(figure) {
  const source = figure.dataset.source;
  const target = figure.querySelector("[data-diagram-target]");
  if (!window.mermaid || !source || !target) return;
  window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: isDark() ? "dark" : "neutral" });
  try {
    const { svg } = await window.mermaid.render(`diagram-${counter++}`, source);
    target.innerHTML = svg; // Mermaid's own output, sanitized in strict mode
    target.hidden = false;
    figure.querySelector("pre.diagram")?.setAttribute("hidden", "");
  } catch {
    figure.classList.add("diagram-failed");
  }
}

export function setUpDiagrams() {
  const figures = [...document.querySelectorAll("figure.diagram-block")];
  for (const figure of figures) {
    const block = figure.querySelector("pre.diagram");
    if (!block) continue;
    figure.dataset.source = block.textContent;
    const target = document.createElement("div");
    target.className = "diagram-svg";
    target.dataset.diagramTarget = "";
    target.hidden = true;
    block.after(target);
    figure.querySelector("[data-diagram-enlarge]")?.addEventListener("click", (event) => {
      const enlarged = figure.classList.toggle("enlarged");
      event.currentTarget.setAttribute("aria-pressed", String(enlarged));
    });
  }
  const drawAll = async () => {
    for (const figure of figures) await draw(figure);
  };
  drawAll();
  document.addEventListener("codetrail:theme", drawAll);
}
