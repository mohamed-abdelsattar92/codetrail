// The command palette (design section 16.3): search the guide and run a few actions, all from the keyboard.
// Results are drawn with textContent and <mark> elements only; nothing from the server is parsed as markup.
import { getJSON } from "./api.js";
import { openAsk } from "./ask.js";
import { cycleTheme } from "./theme.js";
import { startUpdate } from "./update.js";

const dialog = document.querySelector("[data-palette]");
const input = dialog?.querySelector("[data-palette-input]");
const list = dialog?.querySelector("[data-palette-list]");
const status = dialog?.querySelector("[data-palette-status]");
const GROUPS = [
  ["pages", ["area", "concept", "path", "digest"]],
  ["answers", ["answer"]],
  ["decisions", ["decision"]],
  ["facts", ["fact"]],
];
let options = [];
let active = -1;
let pending = null;
let timer = null;

function label(name, query = "") {
  return (dialog.dataset[name] ?? "").replace("{query}", query);
}

function isLocalLink(link) {
  return typeof link === "string" && link.startsWith("/") && !link.startsWith("//");
}

function heading(text) {
  const item = document.createElement("li");
  item.className = "palette-group";
  item.setAttribute("role", "presentation");
  item.textContent = text;
  list.append(item);
}

function option(title, detail, run, kind) {
  const item = document.createElement("li");
  item.className = "palette-option";
  item.id = `palette-option-${options.length}`;
  item.setAttribute("role", "option");
  item.setAttribute("aria-selected", "false");
  const line = document.createElement("span");
  line.className = "title";
  line.textContent = title;
  if (kind) {
    line.lang = "en"; // a result from the guide, which is English; its kind is in the reader's language
    line.dir = "ltr";
    const badge = document.createElement("span");
    badge.className = "kind";
    badge.lang = document.documentElement.lang;
    badge.dir = document.documentElement.dir;
    badge.textContent = label(`kind${kind[0].toUpperCase()}${kind.slice(1)}`) || kind;
    line.append(" ", badge);
  }
  item.append(line);
  if (detail) item.append(detail);
  item.addEventListener("mousedown", (event) => event.preventDefault()); // keep focus in the input
  item.addEventListener("click", () => activate(options.indexOf(entry)));
  const entry = { item, run };
  options.push(entry);
  list.append(item);
}

function snippet(segments) {
  if (!Array.isArray(segments) || !segments.length) return null;
  const line = document.createElement("span");
  line.className = "snippet";
  line.lang = "en";
  line.dir = "ltr";
  for (const [text, matched] of segments) {
    if (matched) {
      const mark = document.createElement("mark");
      mark.textContent = String(text);
      line.append(mark);
    } else {
      line.append(String(text));
    }
  }
  return line;
}

function select(index) {
  if (!options.length) {
    active = -1;
    input.removeAttribute("aria-activedescendant");
    return;
  }
  active = (index + options.length) % options.length;
  options.forEach((entry, position) => entry.item.setAttribute("aria-selected", String(position === active)));
  const current = options[active].item;
  input.setAttribute("aria-activedescendant", current.id);
  current.scrollIntoView({ block: "nearest" });
}

function activate(index) {
  const entry = options[index];
  if (!entry) return;
  dialog.close();
  entry.run();
}

function actions(query) {
  heading(label("groupActions"));
  if (query) {
    option(label("actionAsk", query), null, () => openAsk(query));
    option(label("actionAll", query), null, () => {
      window.location.href = `/search?q=${encodeURIComponent(query)}`;
    });
  }
  option(label("actionUpdate"), null, () => startUpdate());
  option(label("actionProgress"), null, () => {
    window.location.href = "/progress";
  });
  option(label("actionTheme"), null, () => cycleTheme());
}

function draw(query, results, message) {
  list.replaceChildren();
  options = [];
  for (const [group, kinds] of GROUPS) {
    const found = results.filter((result) => kinds.includes(result.kind) && isLocalLink(result.link));
    if (!found.length) continue;
    heading(label(`group${group[0].toUpperCase()}${group.slice(1)}`));
    for (const result of found) {
      option(String(result.title), snippet(result.snippet), () => {
        window.location.href = result.link;
      }, result.kind);
    }
  }
  actions(query);
  status.textContent = message;
  input.setAttribute("aria-expanded", "true");
  select(0);
}

async function search() {
  const query = input.value.trim();
  pending?.abort();
  if (!query) {
    draw("", [], "");
    return;
  }
  pending = new AbortController();
  status.textContent = label("labelSearching");
  try {
    const { results, available } = await getJSON(`/search/results?q=${encodeURIComponent(query)}`, {
      signal: pending.signal,
    });
    let message = "";
    if (!available) message = label("labelUnavailable");
    else if (!results.length) message = label("labelEmpty", query);
    draw(query, results, message);
  } catch (error) {
    if (error.name !== "AbortError") draw(query, [], label("labelUnavailable"));
  }
}

export function openPalette(initial = "") {
  if (!dialog || dialog.open) return;
  input.value = initial;
  dialog.showModal();
  input.focus();
  search();
}

export function setUpPalette() {
  if (!dialog) return;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(search, 120);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      select(active + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      select(active - 1);
    } else if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      activate(active);
    }
  });
  dialog.addEventListener("close", () => {
    pending?.abort();
    input.setAttribute("aria-expanded", "false");
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close(); // a click on the backdrop
  });
  const form = document.querySelector("[data-search-form]");
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    openPalette(form.elements.q.value);
  });
  form?.elements.q.addEventListener("focus", (event) => {
    event.target.blur();
    openPalette(event.target.value);
  });
}
