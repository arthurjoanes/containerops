(() => {
  "use strict";
  const panels = Array.from(document.querySelectorAll("[data-panel]"));
  const links = Array.from(document.querySelectorAll("[data-operation]"));
  const picker = document.querySelector(".operation-picker");
  if (!panels.length || !picker) return;

  const content = document.getElementById("content");
  document.querySelector(".skip-link")?.addEventListener("click", (event) => {
    if (!content) return;
    event.preventDefault();
    content.focus({ preventScroll: true });
    content.scrollIntoView({ block: "start" });
  });

  function select(focus) {
    const target = document.getElementById(window.location.hash.slice(1));
    const panel = target?.closest("[data-panel]") || panels[0];
    for (const candidate of panels) candidate.hidden = candidate !== panel;
    for (const link of links) {
      if (link.dataset.operation === panel.id)
        link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    const label = picker.querySelector("[data-operation-label]");
    if (label) label.textContent = "Operação: " + panel.querySelector("h2").textContent;
    if (window.matchMedia("(max-width: 760px)").matches) picker.open = false;
    if (focus) {
      const destination = target !== panel && target?.hasAttribute("tabindex") && !target.hidden
        ? target : panel.querySelector("h2");
      destination?.focus({ preventScroll: true });
      (destination || panel).scrollIntoView({ block: "start" });
    }
  }

  const form = document.querySelector("[data-catalog-controls]");
  const search = document.querySelector("[data-catalog-search]");
  const restore = document.querySelector("[data-catalog-restore]");
  const rows = Array.from(document.querySelectorAll("[data-backup-row]"));
  const count = document.querySelector("[data-catalog-status]");
  const empty = document.querySelector("[data-catalog-no-results]");
  const normalize = (value) => value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();

  function filterCatalog(updateURL = false) {
    if (!search || !restore || !count) return;
    const query = normalize(search.value.trim());
    let visible = 0;
    for (const row of rows) {
      row.hidden = !normalize(row.dataset.backupSearch).includes(query) ||
        (restore.value !== "all" && row.dataset.backupRestore !== restore.value);
      if (!row.hidden) visible++;
    }
    count.textContent = `${visible} de ${rows.length} cópias no registro. Mais recentes primeiro.`;
    if (empty) empty.hidden = visible > 0;
    if (updateURL) {
      const url = new URL(location.href);
      if (search.value.trim()) url.searchParams.set("copy", search.value.trim());
      else url.searchParams.delete("copy");
      if (restore.value !== "all") url.searchParams.set("receipt", restore.value);
      else url.searchParams.delete("receipt");
      history.replaceState(null, "", url);
    }
  }

  function restoreFilters() {
    if (!search || !restore) return;
    const params = new URL(location.href).searchParams;
    search.value = (params.get("copy") || "").slice(0, 128);
    const state = params.get("receipt") || "all";
    restore.value = ["all", "verified", "unverified", "unknown"].includes(state) ? state : "all";
    filterCatalog();
  }

  if (form && search && restore) {
    form.hidden = false;
    restoreFilters();
    form.addEventListener("submit", (event) => { event.preventDefault(); filterCatalog(true); });
    search.addEventListener("input", () => filterCatalog(true));
    restore.addEventListener("change", () => filterCatalog(true));
    document.querySelector("[data-catalog-reset]")?.addEventListener("click", () => {
      search.value = "";
      restore.value = "all";
      filterCatalog(true);
      search.focus();
    });
    window.addEventListener("popstate", restoreFilters);
  }

  for (const button of document.querySelectorAll("[data-copy-command]")) {
    const command = document.getElementById(button.dataset.commandTarget);
    if (!command) continue;
    button.hidden = false;
    button.addEventListener("click", async () => {
      const feedback = button.closest(".code-block").querySelector(".copy-feedback");
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
        await navigator.clipboard.writeText(command.textContent);
        feedback.textContent = "Comando copiado. Confira a sessão e a origem antes de executá-lo.";
      } catch {
        command.focus();
        const selection = getSelection();
        const range = document.createRange();
        range.selectNodeContents(command);
        selection.removeAllRanges();
        selection.addRange(range);
        feedback.textContent = "Não foi possível copiar. O texto está selecionado; use Ctrl+C ou o menu do navegador.";
      }
    });
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
  const printedDetails = new Map();
  window.addEventListener("beforeprint", () => {
    for (const panel of panels) panel.hidden = false;
    for (const detail of document.querySelectorAll(".record-details,.checksum")) {
      printedDetails.set(detail, detail.open);
      detail.open = true;
    }
  });
  window.addEventListener("afterprint", () => {
    for (const [detail, open] of printedDetails) detail.open = open;
    printedDetails.clear();
    select(false);
  });
})();
