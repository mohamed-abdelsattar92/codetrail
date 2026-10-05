// Applies the reader's chosen theme before the page is drawn, so a dark page never flashes light (design 16.2).
// A classic script, loaded in <head> from Codetrail itself: the security policy allows no inline script.
(function () {
  "use strict";
  try {
    var theme = window.localStorage.getItem("codetrail.theme");
    if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
  } catch (error) {
    // storage refused: the system's theme applies
  }
})();
