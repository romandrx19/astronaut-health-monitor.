/* Pruebas de concurrencia con fetch controlado y DOM mínimo, sin dependencias. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const script = ["telemetry.js", "simulation.js", "navigation.js", "app.js"].map(file => fs.readFileSync(path.join(root, "static/js", file), "utf8")).join("\n");
const html = ["index.html", "oct.html", "research.html"].map(file => fs.readFileSync(path.join(root, "templates", file), "utf8")).join("\n");
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const profile = (id, notes = "") => ({ astronaut_id: id, notes, updated_at: null, references: {} });
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const metricNames = ["heart_rate", "spo2", "systolic", "diastolic", "glucose", "temperature", "sleep_hours"];
function assessment(id, options = {}) {
  return { astronaut_id: id, measurement_id: 1, measurement_source: "simulated", state: "no_reference",
    state_label: "Sin referencia personalizada", message: "Sin referencia personalizada",
    missing_references: metricNames, events: [],
    metrics: Object.fromEntries(metricNames.map(metric => [metric, { metric, label: metric, unit: "demo",
      current_value: 72, reference: null, state: "no_reference", description: "Sin referencia personalizada",
      trend: { state: "insufficient", label: "Datos insuficientes", sample_count: 1, span_seconds: 0, findings: [] } }])), ...options };
}

async function harness(includeOct = false) {
  const elements = new Map(), requests = [], intervals = [], canvasCalls = [];
  let frameId = 0;
  let strokes = 0;
  class Element {
    constructor(tag = "div") {
      this.tag = tag;
      this.children = [];
      this.listeners = {};
      this.dataset = {};
      this.attributes = {};
      this.value = "";
      this.validity = { badInput: false };
      this.disabled = false;
      this.hidden = false;
      this.style = {};
      this.files = [];
      this.clientWidth = 600;
      this.clientHeight = 340;
      const classes = new Set();
      this.classList = {
        toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
        add(name) { classes.add(name); }, remove(name) { classes.delete(name); }, contains(name) { return classes.has(name); },
      };
    }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    append(...children) { this.children.push(...children); children.forEach(child => { child.parent = this; }); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    focus() { documentMock.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
    addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
    emit(name, extra = {}) {
      return Promise.all((this.listeners[name] || []).map(listener => listener({
        target: this, currentTarget: this, preventDefault() {}, ...extra,
      })));
    }
    reset() {
      if (this.id === "oct-exam-form") {
        const defaults = { eye: "OD", phase: "preflight", quality: "unknown", provenance: "manual_entry", "review-status": "pending" };
        for (const [id, element] of elements) {
          if (id.startsWith("oct-form-")) { element.value = defaults[id.slice(9)] || ""; element.checked = false; element.files = []; }
        }
        return;
      }
      if (this.id === "manual-form") { this.payload = {}; return; }
      if (this.id !== "medical-form") return;
      for (const [id, element] of elements) {
        if (id === "medical-notes" || /^medical-.+-(min|max)_value$/.test(id)) element.value = "";
      }
    }
    getBoundingClientRect() {
      if (this.rect) return this.rect;
      if (this.id === "telemetry-layout") return { left: 0, top: 0, width: 900, height: 560, right: 900, bottom: 560 };
      if (this.id === "telemetry-body") return { left: 355, top: 40, width: 190, height: 380, right: 545, bottom: 420 };
      if (this.parent?.id === "metrics-left" || this.parent?.id === "metrics-right") {
        const left = this.parent?.id === "metrics-left" ? 0 : 660;
        const top = (this.parent?.children.indexOf(this) || 0) * 140;
        return { left, top, width: 240, height: 132, right: left + 240, bottom: top + 132 };
      }
      if (this.id?.startsWith("telemetry-chart-")) return { left: 0, top: 0, width: 200, height: 25, right: 200, bottom: 25 };
      return { left: 0, top: 0, width: 600, height: 290, right: 600, bottom: 290 };
    }
    scrollIntoView() {}
    getContext() { return new Proxy({}, { get: (_, key) => (...args) => { canvasCalls.push({ id: this.id, op: key, args }); if (key === "stroke" && this.id === "history-chart") strokes++; } }); }
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) {
    const element = new Element(); element.id = match[1];
  }
  elements.get("metric-select").value = "heart_rate";
  elements.get("oct-eye").value = "OD";
  for (const [key, low, high] of [["heart_rate", 1, 300], ["spo2", 0, 100], ["systolic", 1, 350],
    ["diastolic", 1, 250], ["glucose", 1, 1000], ["temperature", 25, 45], ["sleep_hours", 0, 24]]) {
    elements.get(`manual-${key}`).min = String(low); elements.get(`manual-${key}`).max = String(high);
  }
  const sample = { id: 1, captured_at: "2026-01-01T00:00:00+00:00", source: "simulated", heart_rate: 72,
    spo2: 98, systolic: 116, diastolic: 75, glucose: 94, temperature: 36.7, sleep_hours: 7.4 };
  const crew = [1, 2, 3].map(id => ({ id, name: `Astronauta ${id}`, role: "Demo", latest: sample, alerts: [] }));
  const points = [...html.matchAll(/class="body-point"\s+data-metric="([^"]+)"/g)].map(match => {
    const point = new Element("button"); point.dataset.metric = match[1]; return point;
  });
  const queryAll = selector => {
    const metrics = ["metrics-left", "metrics-right"].flatMap(id => elements.get(id).children);
    if (selector === ".body-point") return points;
    if (selector === ".telemetry-metric") return metrics;
    if (selector === ".telemetry-metric, .body-point") return [...metrics, ...points];
    return [];
  };
  const h = { elements, requests, intervals, route: null, points, canvasCalls,
    simulation: { running: true, revision: 0, last_generated_at: null, interval_seconds: 5 }, get chartStrokes() { return strokes; } };
  const documentMock = { body: { dataset: {} }, activeElement: null, getElementById: id => elements.get(id), createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), querySelectorAll: queryAll };
  h.document = documentMock;
  const context = vm.createContext({
    AbortController,
    URLSearchParams,
    document: documentMock,
    window: { devicePixelRatio: 1, addEventListener() {}, requestAnimationFrame(callback) { queueMicrotask(callback); return ++frameId; } },
    setInterval: (callback, ms) => intervals.push({ callback, ms }),
    FormData: class {
      constructor(form) { this.form = form; this.fields = []; }
      append(key, value) { this.fields.push([key, value]); }
      get(key) { return this.fields.find(item => item[0] === key)?.[1] ?? null; }
      entries() { return this.form ? Object.entries(this.form.payload || {}) : this.fields; }
    },
    fetch: async (url, options = {}) => {
      requests.push({ url, ...options });
      if (h.route) {
        const routed = h.route(url, options);
        if (routed !== undefined) return routed;
      }
      if (url === "/api/simulation") {
        if (options.method === "POST") { h.simulation.running = JSON.parse(options.body).running; h.simulation.revision++; }
        return response({ ...h.simulation });
      }
      const ocularSummary = url.match(/astronauts\/(\d+)\/oct\/summary$/);
      if (ocularSummary) return response({ astronaut_id: Number(ocularSummary[1]), exam_count: 0, last_exam_date: null, baselines: [], last_review: null, latest_notes: "" });
      const ocularExams = url.match(/astronauts\/(\d+)\/oct\/exams$/);
      if (ocularExams) return response({ astronaut_id: Number(ocularExams[1]), exams: [] });
      const medical = url.match(/astronauts\/(\d+)\/medical$/);
      if (medical) return response(profile(Number(medical[1])));
      if (url === "/api/devices/status") return response({ devices: [{ model: "Roadtrip Watch 4 GPS", state: "integration_unavailable",
        connected: false, telemetry_confirmed: false, verified_physical: false,
        message: "Dispositivo no integrado: protocolo de datos pendiente de verificar" }] });
      const analysis = url.match(/astronauts\/(\d+)\/assessment/);
      if (analysis) return response(assessment(Number(analysis[1]), { measurement_source: url.includes("source=manual") ? "manual" : "simulated" }));
      if (url.includes("/events?")) return response([]);
      if (url.includes("/history")) return response([sample]);
      if (url.startsWith("/api/astronauts") && options.method !== "POST") return response(crew);
      if (url.endsWith("/measurements/manual")) return response({ measurement: { id: "reading:1" }, created: true, alerts: [] }, 201);
      if (url === "/api/simulate") return response({ created: 3, simulation: { ...h.simulation } }, 201);
      throw new Error(`Solicitud inesperada: ${url}`);
    },
  });
  const octScript = includeOct ? fs.readFileSync(path.join(root, "static/js/oct.js"), "utf8") + "\n" : "";
  vm.runInContext(octScript + script + "\nglobalThis.frontend = {state, medicalState, assessmentState, loadAssessment, loadEventHistory, renderAssessment, loadMedicalProfile, refreshData, renderChart, loadHistory, loadDeviceStatus, updateReadingFreshness, renderDetail, navigationState, showView, priorityNotices, TelemetryMonitor};", context);
  h.frontend = context.frontend;
  h.get = id => elements.get(id);
  h.select = id => elements.get("crew-cards").children[id - 1].emit("click");
  h.medicalPosts = () => requests.filter(r => r.url.endsWith("/medical") && r.method === "POST");
  await flush();
  return h;
}

test("telemetría presenta siete tarjetas en el orden solicitado y los valores actuales", async () => {
  const h = await harness();
  assert.deepEqual(h.get("metrics-left").children.map(item => item.dataset.metric), ["heart_rate", "spo2", "systolic", "diastolic"]);
  assert.deepEqual(h.get("metrics-right").children.map(item => item.dataset.metric), ["glucose", "temperature", "sleep_hours"]);
  for (const metric of metricNames) {
    const card = [...h.get("metrics-left").children, ...h.get("metrics-right").children].find(item => item.dataset.metric === metric);
    assert.equal(card.children[1].children[0].textContent, String(h.frontend.state.crew[0].latest[metric]));
    assert.ok(h.get(`telemetry-chart-${metric}`));
    assert.match(h.get(`metric-state-${metric}`).textContent, /Sin referencia/);
  }
  assert.equal(h.points.length, 7);
});

test("la pausa compartida mantiene las consultas y el modo manual pausa la generación", async () => {
  const h = await harness();
  await h.get("toggle").emit("click");
  assert.equal(h.simulation.running, false);
  const before = h.requests.filter(item => item.url === "/api/simulate").length;
  h.intervals[0].callback(); await flush();
  assert.equal(h.requests.filter(item => item.url === "/api/simulate").length, before);
  assert.ok(h.requests.some(item => item.url === "/api/simulation" && item.method !== "POST"));
  await h.get("toggle").emit("click");
  assert.equal(h.simulation.running, true);
  h.get("source-filter").value = "smartwatch";
  await h.get("source-filter").emit("change");
  assert.equal(h.simulation.running, false);
  assert.equal(h.frontend.state.running, false);
});

test("un borrador médico se recupera únicamente al volver a su astronauta", async () => {
  const h = await harness();
  h.get("medical-notes").value = "Borrador del astronauta 1";
  h.get("medical-heart_rate-min_value").value = "60";
  h.get("medical-heart_rate-max_value").value = "90";
  await h.select(2);
  assert.equal(h.get("medical-notes").value, "");
  h.get("medical-notes").value = "Borrador del astronauta 2";
  await h.select(1);
  assert.equal(h.get("medical-notes").value, "Borrador del astronauta 1");
  assert.equal(h.get("medical-heart_rate-min_value").value, "60");
  assert.match(h.get("medical-message").textContent, /Borrador recuperado/);
  assert.equal(h.medicalPosts().length, 0);
});

test("los siete gráficos comparten una consulta y actualizan valores almacenados", async () => {
  const h = await harness();
  const before = h.requests.filter(item => item.url.includes("/history?")).length;
  h.route = url => url.includes("/history?") ? response([
    { astronaut_id: 1, captured_at: "2026-01-01T00:00:00Z", source: "manual", heart_rate: 70, spo2: 0 },
    { astronaut_id: 1, captured_at: "2026-01-01T00:00:01Z", source: "manual", heart_rate: 80, spo2: 90 },
  ]) : undefined;
  h.canvasCalls.length = 0;
  await h.frontend.loadHistory();
  assert.equal(h.requests.filter(item => item.url.includes("/history?")).length - before, 1);
  assert.equal(h.canvasCalls.filter(item => item.id === "telemetry-chart-heart_rate" && item.op === "arc").length, 2);
  assert.equal(h.canvasCalls.filter(item => item.id === "telemetry-chart-spo2" && item.op === "arc").length, 2);
  assert.match(h.get("telemetry-chart-glucose").attributes["aria-label"], /Sin historial válido/);
});

test("los sparklines ordenan por fecha y mantienen huecos por NULL o mala calidad", async () => {
  const h = await harness();
  h.frontend.state.history = [
    { captured_at: "2026-01-01T00:00:03Z", source: "manual", sleep_hours: 0 },
    { captured_at: "2026-01-01T00:00:01Z", source: "manual", sleep_hours: 7 },
    { captured_at: "2026-01-01T00:00:02Z", source: "manual", sleep_hours: null },
    { captured_at: "2026-01-01T00:00:04Z", source: "manual", sleep_hours: 8, quality: { sleep_hours: "poor" } },
  ];
  const series = h.frontend.TelemetryMonitor.series(h.frontend.state.history, "sleep_hours");
  assert.deepEqual(Array.from(series, point => point.value), [7, null, 0, null]);
  h.canvasCalls.length = 0;
  h.frontend.renderChart();
  const calls = h.canvasCalls.filter(item => item.id === "telemetry-chart-sleep_hours");
  assert.equal(calls.filter(item => item.op === "lineTo").length, 0);
  assert.equal(calls.filter(item => item.op === "arc").length, 2);
  assert.ok(calls.filter(item => item.op === "arc").every(item => item.args.every(Number.isFinite)));
});

test("la ausencia de lecturas conserva las siete tarjetas con gráficos vacíos", async () => {
  const h = await harness();
  h.frontend.state.crew[0].latest = null;
  h.frontend.state.history = [];
  h.canvasCalls.length = 0;
  h.frontend.renderDetail(); h.frontend.renderChart(); await flush();
  assert.equal(h.get("metrics-left").children.length + h.get("metrics-right").children.length, 7);
  assert.ok(h.get("metrics-left").children.every(card => card.children[1].children[0].textContent === "—"));
  assert.ok(metricNames.every(metric => /Sin historial válido/.test(h.get(`telemetry-chart-${metric}`).attributes["aria-label"])));
  assert.equal(h.canvasCalls.filter(item => item.id?.startsWith("telemetry-chart-") && item.op === "arc").length, 0);
});

test("seleccionar cada sensor activa su tarjeta y su único conector durante actualizaciones", async () => {
  const h = await harness();
  for (const point of h.points) {
    await point.emit("click");
    const selected = point.dataset.metric;
    assert.equal(h.get("metric-select").value, selected);
    assert.equal(h.get("telemetry-connectors").children.filter(path => path.classList.contains("active")).length, 1);
    assert.equal(point.attributes["aria-pressed"], "true");
    const card = [...h.get("metrics-left").children, ...h.get("metrics-right").children].find(item => item.dataset.metric === selected);
    assert.equal(card.attributes["aria-pressed"], "true");
    await h.frontend.refreshData();
    assert.equal(h.get("metric-select").value, selected);
    assert.equal(h.get(`telemetry-chart-${selected}`), card.children[2].children[0]);
  }
});

test("los conectores recalculan coordenadas y viewBox al cambiar el tamaño", async () => {
  const h = await harness();
  const paths = h.get("telemetry-connectors").children;
  const before = paths.map(path => path.attributes.d);
  h.get("telemetry-layout").rect = { left: 0, top: 0, width: 420, height: 1500, right: 420, bottom: 1500 };
  h.get("telemetry-body").rect = { left: 150, top: 10, width: 120, height: 240, right: 270, bottom: 250 };
  h.points.forEach((point, index) => { point.rect = { left: 180, top: 35 + index * 25, width: 20, height: 20, right: 200, bottom: 55 + index * 25 }; });
  [...h.get("metrics-left").children, ...h.get("metrics-right").children].forEach((card, index) => {
    card.rect = { left: 0, top: 380 + index * 140, width: 410, height: 132, right: 410, bottom: 512 + index * 140 };
  });
  h.frontend.TelemetryMonitor.scheduleLayout(); await flush();
  assert.equal(h.get("telemetry-connectors").attributes.viewBox, "0 0 420 1500");
  assert.ok(paths.every((path, index) => path.attributes.d !== before[index] && !/NaN|undefined/.test(path.attributes.d)));
  assert.ok(paths.every(path => path.attributes.d.includes("L 419")));
});

test("el historial de telemetría descarta respuestas atrasadas al cambiar A→B→A", async () => {
  const h = await harness();
  const pending = [];
  h.route = url => {
    if (!url.includes("/history?")) return;
    const request = deferred(); pending.push(request); return request.promise;
  };
  const old = h.frontend.loadHistory();
  const second = h.select(2), first = h.select(1);
  pending[2].resolve(response([{ astronaut_id: 1, captured_at: "2026-01-01T01:00:00Z", source: "manual", heart_rate: 81 }]));
  await first;
  pending[1].resolve(response([{ astronaut_id: 2, captured_at: "2026-01-01T01:00:00Z", source: "manual", heart_rate: 82 }]));
  pending[0].resolve(response([{ astronaut_id: 1, captured_at: "2026-01-01T00:00:00Z", source: "manual", heart_rate: 60 }]));
  await Promise.all([old, second]);
  assert.equal(h.frontend.state.history[0].heart_rate, 81);
  assert.match(h.get("telemetry-chart-heart_rate").attributes["aria-label"], /1 lecturas válidas/);
});

test("el reloj utiliza su recepción confirmada y la fuente real de la lectura visible", async () => {
  const h = await harness();
  const received = "2026-01-01T12:00:00+00:00";
  h.frontend.state.crew[0].latest = { ...h.frontend.state.crew[0].latest, source: "manual", received_at: received };
  h.frontend.renderDetail();
  assert.match(h.get("telemetry-watch-source").textContent, /MANUAL/);
  assert.equal(h.get("telemetry-watch-sync").textContent, "Sin confirmación");
  h.frontend.TelemetryMonitor.setDevice({ state: "connected_unconfirmed", connected: true, verified_physical: false, last_received_at: received });
  assert.doesNotMatch(h.get("device-summary").textContent, /Conectado|Recibiendo/);
  assert.equal(h.get("telemetry-watch-sync").textContent, "Sin confirmación");
  h.frontend.TelemetryMonitor.setDevice({ state: "receiving_telemetry", connected: true, telemetry_confirmed: true, verified_physical: true, last_received_at: received });
  assert.match(h.get("device-summary").textContent, /Recibiendo telemetría verificada/);
  assert.match(h.get("telemetry-watch-sync").textContent, /2026.*UTC/);
  assert.match(h.get("telemetry-watch-source").textContent, /MANUAL/);
  h.frontend.TelemetryMonitor.setDevice({ verified_physical: true, last_received_at: "2026-01-01T12:00:00" });
  assert.equal(h.get("telemetry-watch-sync").textContent, "Sin confirmación");
});

test("un fallo del historial muestra estado vacío y un fallo de dispositivo retira la confirmación", async () => {
  const h = await harness();
  h.frontend.TelemetryMonitor.setDevice({ state: "receiving_telemetry", connected: true, telemetry_confirmed: true, verified_physical: true });
  h.route = url => url.includes("/history?") || url === "/api/devices/status" ? response({ error: "Fallo controlado" }, 503) : undefined;
  await h.frontend.loadHistory();
  assert.match(h.get("telemetry-chart-heart_rate").attributes["aria-label"], /Historial no disponible/);
  await h.frontend.loadDeviceStatus();
  assert.equal(h.get("telemetry-watch").dataset.state, "unavailable");
  assert.equal(h.get("device-summary").textContent, "Estado no disponible");
});

test("Investigación NASA conserva la tripulación y separa su contexto de la telemetría", async () => {
  const h = await harness();
  const selected = h.frontend.state.selectedId;
  const running = h.frontend.state.running;
  await h.get("nav-research").emit("click");
  assert.equal(h.frontend.navigationState.view, "research");
  assert.equal(h.frontend.state.selectedId, selected);
  assert.equal(h.frontend.state.running, running);
  assert.equal(h.get("crew-context").hidden, true);
  assert.equal(h.get("refresh").hidden, true);
  assert.match(h.get("simulation-status").textContent, /Archivo histórico/);
  await h.get("control-home").emit("click");
  assert.equal(h.get("crew-context").hidden, false);
  assert.equal(h.get("refresh").hidden, false);
});

test("genera siete parejas opcionales y la telemetría no borra el borrador ni guarda", async () => {
  const h = await harness();
  assert.equal(h.get("medical-references").children.length, 7);
  assert.equal(h.get("medical-fields").disabled, false);
  h.get("medical-notes").value = "Borrador ficticio sin guardar";
  h.get("medical-heart_rate-min_value").value = "1.25";
  assert.equal(h.intervals[0].ms, 5000);
  h.intervals[0].callback();
  await flush();
  h.get("source-filter").value = "manual";
  await h.get("source-filter").emit("change");
  assert.equal(h.get("medical-notes").value, "Borrador ficticio sin guardar");
  assert.equal(h.get("medical-heart_rate-min_value").value, "1.25");
  assert.equal(h.requests.filter(r => r.url.endsWith("/medical")).length, 1);
  assert.equal(h.medicalPosts().length, 0);
  assert.ok(h.requests.some(r => r.url === "/api/simulate"));
});

test("selección rápida 2→3→2 ignora respuestas antiguas incluso para el mismo ID", async () => {
  const h = await harness();
  const pending = [];
  h.route = url => {
    if (!url.endsWith("/medical")) return;
    const request = deferred(); pending.push(request); return request.promise;
  };
  const oldTwo = h.select(2), three = h.select(3), currentTwo = h.select(2);
  assert.equal(h.get("medical-notes").value, "");
  assert.equal(h.get("medical-fields").disabled, true);
  pending[2].resolve(response(profile(2, "Expediente actual de prueba")));
  await currentTwo;
  pending[1].resolve(response(profile(3, "No debe mostrarse")));
  await three;
  pending[0].resolve(response(profile(2, "Respuesta vieja del mismo ID")));
  await oldTwo;
  assert.equal(h.get("medical-name").textContent, "Astronauta 2");
  assert.equal(h.get("medical-notes").value, "Expediente actual de prueba");
  assert.equal(h.frontend.medicalState.astronautId, 2);
  assert.equal(h.get("medical-save").disabled, false);
});

test("un error de una solicitud antigua no cambia el expediente actual", async () => {
  const h = await harness();
  const old = deferred();
  h.route = url => url.endsWith("/2/medical") ? old.promise : undefined;
  const pending = h.select(2);
  await h.select(3);
  old.reject(new Error("Error tardío de prueba"));
  await pending;
  assert.equal(h.get("medical-name").textContent, "Astronauta 3");
  assert.notEqual(h.get("medical-message").dataset.state, "error");
  assert.equal(h.get("medical-retry").hidden, true);
});

test("guardar bloquea selección y envíos duplicados mientras la telemetría continúa", async () => {
  const h = await harness();
  const save = deferred();
  h.route = (url, options) => options.method === "POST" && url.endsWith("/medical") ? save.promise : undefined;
  h.get("medical-notes").value = "Texto ficticio del astronauta 1";
  h.get("medical-heart_rate-min_value").value = "1.25";
  h.get("medical-heart_rate-max_value").value = "2.75";
  const pending = h.get("medical-form").emit("submit");
  await h.get("medical-form").emit("submit");
  await h.select(2); // También prueba la guardia del manejador, además del botón deshabilitado.
  await h.frontend.refreshData();
  assert.equal(h.frontend.state.selectedId, 1);
  assert.ok(h.get("crew-cards").children.every(button => button.disabled));
  assert.equal(h.get("medical-fields").disabled, true);
  assert.equal(h.get("manual-submit").disabled, true);
  assert.equal(h.get("medical-save").disabled, true);
  assert.equal(h.medicalPosts().length, 1);
  const request = h.medicalPosts()[0];
  assert.equal(request.url, "/api/astronauts/1/medical");
  assert.deepEqual(JSON.parse(request.body), { notes: "Texto ficticio del astronauta 1",
    references: { heart_rate: { min_value: 1.25, max_value: 2.75 } } });
  save.resolve(response({ ...profile(1), ...JSON.parse(request.body), updated_at: "2026-01-01T00:00:00+00:00" }));
  await pending;
  assert.equal(h.get("medical-message").dataset.state, "success");
  assert.equal(h.get("medical-fields").disabled, false);
  assert.ok(h.get("crew-cards").children.every(button => !button.disabled));
});

test("rechaza parejas incompletas, invertidas, no finitas o de entrada inválida sin POST", async () => {
  const h = await harness();
  const low = h.get("medical-heart_rate-min_value"), high = h.get("medical-heart_rate-max_value");
  for (const [minimum, maximum, badInput] of [["1", "", false], ["2", "1", false],
    ["1", "1", false], ["1", "Infinity", false], ["", "", true]]) {
    low.value = minimum; high.value = maximum; low.validity.badInput = badInput;
    await h.get("medical-form").emit("submit");
    assert.equal(h.get("medical-message").dataset.state, "error");
  }
  assert.equal(h.medicalPosts().length, 0);
});

test("un fallo al guardar conserva los cambios y permite volver a guardar", async () => {
  const h = await harness();
  h.get("medical-notes").value = "Borrador de demostración";
  h.route = (url, options) => options.method === "POST" ? response({ error: "Fallo de prueba" }, 503) : undefined;
  await h.get("medical-form").emit("submit");
  assert.equal(h.get("medical-notes").value, "Borrador de demostración");
  assert.equal(h.get("medical-message").dataset.state, "error");
  assert.equal(h.get("medical-save").disabled, false);
});

test("una carga fallida o de otro astronauta deshabilita el guardado y ofrece reintento", async () => {
  const h = await harness();
  h.route = url => url.endsWith("/2/medical") ? response(profile(3, "No debe mostrarse")) : undefined;
  await h.select(2);
  assert.equal(h.get("medical-notes").value, "");
  assert.equal(h.get("medical-save").disabled, true);
  assert.equal(h.get("medical-retry").hidden, false);
  h.route = null;
  await h.get("medical-retry").emit("click");
  assert.equal(h.get("medical-save").disabled, false);
  assert.equal(h.get("medical-retry").hidden, true);
});

test("el formulario manual consolidado hace un solo POST por envío", async () => {
  const h = await harness();
  for (const id of ["manual-form", "manual-form"]) {
    assert.equal(h.get(id).listeners.submit.length, 1);
    h.get(id).payload = { heart_rate: "72", spo2: "98", systolic: "116", diastolic: "75",
      glucose: "94", temperature: "36.7", sleep_hours: "7.4" };
    await h.get(id).emit("submit");
  }
  assert.equal(h.requests.filter(r => r.url.endsWith("/measurements/manual") && r.method === "POST").length, 2);
  assert.equal(h.requests.filter(r => r.url.endsWith("/medical")).length, 1);
});

test("la evaluación sin referencias no muestra una salud completamente normal", async () => {
  const h = await harness();
  assert.equal(h.frontend.assessmentState.data.state, "no_reference");
  assert.match(h.get("assessment-coverage").textContent, /Sin referencia personalizada/);
  assert.match(h.get("body-status-text").textContent, /Referencias incompletas/);
  assert.ok(h.points.every(point => point.dataset.state === "no_reference"));
  assert.match(h.get("metric-state-heart_rate").textContent, /Sin referencia personalizada/);
  assert.equal(h.medicalPosts().length, 0);
});

test("la evaluación rápida 2→3→2 ignora respuestas antiguas del mismo astronauta", async () => {
  const h = await harness(), pending = [];
  h.route = url => {
    if (!url.includes("/assessment")) return;
    const request = deferred(); pending.push(request); return request.promise;
  };
  const oldTwo = h.select(2), three = h.select(3), latestTwo = h.select(2);
  pending[2].resolve(response(assessment(2, { message: "Evaluación actual de prueba" })));
  await latestTwo;
  pending[0].resolve(response(assessment(2, { message: "Respuesta antigua" })));
  pending[1].resolve(response(assessment(3, { message: "Otro astronauta" })));
  await Promise.all([oldTwo, three]);
  assert.equal(h.get("assessment-summary").textContent, "Evaluación actual de prueba");
  assert.equal(h.frontend.assessmentState.data.astronaut_id, 2);
});

test("el filtro de origen invalida evaluaciones pendientes", async () => {
  const h = await harness(), old = deferred();
  h.route = url => url.endsWith("/assessment") ? old.promise : undefined;
  const pending = h.frontend.loadAssessment();
  h.get("source-filter").value = "manual";
  await h.get("source-filter").emit("change");
  old.resolve(response(assessment(1, { message: "Evaluación antigua simulada" })));
  await pending;
  assert.equal(h.frontend.assessmentState.data.measurement_source, "manual");
  assert.notEqual(h.get("assessment-summary").textContent, "Evaluación antigua simulada");
});

test("una evaluación de otra lectura no se aplica a los indicadores actuales", async () => {
  const h = await harness();
  h.route = url => url.includes("/assessment") ? response(assessment(1, { measurement_id: 999 })) : undefined;
  await h.frontend.loadAssessment();
  assert.equal(h.frontend.assessmentState.data, null);
  assert.equal(h.get("assessment-retry").hidden, false);
  assert.match(h.get("assessment-summary").textContent, /lectura cambió/);
  assert.ok(h.points.every(point => point.dataset.state === "pending"));
});

test("los sensores conservan selección, estado personalizado e historial gráfico", async () => {
  const h = await harness();
  const data = assessment(1);
  Object.assign(data.metrics.temperature, { state: "variation", current_value: 38.2,
    reference: { min_value: 36, max_value: 37 }, description: "Variación de prueba" });
  h.route = url => url.includes("/assessment") ? response(data) : undefined;
  await h.frontend.loadAssessment();
  const point = h.points.find(point => point.dataset.metric === "temperature");
  const strokes = h.chartStrokes;
  await point.emit("click");
  assert.equal(h.get("metric-select").value, "temperature");
  assert.equal(point.dataset.state, "variation");
  assert.equal(point.classList.contains("active"), true);
  assert.equal(h.get("events-selected-only").checked, true);
  assert.ok(h.get("selected-assessment").children.some(child => /38.2/.test(child.textContent)));
  assert.ok(h.get("selected-assessment").children.some(child => /36–37/.test(child.textContent)));
  assert.ok(h.chartStrokes > strokes);
  // La selección también se puede realizar desde la tarjeta lateral.
  const diastolic = h.get("metrics-left").children.find(element => element.dataset.metric === "diastolic");
  await diastolic.emit("click");
  assert.equal(h.get("metric-select").value, "diastolic");
});

test("las fuentes se separan y las tarjetas desplegadas se conservan", async () => {
  const h = await harness(), data = assessment(1);
  const event = { id: 11, metric: "heart_rate", severity: "variation", current_value: 112,
    reference: { min_value: 60, max_value: 80 }, comparison: "above", difference: 32,
    description: "Demostración", guidance: ["Verifica la lectura"], source: "individual_reference",
    sources: ["individual_reference"], captured_at: "2026-01-01T00:00:00Z" };
  data.events = [event, { ...event, id: 12, severity: "educational", source: "simulation_rule", sources: ["simulation_rule"] },
    { ...event, id: 13, metric: "spo2" }];
  h.route = url => url.includes("/assessment") ? response(data) : undefined;
  await h.frontend.loadAssessment();
  const cards = () => h.get("alerts").children.filter(child => child.tag === "details");
  assert.equal(cards().length, 3);
  assert.ok(h.get("alerts").children.some(child => /independientes/.test(child.textContent)));
  cards()[0].open = true;
  await cards()[0].emit("toggle");
  h.frontend.renderAssessment();
  assert.equal(cards()[0].open, true);
  await h.points.find(point => point.dataset.metric === "heart_rate").emit("click");
  assert.equal(cards().length, 2);
});

test("el historial de eventos no mezcla astronautas al volver rápidamente al primero", async () => {
  const h = await harness(), old = deferred();
  h.route = url => url.includes("/events?") ? old.promise : undefined;
  const pending = h.frontend.loadEventHistory();
  await h.select(2);
  old.resolve(response([{ astronaut_id: 1, metric: "heart_rate" }]));
  await pending;
  assert.equal(h.get("event-history-list").children.length, 0);
  h.route = url => url.includes("/events?") ? response([{ astronaut_id: 1 }]) : undefined;
  await h.frontend.loadEventHistory();
  assert.match(h.get("event-history-list").children[0].textContent, /no corresponde/);
});

test("guardar referencias recarga la evaluación sin perder el expediente", async () => {
  const h = await harness();
  const before = h.requests.filter(request => request.url.includes("/assessment")).length;
  h.get("medical-notes").value = "Demo nueva";
  h.route = (url, options) => url.endsWith("/medical") && options.method === "POST"
    ? response({ ...profile(1), notes: "Demo nueva" }) : undefined;
  await h.get("medical-form").emit("submit");
  assert.equal(h.requests.filter(request => request.url.includes("/assessment")).length, before + 1);
  assert.equal(h.get("medical-notes").value, "Demo nueva");
  assert.equal(h.get("medical-message").dataset.state, "success");
});

test("registrar un solo indicador omite los vacíos y conserva las observaciones", async () => {
  const h = await harness();
  h.get("manual-form").payload = { heart_rate: "112", spo2: "", glucose: "", observations: "Demo parcial", acquired_at: "" };
  await h.get("manual-form").emit("submit");
  const request = h.requests.find(request => request.url.endsWith("/measurements/manual"));
  assert.deepEqual(JSON.parse(request.body), { heart_rate: 112, observations: "Demo parcial" });
  assert.ok(request.headers["Idempotency-Key"]);
  assert.equal(h.frontend.state.source, "manual");
  assert.equal(h.frontend.state.running, false);
  assert.equal(h.get("medical-notes").value, "");
});

test("un formulario vacío o una observación sola no crea mediciones de valor cero", async () => {
  const h = await harness();
  for (const payload of [{}, { heart_rate: "", observations: "Solo nota" }, { heart_rate: "NaN" }, { heart_rate: "9999" }]) {
    h.get("manual-form").payload = payload;
    await h.get("manual-form").emit("submit");
  }
  assert.equal(h.requests.filter(request => request.url.endsWith("/measurements/manual")).length, 0);
  assert.ok(h.get("manual-message").classList.contains("error"));
});

test("un cero explícito y una fecha válida se envían sin fabricar otros indicadores", async () => {
  const h = await harness();
  h.get("manual-form").payload = { spo2: "0", acquired_at: "2026-01-01T10:00:00" };
  await h.get("manual-form").emit("submit");
  const body = JSON.parse(h.requests.find(request => request.url.endsWith("/measurements/manual")).body);
  assert.equal(body.spo2, 0);
  assert.match(body.acquired_at, /Z$/);
  assert.equal(Object.keys(body).length, 2);
});

test("el guardado manual bloquea selección y envíos duplicados", async () => {
  const h = await harness(), save = deferred();
  h.get("manual-form").payload = { heart_rate: "72" };
  h.route = url => url.endsWith("/measurements/manual") ? save.promise : undefined;
  const pending = h.get("manual-form").emit("submit");
  await h.select(2);
  await h.get("manual-form").emit("submit");
  assert.equal(h.frontend.state.selectedId, 1);
  assert.equal(h.get("manual-fields").disabled, true);
  assert.equal(h.get("medical-save").disabled, true);
  assert.equal(h.requests.filter(request => request.url.endsWith("/measurements/manual")).length, 1);
  save.resolve(response({ created: true, measurement: { id: "reading:42" } }, 201));
  await pending;
  assert.equal(h.get("manual-fields").disabled, false);
});

test("un reintento conserva la clave de idempotencia y no pierde los datos", async () => {
  const h = await harness(); let attempt = 0;
  h.get("manual-form").payload = { heart_rate: "72", observations: "Demostración" };
  h.route = url => {
    if (!url.endsWith("/measurements/manual")) return;
    if (++attempt === 1) throw new Error("Respuesta perdida de prueba");
    return response({ created: false, measurement: { id: "reading:42" } });
  };
  await h.get("manual-form").emit("submit");
  assert.equal(h.get("manual-form").payload.heart_rate, "72");
  await h.get("manual-form").emit("submit");
  const requests = h.requests.filter(request => request.url.endsWith("/measurements/manual"));
  assert.equal(requests[0].headers["Idempotency-Key"], requests[1].headers["Idempotency-Key"]);
  assert.match(h.get("manual-message").textContent, /no se duplicó/);
});

test("el gráfico omite valores ausentes o de baja calidad sin convertirlos en cero", async () => {
  const h = await harness();
  h.frontend.state.history = [
    { captured_at: "2026-01-01T10:00:00Z", source: "manual", heart_rate: 72 },
    { captured_at: "2026-01-01T10:00:05Z", source: "manual", heart_rate: null },
    { captured_at: "2026-01-01T10:00:10Z", source: "simulation", heart_rate: 112, quality: { heart_rate: "poor" } },
  ];
  h.frontend.renderChart();
  assert.match(h.get("chart-caption").textContent, /1 lecturas válidas · 2 sin dato utilizable/);
  assert.equal(h.get("history-records").children.length, 3);
  assert.match(h.get("history-records").children[1].children[2].textContent, /no registrado/);
});

test("la ausencia del reloj y una conexión sin telemetría nunca aparecen como recepción real", async () => {
  const h = await harness();
  assert.match(h.get("device-status").textContent, /Dispositivo no integrado/);
  assert.doesNotMatch(h.get("device-status").textContent, /Recibiendo telemetría/);
  h.route = url => url === "/api/devices/status" ? response({ devices: [{ model: "Demo", state: "receiving_telemetry",
    connected: true, telemetry_confirmed: false, verified_physical: false }] }) : undefined;
  await h.frontend.loadDeviceStatus();
  assert.doesNotMatch(h.get("device-status").textContent, /Recibiendo telemetría/);
});

test("datos de prueba, fuentes desconocidas y lecturas antiguas se identifican", async () => {
  const h = await harness();
  const latest = h.frontend.state.crew[0].latest;
  latest.source = "simulation"; latest.provenance = "adapter_test";
  h.frontend.renderDetail();
  assert.match(h.get("reading-source").textContent, /SIMULACIÓN · PRUEBA/);
  assert.match(h.get("reading-freshness").textContent, /desactualizada/);
  latest.source = "unknown";
  h.frontend.renderDetail();
  assert.match(h.get("reading-source").textContent, /ORIGEN NO VERIFICADO/);
  latest.source = "smartwatch"; latest.provenance = "legacy";
  h.frontend.renderDetail();
  assert.doesNotMatch(h.get("reading-source").textContent, /ADAPTADOR VERIFICADO/);
});

test("pausar detiene la generación, conserva el borrador y sigue consultando datos", async () => {
  const h = await harness();
  await h.get("toggle").emit("click");
  h.get("medical-notes").value = "Borrador médico de prueba";
  h.intervals[0].callback();
  await flush();
  assert.equal(h.requests.filter(request => request.url === "/api/simulate").length, 0);
  assert.ok(h.requests.filter(request => request.url.startsWith("/api/astronauts")).length > 1);
  assert.equal(h.get("medical-notes").value, "Borrador médico de prueba");
});

test("el alias simulated acepta lecturas normalizadas con origen simulation", async () => {
  const h = await harness();
  h.route = url => url.includes("/assessment") ? response(assessment(1, { measurement_source: "simulation" })) : undefined;
  h.get("source-filter").value = "simulated";
  await h.get("source-filter").emit("change");
  assert.equal(h.frontend.assessmentState.data.measurement_source, "simulation");
  assert.equal(h.get("assessment-retry").hidden, true);
});

test("cambiar de astronauta limpia el formulario manual para no guardar datos del anterior", async () => {
  const h = await harness();
  h.get("manual-form").payload = { heart_rate: "112", observations: "Demo del primero" };
  await h.select(2);
  await h.get("manual-form").emit("submit");
  assert.equal(h.frontend.state.selectedId, 2);
  assert.equal(h.requests.filter(request => request.url.endsWith("/measurements/manual")).length, 0);
});

test("las seis vistas conservan astronauta, simulación y borradores sin recargar datos", async () => {
  const h = await harness();
  assert.equal(h.frontend.navigationState.view, "control");
  await h.select(2);
  h.get("medical-notes").value = "Borrador de expediente";
  h.get("manual-form").payload = { heart_rate: "112", observations: "Borrador manual" };
  const before = h.requests.length;
  for (const key of ["manual", "medical", "history", "control"]) {
    await h.get(`nav-${key}`).emit("click");
    assert.equal(h.frontend.navigationState.view, key);
    assert.equal(h.document.body.dataset.view, key);
    assert.equal(h.get(`nav-${key}`).attributes["aria-current"], "page");
    for (const other of ["control", "history", "medical", "manual", "devices", "events"]) {
      assert.equal(h.get(`view-${other}`).hidden, other !== key);
    }
  }
  assert.equal(h.requests.length, before);
  await h.get("nav-devices").emit("click");
  await h.get("nav-events").emit("click");
  // El DOM mínimo no emite toggle al cambiar open: representarlo explícitamente.
  await h.get("health-event-history").emit("toggle");
  assert.ok(h.requests.some(request => request.url.includes("/2/events?")));
  assert.equal(h.frontend.state.selectedId, 2);
  assert.equal(h.frontend.state.running, true);
  assert.equal(h.get("medical-notes").value, "Borrador de expediente");
  assert.equal(h.get("manual-form").payload.observations, "Borrador manual");
  h.intervals[0].callback();
  await flush();
  assert.equal(h.get("medical-notes").value, "Borrador de expediente");
  assert.equal(h.get("manual-form").payload.observations, "Borrador manual");
  assert.match(h.get("selected-crew-name").textContent, /2/);
  assert.match(h.get("device-summary").textContent, /Integración no disponible/);
  assert.equal(h.medicalPosts().length, 0);
});

test("el menú maneja foco, flechas, Escape y retorno al Centro de control", async () => {
  const h = await harness();
  await h.get("menu-toggle").emit("click");
  assert.equal(h.get("module-menu").open, true);
  assert.equal(h.get("menu-toggle").attributes["aria-expanded"], "true");
  assert.equal(h.document.activeElement, h.get("nav-control"));
  await h.get("module-menu").emit("keydown", { key: "ArrowDown" });
  assert.equal(h.document.activeElement, h.get("nav-history"));
  await h.get("module-menu").emit("keydown", { key: "End" });
  assert.equal(h.document.activeElement, h.get("nav-research"));
  await h.get("module-menu").emit("keydown", { key: "Home" });
  assert.equal(h.document.activeElement, h.get("nav-control"));
  await h.get("nav-manual").emit("click");
  assert.equal(h.document.activeElement, h.get("manual-title"));
  assert.equal(h.get("module-menu").open, false);
  assert.equal(h.get("control-home").hidden, false);
  await h.get("menu-toggle").emit("click");
  await h.get("module-menu").emit("cancel");
  assert.equal(h.get("module-menu").open, false);
  assert.equal(h.document.activeElement, h.get("menu-toggle"));
  await h.get("control-home").emit("click");
  assert.equal(h.get("control-home").hidden, true);
});

test("el gráfico vuelve a medir el Canvas al mostrar Historial y análisis", async () => {
  const h = await harness();
  let width = 740;
  h.get("history-chart").getBoundingClientRect = () => ({
    width: h.get("view-history").hidden ? 0 : width,
    height: h.get("view-history").hidden ? 0 : 360,
  });
  const strokes = h.chartStrokes;
  h.frontend.renderChart();
  assert.equal(h.chartStrokes, strokes);
  await h.get("open-history").emit("click");
  assert.equal(h.get("history-chart").width, 740);
  assert.ok(h.chartStrokes > strokes);
  await h.get("control-home").emit("click");
  width = 1024;
  await h.get("open-history").emit("click");
  assert.equal(h.get("history-chart").width, 1024);
  assert.equal(h.get("history-records").children.length, 1);
});

test("una prioridad del backend permanece entre vistas, cargas, errores y lecturas no evaluables", async () => {
  const h = await harness();
  const priority = { id: 91, metric: "heart_rate", severity: "priority", current_value: 112,
    source: "configured_protocol", captured_at: "2026-01-01T00:00:00Z", description: "Fixture de interfaz",
    guidance: ["Orientación de prueba"], protocol: { title: "Fixture", validation_reference: "Prueba" } };
  const setAssessment = data => {
    h.frontend.assessmentState.data = data;
    h.frontend.assessmentState.source = "";
    h.frontend.renderAssessment();
  };
  setAssessment(assessment(1, { state: "priority", events: [priority] }));
  assert.equal(h.get("priority-banner").hidden, false);
  assert.equal(h.get("active-alerts").children[0].dataset.state, "priority");
  for (const view of ["medical", "history", "manual", "devices", "events", "control"]) {
    h.frontend.showView(view);
    assert.equal(h.get("priority-banner").hidden, false);
  }
  h.frontend.assessmentState.loading = true;
  setAssessment(null);
  assert.equal(h.get("priority-banner").hidden, false);
  h.frontend.assessmentState.error = "Error de prueba";
  h.frontend.renderAssessment();
  assert.equal(h.get("priority-banner").hidden, false);
  const stale = assessment(1);
  stale.metrics.heart_rate.state = "stale";
  setAssessment(stale);
  assert.equal(h.get("priority-banner").hidden, false);
  await h.get("priority-notice-0").emit("click");
  assert.equal(h.frontend.navigationState.view, "events");
  assert.equal(h.get("priority-detail").hidden, false);
  assert.equal(h.get("priority-detail").children[1].open, true);
  // Un filtro de otro origen no resuelve una prioridad previa de simulación.
  setAssessment(assessment(1, { measurement_source: "manual" }));
  assert.equal(h.get("priority-banner").hidden, false);
  setAssessment(assessment(1, { measurement_source: "simulation" })); // Alias del mismo origen.
  assert.equal(h.get("priority-banner").hidden, true);
});

test("el resumen prioriza eventos sin ocultarlos al seleccionar otro sensor", async () => {
  const h = await harness();
  h.frontend.assessmentState.data = assessment(1, { events: [
    { id: 1, metric: "temperature", severity: "educational", source: "simulation_rule", current_value: 36.7, description: "Demo" },
    { id: 2, metric: "heart_rate", severity: "priority", source: "configured_protocol", current_value: 112, description: "Fixture" },
  ] });
  h.frontend.assessmentState.source = "";
  await h.points.find(point => point.dataset.metric === "temperature").emit("click");
  assert.equal(h.get("events-selected-only").checked, true);
  assert.equal(h.get("active-alerts").children.length, 2);
  assert.equal(h.get("active-alerts").children[0].dataset.state, "priority");
  assert.equal(h.get("priority-banner").hidden, false);
});

function octExam(astronautId = 1, id = 11, changes = {}) {
  return { id, astronaut_id: astronautId, eye: "OD", exam_date: "2026-01-01T12:00:00Z", mission_phase: "preflight",
    device_model: "Equipo sintético", scan_protocol: "Protocolo A", image_quality: "good", is_baseline: true,
    review_status: "pending", reviewed_at: null, reviewed_by: "", review_notes: "", notes: "Demostración",
    provenance: "manual_entry", source_document: "", is_demo: true, files: [],
    measurements: [{ metric: "rnfl", value: 90, unit: "µm", anatomical_region: "Anillo sintético", measurement_method: "Informe v1" }], ...changes };
}

function octComparison(exam) {
  return { astronaut_id: exam.astronaut_id, current_id: exam.id, baseline_id: exam.id, message: "Examen basal", notices: [],
    comparisons: exam.measurements.map(item => ({ ...item, label: item.metric, baseline_value: item.value, current_value: item.value,
      difference: 0, absolute_difference: 0, percent_difference: 0, baseline_date: exam.exam_date, current_date: exam.exam_date,
      points: [{ exam_id: exam.id, exam_date: exam.exam_date, value: item.value, mission_phase: exam.mission_phase, quality: exam.image_quality, provenance: exam.provenance }] })) };
}

test("OCT abre como séptima vista y conserva borradores durante navegación y simulación", async () => {
  const h = await harness(true);
  await h.get("nav-oct").emit("click"); await flush();
  assert.equal(h.frontend.navigationState.view, "oct");
  assert.equal(h.get("view-control").hidden, true);
  h.get("oct-form-device").value = "Borrador de equipo";
  const before = h.requests.filter(request => request.url.includes("/oct/")).length;
  h.frontend.showView("medical"); h.frontend.showView("oct");
  h.intervals[0].callback(); await flush();
  assert.equal(h.get("oct-form-device").value, "Borrador de equipo");
  assert.equal(h.requests.filter(request => request.url.includes("/oct/")).length, before);
  assert.ok(h.requests.some(request => request.url === "/api/simulate"));
  assert.equal(h.requests.filter(request => request.url.includes("/oct/") && request.method === "POST").length, 0);
});

test("OCT ignora exámenes atrasados al cambiar rápidamente de astronauta", async () => {
  const h = await harness(true), pending = new Map();
  h.route = url => {
    const match = url.match(/astronauts\/(\d+)\/oct\/exams$/);
    if (match) { const request = deferred(); pending.set(Number(match[1]), request); return request.promise; }
    const comparison = url.match(/astronauts\/(\d+)\/oct\/comparison/);
    if (comparison) return response(octComparison(octExam(Number(comparison[1]), Number(comparison[1]) + 10)));
  };
  h.frontend.showView("oct");
  await h.select(2); await h.select(3);
  pending.get(3).resolve(response({ astronaut_id: 3, exams: [octExam(3, 13)] })); await flush();
  pending.get(2).resolve(response({ astronaut_id: 2, exams: [octExam(2, 12)] }));
  pending.get(1).resolve(response({ astronaut_id: 1, exams: [octExam(1, 11)] })); await flush();
  assert.equal(h.get("oct-record").textContent, "Astronauta #3");
  assert.equal(h.get("oct-exam-select").value, "13");
  assert.equal(h.get("oct-form-astronaut").textContent, "Astronauta 3");
});

test("guardar OCT bloquea duplicados y selección y permite reintentar sin perder el formulario", async () => {
  const h = await harness(true), save = deferred(), exam = octExam();
  let posted = 0, saved = false;
  h.route = (url, options) => {
    if (url.endsWith("/1/oct/exams")) {
      if (options.method === "POST") { posted++; if (posted === 1) return save.promise; saved = true; return response({ created: true, exam }, 201); }
      return response({ astronaut_id: 1, exams: saved ? [exam] : [] });
    }
    if (url.includes("/oct/comparison")) return response(octComparison(exam));
  };
  h.frontend.showView("oct"); await flush();
  for (const [id, value] of [["oct-form-date", "2026-01-01T12:00:00"], ["oct-form-device", "Equipo sintético"], ["oct-form-protocol", "Protocolo A"]]) h.get(id).value = value;
  h.get("oct-form-demo").checked = true;
  const first = h.get("oct-exam-form").emit("submit");
  await h.get("oct-exam-form").emit("submit"); await h.select(2);
  assert.equal(posted, 1);
  assert.equal(h.frontend.state.selectedId, 1);
  assert.equal(h.get("oct-exam-fields").disabled, true);
  const original = h.requests.find(request => request.url.endsWith("/1/oct/exams") && request.method === "POST");
  assert.equal(JSON.parse(original.body.get("exam")).device_model, "Equipo sintético");
  save.resolve(response({ error: "Fallo temporal" }, 503)); await first;
  assert.equal(h.get("oct-form-device").value, "Equipo sintético");
  await h.get("oct-exam-form").emit("submit");
  const posts = h.requests.filter(request => request.url.endsWith("/1/oct/exams") && request.method === "POST");
  assert.equal(posts[0].headers["Idempotency-Key"], posts[1].headers["Idempotency-Key"]);
  assert.match(h.get("oct-status").textContent, /guardado/);
  assert.equal(h.get("oct-form-device").value, "");
});

test("un JSON leído para otro astronauta no reemplaza el formulario actual", async () => {
  const h = await harness(true), pending = deferred();
  h.get("oct-json-file").files = [{ name: "demo.json", size: 100, text: () => pending.promise }];
  const importing = h.get("oct-json-file").emit("change");
  await h.select(2);
  h.get("oct-form-device").value = "Borrador del segundo";
  pending.resolve(JSON.stringify({ is_demo: true, measurements: [] })); await importing;
  assert.equal(h.get("oct-form-device").value, "Borrador del segundo");
  assert.equal(h.requests.filter(request => request.url === "/api/oct/validate").length, 0);
});

test("el selector OCT conserva indicador y región al cambiar el periodo", async () => {
  const h = await harness(true);
  const exam = octExam(1, 11, { measurements: [...octExam().measurements,
    { metric: "choroidal", value: 200, unit: "µm", anatomical_region: "Región coroidea sintética", measurement_method: "Informe v1" }] });
  h.route = url => url.endsWith("/1/oct/exams") ? response({ astronaut_id: 1, exams: [exam] })
    : url.includes("/oct/comparison") ? response(octComparison(exam)) : undefined;
  h.frontend.showView("oct"); await flush();
  h.get("oct-metric-select").value = "1"; await h.get("oct-metric-select").emit("change");
  h.get("oct-period-from").value = "2025-12-01";
  await h.get("oct-compare").emit("click");
  assert.equal(h.get("oct-metric-select").value, "1");
  assert.match(h.get("oct-chart-caption").textContent, /Región coroidea/);
  assert.ok(h.requests.some(request => request.url.includes("from=2025-12-01")));
});

test("el visualizador OCT ajusta la imagen, amplía y restablece sin cambiar el examen", async () => {
  const h = await harness(true), exam = octExam(1, 11, { files: [{ id: 7, original_name: "synthetic.png", file_type: "PNG", checksum: "demo", content_url: "/api/oct/files/7/content?astronaut_id=1",
    metadata: { width: 1000, height: 500, preview_transform: "RGB" } }] });
  h.route = url => url.endsWith("/1/oct/exams") ? response({ astronaut_id: 1, exams: [exam] })
    : url.includes("/oct/comparison") ? response(octComparison(exam)) : undefined;
  h.frontend.showView("oct"); await flush();
  h.get("oct-image").naturalWidth = 1000; h.get("oct-image").naturalHeight = 500;
  await h.get("oct-image").emit("load");
  assert.equal(h.get("oct-image").style.width, "600px");
  await h.get("oct-zoom-in").emit("click");
  assert.equal(h.get("oct-image").style.width, "750px");
  assert.equal(h.get("oct-zoom-label").textContent, "125 %");
  await h.get("oct-zoom-reset").emit("click");
  assert.equal(h.get("oct-image").style.width, "600px");
  assert.equal(h.get("oct-exam-select").value, "11");
});

test("el bloqueo OCT dura hasta recuperar el examen y actualizar el resumen médico", async () => {
  const h = await harness(true), exam = octExam(), refresh = deferred(), summary = deferred();
  let committed = false;
  h.route = (url, options) => {
    if (url.endsWith("/1/oct/exams")) {
      if (options.method === "POST") { committed = true; return response({ created: true, exam }, 201); }
      return committed ? refresh.promise : response({ astronaut_id: 1, exams: [] });
    }
    if (url.endsWith("/1/oct/summary") && committed) return summary.promise;
    if (url.includes("/oct/comparison")) return response(octComparison(exam));
  };
  h.frontend.showView("oct"); await flush();
  h.get("oct-form-date").value = "2026-01-01T12:00:00";
  h.get("oct-form-device").value = "Equipo sintético";
  h.get("oct-form-protocol").value = "Protocolo A";
  h.get("oct-form-demo").checked = true;
  const pending = h.get("oct-exam-form").emit("submit"); await flush();
  assert.equal(h.get("oct-exam-submit").disabled, true);
  await h.select(2);
  assert.equal(h.frontend.state.selectedId, 1);
  refresh.resolve(response({ astronaut_id: 1, exams: [exam] })); await flush();
  assert.equal(h.get("oct-exam-submit").disabled, true);
  await h.get("oct-exam-form").emit("submit"); await h.select(2);
  assert.equal(h.requests.filter(request => request.url.endsWith("/1/oct/exams") && request.method === "POST").length, 1);
  assert.equal(h.frontend.state.selectedId, 1);
  summary.resolve(response({ astronaut_id: 1, exam_count: 1, baselines: [], last_exam_date: exam.exam_date, last_review: null, latest_notes: "" }));
  await pending;
  assert.equal(h.get("oct-exam-submit").disabled, false);
  assert.equal(h.document.activeElement, h.get("oct-title"));
});

test("una importación JSON pendiente se descarta cuando el usuario guarda su borrador actual", async () => {
  const h = await harness(true), reading = deferred(), exam = octExam();
  let saved = false;
  h.route = (url, options) => {
    if (url.endsWith("/1/oct/exams")) {
      if (options.method === "POST") { saved = true; return response({ created: true, exam }, 201); }
      return response({ astronaut_id: 1, exams: saved ? [exam] : [] });
    }
    if (url.includes("/oct/comparison")) return response(octComparison(exam));
    if (url === "/api/oct/validate") throw new Error("Una importación antigua no debe reemplazar el examen guardado");
  };
  h.frontend.showView("oct"); await flush();
  h.get("oct-json-file").files = [{ name: "pending.json", size: 100, text: () => reading.promise }];
  const importing = h.get("oct-json-file").emit("change");
  h.get("oct-form-date").value = "2026-01-01T12:00:00";
  h.get("oct-form-device").value = "Equipo sintético";
  h.get("oct-form-protocol").value = "Protocolo A";
  h.get("oct-form-demo").checked = true;
  await h.get("oct-exam-form").emit("submit");
  reading.resolve(JSON.stringify({ is_demo: true, measurements: [] })); await importing;
  assert.equal(h.get("oct-form-device").value, "");
  assert.equal(h.requests.filter(request => request.url === "/api/oct/validate").length, 0);
  assert.match(h.get("oct-status").textContent, /guardado/);
});

test("OCT guarda una medición parcial y deja pendiente su unidad y contexto", async () => {
  const h = await harness(true);
  const exam = octExam(1, 11, { device_model: "", scan_protocol: "", is_baseline: false,
    measurements: [{ metric: "rnfl", value: 90, unit: "", anatomical_region: "", measurement_method: "" }] });
  let saved = false;
  h.route = (url, options) => {
    if (url.endsWith("/1/oct/exams")) {
      if (options.method === "POST") { saved = true; return response({ created: true, exam }, 201); }
      return response({ astronaut_id: 1, exams: saved ? [exam] : [] });
    }
    if (url.includes("/oct/comparison")) return response({ astronaut_id: 1, current_id: 11,
      baseline_id: null, message: "No existe un examen basal comparable", comparisons: [], notices: [] });
  };
  h.frontend.showView("oct"); await flush();
  h.get("oct-form-date").value = "2026-01-01T12:00:00";
  h.get("oct-form-demo").checked = true;
  await h.get("oct-add-measurement").emit("click");
  assert.equal(h.get("oct-row-1-unit").value, "");
  h.get("oct-row-1-metric").value = "rnfl";
  h.get("oct-row-1-value").value = "90";
  await h.get("oct-exam-form").emit("submit");
  const request = h.requests.find(item => item.url.endsWith("/1/oct/exams") && item.method === "POST");
  const measurement = JSON.parse(request.body.get("exam")).measurements[0];
  assert.deepEqual(measurement, { metric: "rnfl", value: 90, unit: "", anatomical_region: "", measurement_method: "" });
  assert.match(h.get("oct-measurements").children[0].children[1].textContent, /unidad pendiente/);
  assert.equal(h.get("oct-comparison-status").textContent, "No existe un examen basal comparable");
});
