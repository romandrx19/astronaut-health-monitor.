/* Presentación del panel de telemetría a partir de las lecturas y estados existentes. */
const TelemetryMonitor = (() => {
  const svgNamespace = "http://www.w3.org/2000/svg";
  const leftMetrics = ["heart_rate", "spo2", "systolic", "diastolic"];
  const rightMetrics = ["glucose", "temperature", "sleep_hours"];
  const iconPaths = {
    heart_rate: "M2 12h4l3-7 4 14 3-7h6",
    spo2: "M12 3C9 7 5 11 5 15a7 7 0 0 0 14 0c0-4-4-8-7-12ZM8 16c0 2 1 3 3 3",
    systolic: "M4 18V6m0 0L1 9m3-3 3 3M9 17h3l2-6 3 3h5",
    diastolic: "M4 6v12m0 0-3-3m3 3 3-3M9 17h3l2-6 3 3h5",
    glucose: "M12 3C9 7 5 11 5 15a7 7 0 0 0 14 0c0-4-4-8-7-12ZM9 15h6m-3-3v6",
    temperature: "M10 14V5a2 2 0 0 1 4 0v9a5 5 0 1 1-4 0Zm2-7v10m5-9h3m-3 4h2",
    sleep_hours: "M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z",
  };
  const cards = new Map();
  const connectors = new Map();
  let history = [];
  let latest = null;
  let device = null;
  let deviceError = false;
  let historyFailed = false;
  let initialized = false;
  let layoutFrame = null;

  function svgElement(tag, attributes) {
    const element = document.createElementNS(svgNamespace, tag);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    return element;
  }

  function createCard(metric, parent) {
    const [name, unit] = METRICS[metric];
    const label = { temperature: "Temperatura corporal", sleep_hours: "Horas de sueño" }[metric] || name;
    const card = node("button", "telemetry-metric");
    card.type = "button"; card.dataset.metric = metric;
    card.dataset.state = "pending";
    const heading = node("span", "metric-heading");
    const icon = svgElement("svg", { viewBox: "0 0 24 24", class: "metric-icon", "aria-hidden": "true", focusable: "false", fill: "none", stroke: "currentColor", "stroke-width": "1.5", "stroke-linecap": "round", "stroke-linejoin": "round" });
    icon.append(svgElement("path", { d: iconPaths[metric] }));
    heading.append(icon, node("span", "metric-label", label));
    const reading = node("span", "metric-reading");
    const value = node("strong", "metric-value", "—");
    reading.append(value, node("small", "metric-unit", unit));
    const graph = node("span", "metric-sparkline-wrap");
    const canvas = node("canvas", "metric-sparkline");
    canvas.id = `telemetry-chart-${metric}`;
    canvas.setAttribute("role", "img");
    const caption = node("small", "metric-history-state", "Sin historial");
    graph.append(canvas, caption);
    const assessment = node("small", "metric-assessment", ASSESSMENT_STATES.pending); assessment.id = `metric-state-${metric}`;
    const reference = node("small", "metric-reference"); reference.id = `metric-reference-${metric}`;
    card.append(heading, reading, graph, assessment, reference);
    card.addEventListener("click", () => selectBodyMetric(metric));
    parent.append(card);
    cards.set(metric, { card, value, canvas, caption });
    const path = svgElement("path", { class: "telemetry-connector", "data-metric": metric });
    $("telemetry-connectors").append(path);
    connectors.set(metric, path);
  }

  function init() {
    if (initialized) return;
    initialized = true;
    for (const metric of leftMetrics) createCard(metric, $("metrics-left"));
    for (const metric of rightMetrics) createCard(metric, $("metrics-right"));
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(scheduleLayout);
      for (const element of [$("telemetry-layout"), $("telemetry-body"), ...Array.from(cards.values(), item => item.card)]) observer.observe(element);
    }
    window.addEventListener("resize", scheduleLayout);
    document.fonts?.ready.then(scheduleLayout);
    select($("metric-select").value);
    scheduleLayout();
  }

  function scheduleLayout() {
    if (layoutFrame !== null) return;
    layoutFrame = window.requestAnimationFrame(() => {
      layoutFrame = null;
      drawConnectors();
      drawSparklines();
    });
  }

  function render(reading) {
    init();
    latest = reading;
    for (const [metric, elements] of cards) {
      const value = reading?.[metric];
      elements.value.textContent = typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
    }
    select($("metric-select").value);
    renderDevice();
    scheduleLayout();
  }

  function select(metric) {
    for (const [key, elements] of cards) {
      elements.card.classList.toggle("active", key === metric);
      elements.card.setAttribute("aria-pressed", String(key === metric));
      connectors.get(key).classList.toggle("active", key === metric);
    }
    document.querySelectorAll(".body-point").forEach(point => {
      point.classList.toggle("active", point.dataset.metric === metric);
      point.setAttribute("aria-pressed", String(point.dataset.metric === metric));
    });
  }

  function drawConnectors() {
    const layout = $("telemetry-layout");
    const bounds = layout.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const svg = $("telemetry-connectors");
    svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
    const body = $("telemetry-body").getBoundingClientRect();
    const sensors = Array.from(document.querySelectorAll(".body-point"));
    for (const [metric, elements] of cards) {
      const sensor = sensors.find(point => point.dataset.metric === metric);
      if (!sensor) continue;
      const card = elements.card.getBoundingClientRect();
      const point = sensor.getBoundingClientRect();
      const end = { x: point.left + point.width / 2 - bounds.left, y: point.top + point.height / 2 - bounds.top };
      const isLeft = card.right <= body.left;
      const isRight = card.left >= body.right;
      let start, path;
      if (isLeft || isRight) {
        start = { x: (isLeft ? card.right : card.left) - bounds.left, y: card.top + card.height / 2 - bounds.top };
        const middle = (start.x + end.x) / 2;
        path = `M ${start.x} ${start.y} C ${middle} ${start.y}, ${middle} ${end.y}, ${end.x} ${end.y}`;
      } else {
        // Al apilarse las tarjetas, la línea pasa por el margen exterior de la columna.
        start = { x: card.right - bounds.left, y: card.top + card.height / 2 - bounds.top };
        const gutter = bounds.width - 1;
        path = `M ${start.x} ${start.y} L ${gutter} ${start.y} L ${gutter} ${end.y} L ${end.x} ${end.y}`;
      }
      connectors.get(metric).setAttribute("d", path);
    }
  }

  function series(records, metric) {
    return records.map((record, index) => ({ record, index, time: new Date(record.captured_at).getTime() }))
      .filter(point => Number.isFinite(point.time))
      .sort((a, b) => a.time - b.time || a.index - b.index)
      .map(point => ({ ...point, value: typeof point.record[metric] === "number" && Number.isFinite(point.record[metric])
        && !["poor", "invalid"].includes(point.record.quality?.[metric]) ? point.record[metric] : null }));
  }

  function drawSparklines() {
    for (const [metric, { canvas, caption }] of cards) {
      const bounds = canvas.getBoundingClientRect();
      if (!bounds.width || !bounds.height) continue;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(bounds.width * dpr); canvas.height = Math.round(bounds.height * dpr);
      const context = canvas.getContext("2d");
      context.scale(dpr, dpr);
      const points = series(history, metric);
      const available = points.filter(point => point.value !== null);
      const description = historyFailed ? "Historial no disponible" : available.length ? `${available.length} lecturas válidas` : "Sin historial válido";
      caption.textContent = description;
      caption.dataset.hasValues = String(available.length > 0);
      canvas.setAttribute("aria-label", `${METRICS[metric][0]} · ${description} · ${METRICS[metric][1]}`);
      if (!available.length) continue;
      const values = available.map(point => point.value);
      const min = Math.min(...values), max = Math.max(...values);
      const spread = Math.max(1, max - min);
      const lower = min - spread * .18, upper = max + spread * .18;
      const span = points.at(-1).time - points[0].time;
      const x = point => span ? 3 + (point.time - points[0].time) / span * (bounds.width - 6) : bounds.width / 2;
      const y = point => 3 + (upper - point.value) / (upper - lower) * (bounds.height - 6);
      context.strokeStyle = "#69bfd0"; context.lineWidth = 1.3;
      context.beginPath();
      let connected = false;
      for (const point of points) {
        if (point.value === null) { connected = false; continue; }
        if (connected) context.lineTo(x(point), y(point)); else context.moveTo(x(point), y(point));
        connected = true;
      }
      context.stroke();
      for (const point of available) {
        context.fillStyle = sourceColor(point.record);
        context.beginPath(); context.arc(x(point), y(point), 1.8, 0, Math.PI * 2); context.fill();
      }
    }
  }

  function renderHistory(records, failed = false) {
    history = records;
    historyFailed = failed;
    drawSparklines();
  }

  function setDevice(status, failed = false) {
    device = status;
    deviceError = failed;
    renderDevice();
  }

  function renderDevice() {
    const confirmed = device?.state === "receiving_telemetry" && device.connected === true
      && device.telemetry_confirmed === true && device.verified_physical === true;
    const labels = { integration_unavailable: "Integración no disponible", ble_unavailable: "Bluetooth no disponible",
      disconnected: "Desconectado", scanning: "Buscando dispositivo", connecting: "Conectando",
      connected: "Enlace pendiente de telemetría", receiving_telemetry: "Recepción pendiente de verificar",
      reconnecting: "Reconectando", error: "Error de integración" };
    $("telemetry-watch-model").textContent = device?.model || "Roadtrip Watch 4 JG74";
    $("device-summary").textContent = deviceError ? "Estado no disponible" : confirmed ? "Recibiendo telemetría verificada"
      : labels[device?.state] || "Estado pendiente de verificar";
    $("telemetry-watch").dataset.state = confirmed && !deviceError ? "receiving" : "unavailable";
    const received = device?.last_received_at;
    const receivedTime = received ? new Date(received).getTime() : NaN;
    const validTime = typeof received === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(received)
      && Number.isFinite(receivedTime) && receivedTime <= Date.now();
    $("telemetry-watch-sync").textContent = device?.verified_physical === true && validTime && !deviceError
      ? eventTime(received) : "Sin confirmación";
    $("telemetry-watch-source").textContent = sourceText(latest);
  }

  return { init, render, select, renderHistory, setDevice, scheduleLayout, series };
})();
