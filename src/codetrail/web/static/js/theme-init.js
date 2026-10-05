// Applies the reader's theme and panel widths before the page is drawn, so nothing flashes or jumps (design 16.1, 16.2).
// A classic script, loaded in <head> from Codetrail itself: the security policy allows no inline script.
// resize.js fits the widths to the window once the page has loaded.
(function () {
  "use strict";
  var root = document.documentElement;
  var widths = [
    ["codetrail.sidebar-width", "--sidebar-width", 200, 420],
    ["codetrail.panel-width", "--panel-width", 320, 720],
  ];
  try {
    var theme = window.localStorage.getItem("codetrail.theme");
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    for (var index = 0; index < widths.length; index += 1) {
      var width = Number(window.localStorage.getItem(widths[index][0]));
      if (width >= widths[index][2] && width <= widths[index][3]) root.style.setProperty(widths[index][1], width + "px");
    }
  } catch (error) {
    // storage refused: the system's theme and the default widths apply
  }
})();
