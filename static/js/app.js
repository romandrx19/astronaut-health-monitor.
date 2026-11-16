/* Frontend: consulta el backend, muestra estado, pinta el gráfico y controla la simulación. */
const INTERVAL_MS = 5000;
const METRICS = {
  heart_rate: ["Frecuencia cardíaca", "bpm"],
  spo2: ["Saturación de oxígeno", "%"],
  systolic: ["Presión sistólica", "mmHg"],
  diastolic: ["Presión diastólica", "mmHg"],
  glucose: ["Glucosa sanguínea", "mg/dL"],
  temperature: ["Temperatura", "°C"],
  sleep_hours: ["Sueño", "h"],
};
const state = { selectedId: 1, running: false, busy: false, crew: [], history: [], source: "", saving: false };
const medicalState = { astronautId: null, requestId: 0, controller: null, loading: false, saving: false, ready: false };
const assessmentState = { data: null, source: "", requestId: 0, controller: null, loading: false, error: "", historyRequestId: 0, openEvents: new Set() };
const ASSESSMENT_STATES = { normal: "✓ Normal según referencia", no_reference: "◇ Sin referencia personalizada",
  variation: "△ Variación detectada", review: "! Requiere revisión", priority: "! Atención prioritaria",
  insufficient: "◇ Datos insuficientes", stale: "◷ Lectura desactualizada", educational: "◇ Regla educativa del simulador", pending: "Evaluación pendiente" };
const EVENT_SOURCES = { individual_reference: "Referencia médica individual", trend_analysis: "Análisis de tendencia",
  simulation_rule: "Regla educativa del simulador", configured_protocol: "Protocolo configurado" };
let refreshRequestId = 0;
let manualRequest = null;
const medicalDrafts = new Map();
let historyRequestId = 0;
let deviceStatusRequestId = 0;
let devicePollTimer = null;
const $ = id => document.getElementById(id);

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Error HTTP ${response.status}`;
    try { detail = (await response.json()).error || detail; } catch (_) { }
    throw new Error(detail);
  }
  return response.json();
}

function setConnection(connected, message) {
  const element = $("connection");
  element.textContent = connected ? "● Conectado" : `● ${message || "Sin conexión"}`;
  element.classList.toggle("online", connected);
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = String(text);
  return element;
}

function displayTime(iso) {
  return iso ? new Date(iso).toLocaleTimeString("es-PE", { timeZone: "UTC", hour12: false }) : "—";
}

function sourceText(reading) {
  if (!reading) return "SIN REGISTROS";
  if (["simulation", "simulated"].includes(reading.source)) return reading.provenance === "adapter_test" ? "SIMULACIÓN · PRUEBA DE ADAPTADOR" : "SIMULACIÓN";
  if (reading.source === "manual") return reading.provenance === "imported" ? "IMPORTACIÓN · NO VERIFICADA" : "MANUAL · NO VALIDADO";
  if (reading.source === "smartwatch" && reading.provenance === "verified_device" && reading.device_id) return "SMARTWATCH · ADAPTADOR VERIFICADO";
  return "ORIGEN NO VERIFICADO";
}

function sourceColor(reading) {
  return reading.source === "manual" ? "#ffbc70" : reading.source === "smartwatch" && reading.provenance === "verified_device" ? "#5dddaf"
    : ["simulation", "simulated"].includes(reading.source) ? "#5dd9e8" : "#97aac7";
}

function updateReadingFreshness() {
  const latest = state.crew.find(a => a.id === state.selectedId)?.latest;
  $("reading-time").textContent = latest ? eventTime(latest.captured_at) : "—";
  const present = latest ? Object.keys(METRICS).filter(key => latest[key] != null).length : 0;
  $("reading-completeness").textContent = latest ? `${present}/7 indicadores registrados${present < 7 ? " · paquete parcial; no se completan los valores ausentes" : ""}.` : "";
  $("reading-observations").textContent = latest?.observations ? `Observaciones de la lectura: ${latest.observations}` : "";
  const age = latest ? (Date.now() - new Date(latest.captured_at).getTime()) / 1000 : NaN;
  const maximum = currentAssessment()?.rules?.max_age_seconds ?? 300;
  const stale = !Number.isFinite(age) || age < 0 || age > maximum;
  $("reading-freshness").textContent = !latest ? "Sin lectura" : stale ? "Lectura desactualizada o fecha inválida" : "Lectura reciente · sin validación clínica";
  $("reading-freshness").dataset.state = stale ? "stale" : "recent";
}

async function loadDeviceStatus() {
  const requestId = ++deviceStatusRequestId;
  try {
    const result = await api("/api/devices/status", { cache: "no-store" });
    if (requestId !== deviceStatusRequestId) return;
    const device = result.devices?.[0];
    TelemetryMonitor.setDevice(device);
    const confirmed = device?.state === "receiving_telemetry" && device.connected === true
      && device.telemetry_confirmed === true && device.verified_physical === true;
    const labels = { integration_unavailable: "Integración no disponible", ble_unavailable: "Bluetooth no disponible",
      disconnected: "Desconectado", scanning: "Buscando dispositivo", connecting: "Conectando",
      connected: "Conectado, sin telemetría confirmada", receiving_telemetry: "Recepción pendiente de verificar",
      reconnecting: "Reconectando", error: "Error de integración" };
    $("device-status").textContent = confirmed ? `${device.model}: Recibiendo telemetría` : `${device?.model || "Roadtrip Watch 4 JG74"}: ${labels[device?.state] || "Sin telemetría física confirmada"}. ${device?.message || "Estado pendiente de verificar"}`;
    $("device-status").dataset.state = confirmed ? "receiving" : "unavailable";
    $("device-verification").textContent = confirmed
      ? "Adaptador físico verificado, conexión y recepción confirmadas por el backend."
      : "Sin telemetría física verificada. El simulador y el registro manual tienen su propio origen.";
    $("device-connect").disabled = ["scanning", "connecting", "connected", "receiving_telemetry", "reconnecting"].includes(device?.state);
    $("device-disconnect").disabled = !["scanning", "connecting", "connected", "receiving_telemetry", "reconnecting"].includes(device?.state);
    globalThis.clearTimeout?.(devicePollTimer);
    if (globalThis.setTimeout && ["scanning", "connecting", "connected", "receiving_telemetry", "reconnecting"].includes(device?.state)) {
      devicePollTimer = globalThis.setTimeout(() => void loadDeviceStatus(), 2000);
    }
  } catch (error) {
    if (requestId !== deviceStatusRequestId) return;
    TelemetryMonitor.setDevice(null, true);
    $("device-status").textContent = `No se pudo consultar la integración del reloj: ${error.message}`;
    $("device-status").dataset.state = "unavailable";
    $("device-verification").textContent = "No se pudo verificar el estado actual del dispositivo.";
  }
}

async function connectDevice(event) {
  event.preventDefault();
  const message = $("device-action-message");
  const deviceId = $("device-id").value.trim();
  if (!deviceId) { message.textContent = "Introduce un identificador BLE o usa auto."; return; }
  $("device-connect").disabled = true;
  message.textContent = `Iniciando conexión para el astronauta ${state.selectedId}…`;
  try {
    const status = await api("/api/devices/connect", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: deviceId, astronaut_id: state.selectedId }) });
    message.textContent = status.message || "Conexión iniciada.";
    await loadDeviceStatus();
  } catch (error) {
    message.textContent = `No se pudo iniciar la conexión: ${error.message}`;
    $("device-connect").disabled = false;
  }
}

async function disconnectDevice() {
  const message = $("device-action-message");
  $("device-disconnect").disabled = true;
  try {
    const status = await api("/api/devices/disconnect", { method: "POST" });
    message.textContent = status.message || "JG74 desconectado.";
    await loadDeviceStatus();
  } catch (error) {
    message.textContent = `No se pudo desconectar: ${error.message}`;
  }
}

function renderSummary() {
  const normal = state.crew.filter(a => a.latest && a.alerts.length === 0).length;
  $("total-crew").textContent = state.crew.length;
  $("normal-count").textContent = normal;
  $("warning-count").textContent = state.crew.filter(a => a.alerts.length > 0).length;
  const latest = state.crew.map(a => a.latest?.captured_at).filter(Boolean).sort().at(-1);
  $("last-update").textContent = displayTime(latest);
}

function renderCrew() {
  const container = $("crew-cards");
  container.replaceChildren();
  for (const astronaut of state.crew) {
    const button = node("button", `crew-card ${astronaut.id === state.selectedId ? "selected" : ""}`);
    button.type = "button";
    button.disabled = state.saving || medicalState.saving || (typeof OctModule !== "undefined" && OctModule.saving);
    button.setAttribute("aria-pressed", String(astronaut.id === state.selectedId));
    const top = node("div", "crew-top");
    top.append(node("div", "avatar", astronaut.name.split(" ").map(s => s[0]).slice(0, 2).join("")));
    const info = node("div");
    info.append(node("div", "crew-name", astronaut.name), node("div", "crew-role", astronaut.role));
    top.append(info);
    const bottom = node("div", "crew-bottom");
    const isWarning = astronaut.alerts.length > 0;
    bottom.append(node("span", `status ${!astronaut.latest ? "muted" : isWarning ? "warning" : "ok"}`,
      !astronaut.latest ? "◇ Sin lecturas" : isWarning ? `⚠ ${astronaut.alerts.length} alerta(s) demo` : "◇ Sin alertas demo"),
      node("span", "muted", `${astronaut.latest?.heart_rate ?? "—"} bpm`));
    button.append(top, bottom);
    button.addEventListener("click", () => selectAstronaut(astronaut.id));
    container.append(button);
  }
}


async function selectAstronaut(id) {
  if (state.saving || medicalState.saving || (typeof OctModule !== "undefined" && OctModule.saving) || state.selectedId === id) return;
  const astronaut = state.crew.find(a => a.id === id);
  if (!astronaut) return;
  rememberMedicalDraft();
  state.selectedId = id;
  $("priority-detail").hidden = true;
  $("manual-form").reset();
  manualRequest = null;
  $("manual-message").classList.remove("error");
  $("manual-message").textContent = `Formulario listo para ${astronaut.name}. Los datos sin guardar se vaciaron al cambiar de astronauta.`;
  state.history = [];
  renderChart();
  renderCrew();
  renderDetail();
  await Promise.all([loadHistory(), loadMedicalProfile(), loadAssessment()]);
}


function renderDetail() {
  const astronaut = state.crew.find(
    a => a.id === state.selectedId
  );

  if (!astronaut) return;
  syncNavigationContext();
  $("manual-astronaut").textContent = astronaut.name;
  $("device-astronaut").textContent = `${astronaut.name} (ID ${astronaut.id})`;
  const sourceLabel = $("reading-source");
  sourceLabel.textContent = sourceText(astronaut.latest);
  sourceLabel.classList.toggle("manual", astronaut.latest?.source === "manual");
  updateReadingFreshness();
  $("detail-title").textContent = astronaut.name;
  TelemetryMonitor.render(astronaut.latest);
  renderAssessment();
}


function selectBodyMetric(metric) {

  // Validar indicador
  if (!METRICS[metric]) return;

  // Actualizar selector del historial
  $("metric-select").value = metric;

  // Resaltar el sensor seleccionado
  document.querySelectorAll(".body-point").forEach(point => {
    point.classList.toggle(
      "active",
      point.dataset.metric === metric
    );
  });

  // Resaltar tarjeta correspondiente
  document.querySelectorAll(".telemetry-metric").forEach(box => {
    box.classList.toggle(
      "active",
      box.dataset.metric === metric
    );
  });

  TelemetryMonitor.select(metric);

  // Actualizar gráfico
  renderChart();
  $("events-selected-only").checked = true;
  renderAssessment();
  if ($("health-event-history").open) void loadEventHistory();
}


function currentAssessment() {
  const data = assessmentState.data;
  const latest = state.crew.find(a => a.id === state.selectedId)?.latest;
  return data && data.astronaut_id === state.selectedId && assessmentState.source === state.source
    && data.measurement_id === (latest?.id ?? null) ? data : null;
}

function referenceText(reference, unit = "") {
  return reference ? `${reference.min_value}–${reference.max_value} ${unit}` : "Sin referencia personalizada";
}

function eventTime(iso) {
  const date = new Date(iso);
  return iso && Number.isFinite(date.getTime())
    ? `${date.toLocaleString("es-PE", { timeZone: "UTC", hour12: false })} UTC` : "Fecha no disponible";
}

function assessmentBadge(status) {
  const badge = node("span", "assessment-badge", ASSESSMENT_STATES[status] || ASSESSMENT_STATES.pending);
  badge.dataset.state = status;
  return badge;
}

function eventCard(item, historical = false) {
  const box = node("details", "assessment-event");
  box.dataset.state = item.severity;
  const key = `${historical ? "history-" : "current-"}${item.id || `${item.source}-${item.metric}`}`;
  box.open = assessmentState.openEvents.has(key);
  box.addEventListener("toggle", () => {
    if (box.open) assessmentState.openEvents.add(key); else assessmentState.openEvents.delete(key);
  });
  const summary = node("summary");
  summary.append(assessmentBadge(item.severity), node("strong", "", METRICS[item.metric]?.[0] || item.label));
  const unit = METRICS[item.metric]?.[1] || "";
  summary.append(node("span", "event-value", `${item.current_value} ${unit}`));
  box.append(summary);
  const content = node("div", "event-content");
  const reference = item.reference || (item.reference_min != null && item.reference_max != null
    ? { min_value: item.reference_min, max_value: item.reference_max } : null);
  content.append(node("p", "", item.source === "simulation_rule"
    ? "Regla educativa independiente: no utiliza referencias individuales."
    : `Referencia individual: ${referenceText(reference, unit)}`));
  if (item.comparison === "above" || item.comparison === "below") {
    content.append(node("p", "", `${item.difference ?? "Diferencia no representable"} ${unit} ${item.comparison === "above" ? "por encima del máximo" : "por debajo del mínimo"}.`));
  }
  content.append(node("p", "", item.description));
  const guidance = node("ul", "event-guidance");
  for (const instruction of item.guidance || []) guidance.append(node("li", "", instruction));
  content.append(guidance);
  const sources = item.sources || [item.source];
  content.append(node("p", "event-meta", `Fuente de evaluación: ${sources.map(s => EVENT_SOURCES[s] || s).join(" · ")}`),
    node("p", "event-meta", `Medición: ${eventTime(item.captured_at)} · Origen: ${item.measurement_source || currentAssessment()?.measurement_source || "—"}`));
  if (item.protocol) content.append(node("p", "event-meta", `Protocolo: ${item.protocol.title}. Referencia de validación configurada: ${item.protocol.validation_reference}`));
  if (historical) {
    const labels = { active: "Condición detectada", unverified: "Pendiente de verificar con datos actuales",
      resolved: "Condición ya no detectada", superseded: "Referencia modificada" };
    content.append(node("p", "event-meta", `${labels[item.status] || item.status} · Primera detección: ${eventTime(item.detected_at)} · Muestras evaluadas en este episodio: ${item.occurrences}`));
  }
  box.append(content);
  return box;
}

function renderAssessment() {
  const data = currentAssessment();
  const status = data?.state || "pending";
  const waiting = assessmentState.error || (assessmentState.loading ? "Cargando evaluación…" : "Evaluación pendiente.");
  const alerts = $("alerts");
  alerts.replaceChildren();
  alerts.setAttribute("aria-busy", String(assessmentState.loading));
  $("assessment-retry").hidden = !assessmentState.error;
  $("assessment-summary").textContent = data ? data.message : waiting;
  $("assessment-summary").dataset.state = status;
  $("body-status-light").classList.remove("warning");
  $("body-status-light").dataset.state = status;
  $("body-status-text").textContent = data ? `${data.state_label}${data.missing_references.length ? " · Referencias incompletas" : ""}` : waiting;

  for (const key of Object.keys(METRICS)) {
    const item = data?.metrics[key];
    if ($(`metric-state-${key}`)) $(`metric-state-${key}`).textContent = ASSESSMENT_STATES[item?.state || "pending"];
    if ($(`metric-reference-${key}`)) $(`metric-reference-${key}`).textContent = item && item.state !== "no_reference" ? referenceText(item.reference, item.unit) : "";
  }
  document.querySelectorAll(".telemetry-metric, .body-point").forEach(element => {
    const item = data?.metrics[element.dataset.metric];
    element.dataset.state = item?.state || "pending";
    element.classList.remove("warning");
    element.setAttribute("aria-label", `${METRICS[element.dataset.metric]?.[0]}: ${item?.current_value ?? "sin lectura"} ${item?.unit || ""}. ${ASSESSMENT_STATES[item?.state || "pending"]}. ${item ? referenceText(item.reference, item.unit) : ""}`);
  });
  const inspector = $("selected-assessment");
  inspector.replaceChildren();
  const key = $("metric-select").value;
  const selected = data?.metrics[key];
  if (selected) {
    inspector.append(node("strong", "", `${METRICS[key][0]}: ${selected.current_value ?? "—"} ${selected.unit}`),
      assessmentBadge(selected.state), node("p", "muted", `Referencia individual: ${referenceText(selected.reference, selected.unit)}`),
      node("p", "muted", selected.description),
      node("p", "muted", `Calidad de lectura: ${{ good: "informada como buena por el origen", poor: "baja; no evaluada", invalid: "inválida; no evaluada", unknown: "no informada" }[selected.quality || "unknown"]}.`),
      node("p", "muted", `${selected.trend.label} · ${selected.trend.sample_count} muestras · ${selected.trend.span_seconds} s de intervalo observado.`));
  } else inspector.append(node("p", "muted", waiting));

  renderControlAssessment(data, waiting);
  updatePriorityNotices(data);

  if (!data) {
    $("alert-count").textContent = "Sin evaluación actual";
    $("assessment-coverage").textContent = "No se puede afirmar un estado normal sin una evaluación actual.";
    return;
  }
  const missing = data.missing_references.map(metric => METRICS[metric][0]);
  const quality = Object.values(data.metrics).filter(item => ["stale", "insufficient"].includes(item.state));
  const insufficientTrends = Object.values(data.metrics).filter(item => item.trend.state !== "available").length;
  $("assessment-coverage").textContent = [missing.length ? `Sin referencia personalizada: ${missing.join(", ")}.` : "Siete referencias configuradas.",
    quality.length ? `Lecturas pendientes de verificar: ${quality.map(item => `${item.label} (${item.state_label})`).join(", ")}.` : "",
    insufficientTrends ? `Tendencias sin datos actuales suficientes: ${insufficientTrends} indicadores.` : ""].filter(Boolean).join(" ");
  const personal = data.events.filter(item => item.source !== "simulation_rule");
  const educational = data.events.filter(item => item.source === "simulation_rule");
  $("alert-count").textContent = `${personal.length} evento(s) · ${educational.length} demo`;
  const selectedOnly = $("events-selected-only").checked;
  for (const [items, title] of [[personal, "Evaluación individual y tendencias"], [educational, "Reglas educativas del simulador · independientes"]]) {
    const visible = items.filter(item => !selectedOnly || item.metric === key);
    if (!visible.length) continue;
    alerts.append(node("h3", "event-source-heading", title));
    for (const item of visible) alerts.append(eventCard(item));
  }
  if (!alerts.children.length) alerts.append(node("p", "muted", selectedOnly
    ? `Sin eventos actuales para ${METRICS[key][0]}. Consulta su estado y referencia en la telemetría.`
    : "No hay eventos actuales. Consulta la cobertura de referencias y la calidad de las lecturas."));
}

async function loadAssessment() {
  const id = state.selectedId, source = state.source;
  const requestId = ++assessmentState.requestId;
  assessmentState.controller?.abort();
  const controller = new AbortController(); assessmentState.controller = controller;
  assessmentState.loading = true; assessmentState.error = "";
  assessmentState.data = null;
  ++assessmentState.historyRequestId;
  $("event-history-list").replaceChildren();
  renderAssessment();
  try {
    const data = await api(`/api/astronauts/${id}/assessment${source ? `?source=${source}` : ""}`, { signal: controller.signal, cache: "no-store" });
    if (requestId !== assessmentState.requestId || id !== state.selectedId || source !== state.source) return;
    if (data.astronaut_id !== id || !data.metrics || !Array.isArray(data.events)) throw new Error("La evaluación no corresponde al astronauta seleccionado.");
    const latest = state.crew.find(a => a.id === id)?.latest;
    if ((latest?.id ?? null) !== data.measurement_id) throw new Error("La lectura cambió durante la evaluación. Vuelve a cargarla.");
    const sameSource = source === data.measurement_source || (["simulated", "simulation"].includes(source) && ["simulated", "simulation"].includes(data.measurement_source));
    if (source && data.measurement_source && !sameSource) throw new Error("El origen de la evaluación no coincide con el filtro.");
    assessmentState.data = data; assessmentState.source = source;
    if ($("health-event-history").open) await loadEventHistory();
  } catch (error) {
    if (requestId !== assessmentState.requestId || id !== state.selectedId || source !== state.source) return;
    assessmentState.error = `No se pudo evaluar: ${error.message}`;
  } finally {
    if (requestId === assessmentState.requestId) {
      assessmentState.loading = false; assessmentState.controller = null;
      renderAssessment();
    }
  }
}

async function loadEventHistory() {
  const id = state.selectedId, metric = $("events-selected-only").checked ? $("metric-select").value : "";
  const requestId = ++assessmentState.historyRequestId;
  const container = $("event-history-list");
  container.replaceChildren(node("p", "muted", "Cargando historial de eventos…"));
  try {
    const events = await api(`/api/astronauts/${id}/events?limit=50${metric ? `&metric=${metric}` : ""}`, { cache: "no-store" });
    if (requestId !== assessmentState.historyRequestId || id !== state.selectedId) return;
    if (!Array.isArray(events) || events.some(event => event.astronaut_id !== id)) throw new Error("El historial no corresponde al astronauta seleccionado.");
    container.replaceChildren();
    if (!events.length) container.append(node("p", "muted", "Sin eventos registrados para esta selección."));
    for (const event of events) container.append(eventCard(event, true));
  } catch (error) {
    if (requestId === assessmentState.historyRequestId && id === state.selectedId) container.replaceChildren(node("p", "event-error", error.message));
  }
}

async function loadHistory() {
  const requestId = ++historyRequestId;
  const id = state.selectedId;
  const source = state.source;
  try {
    const records = await api(`/api/astronauts/${id}/history?limit=36${source ? `&source=${source}` : ""}`);
    if (requestId !== historyRequestId || id !== state.selectedId || source !== state.source) return;
    if (!Array.isArray(records) || records.some(record => record.astronaut_id !== undefined && record.astronaut_id !== id)) {
      throw new Error("El historial no corresponde al astronauta seleccionado.");
    }
    state.history = records;
    renderChart();
  } catch (error) {
    if (requestId !== historyRequestId || id !== state.selectedId || source !== state.source) return;
    state.history = [];
    renderChart();
    TelemetryMonitor.renderHistory([], true);
    setConnection(false, error.message);
  }
}


async function refreshData() {
  const requestId = ++refreshRequestId;
  try {
    const source = state.source;
    const crew = await api(`/api/astronauts${source ? `?source=${source}` : ""}`);
    if (source !== state.source || requestId !== refreshRequestId) return;
    state.crew = crew;
    setConnection(true);
    renderSummary();
    renderCrew();
    renderDetail();
    // Solo la primera carga o la selección explícita consulta el expediente.
    // La actualización de telemetría nunca reemplaza lo escrito en el formulario.
    if (medicalState.astronautId === null) void loadMedicalProfile();
    await Promise.all([loadHistory(), loadAssessment()]);
    if (navigationState.view === "control") void loadDeviceStatus();
  } catch (error) {
    setConnection(false, error.message);
  }
}

async function simulateOnce(automatic = false) {
  if (state.busy || state.saving) return;
  state.busy = true;
  $("refresh").disabled = true;
  try {
    const result = await api("/api/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ automatic }) });
    SimulationControl.apply(result.simulation);
    await refreshData();
  } catch (error) {
    setConnection(false, error.message);
  } finally {
    state.busy = false;
    $("refresh").disabled = false;
  }
}

function renderChart() {
  TelemetryMonitor.renderHistory(state.history);
  const canvas = $("history-chart");
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(rect.width * dpr);
  canvas.height = Math.round(rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height;
  const pad = { left: 50, right: 18, top: 24, bottom: 32 };
  const key = $("metric-select").value;
  const points = state.history.map((record, index) => ({ record, index })).filter(({record}) =>
    typeof record[key] === "number" && Number.isFinite(record[key])
    && !["poor", "invalid"].includes(record.quality?.[key]) && Number.isFinite(new Date(record.captured_at).getTime()));
  const values = points.map(({record}) => record[key]);
  const rows = $("history-records"); rows.replaceChildren();
  for (const record of state.history.slice().reverse()) {
    const tr = node("tr");
    const quality = record.quality?.[key];
    tr.append(node("td", "", eventTime(record.captured_at)),
      node("td", "", sourceText(record)),
      node("td", "", record[key] == null ? "— · indicador no registrado" : `${record[key]} ${METRICS[key][1]}${["poor", "invalid"].includes(quality) ? " · baja calidad, no evaluado" : ""}`));
    rows.append(tr);
  }
  if (!values.length) { $("chart-caption").textContent = "Sin lecturas válidas del indicador para este origen"; return; }
  let min = Math.min(...values), max = Math.max(...values);
  const spread = Math.max(1, max - min);
  min -= spread * 0.18;
  max += spread * 0.18;
  const times = points.map(({record}) => new Date(record.captured_at).getTime());
  const toX = i => pad.left + (times[i] - times[0]) / Math.max(1, times.at(-1) - times[0]) * (w - pad.left - pad.right);
  const toY = v => pad.top + (max - v) / (max - min) * (h - pad.top - pad.bottom);
  ctx.font = "11px system-ui";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const value = min + (max - min) * i / 4;
    const y = toY(value);
    ctx.strokeStyle = "#293b52";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    ctx.fillStyle = "#98abc7";
    ctx.fillText(value.toFixed(key === "temperature" || key === "sleep_hours" ? 1 : 0), pad.left - 9, y + 4);
  }
  ctx.strokeStyle = "#5dd9e8";
  ctx.lineWidth = 2.4;
  ctx.beginPath();
  values.forEach((v, i) => i && points[i].index === points[i - 1].index + 1 ? ctx.lineTo(toX(i), toY(v)) : ctx.moveTo(toX(i), toY(v)));
  ctx.stroke();
  values.forEach((value, i) => {
    ctx.fillStyle = sourceColor(points[i].record);
    ctx.beginPath(); ctx.arc(toX(i), toY(value), 3.5, 0, Math.PI * 2); ctx.fill();
  });
  const last = values.length - 1;
  ctx.fillStyle = sourceColor(points[last].record);
  ctx.beginPath(); ctx.arc(toX(last), toY(values[last]), 4, 0, Math.PI * 2); ctx.fill();
  ctx.textAlign = "left";
  ctx.fillStyle = "#98abc7";
  ctx.fillText(displayTime(points[0].record.captured_at), pad.left, h - 7);
  ctx.textAlign = "right";
  ctx.fillText(displayTime(points[last].record.captured_at), w - pad.right, h - 7);
  $("chart-caption").textContent = `${METRICS[key][0]} (${METRICS[key][1]}) · ${values.length} lecturas válidas · ${state.history.length - values.length} sin dato utilizable · hora UTC`;
}

$("toggle").addEventListener("click", () => SimulationControl.toggle());
$("refresh").addEventListener("click", () => simulateOnce(false));

$("metric-select").addEventListener("change", () => {
  selectBodyMetric($("metric-select").value);
});

// Activar sensores de la silueta
document.querySelectorAll(".body-point").forEach(point => {

  point.addEventListener("click", () => {
    const metric = point.dataset.metric;
    selectBodyMetric(metric);
  });

});

function medicalMessage(message, status = "") {
  $("medical-message").textContent = message;
  $("medical-message").dataset.state = status;
}

function updateMedicalControls() {
  const locked = medicalState.loading || medicalState.saving || !medicalState.ready;
  $("medical-fields").disabled = locked;
  $("medical-save").disabled = locked || state.saving;
  $("medical-retry").disabled = medicalState.loading || medicalState.saving || state.saving;
  $("medical-form").setAttribute("aria-busy", String(medicalState.loading || medicalState.saving));
  $("manual-submit").disabled = state.saving || medicalState.saving;
  $("manual-fields").disabled = state.saving;
}

function initMedicalForm() {
  const container = $("medical-references");
  container.replaceChildren();
  for (const [key, [label, unit]] of Object.entries(METRICS)) {
    const group = node("fieldset", "medical-reference");
    group.append(node("legend", "", `${label} (${unit})`));
    const range = node("div", "medical-range");
    for (const [bound, caption] of [["min_value", "Mínimo"], ["max_value", "Máximo"]]) {
      const fieldLabel = node("label", "", caption);
      const input = node("input");
      input.id = `medical-${key}-${bound}`;
      input.name = `${key}-${bound}`;
      input.type = "number";
      input.step = "any";
      input.setAttribute("aria-label", `${label}: ${caption.toLowerCase()} (${unit})`);
      fieldLabel.htmlFor = input.id;
      fieldLabel.append(input);
      range.append(fieldLabel);
    }
    group.append(range);
    container.append(group);
  }
  $("medical-form").addEventListener("submit", saveMedicalProfile);
  $("medical-retry").addEventListener("click", loadMedicalProfile);
  updateMedicalControls();
}

function showMedicalProfile(profile) {
  $("medical-notes").value = profile.notes;
  for (const key of Object.keys(METRICS)) {
    for (const bound of ["min_value", "max_value"]) {
      $(`medical-${key}-${bound}`).value = profile.references[key]?.[bound] ?? "";
    }
  }
  $("medical-updated").textContent = profile.updated_at
    ? `Último guardado: ${new Date(profile.updated_at).toLocaleString("es-PE", { timeZone: "UTC" })} UTC.`
    : "Sin información registrada.";
  medicalState.savedForm = JSON.stringify(medicalSnapshot());
}

function medicalSnapshot() {
  return {
    notes: $("medical-notes").value,
    fields: Object.fromEntries(Object.keys(METRICS).map(key => [key, {
      min_value: $(`medical-${key}-min_value`).value,
      max_value: $(`medical-${key}-max_value`).value,
    }])),
  };
}

function rememberMedicalDraft() {
  if (!medicalState.ready || medicalState.loading || medicalState.astronautId === null) return;
  const draft = medicalSnapshot();
  if (JSON.stringify(draft) === medicalState.savedForm) medicalDrafts.delete(medicalState.astronautId);
  else medicalDrafts.set(medicalState.astronautId, draft);
}

function restoreMedicalDraft(id) {
  const draft = medicalDrafts.get(id);
  if (!draft) return false;
  $("medical-notes").value = draft.notes;
  for (const key of Object.keys(METRICS)) {
    for (const bound of ["min_value", "max_value"]) $(`medical-${key}-${bound}`).value = draft.fields[key][bound];
  }
  return true;
}

async function loadMedicalProfile() {
  if (medicalState.saving || state.saving) return;
  rememberMedicalDraft();
  const id = state.selectedId;
  const requestId = ++medicalState.requestId;
  medicalState.controller?.abort();
  const controller = new AbortController();
  medicalState.controller = controller;
  medicalState.astronautId = id;
  medicalState.loading = true;
  medicalState.ready = false;
  $("medical-form").reset();
  $("medical-name").textContent = state.crew.find(a => a.id === id)?.name || "—";
  $("medical-updated").textContent = "";
  $("medical-retry").hidden = true;
  updateMedicalControls();
  medicalMessage("Cargando expediente…");
  try {
    const profile = await api(`/api/astronauts/${id}/medical`, { signal: controller.signal });
    if (requestId !== medicalState.requestId || id !== state.selectedId) return;
    if (profile.astronaut_id !== id) throw new Error("El expediente recibido no corresponde al astronauta seleccionado.");
    showMedicalProfile(profile);
    medicalState.ready = true;
    const restored = restoreMedicalDraft(id);
    medicalMessage(restored ? "Borrador recuperado. Pulsa Guardar expediente para persistir los cambios." : profile.updated_at ? "Expediente cargado." : "Expediente vacío. Puedes registrar información de demostración.");
  } catch (error) {
    if (requestId !== medicalState.requestId || id !== state.selectedId) return;
    medicalMessage(`No se pudo cargar el expediente: ${error.message}`, "error");
    $("medical-retry").hidden = false;
  } finally {
    if (requestId === medicalState.requestId) {
      medicalState.loading = false;
      medicalState.controller = null;
      updateMedicalControls();
    }
  }
}

async function saveMedicalProfile(event) {
  event.preventDefault();
  if (medicalState.saving || medicalState.loading || !medicalState.ready) return;
  if (state.saving) {
    medicalMessage("Espera a que termine el guardado de la medición.", "error");
    return;
  }
  const id = medicalState.astronautId;
  if (id !== state.selectedId) return;
  const payload = { notes: $("medical-notes").value, references: {} };
  try {
    if (payload.notes.length > 20000) throw new Error("Las observaciones admiten hasta 20000 caracteres.");
    for (const [key, [label]] of Object.entries(METRICS)) {
      const low = $(`medical-${key}-min_value`);
      const high = $(`medical-${key}-max_value`);
      if (low.validity.badInput || high.validity.badInput) throw new Error(`${label}: introduce números válidos.`);
      const lowEmpty = low.value.trim() === "", highEmpty = high.value.trim() === "";
      if (lowEmpty && highEmpty) continue;
      if (lowEmpty || highEmpty) throw new Error(`${label}: completa tanto el mínimo como el máximo.`);
      const min_value = Number(low.value), max_value = Number(high.value);
      if (!Number.isFinite(min_value) || !Number.isFinite(max_value)) throw new Error(`${label}: los valores deben ser números finitos.`);
      if (min_value >= max_value) throw new Error(`${label}: el mínimo debe ser menor que el máximo.`);
      payload.references[key] = { min_value, max_value };
    }
  } catch (error) {
    medicalMessage(error.message, "error");
    return;
  }

  medicalState.saving = true;
  updateMedicalControls();
  renderCrew();
  medicalMessage("Guardando expediente…");
  try {
    const profile = await api(`/api/astronauts/${id}/medical`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    });
    if (id !== state.selectedId || id !== medicalState.astronautId) return;
    if (profile.astronaut_id !== id) throw new Error("La respuesta no corresponde al astronauta seleccionado.");
    showMedicalProfile(profile);
    medicalDrafts.delete(id);
    medicalMessage("Expediente guardado correctamente.", "success");
    await loadAssessment();
  } catch (error) {
    medicalMessage(`No se pudo guardar: ${error.message}. Tus cambios siguen en el formulario.`, "error");
  } finally {
    medicalState.saving = false;
    updateMedicalControls();
    renderCrew();
  }
}

window.addEventListener("resize", renderChart);
window.addEventListener("beforeunload", event => {
  rememberMedicalDraft();
  if (medicalDrafts.size) { event.preventDefault(); event.returnValue = ""; }
});
$("assessment-retry").addEventListener("click", refreshData);
$("events-selected-only").addEventListener("change", () => {
  renderAssessment();
  if ($("health-event-history").open) void loadEventHistory();
});
$("health-event-history").addEventListener("toggle", () => {
  if ($("health-event-history").open) void loadEventHistory();
});
TelemetryMonitor.init();
initMedicalForm();
if (typeof OctModule !== "undefined") OctModule.init();
initNavigation();
$("device-connect-form").addEventListener("submit", connectDevice);
$("device-disconnect").addEventListener("click", disconnectDevice);
loadDeviceStatus();
refreshData();
void SimulationControl.refresh().catch(error => setConnection(false, error.message));
setInterval(() => {
  updateReadingFreshness();
  void SimulationControl.poll();
}, INTERVAL_MS);
// El filtro afecta al panel y al historial; no altera los datos almacenados.
$("source-filter").addEventListener("change", async () => {
  state.source = $("source-filter").value;
  if (["manual", "smartwatch"].includes(state.source)) {
    try { await SimulationControl.setRunning(false); }
    catch (error) { setConnection(false, error.message); }
  }
  state.history = []; renderChart();
  await refreshData();
});
async function saveManualMeasurement(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const message = $("manual-message");
  if (state.saving || state.busy || medicalState.saving) {
    message.textContent = "Espera a que termine la operación actual y vuelve a guardar.";
    return;
  }
  const id = state.selectedId;
  const entries = Object.fromEntries(new FormData(form).entries());
  const payload = {};
  try {
    for (const key of Object.keys(METRICS)) {
      const raw = String(entries[key] ?? "").trim();
      const field = $(`manual-${key}`);
      if (field.validity.badInput) throw new Error(`${METRICS[key][0]}: introduce un número válido.`);
      if (!raw) continue;
      const value = Number(raw);
      if (!Number.isFinite(value) || (field.min !== undefined && field.min !== "" && value < Number(field.min))
          || (field.max !== undefined && field.max !== "" && value > Number(field.max))
          || (!["temperature", "sleep_hours"].includes(key) && !Number.isInteger(value))) throw new Error(`${METRICS[key][0]}: revisa los límites técnicos del campo.`);
      payload[key] = value;
    }
    if (!Object.keys(payload).length) throw new Error("Introduce al menos un indicador; los campos vacíos no se guardan como cero.");
    if (payload.systolic !== undefined && payload.diastolic !== undefined && payload.systolic <= payload.diastolic) throw new Error("La presión sistólica debe superar a la diastólica.");
    if (entries.acquired_at) {
      const acquired = new Date(entries.acquired_at);
      if (!Number.isFinite(acquired.getTime()) || acquired.getTime() > Date.now()) throw new Error("Revisa la fecha de adquisición; no puede estar en el futuro.");
      payload.acquired_at = acquired.toISOString();
    }
    if (entries.observations) payload.observations = entries.observations;
  } catch (error) {
    message.classList.add("error"); message.textContent = error.message; return;
  }
  const fingerprint = JSON.stringify({ astronaut_id: id, ...payload });
  if (!manualRequest || manualRequest.fingerprint !== fingerprint) manualRequest = {
    fingerprint, key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`,
  };
  state.saving = true;
  updateMedicalControls();
  renderCrew();
  $("source-filter").disabled = true;
  message.classList.remove("error");
  message.textContent = "Guardando…";
  try {
    const result = await api(`/api/astronauts/${id}/measurements/manual`, {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": manualRequest.key }, body: JSON.stringify(payload)
    });
    let pauseNote = "Simulación pausada en todas las ventanas.";
    try { await SimulationControl.setRunning(false); }
    catch (error) { pauseNote = `Medición guardada. No se pudo confirmar la pausa global: ${error.message}`; }
    state.source = "manual"; $("source-filter").value = "manual";
    state.history = []; renderChart();
    message.textContent = `${result.created === false ? "La medición ya estaba registrada; no se duplicó." : "Medición registrada."} ${pauseNote}${result.assessment && result.assessment.measurement_id !== result.measurement.id ? " La telemetría conserva la lectura más reciente por fecha de adquisición." : ""}`;
    manualRequest = null;
    form.reset();
    await refreshData();
  } catch (error) {
    message.classList.add("error"); message.textContent = error.message;
  } finally {
    state.saving = false; $("source-filter").disabled = false;
    updateMedicalControls();
    renderCrew();
  }
}

$("manual-form").addEventListener("submit", saveManualMeasurement);
