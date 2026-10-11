# Phase 19: the sidebar implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the shell an Arc-style sidebar: hidden or docked (a header button or **\\**), peeking over the page from the window's edge, drawn in a tinted frame around the page's card, with folding sections and pinned pages as tiles kept in the target's settings.

**Architecture:** The server draws everything: the frame, the folding headings (open by default), the pinned tiles from the target's `settings` table, and hidden pin and unpin buttons. `theme-init.js` applies the remembered hidden and folded states as attributes on `<html>` before the page is drawn, and the styles read them. A new module, `static/js/sidebar.js`, runs the toggle, the peek, the folding and the pins; a new endpoint, `POST /settings/pins`, replaces the pinned list.

**Tech Stack:** Python 3.14, FastAPI with Jinja (i18n), plain ES modules, Playwright. No new dependency.

**Spec:** [design section 22](../2026-10-05-codetrail-design.md#22-the-sidebar), changing section 16.1's sidebar.

## Global Constraints

- One new endpoint, `POST /settings/pins`, behind the session, origin and token checks; no new dependency; no inline script (the tile template is a `<template>` element).
- Titles reach the page only through autoescaping templates; the script moves them with `textContent` and attributes only.
- Stylesheets use logical properties only (`tests/api/test_shell.py::test_stylesheets_use_logical_properties_only`); every color is a token with its dark value.
- Hidden and folded states live in `localStorage` (`codetrail.sidebar`, `codetrail.sidebar-folded`); pins in the target's `settings` table under `pinned_pages`.
- `ui.max_pins = 9`, 1 to 50.
- Below 900 px the drawer behind the menu button is unchanged; hiding and peeking apply only above it.
- Existing markup the tests and scripts rely on stays: `<nav class="sidebar"`, `href="…" aria-current="page"` in that order, `[data-nav-answers]`, `[data-nav-empty]`, `.nav-heading`, `[data-resize="sidebar"]`, `[data-menu]`.
- Every new label goes through the catalogs; Arabic gets each one.
- Commits: Conventional Commits with a scope and the What, Why, Alternatives considered, Risks, Agent and model sections; run commands through `mise exec --`.

## Review Focus

1. **A pinned page that was later removed from the guide** (an update dropped the concept): its tile disappears, and the next pin or unpin request doesn't send it (the script reads the drawn tiles), so the request isn't refused. Test in Task 2 (`test_a_removed_page_drops_out_of_the_pins`).
2. **Pinning from a peeking sidebar:** the click doesn't close the peek (only links do), and the new tile appears in it. Test in Task 4.
3. **A narrow window with the sidebar remembered as hidden:** the drawer still opens from the menu button, and the toggle button isn't shown. Test in Task 3.
4. **Pressing \\ while typing in the search box or the Ask panel:** nothing happens. Test in Task 3.
5. **A failed pin request (the session expired):** the sidebar stays as it was; no tile appears. Test in Task 4.

---

### Task 1: the pins' helpers and setting

**Files:**
- Modify: `src/codetrail/config.py` (`InterfaceSettings`)
- Modify: `src/codetrail/web/navigation.py`
- Test: `tests/unit/test_navigation.py`, `tests/unit/test_config.py`

**Interfaces — produces:**
- `InterfaceSettings.max_pins: int = 9` (1–50).
- In `codetrail.web.navigation`: `PINNABLE = ("path", "area", "concept", "answer")`; `initials(title: str) -> str`; `read_pins(value: str | None) -> list[str]`; `Navigation.pins: list[Page]`; `build_navigation(..., pinned: Sequence[str] = ())` fills `pins` with the pinnable pages named, in that order, leaving out the missing.

- [ ] **Step 1: Failing tests** — in `tests/unit/test_navigation.py`:

```python
def test_initials_take_two_words_or_a_words_first_two_letters() -> None:
    assert initials("Payment retries") == "PR"
    assert initials("billing") == "Bi"
    assert initials("The ledger, again") == "TL"
    assert initials("الدفع الآمن") == "اا"
    assert initials("  ") == "·"


def test_stored_pins_read_back_or_read_as_none() -> None:
    assert read_pins('["concepts/retries", "areas/app"]') == ["concepts/retries", "areas/app"]
    assert read_pins(None) == [] and read_pins("not json") == [] and read_pins('{"a": 1}') == []
    assert read_pins('["areas/app", 3]') == ["areas/app"]


def test_pins_keep_their_order_and_leave_out_missing_or_unpinnable_pages() -> None:
    pages = [Page("areas/app", {"kind": "area", "title": "The app"}, ""),
             Page("concepts/db", {"kind": "concept", "title": "The database"}, ""),
             Page("digests/1", {"kind": "digest", "title": "A digest"}, "")]  # fmt: skip
    nav = build_navigation(pages, {}, [], pinned=["concepts/db", "areas/gone", "digests/1", "areas/app"])
    assert [page.id for page in nav.pins] == ["concepts/db", "areas/app"]
```

and in `tests/unit/test_config.py`:

```python
@pytest.mark.parametrize("value", [0, 51])
def test_refuses_a_pin_limit_out_of_range(value: int) -> None:
    with pytest.raises(ValidationError):
        GlobalConfig.model_validate({"ui": {"max_pins": value}})
```

(`Page(id, meta, body)` exposes `kind` and `title` from its meta, as the existing tests in that file use.)

- [ ] **Step 2: Run them, see them fail** — `mise exec -- uv run pytest tests/unit/test_navigation.py tests/unit/test_config.py -q` → ImportError for `initials`.
- [ ] **Step 3: Implement.** In `config.py`, `InterfaceSettings` gains `max_pins: int = Field(default=9, ge=1, le=50)  # pinned pages the sidebar keeps`. In `navigation.py` (imports `json`, `re`):

```python
PINNABLE = ("path", "area", "concept", "answer")  # the guide pages the sidebar lists, which can be pinned
WORD = re.compile(r"[^\W_]+")


def initials(title: str) -> str:
    """A pinned tile's letters: the first letters of the title's first two words, or one word's first two."""
    words = WORD.findall(title)
    if not words:
        return "·"
    if len(words) == 1:
        return words[0][:1].upper() + words[0][1:2]
    return (words[0][:1] + words[1][:1]).upper()


def read_pins(value: str | None) -> list[str]:
    """The pinned page ids as stored, a JSON list of strings; anything else reads as no pins."""
    try:
        loaded = json.loads(value) if value else []
    except ValueError:
        return []
    return [item for item in loaded if isinstance(item, str)] if isinstance(loaded, list) else []
```

`Navigation` gains `pins: list[Page] = field(default_factory=list)  # pinned pages, in the order they were pinned`; `build_navigation` gains `pinned: Sequence[str] = ()` and passes `pins=[pinnable[page_id] for page_id in pinned if page_id in pinnable]` with `pinnable = {page.id: page for page in pages if page.kind in PINNABLE}`.
- [ ] **Step 4: Run, see them pass; lint and type-check.**
- [ ] **Step 5: Commit** `feat(web): read pinned pages and their tiles' initials`.

### Task 2: the endpoint and the sidebar's markup and look

**Files:**
- Modify: `src/codetrail/web/app.py` (a `setting` reader shared by `language()` and `navigation()`, `PinChoice`, `POST /settings/pins`, `max_pins` in `render`, the `initials` filter)
- Modify: `src/codetrail/web/templates/layout/sidebar.html`, `layout/header.html`, `layout/icons.html`
- Modify: `src/codetrail/web/static/css/layout.css`, `components.css`
- Test: create `tests/api/test_sidebar.py`

**Interfaces — produces (for Tasks 3 and 4):** the header's `[data-sidebar-toggle]` (with `data-label-hide`, `data-label-show`); the sidebar `#sidebar` with `data-max-pins`, `data-label-unpin` ("Unpin {title}"), `data-label-full`; `<ul class="pins" data-pins>` of `<li class="pin-item">` holding `<a class="pin">` and `<button class="pin-remove" data-unpin="<id>" hidden>`; `<template data-pin-template>`; each section `<div class="nav-section" data-section="<name>">` with `<button class="nav-fold" data-fold="<name>" aria-expanded="true" aria-controls="nav-<name>">` and `<ul id="nav-<name>">`; each pinnable row `<li class="nav-row">` with `<button class="nav-pin" data-pin="<id>" data-initials="<letters>" aria-pressed hidden>`; `<button class="sidebar-edge" data-sidebar-edge>` after the resize handle. Section names: `paths`, `areas`, `concepts`, `answers`, `more`.

- [ ] **Step 1: Failing API tests** in `tests/api/test_sidebar.py` (the `paths` fixture and `signed_in` helper copied from `tests/api/test_shell.py`, with the same `PLAN`, `FILES` and `writer`):

```python
def sidebar_of(page: str) -> str:
    return page.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]


def test_pins_are_stored_and_drawn_in_order(paths: Paths) -> None:
    client, headers = signed_in(paths)
    pages = ["concepts/db", "areas/app"]
    assert client.post("/settings/pins", json={"pages": pages}, headers=headers).status_code == 204
    tiles = sidebar_of(client.get("/").text).split('data-pins', 1)[1].split("</ul>", 1)[0]
    assert tiles.index('href="/pages/concepts/db"') < tiles.index('href="/pages/areas/app"')
    assert ">TD</a>" in tiles and 'aria-label="The database"' in tiles


def test_pins_need_the_session_token_and_origin(paths: Paths) -> None:
    client, headers = signed_in(paths)
    body = {"pages": ["areas/app"]}
    assert client.post("/settings/pins", json=body, headers={"origin": ORIGIN}).status_code == 403
    assert client.post("/settings/pins", json=body, headers={**headers, "origin": "http://evil.test"}).status_code == 403


@pytest.mark.parametrize(("pages", "status"), [
    (["areas/missing"], 400), (["module:app/db.py"], 400), (["areas/app"] * 2 + [f"x{n}" for n in range(9)], 400),
    (["a" * 201], 422),
])  # fmt: skip
def test_pins_refuse_what_the_guide_doesnt_hold(paths: Paths, pages: list[str], status: int) -> None:
    client, headers = signed_in(paths)
    assert client.post("/settings/pins", json={"pages": pages}, headers=headers).status_code == status


def test_pins_refuse_another_field(paths: Paths) -> None:
    client, headers = signed_in(paths)
    body = {"pages": [], "extra": 1}
    assert client.post("/settings/pins", json=body, headers=headers).status_code == 422


def test_more_pins_than_the_limit_are_refused(paths: Paths) -> None:
    client, headers = signed_in(paths, settings=GlobalConfig.model_validate({"ui": {"max_pins": 1}}))
    body = {"pages": ["areas/app", "concepts/db"]}
    assert client.post("/settings/pins", json=body, headers=headers).status_code == 400
    assert client.post("/settings/pins", json={"pages": ["areas/app", "areas/app"]}, headers=headers).status_code == 204


def test_a_removed_page_drops_out_of_the_pins(paths: Paths) -> None:
    client, headers = signed_in(paths)
    client.post("/settings/pins", json={"pages": ["areas/lonely", "areas/app"]}, headers=headers)
    guide = GuideRepository(paths.target_data("t") / "guide")
    guide.path_of("areas/lonely").unlink()
    guide.commit("Drop an area")
    tiles = sidebar_of(client.get("/").text).split("data-pins", 1)[1].split("</ul>", 1)[0]
    assert "areas/lonely" not in tiles and 'href="/pages/areas/app"' in tiles


def test_a_title_holding_markup_stays_text_in_its_tile(paths: Paths) -> None:
    guide = GuideRepository(paths.target_data("t") / "guide")
    page = guide.read_page("areas/app")
    assert page is not None
    guide.write_page(Page("areas/app", {**page.meta, "title": "<b>App</b>"}, page.body))
    guide.commit("Markup title")
    client, headers = signed_in(paths)
    client.post("/settings/pins", json={"pages": ["areas/app"]}, headers=headers)
    sidebar = sidebar_of(client.get("/").text)
    assert "<b>App</b>" not in sidebar and 'aria-label="&lt;b&gt;App&lt;/b&gt;"' in sidebar


def test_the_sidebar_carries_its_folds_pins_and_toggle(paths: Paths) -> None:
    client, _ = signed_in(paths)
    page = client.get("/pages/areas/app").text
    sidebar = sidebar_of(page)
    for name in ("paths", "areas", "concepts", "answers", "more"):
        assert f'data-fold="{name}" aria-expanded="true" aria-controls="nav-{name}"' in sidebar
    assert 'data-pin="areas/app" data-initials="TA" aria-pressed="false"' in sidebar
    assert "data-pin-template" in sidebar and 'data-max-pins="9"' in sidebar
    assert "data-sidebar-toggle" in page and "data-sidebar-edge" in page
    assert 'href="/pages/areas/app" aria-current="page"' in sidebar
```

`signed_in` takes an optional `settings: GlobalConfig | None = None`.
- [ ] **Step 2: Run, see them fail** — 404 for the endpoint, no `data-fold`.
- [ ] **Step 3: The endpoint and reading** in `app.py`:

```python
PINS_SETTING = "pinned_pages"


class PinChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[Annotated[str, StringConstraints(max_length=200)]] = Field(max_length=50)
```

Inside `create_app`, a reader both callers use:

```python
    def setting(key: str) -> str | None:
        """One of the target's stored settings, or None before the database exists."""
        database = view.data / "codetrail.db"
        if not database.exists():
            return None
        connection = connect(database)
        try:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        finally:
            connection.close()
        return row["value"] if row else None
```

`language()` becomes `chosen = setting(LANGUAGE_SETTING)` then the same return; `navigation()` passes `pinned=read_pins(setting(PINS_SETTING))` to `build_navigation`; `render` passes `max_pins=settings.ui.max_pins`; `_environment` registers `environment.filters["initials"] = initials`. The endpoint, beside `/settings/language`:

```python
    @app.post("/settings/pins")
    def set_pins(choice: PinChoice) -> Response:
        """The pinned pages (design section 22.3): the whole list, replaced; only pages the guide holds."""
        pages = list(dict.fromkeys(choice.pages))
        pinnable = {page.id for page in guide.pages() if page.kind in PINNABLE} if guide.root.exists() else set()
        if len(pages) > settings.ui.max_pins or any(page not in pinnable for page in pages):
            return Response(status_code=400)
        connection = connect(view.data / "codetrail.db")
        try:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (PINS_SETTING, json.dumps(pages)),
            )
        finally:
            connection.close()
        return Response(status_code=204)
```

- [ ] **Step 4: Icons** in `layout/icons.html`: `sidebar` (`<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>`), `pin` (`<path d="M9 4h6l-1 6 3 3H7l3-3-1-6Z"/><path d="M12 13v7"/>`), `chevron` (`<path d="m9 6 6 6-6 6"/>`).
- [ ] **Step 5: The header's toggle** in `layout/header.html`, before the menu button:

```jinja
  <button type="button" class="icon-button sidebar-toggle" data-sidebar-toggle aria-controls="sidebar" aria-expanded="true"
          aria-label="{{ _('Hide the sidebar') }}" data-label-hide="{{ _('Hide the sidebar') }}"
          data-label-show="{{ _('Show the sidebar') }}">{{ icon("sidebar") }}</button>
```

- [ ] **Step 6: Rewrite `layout/sidebar.html`.** Keep its links, order and attributes, and add, as listed in this task's Interfaces: the pins list and template at the top (before Home's list); each of Paths, Areas, Concepts, Saved answers and More in a `div.nav-section[data-section]` whose `h2.nav-heading` holds the fold button (chevron icon, the heading, and the item count for paths, areas, concepts and saved answers); a `pin(page)` macro after each pinnable row's link (paths, areas, concepts, saved answers), disabled with the "Unpin a page first" title when the list is full and the page isn't in it; the edge button after the resize handle:

```jinja
{% set pinned_ids = nav.pins|map(attribute="id")|list %}
{% set full = pinned_ids|length >= max_pins %}
{% macro fold(name, heading, count=none) -%}
<h2 class="nav-heading"><button type="button" class="nav-fold" data-fold="{{ name }}" aria-expanded="true" aria-controls="nav-{{ name }}">{{ icon("chevron") }}<span class="nav-fold-label">{{ heading }}</span>{% if count is not none %}<span class="count">{{ count }}</span>{% endif %}</button></h2>
{%- endmacro %}
{% macro pin(page) -%}
<button type="button" class="nav-pin" data-pin="{{ page.id }}" data-initials="{{ page.title|initials }}" aria-pressed="{{ 'true' if page.id in pinned_ids else 'false' }}" aria-label="{{ _('Pin %(title)s', title=page.title) }}"{% if full and page.id not in pinned_ids %} disabled title="{{ _('Unpin a page first') }}"{% endif %} hidden>{{ icon("pin") }}</button>
{%- endmacro %}
<nav class="sidebar" id="sidebar" aria-label="{{ _('Guide') }}" data-max-pins="{{ max_pins }}"
     data-label-unpin="{{ _('Unpin %(title)s', title='{title}') }}" data-label-full="{{ _('Unpin a page first') }}">
  <ul class="pins" data-pins aria-label="{{ _('Pinned pages') }}"{% if not nav.pins %} hidden{% endif %}>
    {% for page in nav.pins %}<li class="pin-item"><a class="pin" href="/pages/{{ page.id }}" {% if active == page.id %}aria-current="page" {% endif %}title="{{ page.title }}" aria-label="{{ page.title }}" lang="en" dir="ltr">{{ page.title|initials }}</a><button type="button" class="pin-remove" data-unpin="{{ page.id }}" aria-label="{{ _('Unpin %(title)s', title=page.title) }}" hidden>{{ icon("close") }}</button></li>{% endfor %}
  </ul>
  <template data-pin-template><li class="pin-item"><a class="pin" lang="en" dir="ltr"></a><button type="button" class="pin-remove">{{ icon("close") }}</button></li></template>
```

(the rest of the sidebar as today, wrapped as above; the edge button is `<button type="button" class="sidebar-edge" data-sidebar-edge aria-controls="sidebar" aria-expanded="false" aria-label="{{ _('Show the sidebar for a moment') }}"></button>`).
- [ ] **Step 7: The look** — in `layout.css`: `body` takes `var(--sidebar)`; `.topbar` takes `var(--sidebar)` with no bottom border or blur; `.sidebar` loses its border and background (the drawer below 900 px keeps `background: var(--sidebar)`); `.page-area` becomes the card (`margin-inline-end` and `margin-block-end` `var(--space-2)`, `border-radius: var(--radius-card)`, `background: var(--page)`, `box-shadow: 0 1px 2px rgb(16 24 64 / 0.06), 0 0 0 1px var(--line)`), with no margin, radius or shadow below 900 px; `.sidebar-toggle` hidden below 900 px. In `components.css`, the rows (30 px, 8 px radius, a soft hover wash, the current page as a `var(--raised)` pill with accent text), the fold buttons (quiet capitals, the chevron turned 90° when open, the count at the end; mirrored with `:dir(rtl)`), the pin buttons (absolutely placed at the row's end, shown on hover or focus, accent when pressed; the row's count, check and dot hidden while it shows), the tiles (a three-column grid, 36 px tiles in a `var(--raised)` wash, the current tile a pill, the ✕ at the tile's top end corner on hover or focus), and the folded rows:

```css
:root[data-folded~="paths"] #nav-paths > li:not(:has([aria-current="page"])),
:root[data-folded~="areas"] #nav-areas > li:not(:has([aria-current="page"])),
:root[data-folded~="concepts"] #nav-concepts > li:not(:has([aria-current="page"])),
:root[data-folded~="answers"] #nav-answers > li:not(:has([aria-current="page"])),
:root[data-folded~="more"] #nav-more > li:not(:has([aria-current="page"])) {
  display: none;
}
```

- [ ] **Step 8: Run the API tests, the shell tests, lint and type-check;** run `just test-browser -k "accessibility or resize or keyboard"` to see the new look keeps the scan, the resizing and the shortcuts.
- [ ] **Step 9: Commit** `feat(web): pin pages to the sidebar, and draw it as a frame around the page`.

### Task 3: hiding and peeking

**Files:**
- Modify: `src/codetrail/web/static/js/theme-init.js`, `src/codetrail/web/static/css/layout.css`
- Create: `src/codetrail/web/static/js/sidebar.js`
- Modify: `src/codetrail/web/static/js/shortcuts.js`, `main.js`, `templates/layout/shortcuts.html`
- Test: create `tests/browser/test_sidebar.py`

**Interfaces — produces:** `setUpSidebar()`, `toggleSidebar()`, `isPeeking()`, `closePeek(returnFocus = false)` from `sidebar.js`; `<html data-sidebar="hidden">`; `body.sidebar-peek`.

- [ ] **Step 1: Failing browser tests** in `tests/browser/test_sidebar.py`:

```python
"""The sidebar: hiding, peeking, folding and pinning (design section 22)."""

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser


def wide(page: Page) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})


def test_the_key_and_the_button_hide_the_sidebar_and_a_reload_keeps_it(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    sidebar, toggle = page.locator("#sidebar"), page.locator("[data-sidebar-toggle]")
    page.keyboard.press("\\")
    expect(page.locator("html")).to_have_attribute("data-sidebar", "hidden")
    expect(sidebar).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    page.reload()
    ready(page)
    expect(page.locator("html")).to_have_attribute("data-sidebar", "hidden")
    toggle.click()
    expect(sidebar).to_be_visible()
    expect(page.locator("html")).not_to_have_attribute("data-sidebar", "hidden")


def test_the_key_does_nothing_while_typing(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.locator("[data-search-form] input").fill("x\\")
    expect(page.locator("html")).not_to_have_attribute("data-sidebar", "hidden")


def test_resting_on_the_edge_peeks_and_leaving_hides_it_again(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    sidebar = page.locator("#sidebar")
    page.mouse.move(3, 400)
    expect(sidebar).to_be_visible()
    page.mouse.move(900, 400)
    expect(sidebar).to_be_hidden()
    page.mouse.move(3, 400)
    expect(sidebar).to_be_visible()
    page.keyboard.press("Escape")
    expect(sidebar).to_be_hidden()


def test_the_edge_button_peeks_from_the_keyboard_and_returns_the_focus(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    edge = page.locator("[data-sidebar-edge]")
    edge.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#sidebar")).to_be_visible()
    assert page.evaluate("document.querySelector('#sidebar').contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(edge).to_be_focused()


def test_following_a_link_from_the_peek_closes_it(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    page.mouse.move(3, 400)
    page.locator("#sidebar a[href='/progress']").click()
    page.wait_for_url("**/progress")
    expect(page.locator("#sidebar")).to_be_hidden()


def test_a_narrow_window_keeps_its_drawer(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    page.set_viewport_size({"width": 375, "height": 812})
    expect(page.locator("[data-sidebar-toggle]")).to_be_hidden()
    page.locator("[data-menu]").click()
    expect(page.locator("#sidebar")).to_be_in_viewport()
```

- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement.** `theme-init.js` sets `data-sidebar="hidden"` when `codetrail.sidebar` is `"hidden"` (and, for Task 4, `data-folded` from `codetrail.sidebar-folded`, keeping only the five section names). `layout.css`, above 900 px: when hidden, the shell is one column, the card gains `margin-inline-start: var(--space-2)`, the sidebar is a fixed floating card (`inset-block: calc(var(--header-height) + var(--space-2)) var(--space-2)`, `inset-inline-start: var(--space-2)`, `inline-size: var(--sidebar-width)`, `border-radius: var(--radius-card)`, `background: var(--sidebar)`, `box-shadow: var(--shadow-float)`) moved out of view with `transform` and `visibility: hidden`, and `body.sidebar-peek` brings it back; the resize handle is hidden; the edge button shows as a 10 px strip with a faint 3 px grip. `sidebar.js`:

```js
// The sidebar (design section 22): hiding and showing it, peeking at it while hidden, folding its sections and
// pinning pages. Hidden and folded are remembered in this browser; pins are kept by Codetrail. Titles are only ever
// moved with textContent and attributes.
import { post, remember } from "./api.js";

const root = document.documentElement;
const PEEK_DELAY = 150; // ms the pointer rests on the edge before the sidebar slides out
const LEAVE_DELAY = 300; // ms after the pointer leaves before it slides away
const narrow = window.matchMedia("(max-width: 900px)");
let peekTimer = 0;

const panel = () => document.querySelector("#sidebar");
const edge = () => document.querySelector("[data-sidebar-edge]");
const isHidden = () => root.dataset.sidebar === "hidden";
export const isPeeking = () => document.body.classList.contains("sidebar-peek");

function syncToggle() {
  const button = document.querySelector("[data-sidebar-toggle]");
  if (!button) return;
  button.setAttribute("aria-expanded", String(!isHidden()));
  button.setAttribute("aria-label", isHidden() ? button.dataset.labelShow : button.dataset.labelHide);
}

export function closePeek(returnFocus = false) {
  clearTimeout(peekTimer);
  if (!isPeeking()) return;
  document.body.classList.remove("sidebar-peek");
  edge()?.setAttribute("aria-expanded", "false");
  if (returnFocus) edge()?.focus();
}

function openPeek(focusFirst) {
  clearTimeout(peekTimer);
  if (!isHidden() || narrow.matches) return;
  document.body.classList.add("sidebar-peek");
  edge()?.setAttribute("aria-expanded", "true");
  if (focusFirst) panel()?.querySelector("a[href]")?.focus();
}

function closeSoon() {
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => closePeek(), LEAVE_DELAY);
}

export function toggleSidebar() {
  if (narrow.matches) return;
  const hide = !isHidden(); // while peeking it is hidden, so this docks it
  if (hide) root.dataset.sidebar = "hidden";
  else delete root.dataset.sidebar;
  remember("localStorage", "codetrail.sidebar", hide ? "hidden" : null);
  closePeek();
  syncToggle();
}

function setUpPeek() {
  const strip = edge();
  const sidebar = panel();
  if (!strip || !sidebar) return;
  strip.addEventListener("pointerenter", () => {
    clearTimeout(peekTimer);
    peekTimer = setTimeout(() => openPeek(false), PEEK_DELAY);
  });
  strip.addEventListener("pointerleave", () => (isPeeking() ? closeSoon() : clearTimeout(peekTimer)));
  strip.addEventListener("click", () => (isPeeking() ? closePeek(true) : openPeek(true)));
  sidebar.addEventListener("pointerenter", () => clearTimeout(peekTimer));
  sidebar.addEventListener("pointerleave", () => isPeeking() && closeSoon());
  sidebar.addEventListener("focusout", (event) => {
    const next = event.relatedTarget;
    if (isPeeking() && next instanceof Node && !sidebar.contains(next) && next !== strip) closePeek();
  });
  sidebar.addEventListener("click", (event) => {
    if (isPeeking() && event.target instanceof Element && event.target.closest("a[href]")) closePeek();
  });
}

export function setUpSidebar() {
  document.querySelector("[data-sidebar-toggle]")?.addEventListener("click", toggleSidebar);
  syncToggle();
  setUpPeek();
}
```

`shortcuts.js`: imports `closePeek, isPeeking, toggleSidebar`; Escape closes the peek first (`if (isPeeking()) closePeek(true); else if (…menu-open…)`); the actions gain `"\\": () => toggleSidebar()`. `main.js` imports and runs `setUpSidebar()` before marking the page ready. `shortcuts.html` gains a row: `<kbd>\</kbd>` "Hide or show the sidebar".
- [ ] **Step 4: Run** `just test-browser -k "sidebar or keyboard or resize"`, see them pass; lint.
- [ ] **Step 5: Commit** `feat(web): hide the sidebar, and peek at it from the window's edge`.

### Task 4: folding and pinning in the browser

**Files:** `src/codetrail/web/static/js/sidebar.js`, `theme-init.js` (if not done in Task 3); tests in `tests/browser/test_sidebar.py`.

- [ ] **Step 1: Failing browser tests** (appended):

```python
def test_a_section_folds_and_stays_folded_but_shows_the_current_page(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/concepts/retries")
    page.locator('[data-fold="concepts"]').click()
    expect(page.locator('[data-fold="concepts"]')).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#nav-concepts a[href='/pages/concepts/ledger']")).to_be_hidden()
    expect(page.locator("#nav-concepts a[href='/pages/concepts/retries']")).to_be_visible()
    page.reload()
    ready(page)
    expect(page.locator("html")).to_have_attribute("data-folded", "concepts")
    expect(page.locator("#nav-concepts a[href='/pages/concepts/ledger']")).to_be_hidden()


def test_pinning_adds_a_tile_that_survives_a_reload_and_unpinning_removes_it(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    row = page.locator("#nav-concepts li", has=page.locator("a[href='/pages/concepts/retries']"))
    row.hover()
    row.locator("[data-pin]").click()
    tile = page.locator("[data-pins] a.pin[href='/pages/concepts/retries']")
    expect(tile).to_have_text("PR")
    expect(row.locator("[data-pin]")).to_have_attribute("aria-pressed", "true")
    page.reload()
    ready(page)
    expect(tile).to_have_attribute("aria-label", "Payment retries")
    tile.hover()
    page.locator("[data-pins] [data-unpin='concepts/retries']").click()
    expect(tile).to_have_count(0)
    page.reload()
    ready(page)
    expect(tile).to_have_count(0)


@pytest.mark.settings({"ui": {"max_pins": 1}})
def test_at_the_limit_the_pin_buttons_say_so(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    row = page.locator("#nav-areas li").first
    row.hover()
    row.locator("[data-pin]").click()
    other = page.locator("#nav-concepts [data-pin]").first
    expect(other).to_be_disabled()
    expect(other).to_have_attribute("title", "Unpin a page first")


def test_pinning_from_the_peek_keeps_it_open(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    page.mouse.move(3, 400)
    row = page.locator("#nav-concepts li").first
    row.hover()
    row.locator("[data-pin]").click()
    expect(page.locator("[data-pins] a.pin")).to_have_count(1)
    expect(page.locator("#sidebar")).to_be_visible()


def test_a_failed_pin_request_changes_nothing(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.route("**/settings/pins", lambda route: route.fulfill(status=403))
    row = page.locator("#nav-concepts li").first
    row.hover()
    row.locator("[data-pin]").click()
    expect(row.locator("[data-pin]")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("[data-pins] a.pin")).to_have_count(0)
```

- [ ] **Step 2: Run, see them fail.**
- [ ] **Step 3: Implement** in `sidebar.js`, called from `setUpSidebar()`:

```js
const SECTIONS = ["paths", "areas", "concepts", "answers", "more"];

function folded() {
  return (root.dataset.folded ?? "").split(" ").filter((name) => SECTIONS.includes(name));
}

function setUpFolding() {
  for (const button of document.querySelectorAll("[data-fold]")) {
    const name = button.dataset.fold;
    button.setAttribute("aria-expanded", String(!folded().includes(name)));
    button.addEventListener("click", () => {
      const now = folded();
      const next = now.includes(name) ? now.filter((other) => other !== name) : [...now, name];
      if (next.length) root.dataset.folded = next.join(" ");
      else delete root.dataset.folded;
      remember("localStorage", "codetrail.sidebar-folded", next.length ? next.join(" ") : null);
      button.setAttribute("aria-expanded", String(!next.includes(name)));
    });
  }
}

const pageHref = (id) => `/pages/${id.split("/").map(encodeURIComponent).join("/")}`;

function setUpPins() {
  const sidebar = panel();
  const list = sidebar?.querySelector("[data-pins]");
  const template = sidebar?.querySelector("[data-pin-template]");
  if (!sidebar || !list || !template) return;
  const limit = Number(sidebar.dataset.maxPins) || 1;
  const pinned = () => [...list.querySelectorAll("[data-unpin]")].map((button) => button.dataset.unpin);

  function sync(ids) {
    list.hidden = ids.length === 0;
    for (const button of sidebar.querySelectorAll("[data-pin]")) {
      const on = ids.includes(button.dataset.pin);
      button.setAttribute("aria-pressed", String(on));
      button.disabled = ids.length >= limit && !on;
      if (button.disabled) button.title = sidebar.dataset.labelFull;
      else button.removeAttribute("title");
    }
  }

  function tile(button) {
    const id = button.dataset.pin;
    const title = button.closest("li")?.querySelector(".label")?.textContent.trim() ?? id;
    const item = template.content.firstElementChild.cloneNode(true);
    const link = item.querySelector("a");
    link.href = pageHref(id);
    link.title = title;
    link.setAttribute("aria-label", title);
    link.textContent = button.dataset.initials;
    if (window.location.pathname === link.pathname) link.setAttribute("aria-current", "page");
    const remove = item.querySelector("button");
    remove.dataset.unpin = id;
    remove.setAttribute("aria-label", sidebar.dataset.labelUnpin.replace("{title}", title));
    return item;
  }

  async function save(ids) {
    try {
      return (await post("/settings/pins", { pages: ids })).ok;
    } catch {
      return false; // the sidebar stays as it was
    }
  }

  sidebar.addEventListener("click", async (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const pin = target?.closest("[data-pin]");
    const unpin = target?.closest("[data-unpin]");
    if (!pin && !unpin) return;
    const id = (pin ?? unpin).dataset[pin ? "pin" : "unpin"];
    const ids = pinned();
    const next = ids.includes(id) ? ids.filter((other) => other !== id) : [...ids, id];
    if (!(await save(next))) return;
    if (next.includes(id)) list.append(tile(pin));
    else list.querySelector(`[data-unpin="${CSS.escape(id)}"]`)?.closest("li")?.remove();
    sync(next);
    if (unpin) sidebar.querySelector("a[href]")?.focus();
  });
  for (const button of sidebar.querySelectorAll("[data-pin], [data-unpin]")) button.hidden = false;
}
```

(the `[data-unpin]` remove buttons of new tiles are visible since the template's button carries no `hidden`).
- [ ] **Step 4: Run** `just test-browser -k sidebar`, see them pass; lint.
- [ ] **Step 5: Commit** `feat(web): fold the sidebar's sections, and pin pages from it`.

### Task 5: language and accessibility

**Files:** catalogs (`just catalogs`, then the Arabic entries); `tests/fixtures/locales/ar/LC_MESSAGES/codetrail.po`; `tests/browser/test_languages.py`, `tests/browser/test_accessibility.py`.

- [ ] **Step 1: Failing tests.** In `test_languages.py`:

```python
def test_the_sidebar_sits_on_the_right_in_arabic(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})
    in_arabic(page, site, "/pages/areas/app")
    box = page.locator("#sidebar").bounding_box()
    assert box is not None and box["x"] > 640
    expect(page.locator("[data-sidebar-toggle]")).to_have_attribute("aria-label", "إخفاء الشريط الجانبي")
```

and in `test_accessibility.py`, a scan of `/pages/areas/app` hidden and peeking, in both themes:

```python
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("state", ["hidden", "peek"])
def test_the_sidebar_hidden_or_peeking(page: Page, site: Site, errors: list[str], scheme: str, state: str) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    page.set_viewport_size({"width": 1280, "height": 800})
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    if state == "peek":
        page.mouse.move(3, 400)
        page.locator("#sidebar").wait_for(state="visible")
    page.evaluate(AXE)
    result = page.evaluate(
        "async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
        ".filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id)"
    )
    assert result == []
    assert errors == []
```

The test catalog gains `msgid "Hide the sidebar"` / `msgstr "إخفاء الشريط الجانبي"`.
- [ ] **Step 2: Run, see the language test fail.**
- [ ] **Step 3: Translate.** `just catalogs`; Arabic entries, in the catalog's style: "Hide the sidebar" إخفاء الشريط الجانبي; "Show the sidebar" إظهار الشريط الجانبي; "Show the sidebar for a moment" إظهار الشريط الجانبي لحظة; "Pinned pages" الصفحات المثبّتة; "Pin %(title)s" تثبيت %(title)s; "Unpin %(title)s" إلغاء تثبيت %(title)s; "Unpin a page first" ألغِ تثبيت صفحة أولًا; "Hide or show the sidebar" إخفاء الشريط الجانبي أو إظهاره. Drop obsolete entries; compile; check with `pybabel compile --statistics` that nothing is untranslated or fuzzy.
- [ ] **Step 4: Run** `just test` and `just test-browser -k "language or accessibility or sidebar"`.
- [ ] **Step 5: Commit** `feat(web): translate the sidebar, and check it hidden and peeking`.

### Task 6: docs, the whole suite, review and finish

- [ ] README's status and phase table (Phase 19), the getting-started page's sidebar and shortcuts (**\\**), a CHANGELOG line under Unreleased; commit `docs(docs): describe the sidebar`.
- [ ] `just ci`; a whole-branch code review and the `security-reviewer` agent; fix every critical, high and important finding test-first; finish into `develop` with the review's verdict; report.
