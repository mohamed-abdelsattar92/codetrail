// "On this page": an outline of the reading column's headings that follows the reader (design section 16.1).
const outline = document.querySelector("[data-outline]");

function slug(text, used) {
  const base = text.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "") || "section";
  let candidate = base;
  for (let number = 2; used.has(candidate) || document.getElementById(candidate); number += 1) {
    candidate = `${base}-${number}`;
  }
  used.add(candidate);
  return candidate;
}

export function setUpOutline() {
  if (!outline) return;
  const headings = [...document.querySelectorAll("main .prose h2, main .prose h3, main .checks > h2")];
  const list = outline.querySelector("ol");
  if (headings.length < 2 || !list) return;
  const used = new Set();
  const links = new Map();
  for (const heading of headings) {
    if (!heading.id) heading.id = slug(heading.textContent, used);
    const item = document.createElement("li");
    item.className = heading.tagName === "H3" ? "depth-3" : "depth-2";
    const link = document.createElement("a");
    link.href = `#${heading.id}`;
    link.textContent = heading.textContent;
    item.append(link);
    list.append(item);
    links.set(heading, link);
  }
  outline.hidden = false;
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const link of links.values()) link.removeAttribute("aria-current");
        links.get(entry.target)?.setAttribute("aria-current", "location");
      }
    },
    { rootMargin: "-70px 0px -70% 0px" },
  );
  for (const heading of headings) observer.observe(heading);
}
