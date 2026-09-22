(() => {
  "use strict";
  const panels = Array.from(document.querySelectorAll("[data-panel]"));
  const links = Array.from(document.querySelectorAll("[data-operation]"));
  const picker = document.querySelector(".operation-picker");
  if (!panels.length || !picker) return;

  function select(focus) {
    const target = document.getElementById(window.location.hash.slice(1));
    const panel = target?.closest("[data-panel]") || panels[0];
    for (const candidate of panels) candidate.hidden = candidate !== panel;
    for (const link of links) {
      if (link.dataset.operation === panel.id)
        link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    if (window.matchMedia("(max-width: 760px)").matches) picker.open = false;
    if (focus) {
      panel.querySelector("h2")?.focus({ preventScroll: true });
      panel.scrollIntoView({ block: "start" });
    }
  }

  select(false);
  window
    .matchMedia("(max-width: 760px)")
    .addEventListener("change", (event) => {
      picker.open = !event.matches;
    });
  window.addEventListener("hashchange", () => select(true));
  for (const link of links)
    link.addEventListener("click", () => {
      if (link.hash === window.location.hash) select(true);
    });
  window.addEventListener("beforeprint", () => {
    for (const panel of panels) panel.hidden = false;
  });
  window.addEventListener("afterprint", () => select(false));
})();
