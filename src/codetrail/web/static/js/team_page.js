// Mark as read on the team page (design section 24.7). A card's digest is marked read through its repository's own
// route, so it counts as read in that guide too; the overview through the team's. Each leaves the page without a
// reload, and a card with nothing left (no digest, no commits waiting) leaves with it. Focus moves to the
// repositories' heading, so a keyboard reader isn't left on a removed button.
import { post } from "./api.js";

const repositories = document.getElementById("repositories-title");

async function markRead(button, url, data, remove) {
  button.disabled = true;
  const response = await post(url, data).catch(() => null);
  if (!response?.ok) {
    button.disabled = false;
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
