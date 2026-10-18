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

  const activity = document.querySelector("[data-activity]");
  if (activity) {
    const controls = activity.querySelector("[data-activity-controls]");
    const from = activity.querySelector("[data-activity-from]");
    const to = activity.querySelector("[data-activity-to]");
    const feedback = activity.querySelector("[data-activity-feedback]");
    const jobs = Array.from(activity.querySelectorAll("[data-job-day]"));
    const operations = Array.from(activity.querySelectorAll("[data-operation-day]"));
    const labels = {queued: "Na fila", running: "Em execução", succeeded: "Concluídos", failed: "Falhos"};
    const number = new Intl.NumberFormat("pt-BR", {maximumFractionDigits: 1});

    function chartRow(label, values, maximum, description) {
      const row = document.createElement("div");
      row.className = "chart-row";
      const name = document.createElement("span");
      name.className = "chart-row-label";
      name.textContent = label;
      const track = document.createElement("span");
      track.className = "chart-row-track";
      track.setAttribute("aria-hidden", "true");
      for (const [state, value] of Object.entries(values)) {
        const bar = document.createElement("span");
        bar.className = `chart-segment chart-${state}`;
        bar.style.width = `${maximum ? 100 * value / maximum : 0}%`;
        track.append(bar);
      }
      const total = document.createElement("strong");
      total.className = "chart-row-value";
      total.textContent = description;
      row.append(name, track, total);
      return row;
    }

    function filterActivity(updateURL = false) {
      let error = "";
      if (!from.value || !to.value) error = "Preencha as duas datas em UTC.";
      else if ([from, to].some(input => input.value < input.min || input.value > input.max)) {
        const formatDay = day => day.split("-").reverse().join("/");
        error = from.min === to.max
          ? `O registro contém somente ${formatDay(from.min)} UTC.`
          : `Datas disponíveis: ${formatDay(from.min)} a ${formatDay(to.max)} UTC.`;
      } else if (from.value > to.value) error = "A data inicial deve ser anterior ou igual à final.";
      else if (!from.checkValidity() || !to.checkValidity()) error = "Informe datas válidas em UTC.";
      const valid = !error;
      from.setAttribute("aria-invalid", String(!valid));
      to.setAttribute("aria-invalid", String(!valid));
      if (!valid) {
        feedback.textContent = `Filtro não aplicado. ${error}`;
        return;
      }
      const visibleJobs = jobs.filter(row => {
        row.hidden = row.dataset.jobDay < from.value || row.dataset.jobDay > to.value;
        return !row.hidden;
      });
      const visibleOperations = operations.filter(row => {
        row.hidden = row.dataset.operationDay < from.value || row.dataset.operationDay > to.value;
        return !row.hidden;
      });
      const totals = Object.fromEntries(Object.keys(labels).map(state => [state, 0]));
      const days = new Map();
      for (const job of visibleJobs) {
        totals[job.dataset.jobState]++;
        const values = days.get(job.dataset.jobDay) || Object.fromEntries(Object.keys(labels).map(state => [state, 0]));
        values[job.dataset.jobState]++;
        days.set(job.dataset.jobDay, values);
      }
      for (const [state, value] of Object.entries(totals))
        activity.querySelector(`[data-state-total="${state}"]`).textContent = number.format(value);
      const jobChart = activity.querySelector("[data-job-chart]");
      const maximum = Math.max(1, ...Array.from(days.values(), counts => Object.values(counts).reduce((a, b) => a + b, 0)));
      jobChart.replaceChildren(...Array.from(days, ([day, counts]) => {
        const row = chartRow(day.slice(5).split("-").reverse().join("/"), counts, maximum,
          number.format(Object.values(counts).reduce((a, b) => a + b, 0)));
        row.setAttribute("aria-label", `${day} UTC. ${Object.entries(counts).map(([state, value]) => `${labels[state]}: ${value}`).join("; ")}`);
        row.title = row.getAttribute("aria-label");
        row.setAttribute("role", "img");
        return row;
      }));
      jobChart.hidden = visibleJobs.length === 0;
      const durationChart = activity.querySelector("[data-duration-chart]");
      const longest = Math.max(1, ...visibleOperations.map(row => Number(row.dataset.seconds)));
      durationChart.replaceChildren(...visibleOperations.map(row => chartRow(
        row.querySelector("th").textContent, {duration: Number(row.dataset.seconds)}, longest,
        `${number.format(Number(row.dataset.seconds))} s`)));
      durationChart.hidden = visibleOperations.length === 0;
      activity.querySelector("[data-jobs-empty]").hidden = visibleJobs.length > 0;
      activity.querySelector("[data-operations-empty]").hidden = visibleOperations.length > 0;
      feedback.textContent = `${visibleJobs.length} jobs únicos · ${visibleOperations.length} operações · ${from.value} a ${to.value}, UTC (inclusive).`;
      if (updateURL) {
        const url = new URL(location.href);
        url.searchParams.set("jobs_from", from.value);
        url.searchParams.set("jobs_to", to.value);
        history.replaceState(null, "", url);
      }
    }

    function restoreActivity() {
      const params = new URL(location.href).searchParams;
      for (const [input, key] of [[from, "jobs_from"], [to, "jobs_to"]]) {
        const requested = params.get(key);
        input.value = requested && /^\d{4}-\d{2}-\d{2}$/.test(requested) && requested >= input.min && requested <= input.max
          ? requested : input.defaultValue;
      }
      filterActivity();
    }

    if (jobs.length || operations.length) {
      controls.hidden = false;
      restoreActivity();
      from.addEventListener("change", () => filterActivity(true));
      to.addEventListener("change", () => filterActivity(true));
      controls.addEventListener("submit", event => { event.preventDefault(); filterActivity(true); });
      activity.querySelector("[data-activity-reset]").addEventListener("click", () => {
        from.value = from.defaultValue;
        to.value = to.defaultValue;
        filterActivity(true);
        from.focus();
      });
      window.addEventListener("popstate", restoreActivity);
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
