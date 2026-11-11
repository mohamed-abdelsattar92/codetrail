// Mark read on the team page (design section 24.7). A card's digest is marked read through its repository's own
// route, so it counts as read in that guide too; the overview through the team's. Each leaves the page without a
// reload, and a card with nothing left (no digest, nothing waiting) leaves with it. Focus moves to the
// repositories' heading, so a keyboard reader isn't left on a removed button. A refusal says so beside the button,
// as text, and the button can be pressed again.
import { post } from "./api.js";

const repositories = document.getElementById("repositories-title");
const failedText = document.querySelector("[data-read-failed]")?.dataset.readFailed ?? "";

function sayFailed(button) {
  let message = button.parentElement.querySelector("[data-read-error]");
  if (!message) {
    message = document.createElement("span");
    message.className = "team-read-error";
    message.dataset.readError = "";
    message.setAttribute("role", "status");
    button.before(message);
  }
  message.textContent = failedText;
}

async function markRead(button, url, data, remove) {
  button.disabled = true;
  const response = await post(url, data).catch(() => null);
  button.disabled = false;
  if (!response?.ok) {
    sayFailed(button);
    return;
  }
  remove();
  repositories?.focus();
}

for (const button of document.querySelectorAll("[data-digest-read]")) {
  const url = `/repos/${button.dataset.target}/learn/read`;
  button.addEventListener("click", () =>
    markRead(button, url, { page_id: button.dataset.digestRead }, () => {
      const card = button.closest("[data-team-card]");
      button.closest("[data-digest]").remove();
      if (!card.querySelector("[data-digest]") && !card.classList.contains("not-caught-up")) card.remove();
    }),
  );
}

const overview = document.querySelector("[data-overview-read]");
overview?.addEventListener("click", () =>
  markRead(overview, "/team/overview/read", { overview_id: overview.dataset.overviewRead }, () =>
    overview.closest("[data-overview]").remove(),
  ),
);
