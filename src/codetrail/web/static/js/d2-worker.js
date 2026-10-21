// D2's WebAssembly build, in a worker of its own (ADR 0016). Its dagre layout evaluates JavaScript from strings, so
// this file is served with its own policy: eval is allowed here, but there is no DOM and no network but d2.wasm.
// It gets D2 text Codetrail wrote from facts and answers with an SVG, which the page shows only as an image.
// Diagrams are laid out by ELK (ADR 0018): D2 calls the elkLayoutSync that elk.js defines, which needs an ELK
// instance to exist first. D2.js also runs a setup.js, which isn't loaded here: it would replace setTimeout.
importScripts("/static/vendor/d2/wasm_exec.js", "/static/vendor/d2/elk.js");
new ELK();

const ready = (async () => {
  const go = new Go();
  const { instance } = await WebAssembly.instantiateStreaming(fetch("/static/vendor/d2/d2.wasm"), go.importObject);
  go.run(instance); // D2's main keeps running and sets self.d2
  return self.d2;
})();

function data(response) {
  const parsed = JSON.parse(response);
  if (parsed.error) throw new Error(parsed.error.message);
  return parsed.data;
}

self.onmessage = async ({ data: { id, source, dark } }) => {
  try {
    const d2 = await ready;
    const compiled = data(await d2.compile(JSON.stringify({ fs: { index: source }, options: { layout: "elk" } })));
    // At its natural size (scale 1): the page's CSS shrinks a wide diagram to fit, and a small one isn't blown up.
    const options = { ...compiled.renderOptions, themeID: dark ? 200 : 0, pad: 16, scale: 1, noXMLTag: true };
    const encoded = data(await d2.render(JSON.stringify({ diagram: compiled.diagram, options })));
    const svg = new TextDecoder().decode(Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0)));
    self.postMessage({ id, svg });
  } catch (error) {
    self.postMessage({ id, error: String(error?.message ?? error) });
  }
};
