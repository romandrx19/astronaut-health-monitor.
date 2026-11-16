const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const script = fs.readFileSync(path.join(root, "static/js/research.js"), "utf8");
const html = fs.readFileSync(path.join(root, "templates/research.html"), "utf8");
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

function harness() {
  const elements = new Map(), requests = [], drawing = [];
  const ctx = new Proxy({}, { get(target, key) { return key in target ? target[key] : (...args) => drawing.push({ op: key, args, strokeStyle: target.strokeStyle }); } });
  class Element {
    constructor(tag = "div") { this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {}; this.value = ""; this.hidden = false; this.disabled = false; this.classList = { add() {}, remove() {} }; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(key, handler) { (this.listeners[key] ||= []).push(handler); }
    async emit(key, extra = {}) { for (const handler of this.listeners[key] || []) await handler({ preventDefault() {}, ...extra }); await flush(); }
    getBoundingClientRect() { return { left: 0, width: 900, height: 300 }; }
    getContext() { return ctx; }
    set innerHTML(_) { throw new Error("No se permite insertar HTML desde datos científicos"); }
  }
  for (const match of html.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], new Element());
  const page = (items, params, total = items.length) => ({ items, total, page: Number(params.get("page") || 1),
    page_size: Number(params.get("page_size") || 50), pages: Math.ceil(total / Number(params.get("page_size") || 50)) });
  const source = { source_code: "CRF", entities: ["vital_signs", "hydration"], selected_families: "Daily Vitals", note: "Sin SpO2", archive_filename: "fuente.zip", sha256: "abc123" };
  const metrics = [{ entity: "vital_signs", metric: "heart_rate_bpm", label: "Frecuencia cardíaca", unit: "bpm", available: 2, records: 3, dated_records: 3 },
    { entity: "vital_signs", metric: "weight_kg", label: "Peso", unit: "kg", available: 0, records: 3, dated_records: 3 }];
  const h = { elements, requests, drawing, route: null, metricFixture: metrics };
  const get = id => elements.get(`research-${id}`);
  h.get = get;
  const context = vm.createContext({ document: { body: { dataset: { view: "research" } }, getElementById: id => elements.get(id), createElement: tag => new Element(tag) },
    window: { devicePixelRatio: 1, addEventListener() {} }, AbortController, URLSearchParams,
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      if (h.route) { const routed = h.route(url, options); if (routed !== undefined) return routed; }
      const [route, query] = url.split("?"), params = new URLSearchParams(query);
      if (route.endsWith("datasets")) return response(page([source, { ...source, source_code: "BRSMAct", entities: ["sleep"] }], params));
      if (route.endsWith("subjects")) return response(page([{ subject_id: "C1:A", campaign: "C1" }, { subject_id: "C1:B", campaign: "C1" }], params, params.get("dataset") ? 2 : 52));
      if (route.endsWith("summary")) return response({ metrics: h.metricFixture, quality_count: 1, quality: [], records: { vital_signs: 3 } });
      if (route.endsWith("quality-flags")) return response(page([{ flag: "conflicting_values", source_date: "2004-01-02", field_name: "heart_rate_bpm", original_value: null,
        source_file: "source", source_row: 1, notes: "<script>texto de origen</script>" }], params, 11));
      if (route.endsWith("series")) return response(page([70, null, 0].map((value, i) => ({ date: `2004-01-0${i + 1}`, value: params.get("metric") === "weight_kg" ? null : value,
        quality_flags: i === 2 ? ["possible_outlier"] : [] })), params));
      if (route.endsWith("vital-signs")) return response(page([70, null, 0].map((value, i) => ({ id: i, date: `2004-01-0${i + 1}`,
        heart_rate_bpm: value, weight_kg: null, phase: "PRE_TEST", br_day: i, source_file: "<img src=x onerror=alert(1)>", source_row: i, quality_flags: [] })), params, 25));
      throw new Error(`Ruta inesperada: ${route}`);
    } });
  vm.runInContext(script + "\nglobalThis.researchModule = ResearchModule;", context);
  h.module = context.researchModule;
  return h;
}

test("la sección se carga a petición y solo consulta rutas de investigación", async () => {
  const h = harness(); assert.equal(h.requests.length, 0);
  await h.module.show();
  assert.match(h.get("catalog-count").textContent, /52 participantes/);
  assert.ok(h.requests.every(item => item.url.startsWith("/api/research/")));
  assert.equal(h.get("content").hidden, false);
  assert.match(h.get("status").textContent, /C1:A/);
});

test("los valores NULL siguen ausentes y el cero se representa como dato", async () => {
  const h = harness(); await h.module.show();
  const rows = h.get("results").children;
  assert.equal(rows[1].children[1].textContent, "No disponible");
  assert.equal(rows[2].children[1].textContent, "0 bpm");
  const coordinates = h.module.chartGeometry([{ date: "2004-01-01", value: 10 }, { date: "2004-01-02", value: null }, { date: "2004-01-03", value: 0 }], 600, 300);
  assert.equal(coordinates[1].y, null);
  assert.ok(Number.isFinite(coordinates[2].y));
  assert.ok(coordinates[0].x < coordinates[2].x);
});

test("el trazado no conecta a través de un valor ausente y marca puntos señalados", async () => {
  const h = harness(); await h.module.show();
  assert.equal(h.drawing.filter(item => item.op === "lineTo" && item.strokeStyle === "#5cdeed").length, 0);
  assert.equal(h.drawing.filter(item => item.op === "moveTo" && item.strokeStyle === "#5cdeed").length, 2);
  assert.equal(h.drawing.filter(item => item.op === "arc").length, 2);
  assert.match(h.get("chart").attributes["aria-label"], /2 valores diarios/);
});

test("un indicador sin datos muestra estado vacío sin inventar puntos", async () => {
  const h = harness(); await h.module.show();
  h.get("metric").value = "weight_kg"; await h.get("metric").emit("change");
  assert.equal(h.get("chart-empty").hidden, false);
  assert.equal(h.get("results").children[0].children[1].textContent, "No disponible");
});

test("fechas y paginación se envían al backend sin cambiar de participante", async () => {
  const h = harness(); await h.module.show();
  h.get("from").value = "2004-01-01"; h.get("to").value = "2004-01-31";
  await h.get("filters").emit("submit");
  assert.ok(h.requests.some(item => item.url.includes("from=2004-01-01") && item.url.includes("to=2004-01-31")));
  await h.get("next").emit("click");
  assert.match(h.get("page").textContent, /Página 2 de 3/);
  assert.ok(h.requests.some(item => item.url.includes("vital-signs?") && item.url.includes("page=2") && item.url.includes("subject_id=C1%3AA")));
});

test("la procedencia y las banderas usan texto seguro y conservan sus detalles", async () => {
  const h = harness(); await h.module.show();
  assert.match(h.get("flags").children[0].children[1].textContent, /<script>/);
  assert.match(h.get("results").children[0].children[4].textContent, /<img/);
  assert.equal(h.get("source").children[0].textContent, "CRF");
  assert.match(h.get("quality-count").textContent, /11 bandera/);
  await h.get("flags-next").emit("click");
  assert.match(h.get("flags-page").textContent, /Página 2 de 3/);
});

test("cambiar rápidamente A→B→A descarta la primera respuesta de A", async () => {
  const h = harness(); await h.module.show();
  const pending = [];
  h.route = url => { if (url.includes("/summary?")) { const request = deferred(); pending.push(request); return request.promise; } };
  h.get("subject").value = "C1:A"; void h.get("subject").emit("change"); await flush();
  h.get("subject").value = "C1:B"; void h.get("subject").emit("change"); await flush();
  h.get("subject").value = "C1:A"; void h.get("subject").emit("change"); await flush();
  assert.equal(pending.length, 3);
  pending[2].resolve(response({ metrics: h.metricFixture })); await flush();
  const latest = h.get("chart-title").textContent;
  pending[1].resolve(response({ metrics: [{ ...h.metricFixture[0], label: "Respuesta de B" }] }));
  pending[0].resolve(response({ metrics: [{ ...h.metricFixture[0], label: "Respuesta antigua de A" }] })); await flush();
  assert.equal(h.get("chart-title").textContent, latest);
  assert.doesNotMatch(h.get("chart-title").textContent, /antigua|Respuesta de B/);
});

test("un error de consulta muestra reintento y oculta los datos anteriores", async () => {
  const h = harness(); await h.module.show();
  h.route = url => url.includes("/summary?") ? response({ error: "Fecha inicial inválida" }, 400) : undefined;
  await h.get("filters").emit("submit");
  assert.equal(h.get("status").textContent, "Fecha inicial inválida");
  assert.equal(h.get("content").hidden, true);
  assert.equal(h.get("retry").hidden, false);
  h.route = null; await h.get("retry").emit("click");
  assert.equal(h.get("content").hidden, false);
});
