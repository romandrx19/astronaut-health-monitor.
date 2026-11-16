/* Navegación y resúmenes de presentación. La evaluación sigue a cargo del backend. */
const MODULES = {
  control: ["Centro de control", "control-title"],
  history: ["Historial y análisis", "history-title"],
  medical: ["Expedientes médicos", "medical-title"],
  manual: ["Registro manual", "manual-title"],
  devices: ["Dispositivos", "devices-title"],
  events: ["Historial de eventos", "events-title"],
  oct: ["Salud ocular — OCT", "oct-title"],
  research: ["Investigación NASA", "research-title"],
};
const navigationState = { view: "control", prioritySignature: "" };
const priorityNotices = new Map();

function closeModuleMenu(restoreFocus = true) {
  $("module-menu").close();
  $("menu-toggle").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("menu-toggle").focus();
}

function showView(key, { focus = true, history = true } = {}) {
  if (!MODULES[key]) return;
  for (const module of Object.keys(MODULES)) {
    $(`view-${module}`).hidden = module !== key;
    if (module === key) $(`nav-${module}`).setAttribute("aria-current", "page");
    else $(`nav-${module}`).removeAttribute("aria-current");
  }
  navigationState.view = key;
  document.body.dataset.view = key;
  $("current-module").textContent = MODULES[key][0];
  $("workspace-title").textContent = key === "research" ? "Investigación científica" : "Salud de la tripulación";
  $("crew-context").hidden = key === "research";
  $("toggle").hidden = key === "research";
  $("refresh").hidden = key === "research";
  $("simulation-status").textContent = key === "research" ? "Archivo histórico · Sin actualización en tiempo real"
    : state.running ? "Simulación activa · cada 5 s" : "Simulación pausada · consulta de datos activa";
  $("control-home").hidden = key === "control";
  if ($("module-menu").open) closeModuleMenu(false);
  if (focus) $(MODULES[key][1]).focus();
  if (key === "history") renderChart(); // Canvas mide la vista una vez visible.
  if (key === "devices") void loadDeviceStatus();
  if (key === "oct" && typeof OctModule !== "undefined") void OctModule.show();
  if (key === "research" && typeof ResearchModule !== "undefined") void ResearchModule.show();
  if (key === "events" && history) {
    if ($("health-event-history").open) void loadEventHistory();
    else $("health-event-history").open = true; // El evento toggle carga los datos.
  }
}

function syncNavigationContext() {
  const name = state.crew.find(a => a.id === state.selectedId)?.name || "—";
  $("selected-crew-name").textContent = name;
  $("history-name").textContent = name;
  if (typeof OctModule !== "undefined") OctModule.syncContext();
  $("simulation-status").textContent = navigationState.view === "research" ? "Archivo histórico · Sin actualización en tiempo real" : state.running
    ? "Simulación activa · cada 5 s" : "Simulación pausada · consulta de datos activa";
}

function renderControlAssessment(data, waiting) {
  const metric = $("metric-select").value;
  const selected = data?.metrics[metric];
  $("selected-metric-summary").textContent = selected
    ? `${METRICS[metric][0]} · ${ASSESSMENT_STATES[selected.state] || selected.state}` : waiting;
  $("assessment-overview").textContent = data?.state_label || waiting;
  $("assessment-overview").dataset.state = data?.state || "pending";
  const unavailable = Object.values(data?.metrics || {}).filter(item => ["stale", "insufficient"].includes(item.state)).length;
  $("assessment-coverage-summary").textContent = data
    ? `${data.missing_references.length}/7 sin referencia · ${unavailable}/7 lecturas no evaluables.`
    : "Sin evaluación actual; no se puede afirmar normalidad.";
  const events = [...(data?.events || [])].sort((a, b) =>
    ({ priority: 0, review: 1, variation: 2, educational: 3 }[a.severity] ?? 4)
    - ({ priority: 0, review: 1, variation: 2, educational: 3 }[b.severity] ?? 4));
  $("active-alert-count").textContent = data ? `${events.length} evento(s)` : "Pendiente";
  const container = $("active-alerts");
  container.replaceChildren();
  for (const item of events) {
    const card = node("article", "active-event"); card.dataset.state = item.severity;
    card.append(assessmentBadge(item.severity),
      node("strong", "", `${METRICS[item.metric]?.[0] || item.label}: ${item.current_value ?? "—"} ${METRICS[item.metric]?.[1] || ""}`),
      node("p", "", item.description));
    if (item.guidance?.length) card.append(node("p", "event-brief-guidance", item.guidance[0]));
    const details = node("button", "secondary", "Ver explicación"); details.type = "button";
    details.addEventListener("click", () => {
      assessmentState.openEvents.add(`current-${item.id || `${item.source}-${item.metric}`}`);
      selectBodyMetric(item.metric);
      showView("events", { history: false });
    });
    card.append(details); container.append(card);
  }
  if (!events.length) container.append(node("p", "muted", data
    ? "Sin eventos actuales. Revisa la cobertura de referencias antes de interpretar el estado." : waiting));
}

function updatePriorityNotices(data) {
  // Solo se muestran prioridades que ya emitió la evaluación del servidor.
  // Una carga, error, filtro distinto o lectura no evaluable no las resuelve.
  const crewName = state.crew.find(a => a.id === state.selectedId)?.name || "Astronauta";
  const canonicalSource = source => ["simulated", "simulation"].includes(source) ? "simulation" : source;
  if (data) {
    const priorities = data.events.filter(item => item.severity === "priority");
    for (const [key, notice] of priorityNotices) {
      if (notice.astronautId !== data.astronaut_id || canonicalSource(notice.source) !== canonicalSource(data.measurement_source)) continue;
      const metricState = data.metrics[notice.item.metric]?.state;
      if (metricState && !["stale", "insufficient", "pending"].includes(metricState)
          && !priorities.some(item => item.metric === notice.item.metric)) priorityNotices.delete(key);
    }
    for (const item of priorities) priorityNotices.set(`${data.astronaut_id}:${canonicalSource(data.measurement_source)}:${item.metric}`, {
      astronautId: data.astronaut_id, name: crewName, source: data.measurement_source, item,
    });
  }
  const signature = JSON.stringify([...priorityNotices].map(([key, notice]) =>
    [key, notice.item.current_value, notice.item.captured_at, notice.item.description]));
  $("priority-banner").hidden = priorityNotices.size === 0;
  if (signature === navigationState.prioritySignature) return;
  navigationState.prioritySignature = signature;
  const focusedId = document.activeElement?.id;
  const container = $("priority-list"); container.replaceChildren();
  let index = 0;
  for (const [key, notice] of priorityNotices) {
    const button = node("button", "secondary", `${notice.name} · ${METRICS[notice.item.metric]?.[0]} · ${notice.item.current_value} ${METRICS[notice.item.metric]?.[1] || ""} · Ver detalles`);
    button.id = `priority-notice-${index++}`; button.type = "button";
    button.addEventListener("click", async () => {
      if (notice.astronautId !== state.selectedId) {
        await selectAstronaut(notice.astronautId);
        if (notice.astronautId !== state.selectedId) return;
      }
      const detail = $("priority-detail"); detail.hidden = false;
      detail.replaceChildren(node("p", "muted", `Última prioridad confirmada · ${notice.name} · ${eventTime(notice.item.captured_at)} · ${notice.source}. Comprueba la evaluación actual; esta evidencia se conserva durante cargas, errores y lecturas no evaluables.`), eventCard(notice.item));
      detail.children[1].open = true;
      selectBodyMetric(notice.item.metric);
      showView("events", { history: false });
    });
    container.append(button);
  }
  container.append(node("small", "", "Última evaluación confirmada. Se conserva hasta una nueva evaluación válida del mismo origen; consulta los detalles y la fecha."));
  if (focusedId && $(focusedId)) $(focusedId).focus();
}

function initNavigation() {
  $("menu-toggle").addEventListener("click", () => {
    $("module-menu").showModal();
    $("menu-toggle").setAttribute("aria-expanded", "true");
    $(`nav-${navigationState.view}`).focus();
  });
  $("menu-close").addEventListener("click", () => closeModuleMenu());
  $("module-menu").addEventListener("cancel", event => { event.preventDefault(); closeModuleMenu(); });
  $("module-menu").addEventListener("click", event => {
    // Solo el fondo exterior del diálogo, no sus espacios internos.
    if (event.target !== $("module-menu") || !Number.isFinite(event.clientX)) return;
    const box = $("module-menu").getBoundingClientRect();
    if (event.clientX > box.right || event.clientX < box.left) closeModuleMenu();
  });
  const keys = Object.keys(MODULES);
  for (const key of keys) $(`nav-${key}`).addEventListener("click", () => showView(key));
  $("module-menu").addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const index = keys.findIndex(key => document.activeElement === $(`nav-${key}`));
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? keys.length - 1
      : (index + (event.key === "ArrowDown" ? 1 : -1) + keys.length) % keys.length;
    $(`nav-${keys[next]}`).focus();
  });
  for (const [id, key] of [["control-home", "control"], ["open-manual", "manual"],
    ["open-history", "history"], ["open-devices", "devices"], ["open-event-history", "events"]]) {
    $(id).addEventListener("click", () => showView(key));
  }
  $("open-event-details").addEventListener("click", () => showView("events", { history: false }));
  $("device-refresh").addEventListener("click", loadDeviceStatus);
  syncNavigationContext();
  showView("control", { focus: false });
}
