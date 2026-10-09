// Marking pages read and grading checks (design section 8). Grading feedback is shown as text, never as HTML.
import { post, usedText } from "./api.js";

export async function toggleRead() {
  const read = document.querySelector("[data-mark-read]");
  const unread = document.querySelector("[data-mark-unread]");
  const target = read ?? unread;
  if (!target) return;
  const url = read ? "/learn/read" : "/learn/unread";
  const response = await post(url, { page_id: (read ?? unread).dataset[read ? "markRead" : "markUnread"] });
  if (response.ok) window.location.reload();
}

export function setUpLearning() {
  for (const button of document.querySelectorAll("[data-mark-read], [data-mark-unread]")) {
    button.addEventListener("click", toggleRead);
  }
  for (const form of document.querySelectorAll("form[data-check]")) {
    const feedback = form.querySelector("[data-feedback]");
    const labels = form.closest("[data-label-pass]").dataset; // the verdicts in the reader's language
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
          feedback.textContent = result.error ?? labels.labelUngraded;
          return;
        }
        const verdict = { pass: labels.labelPass, partial: labels.labelPartial, fail: labels.labelFail }[result.verdict];
        feedback.textContent = `${verdict}: ${result.feedback}`;
        const usage = document.querySelector("[data-ask-status]");
        if (usage && result.usage) feedback.textContent += ` (${usedText(usage, result.usage)})`;
        if (result.state === "learned") window.setTimeout(() => window.location.reload(), 1500);
      } finally {
        button.disabled = false;
      }
    });
  }
}
