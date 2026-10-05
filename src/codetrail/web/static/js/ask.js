// The Ask panel (design section 16.4): this session's answers, a question about the page being read, and saving
// answers to the guide. A question is sent only by the Send button, which shows its estimate; nothing else spends.
import { getJSON, post, recall, remember, usedText } from "./api.js";

const panel = document.querySelector("[data-ask]");
const thread = panel?.querySelector("[data-ask-thread]");
const intro = panel?.querySelector("[data-ask-intro]");
const form = panel?.querySelector("[data-ask-form]");
const status = panel?.querySelector("[data-ask-status]");
let loaded = false;
let opener = null;

function item(question, answerLanguage) {
  const article = document.createElement("article");
  article.className = "ask-item";
  const asked = document.createElement("p");
  asked.className = "ask-question";
  asked.textContent = question;
  const answer = document.createElement("div");
  answer.className = "ask-answer";
  answer.lang = answerLanguage;
  answer.dir = "auto";
  const actions = document.createElement("div");
  actions.className = "ask-actions";
  article.append(asked, answer, actions);
  intro.hidden = true;
  thread.append(article);
  return { article, answer, actions };
}

function addSaveButton(actions, answerId, question) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "button small";
  button.dataset.saveAnswer = "";
  button.textContent = panel.dataset.labelSave;
  button.addEventListener("click", async () => {
    button.disabled = true;
    const response = await post(`/bridge/answers/${encodeURIComponent(answerId)}/save`, {});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      button.disabled = false;
      const problem = document.createElement("span");
      problem.textContent = result.error ?? panel.dataset.labelSaveFailed;
      actions.append(problem);
      return;
    }
    const link = document.createElement("a");
    link.href = `/pages/${result.page_id}`;
    link.textContent = panel.dataset.labelSaved;
    button.replaceWith(link);
    addToSidebar(result.page_id, question);
  });
  actions.prepend(button);
}

function addToSidebar(pageId, question) {
  const list = document.querySelector("[data-nav-answers]");
  if (!list) return;
  list.querySelector("[data-nav-empty]")?.remove();
  const entry = document.createElement("li");
  const link = document.createElement("a");
  link.className = "nav-link";
  link.href = `/pages/${pageId}`;
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = question;
  link.append(label);
  entry.append(link);
  list.prepend(entry);
}

async function loadSessionAnswers() {
  if (loaded) return;
  loaded = true;
  try {
    const { answers } = await getJSON("/bridge/answers");
    for (const answer of answers) {
      const { answer: body, actions } = item(answer.question, panel.dataset.language);
      body.innerHTML = answer.html; // rendered and sanitized on the server, raw HTML disabled
      addSaveButton(actions, answer.id, answer.question);
    }
    thread.scrollTop = thread.scrollHeight;
  } catch {
    loaded = false; // try again next time the panel opens
  }
}

export function isAskOpen() {
  return Boolean(panel && !panel.hidden);
}

export function openAsk(question, focus = true) {
  if (!panel) return;
  if (focus) opener ??= document.activeElement;
  panel.hidden = false;
  document.body.classList.add("ask-open");
  remember("sessionStorage", "codetrail.ask", "open");
  loadSessionAnswers();
  const field = form.elements.question;
  if (typeof question === "string" && question) field.value = question;
  if (focus) field.focus();
}

export function closeAsk() {
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  document.body.classList.remove("ask-open");
  remember("sessionStorage", "codetrail.ask", null);
  if (opener instanceof HTMLElement) opener.focus();
  opener = null;
}

async function send(event) {
  event.preventDefault();
  const question = form.elements.question.value.trim();
  if (!question) return;
  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  status.textContent = status.dataset.labelReading;
  const { answer, actions } = item(question, panel.dataset.language);
  thread.scrollTop = thread.scrollHeight;
  const body = { question };
  if (panel.dataset.pageId) body.page_id = panel.dataset.pageId;
  try {
    const response = await post("/bridge/questions", body);
    if (!response.ok) {
      const problem = await response.json().catch(() => ({}));
      answer.textContent = problem.error ?? `The question was refused (${response.status}).`;
      status.textContent = "";
      return;
    }
    form.elements.question.value = "";
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
          addSaveButton(actions, message.answer_id, question);
          const used = document.createElement("span");
          used.textContent = usedText(status, message.usage);
          actions.append(used);
          status.textContent = "";
        } else if (message.type === "error") {
          answer.textContent = message.message; // nothing of a failed or withheld answer stays on the page
          status.textContent = "";
        }
        thread.scrollTop = thread.scrollHeight;
      }
    }
  } finally {
    button.disabled = false;
  }
}

export function setUpAsk() {
  if (!panel) return;
  form.addEventListener("submit", send);
  for (const button of document.querySelectorAll("[data-ask-open]")) {
    button.addEventListener("click", () => openAsk());
  }
  panel.querySelector("[data-ask-close]")?.addEventListener("click", closeAsk);
  if (recall("sessionStorage", "codetrail.ask") === "open") openAsk(undefined, false); // reopened, not focused
}
