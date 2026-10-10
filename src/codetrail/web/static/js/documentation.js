// The Documentation page's filter chips (design section 21.2): each shows one kind of item, in its list and in the
// list's folded rest. The server writes every count and label; this sets only hidden, aria-pressed and the live
// region's words, copied from the chip. Without it, every item shows.
export function setUpDocumentation() {
  for (const area of document.querySelectorAll("[data-filterable]")) {
    const group = area.querySelector("[data-filter]");
    if (!group) continue;
    const status = area.querySelector("[data-filter-status]");
    const buttons = [...group.querySelectorAll("[data-show]")];
    group.hidden = false;
    for (const button of buttons) {
      button.addEventListener("click", () => {
        const kind = button.dataset.show;
        for (const other of buttons) other.setAttribute("aria-pressed", String(other === button));
        for (const item of area.querySelectorAll("li[data-kind]")) item.hidden = kind !== "all" && item.dataset.kind !== kind;
        for (const more of area.querySelectorAll("[data-more]")) {
          more.hidden = more.dataset.more !== kind;
          if (!more.hidden) more.closest("details").hidden = more.dataset.count === "0";
        }
        if (status) status.textContent = button.textContent;
      });
    }
  }
}
