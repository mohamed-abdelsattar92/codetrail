// Draws D2 text as an SVG in d2-worker.js, whose own policy allows the eval D2's layout needs; the page's never does
// (ADR 0016). One worker serves every diagram on the page.
let worker;
let next = 0;
const waiting = new Map();

function start() {
  worker = new Worker("/static/js/d2-worker.js");
  worker.onmessage = ({ data }) => {
    const request = waiting.get(data.id);
    waiting.delete(data.id);
    if (data.error === undefined) request?.resolve(data.svg);
    else request?.reject(new Error(data.error));
  };
  worker.onerror = () => {
    for (const request of waiting.values()) request.reject(new Error("D2's worker failed"));
    waiting.clear();
  };
}

export function renderD2(source, dark) {
  if (!worker) start();
  return new Promise((resolve, reject) => {
    const id = next++;
    waiting.set(id, { resolve, reject });
    worker.postMessage({ id, source, dark });
  });
}
