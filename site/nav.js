/* Mobile nav toggle for the shared topbar (index / analytics / teams).
   The MENU button is hidden on desktop (CSS) and, on viewports <= 760px, reveals
   .mainnav as a full-width dropdown panel below the sticky topbar. Lives in its
   own file because the topbar is shared across pages that each load a different
   page script (app.js / analytics.js / inline), so the toggle can't live in any
   one of them. */
(function () {
  function init() {
    var btn = document.querySelector('.topbar .icon-btn[aria-label="Menu"]');
    var nav = document.querySelector(".topbar .mainnav");
    if (!btn || !nav) return;

    if (!nav.id) nav.id = "mainnav";
    btn.setAttribute("aria-controls", nav.id);
    btn.setAttribute("aria-expanded", "false");

    function setOpen(open) {
      nav.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", String(open));
    }

    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      setOpen(!nav.classList.contains("open"));
    });

    // Tapping a link navigates; close the menu behind it.
    nav.addEventListener("click", function (e) {
      if (e.target.closest("a")) setOpen(false);
    });

    // Click outside or Escape closes it.
    document.addEventListener("click", function (e) {
      if (
        nav.classList.contains("open") &&
        !nav.contains(e.target) &&
        !btn.contains(e.target)
      ) {
        setOpen(false);
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setOpen(false);
    });

    // Returning to desktop width should clear the mobile-open state.
    window.addEventListener("resize", function () {
      if (window.innerWidth > 760 && nav.classList.contains("open")) setOpen(false);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
