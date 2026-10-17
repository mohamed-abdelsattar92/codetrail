// The Repository page's charts (design section 20): the timeline, the day-by-day calendar, the change heat map and the
// area-by-month grid's read-out, drawn from the page's JSON block. Text from the repository (paths, tag names and
// notes, package names) is only ever set with textContent or setAttribute, never parsed as markup.
const SVG = "http://www.w3.org/2000/svg";
const DAY = 86400000;
const PLOT_HEIGHT = 180;
const RANGES = { all: Infinity, "2y": 730, "1y": 365, "90d": 90 }; // days each range shows
const UNIT_DAYS = { day: 1, week: 7, month: 30.44 };

// Bars per day up to 120 days, per week up to 400, per month beyond: enough bars to read, few enough to tell apart.
function unitFor(span) {
  return span <= 120 ? "day" : span <= 400 ? "week" : "month";
}

function svg(tag, attributes = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const dayOf = (iso) => Date.parse(`${iso}T00:00:00Z`);
const isoOf = (time) => new Date(time).toISOString().slice(0, 10);
const sourceHref = (path) => `/source/${path.split("/").map(encodeURIComponent).join("/")}`;

// The highest of many numbers, without spreading them into arguments (a long history has too many).
const highestOf = (numbers, floor = 1) => numbers.reduce((highest, value) => Math.max(highest, value), floor);

// The five-step ramp of the heat map and calendar: 0 for nothing, then quarters of the highest (as ramp_step does).
function rampStep(count, highest) {
  if (count <= 0 || highest <= 0) return 0;
  return Math.min(4, 1 + Math.floor((count * 4 - 1) / highest));
}

class Labels {
  constructor(data) {
    this.labels = data.labels;
    this.rules = new Intl.PluralRules(data.lang);
    this.numbers = new Intl.NumberFormat(`${data.lang}-u-nu-latn`);
  }

  number(value) {
    return this.numbers.format(value);
  }

  fill(template, values) {
    return template.replace(/\{(\w+)\}/g, (match, key) => (key in values ? String(values[key]) : match));
  }

  // The catalog's phrase for a sample number in the same plural category as the value.
  count(key, value, extra = {}) {
    const forms = this.labels[key];
    const category = this.rules.select(value);
    const samples = Object.keys(forms).map(Number).sort((a, b) => a - b);
    const sample = samples.find((number) => this.rules.select(number) === category) ?? samples[samples.length - 1];
    return this.fill(forms[String(sample)], { count: this.number(value), ...extra });
  }

  text(key, values = {}) {
    return this.fill(this.labels[key], values);
  }

  month(time) {
    const date = new Date(time);
    return `${this.labels.months[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
  }
}

// Commits per day, week (from Monday) and month, each period with its start time.
function periods(days) {
  const start = dayOf(days.start);
  const daily = days.counts.map((count, index) => ({ time: start + index * DAY, count }));
  const group = (keyOf, startOf) => {
    const grouped = [];
    for (const day of daily) {
      const key = keyOf(day.time);
      const last = grouped[grouped.length - 1];
      if (last && last.key === key) last.count += day.count;
      else grouped.push({ key, time: startOf(day.time), count: day.count });
    }
    return grouped;
  };
  const monday = (time) => time - ((new Date(time).getUTCDay() + 6) % 7) * DAY;
  const monthStart = (time) => { const date = new Date(time); return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1); };
  return { day: daily, week: group(monday, monday), month: group(monthStart, monthStart) };
}

function periodEnd(unit, time) {
  if (unit === "day") return time + DAY;
  if (unit === "week") return time + 7 * DAY;
  const date = new Date(time);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1);
}

function setUpTimeline(data, labels) {
  const container = document.querySelector('[data-chart="timeline"]');
  if (!container || !data.days) return;
  const all = periods(data.days);
  const picker = document.querySelector("[data-range-picker]");
  const pan = document.querySelector("[data-pan]");
  const windowText = document.querySelector("[data-window-text]");
  const state = { range: "all", offset: 0, active: -1, release: data.releases?.length ? data.releases.length - 1 : -1 };

  function view() {
    const span = Math.min(RANGES[state.range], all.day.length);
    const unit = unitFor(span);
    const series = all[unit];
    const shown = state.range === "all" ? series.length : Math.min(Math.ceil(span / UNIT_DAYS[unit]), series.length);
    const step = Math.max(1, Math.floor(shown / 2));
    const maxOffset = Math.max(0, Math.ceil((series.length - shown) / step));
    state.offset = Math.min(state.offset, maxOffset);
    // The earliest window keeps its full width: it starts at the first period rather than running short.
    const start = Math.max(0, series.length - state.offset * step - shown);
    const slice = series.slice(start, start + shown);
    const first = Math.max(slice[0].time, dayOf(data.days.start)); // a week or month may begin before the first commit
    return { unit, slice, maxOffset, from: first, to: periodEnd(unit, slice[slice.length - 1].time), barsFrom: slice[0].time };
  }

  function periodLabel(unit, time) {
    if (unit === "month") return labels.month(time);
    if (unit === "week") return labels.text("weekOf", { date: isoOf(time) });
    return isoOf(time);
  }

  function draw() {
    const shown = view();
    const { unit, slice, from, to, barsFrom } = shown;
    const position = (time) => ((time - barsFrom) / (to - barsFrom)) * 100;
    const highest = highestOf(slice.map((period) => period.count));
    const total = slice.reduce((sum, period) => sum + period.count, 0);
    container.replaceChildren();
    const shownEnd = Math.min(to - DAY, dayOf(data.today));
    windowText.textContent = `${labels.text("window", { start: isoOf(from), end: isoOf(shownEnd) })} · ${labels.count("commits", total)}`;

    const unitKey = { month: "perMonth", week: "perWeek", day: "perDay" }[unit];
    container.append(element("span", "timeline-label", labels.text(unitKey)));
    const plot = element("div", "timeline-plot");
    plot.tabIndex = 0;
    plot.setAttribute("role", "group");
    plot.setAttribute("aria-label", `${labels.text(unitKey)}. ${labels.text("plotHelp")}`);
    const chart = svg("svg", { viewBox: `0 0 1000 ${PLOT_HEIGHT}`, preserveAspectRatio: "none", "aria-hidden": "true" });
    for (const y of [1, PLOT_HEIGHT / 2]) chart.append(svg("line", { class: "gridline", x1: 0, x2: 1000, y1: y, y2: y }));
    const width = 1000 / slice.length;
    const gap = slice.length > 120 ? 0.5 : Math.min(2, width * 0.2);
    const bars = slice.map((period, index) => {
      const height = period.count ? Math.max(2, (period.count / highest) * (PLOT_HEIGHT - 10)) : 0;
      const bar = svg("rect", { class: "timeline-bar", x: index * width + gap / 2, y: PLOT_HEIGHT - height, width: Math.max(0.5, width - gap), height, rx: Math.min(3, width / 4) });
      chart.append(bar);
      return bar;
    });
    plot.append(chart);
    const tip = element("div", "timeline-tip");
    tip.hidden = true;
    const tipTitle = element("strong");
    const tipBody = element("span");
    tip.append(tipTitle, tipBody);
    plot.append(tip);

    const activate = (index) => {
      bars[state.active]?.classList.remove("is-active");
      state.active = index;
      if (index < 0) { tip.hidden = true; return; }
      const period = slice[index];
      bars[index].classList.add("is-active");
      tipTitle.textContent = periodLabel(unit, period.time);
      tipBody.textContent = labels.count("commits", period.count);
      tip.hidden = false;
      tip.style.insetInlineStart = `${Math.min(78, Math.max(0, ((index + 0.5) / slice.length) * 100 - 10))}%`;
    };
    plot.addEventListener("mousemove", (event) => {
      const box = plot.getBoundingClientRect();
      activate(Math.min(slice.length - 1, Math.max(0, Math.floor(((event.clientX - box.left) / box.width) * slice.length))));
    });
    plot.addEventListener("mouseleave", () => activate(-1));
    plot.addEventListener("blur", () => activate(-1));
    plot.addEventListener("keydown", (event) => {
      const moves = { ArrowRight: 1, ArrowLeft: -1, Home: -Infinity, End: Infinity };
      if (!(event.key in moves)) return;
      event.preventDefault();
      const next = state.active < 0 ? slice.length - 1 : state.active + moves[event.key];
      activate(Math.min(slice.length - 1, Math.max(0, Number.isFinite(next) ? next : moves[event.key] < 0 ? 0 : slice.length - 1)));
    });
    container.append(plot);

    container.append(element("span"));
    const axis = element("div", "timeline-axis");
    axis.setAttribute("aria-hidden", "true");
    slice.forEach((period, index) => {
      const date = new Date(period.time);
      const previous = index ? new Date(slice[index - 1].time) : null;
      let text = null;
      if (unit === "month" && date.getUTCMonth() === 0) text = String(date.getUTCFullYear());
      else if (unit === "month" && slice.length <= 30 && date.getUTCMonth() === 6) text = labels.labels.months[6];
      else if (unit === "week" && (!previous || previous.getUTCMonth() !== date.getUTCMonth()) && date.getUTCMonth() % 2 === 0) text = labels.labels.months[date.getUTCMonth()];
      else if (unit === "day" && date.getUTCDate() === 1) text = labels.labels.months[date.getUTCMonth()];
      if (text) {
        const tick = element("span", "", text);
        tick.style.insetInlineStart = `${(index / slice.length) * 100}%`;
        axis.append(tick);
      }
    });
    container.append(axis);

    container.append(element("span", "timeline-label", labels.text("releases")));
    const releaseLane = element("div", "timeline-lane");
    const releases = (data.releases ?? []).map((release, index) => ({ ...release, index, time: dayOf(release.date) })).filter((release) => release.time >= from && release.time < to);
    if (!releases.length) releaseLane.append(element("p", "lane-empty", labels.text("noReleases")));
    let lastLabelled = -Infinity;
    for (const release of releases) {
      // A name shows only with room for it; every diamond keeps its name for assistive technology and on hover.
      const crowded = position(release.time) - lastLabelled < 8;
      if (!crowded) lastLabelled = position(release.time);
      const mark = element("button", "release-mark lane-mark", crowded ? "" : release.name);
      mark.setAttribute("title", `${release.name}, ${release.date}`);
      mark.type = "button";
      mark.setAttribute("aria-pressed", String(release.index === state.release));
      mark.setAttribute("aria-label", `${release.name}, ${release.date}`);
      mark.style.insetInlineStart = `calc(${position(release.time)}% - 6px)`;
      mark.addEventListener("click", () => {
        // The marks stay in place, so the focus stays on the one chosen.
        state.release = release.index;
        for (const other of releaseLane.querySelectorAll(".release-mark")) other.setAttribute("aria-pressed", String(other === mark));
        showRelease();
      });
      releaseLane.append(mark);
    }
    container.append(releaseLane);

    container.append(element("span", "timeline-label", labels.text("dependencies")));
    const depLane = element("div", "timeline-lane");
    let watched = null;
    if (data.watchStart && dayOf(data.watchStart) < to) {
      watched = element("div", "lane-watch");
      watched.style.insetInlineStart = `${Math.max(0, position(dayOf(data.watchStart)))}%`;
      watched.append(element("span", "lane-watch-label", labels.text("watched", { date: data.watchStart })));
      depLane.append(watched);
    }
    const deps = data.deps.map((dep) => ({ ...dep, time: dayOf(dep.date) })).filter((dep) => dep.time >= from && dep.time < to);
    if (!deps.length && !(data.watchStart && dayOf(data.watchStart) < to)) depLane.append(element("p", "lane-empty", labels.text("noDeps")));
    for (const dep of deps) {
      const arrived = dep.sign === "+";
      const mark = element("span", `dep-mark lane-mark ${arrived ? "arrived" : "left"}`, arrived ? "+" : "−");
      const words = labels.text(arrived ? "arrived" : "left", { name: dep.name });
      mark.setAttribute("role", "img");
      mark.setAttribute("aria-label", `${words}, ${dep.date}`);
      mark.setAttribute("title", `${words}, ${dep.date}`);
      mark.style.insetInlineStart = `calc(${position(dep.time)}% - 7px)`;
      depLane.append(mark);
    }
    container.append(depLane);
    // A band too narrow for its label (Codetrail started watching late in the window) shows it just before its edge.
    if (watched) watched.classList.toggle("narrow", watched.firstElementChild.scrollWidth > watched.clientWidth);

    container.append(element("span", "timeline-label", labels.text("wholeHistory")));
    const mini = element("div", "timeline-mini");
    mini.setAttribute("aria-hidden", "true");
    const wholeUnit = unitFor(all.day.length);
    const whole = all[wholeUnit];
    const miniHighest = highestOf(whole.map((period) => period.count));
    const miniChart = svg("svg", { viewBox: `0 0 ${whole.length * 10} 34`, preserveAspectRatio: "none" });
    whole.forEach((period, index) => {
      const height = Math.max(period.count ? 2 : 0, (period.count / miniHighest) * 30);
      miniChart.append(svg("rect", { x: index * 10 + 1, y: 34 - height, width: 8, height }));
    });
    const first = whole[0].time;
    const last = periodEnd(wholeUnit, whole[whole.length - 1].time);
    const scale = (time) => ((time - first) / (last - first)) * whole.length * 10;
    miniChart.append(svg("rect", { class: "mini-window", x: Math.max(0, scale(from)), y: 1, width: Math.max(2, scale(to) - scale(from)), height: 32, rx: 3 }));
    mini.append(miniChart);
    container.append(mini);

    if (pan) {
      pan.hidden = shown.maxOffset === 0;
      pan.querySelector("[data-pan-earlier]").disabled = state.offset >= shown.maxOffset;
      pan.querySelector("[data-pan-later]").disabled = state.offset === 0;
    }
  }

  function showRelease() {
    const panel = document.querySelector("[data-release-panel]");
    const release = data.releases?.[state.release];
    if (!panel || !release) return;
    const previous = data.releases[state.release - 1];
    panel.querySelector("[data-release-name]").textContent = release.name;
    let meta = release.date;
    if (previous) {
      const start = dayOf(previous.date);
      const end = dayOf(release.date);
      const since = all.day.filter((day) => day.time > start && day.time <= end).reduce((sum, day) => sum + day.count, 0);
      meta += ` · ${labels.count("commitsSince", since, { name: previous.name })} · ${labels.count("daysAfter", Math.round((end - start) / DAY))}`;
    } else {
      meta += ` · ${labels.text("firstRelease")}`;
    }
    panel.querySelector("[data-release-meta]").textContent = meta;
    panel.querySelector("[data-release-notes]").textContent = release.notes || "—";
    panel.hidden = false;
  }

  if (picker) {
    picker.hidden = false;
    for (const button of picker.querySelectorAll("[data-range]")) {
      button.addEventListener("click", () => {
        state.range = button.dataset.range;
        state.offset = 0;
        state.active = -1;
        for (const other of picker.querySelectorAll("[data-range]")) other.setAttribute("aria-pressed", String(other === button));
        draw();
      });
    }
  }
  pan?.querySelector("[data-pan-earlier]").addEventListener("click", () => { state.offset += 1; state.active = -1; draw(); });
  pan?.querySelector("[data-pan-later]").addEventListener("click", () => { state.offset = Math.max(0, state.offset - 1); state.active = -1; draw(); });
  draw();
  showRelease();
}

function setUpCalendar(data, labels) {
  const container = document.querySelector('[data-chart="calendar"]');
  const status = document.querySelector("[data-calendar-status]");
  if (!container || !data.days) return;
  const today = dayOf(data.today);
  const start = dayOf(data.days.start);
  const lastMonday = today - ((new Date(today).getUTCDay() + 6) % 7) * DAY;
  const first = lastMonday - 52 * 7 * DAY;
  const counts = new Map(data.days.counts.map((count, index) => [start + index * DAY, count]));
  const highest = highestOf([...counts.entries()].filter(([time]) => time >= first).map(([, count]) => count));
  const cell = 14;
  const left = 30;
  const top = 16;
  const chart = svg("svg", { width: left + 53 * cell, height: top + 7 * cell, role: "img", "aria-label": labels.text("calendarHelp") });
  [0, 2, 4].forEach((weekday) => {
    const label = svg("text", { x: 0, y: top + weekday * cell + 10 });
    label.textContent = labels.labels.weekdays[weekday];
    chart.append(label);
  });
  for (let week = 0; week < 53; week++) {
    for (let weekday = 0; weekday < 7; weekday++) {
      const time = first + (week * 7 + weekday) * DAY;
      if (time > today) continue;
      const count = counts.get(time) ?? 0;
      if (weekday === 0 && new Date(time).getUTCDate() <= 7) {
        const month = svg("text", { x: left + week * cell, y: 10 });
        month.textContent = labels.labels.months[new Date(time).getUTCMonth()];
        chart.append(month);
      }
      const day = svg("rect", { class: `day heat-${rampStep(count, highest)}`, x: left + week * cell, y: top + weekday * cell, width: 11, height: 11, rx: 2 });
      const title = svg("title");
      title.textContent = `${isoOf(time)}: ${labels.count("commits", count)}`;
      day.append(title);
      day.addEventListener("mouseenter", () => { if (status) status.textContent = title.textContent; });
      chart.append(day);
    }
  }
  container.replaceChildren(chart);
}

// A squarified treemap: rectangles close to square, the largest first, in a width × height box.
function squarify(items, width, height) {
  const total = items.reduce((sum, item) => sum + item.value, 0) || 1;
  const scale = (width * height) / total;
  const queue = items.map((item) => ({ ...item, area: Math.max(item.value, 0) * scale })).sort((a, b) => b.area - a.area);
  const placed = [];
  let box = { x: 0, y: 0, w: width, h: height };
  let row = [];
  const worst = (cells, side) => {
    const sum = cells.reduce((acc, cellItem) => acc + cellItem.area, 0);
    const largest = Math.max(...cells.map((cellItem) => cellItem.area));
    const smallest = Math.min(...cells.map((cellItem) => cellItem.area));
    return Math.max((side * side * largest) / (sum * sum), (sum * sum) / (side * side * Math.max(smallest, 1e-9)));
  };
  const lay = (cells) => {
    const sum = cells.reduce((acc, cellItem) => acc + cellItem.area, 0);
    if (box.w >= box.h) {
      const columnWidth = sum / box.h;
      let y = box.y;
      for (const cellItem of cells) { const h = cellItem.area / columnWidth; placed.push({ ...cellItem, x: box.x, y, w: columnWidth, h }); y += h; }
      box = { x: box.x + columnWidth, y: box.y, w: box.w - columnWidth, h: box.h };
    } else {
      const rowHeight = sum / box.w;
      let x = box.x;
      for (const cellItem of cells) { const w = cellItem.area / rowHeight; placed.push({ ...cellItem, x, y: box.y, w, h: rowHeight }); x += w; }
      box = { x: box.x, y: box.y + rowHeight, w: box.w, h: box.h - rowHeight };
    }
  };
  for (const item of queue) {
    if (item.area <= 0) continue;
    const side = Math.min(box.w, box.h);
    if (!row.length || worst([...row, item], side) <= worst(row, side)) row.push(item);
    else { lay(row); row = [item]; }
  }
  if (row.length) lay(row);
  return placed;
}

function setUpHeatmap(data, labels) {
  const container = document.querySelector('[data-chart="heatmap"]');
  const panel = document.querySelector("[data-heat-panel]");
  const picker = document.querySelector("[data-level-picker]");
  if (!container || !data.heat) return;
  const state = { level: data.heat.areas.length ? "areas" : "folders", selected: 0 };
  const kinds = { areas: "area", folders: "folder", files: "file" };

  function select(item) {
    if (!panel) return;
    panel.hidden = false;
    panel.querySelector("[data-panel-kind]").textContent = labels.text(kinds[state.level]);
    panel.querySelector("[data-panel-name]").textContent = item.name;
    panel.querySelector("[data-panel-path]").textContent = item.path;
    panel.querySelector("[data-panel-commits]").textContent = labels.number(item.commits);
    panel.querySelector("[data-panel-lines]").textContent = labels.number(item.lines);
    const rationale = panel.querySelector("[data-panel-rationale]");
    rationale.hidden = state.level !== "areas";
    if (state.level === "areas") {
      panel.querySelector("[data-panel-documented]").textContent = labels.number(item.documented);
      panel.querySelector("[data-panel-inferred]").textContent = labels.number(item.inferred);
      panel.querySelector("[data-panel-documented-bar]").style.flexGrow = String(item.documented || 0.001);
      panel.querySelector("[data-panel-inferred-bar]").style.flexGrow = String(item.inferred || 0.001);
    }
    const list = panel.querySelector("[data-panel-files]");
    list.replaceChildren();
    const files = state.level === "files" ? [] : item.files ?? [];
    if (state.level !== "files" && !files.length) list.append(element("li", "muted", labels.text("noFiles")));
    for (const [path, count] of files) {
      const row = element("li");
      const link = element("a", "grow", path);
      link.href = sourceHref(path);
      row.append(link, element("span", "faint", labels.number(count)));
      list.append(row);
    }
    const link = panel.querySelector("[data-panel-link]");
    link.hidden = !item.href;
    if (item.href) {
      link.href = item.href;
      link.textContent = labels.text(state.level === "areas" ? "openArea" : "openFile");
    }
  }

  function draw() {
    const items = data.heat[state.level];
    container.replaceChildren();
    for (const note of document.querySelectorAll("[data-left-out]")) note.hidden = note.dataset.leftOut !== state.level;
    if (!items.length) {
      container.append(element("p", "chart-fallback muted", labels.text(state.level === "areas" ? "noAreas" : "noCode")));
      panel && (panel.hidden = true);
      return;
    }
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 430;
    const highest = highestOf(items.map((item) => item.commits));
    const tiles = squarify(items.map((item, index) => ({ ...item, index, value: item.lines })), width, height);
    for (const tile of tiles) {
      const step = rampStep(tile.commits, highest);
      const slot = element("div", "heat-tile");
      Object.assign(slot.style, { left: `${(tile.x / width) * 100}%`, top: `${(tile.y / height) * 100}%`, width: `${(tile.w / width) * 100}%`, height: `${(tile.h / height) * 100}%` });
      const button = element("button", `heat-${step}`);
      button.type = "button";
      button.setAttribute("aria-pressed", String(tile.index === state.selected));
      const words = `${tile.name}: ${labels.count("commits", tile.commits)}, ${labels.count("lines", tile.lines)}${tile.mostlyInferred ? `, ${labels.text("mostlyInferred")}` : ""}`;
      button.setAttribute("aria-label", words);
      button.setAttribute("title", words);
      if (tile.w > 84 && tile.h > 40) {
        button.append(element("span", "heat-tile-name", tile.name));
        button.append(element("span", "heat-tile-sub", `${labels.count("commits", tile.commits)} · ${labels.count("lines", tile.lines)}`));
      }
      if (tile.mostlyInferred) {
        const mark = element("span", "inferred-mark");
        mark.setAttribute("aria-hidden", "true");
        button.append(mark);
      }
      button.addEventListener("click", () => {
        state.selected = tile.index;
        for (const other of container.querySelectorAll("button")) other.setAttribute("aria-pressed", String(other === button));
        select(items[tile.index]);
      });
      slot.append(button);
      container.append(slot);
    }
    select(items[Math.min(state.selected, items.length - 1)]);
  }

  if (picker) {
    picker.hidden = false;
    for (const button of picker.querySelectorAll("[data-level]")) {
      button.setAttribute("aria-pressed", String(button.dataset.level === state.level));
      button.addEventListener("click", () => {
        state.level = button.dataset.level;
        state.selected = 0;
        for (const other of picker.querySelectorAll("[data-level]")) other.setAttribute("aria-pressed", String(other === button));
        draw();
      });
    }
  }
  let resizing = 0;
  window.addEventListener("resize", () => { clearTimeout(resizing); resizing = setTimeout(draw, 150); });
  draw();
}

function setUpGrid() {
  const grid = document.querySelector('[data-chart="grid"]');
  const status = document.querySelector("[data-grid-status]");
  if (!grid || !status) return;
  grid.addEventListener("mouseover", (event) => {
    const cell = event.target.closest?.("[data-cell]");
    if (cell) status.textContent = cell.dataset.cell;
  });
}

export function setUpRepository() {
  const block = document.querySelector("[data-repository-data]");
  if (!block) return;
  let data;
  try {
    data = JSON.parse(block.textContent);
  } catch {
    return; // the tables below still hold every number
  }
  const labels = new Labels(data);
  setUpTimeline(data, labels);
  setUpCalendar(data, labels);
  setUpHeatmap(data, labels);
  setUpGrid();
}
