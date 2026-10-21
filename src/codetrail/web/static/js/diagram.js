// Diagrams drawn from facts, written in D2 and drawn by D2's WebAssembly build in its own worker (ADR 0016), in the
// page's light or dark theme. Pages draw theirs on load; the Ask panel draws an answer's when it arrives.
import { renderD2 } from "./d2.js";
import { isDark } from "./theme.js";

const figures = [];

// D2's SVG is shown as an image, where it can't run script or follow links, whatever the worker sent.
async function draw(figure) {
  const source = figure.dataset.source;
  const target = figure.querySelector("[data-diagram-target]");
  if (!source || !target) return;
  try {
    const image = document.createElement("img");
    image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(await renderD2(source, isDark()))}`;
    image.alt = figure.dataset.alt ?? "";
    target.replaceChildren(image);
    target.hidden = false;
    figure.querySelector("pre.diagram")?.setAttribute("hidden", "");
  } catch {
    figure.classList.add("diagram-failed");
  }
}

function prepare(figure) {
  const block = figure.querySelector("pre.diagram");
  if (!block || figure.dataset.source !== undefined) return false;
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
  figures.push(figure);
  return true;
}

export async function drawDiagramsIn(root) {
  const found = [...root.querySelectorAll("figure.diagram-block")].filter(prepare);
  for (const figure of found) await draw(figure);
}

export function setUpDiagrams() {
  drawDiagramsIn(document);
  document.addEventListener("codetrail:theme", async () => {
    for (const figure of figures.filter((each) => each.isConnected)) await draw(figure);
  });
}
