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

function usedText(labels, usage) {
  // What a call actually used, beside its estimate on the button (design section 15.4).
  if (!usage) return "";
  const tokens = usage.tokens >= 1000 ? `${Math.round(usage.tokens / 1000)}k` : `${usage.tokens}`;
  const cost = usage.cost_usd ? ` · $${usage.cost_usd.toFixed(2)}` : "";
  return `${labels.dataset.labelUsed} ${tokens} ${labels.dataset.labelTokens}${cost}`;
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

function watchUpdate() {
  const button = document.querySelector("[data-update-button]");
  const status = document.querySelector("[data-update-status]");
  const dialog = document.querySelector("[data-estimate-dialog]");
  if (!button || !status || !dialog) return;
  const labels = {
    preparing: status.dataset.labelPreparing,
    running: status.dataset.labelRunning,
    done: status.dataset.labelDone,
    failed: status.dataset.labelFailed + " ",
  };
  let estimateId = null;
  async function decide(url) {
    if (!estimateId) return;
    const id = estimateId;
    estimateId = null;
    dialog.close();
    await post(url, { estimate_id: id });
    poll();
  }
  dialog.querySelector("[data-estimate-go]").addEventListener("click", () => decide("/update/confirm"));
  dialog.querySelector("[data-estimate-cancel]").addEventListener("click", () => decide("/update/cancel"));
  dialog.addEventListener("cancel", () => decide("/update/cancel")); // Escape cancels too
  async function poll(reloadWhenDone = true) {
    const response = await fetch("/update/status", { credentials: "same-origin" });
    if (!response.ok) return;
    const result = await response.json();
    const { state, message } = result;
    status.textContent = state === "idle" || state === "waiting" ? "" : (labels[state] ?? "") + (message ?? "");
    button.disabled = ["preparing", "waiting", "running"].includes(state);
    if (state === "waiting" && result.estimate_id && result.estimate_id !== estimateId) {
      estimateId = result.estimate_id;
      dialog.querySelector("[data-estimate-body]").innerHTML = result.estimate_html; // rendered and escaped on the server
      dialog.showModal();
    }
    if (["preparing", "waiting", "running"].includes(state)) setTimeout(() => poll(reloadWhenDone), 1500);
    else if (state === "done" && reloadWhenDone) window.location.reload();
  }
  button.addEventListener("click", async () => {
    await post("/update", {});
    poll();
  });
  poll(false); // show an update that is already running or waiting
}

function watchQuestions() {
  const section = document.querySelector("[data-ask]");
  if (!section) return;
  const form = section.querySelector("[data-ask-form]");
  const status = section.querySelector("[data-ask-status]");
  const answer = section.querySelector("[data-answer]");
  const save = section.querySelector("[data-save-answer]");
  let answerId = null;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = form.elements.question.value.trim();
    if (!question) return;
    form.querySelector("button").disabled = true;
    save.hidden = true;
    answer.textContent = "";
    status.textContent = status.dataset.labelReading;
    const body = { question };
    if (section.dataset.pageId) body.page_id = section.dataset.pageId;
    try {
      const response = await post("/bridge/questions", body);
      if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        status.textContent = problem.error ?? `The question was refused (${response.status}).`;
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          if (!line.trim()) continue;
          const message = JSON.parse(line);
          if (message.type === "text") {
            answer.textContent += message.text; // plain text while it streams
            status.textContent = "";
          } else if (message.type === "done") {
            answer.innerHTML = message.html; // rendered on the server with raw HTML disabled
            answerId = message.answer_id;
            save.hidden = false;
            status.textContent = usedText(status, message.usage);
          } else if (message.type === "error") {
            answer.textContent = ""; // nothing of a failed or withheld answer stays on the page
            status.textContent = message.message;
          }
        }
      }
    } finally {
      form.querySelector("button").disabled = false;
    }
  });

  save.addEventListener("click", async () => {
    if (!answerId) return;
    const response = await post(`/bridge/answers/${answerId}/save`, {});
    const result = await response.json().catch(() => ({}));
    if (response.ok) {
      save.hidden = true;
      status.textContent = "Saved.";
      const link = document.createElement("a");
      link.href = `/pages/${result.page_id}`;
      link.textContent = result.page_id;
      status.append(" ", link);
    } else {
      status.textContent = result.error ?? "Couldn't save the answer.";
    }
  });
}

function watchLearning() {
  for (const button of document.querySelectorAll("[data-mark-read]")) {
    button.addEventListener("click", async () => {
      const response = await post("/learn/read", { page_id: button.dataset.markRead });
      if (response.ok) window.location.reload();
    });
  }
  for (const form of document.querySelectorAll("form[data-check]")) {
    const feedback = form.querySelector("[data-feedback]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button");
      button.disabled = true;
      feedback.textContent = "…";
      try {
        const response = await post("/learn/checks", {
          page_id: form.dataset.page,
          check_id: form.dataset.check,
          answer: form.elements.answer.value,
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
          feedback.textContent = result.error ?? "The answer couldn't be graded.";
          return;
        }
        feedback.textContent = `${result.verdict}: ${result.feedback}`; // text only, never HTML
        const usage = document.querySelector("[data-ask-status]");
        if (usage && result.usage) feedback.textContent += ` (${usedText(usage, result.usage)})`;
        if (result.state === "learned") window.setTimeout(() => window.location.reload(), 1500);
      } finally {
        button.disabled = false;
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  watchLearning();
  watchQuestions();
  watchLanguage();
  watchUpdate();
  renderDiagrams();
});
