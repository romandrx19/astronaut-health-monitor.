/* Archivo científico histórico. No modifica tripulación, simulación, reglas ni dispositivos. */
const ResearchModule = (() => {
  const labels = { vital_signs: "Signos vitales", sleep: "Sueño", nutrition: "Nutrición", hydration: "Hidratación", lab_results: "Laboratorio" };
  const flagLabels = { possible_outlier: "Posible valor atípico", suspect_year: "Año sospechoso", br_day_mismatch: "Día de estudio inconsistente",
    conflicting_values: "Valores en conflicto", duplicate_daily_key: "Registros diarios duplicados", invalid_numeric: "Valor numérico inválido", date_unavailable: "Fecha no disponible" };
  const research = { initialized: false, loaded: false, revision: 0, controller: null, datasets: [], metrics: [],
    dataset: "CRF", subject: "", entity: "", metric: "", page: 1, flagsPage: 1, points: [], plotted: [], summary: null };
  const el = id => document.getElementById(`research-${id}`);
  const make = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  };
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const display = value => value === null || value === undefined ? "No disponible" : finite(value)
    ? value.toLocaleString("es-PE", { maximumFractionDigits: 4 }) : String(value);
  const flagText = flag => flagLabels[flag] || flag;

  function options(select, items, selected) {
    select.replaceChildren(...items.map(([value, label]) => {
      const option = make("option", "", label); option.value = value; return option;
    }));
    select.value = items.some(([value]) => value === selected) ? selected : items[0]?.[0] || "";
  }

  function parameters(extra = {}) {
    const params = { dataset: research.dataset, subject_id: research.subject,
      from: el("from").value, to: el("to").value, ...extra };
    return new URLSearchParams(Object.entries(params).filter(([, value]) => value !== "" && value !== null && value !== undefined));
  }

  async function request(route, params, signal) {
    const response = await fetch(`/api/research/${route}?${params}`, { signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Error HTTP ${response.status}`);
    return result;
  }

  function begin() {
    research.controller?.abort();
    research.controller = new AbortController();
    const revision = ++research.revision;
    el("status").classList.remove("error"); el("status").textContent = "Consultando archivo histórico…";
    el("retry").hidden = true; el("content").hidden = true;
    return { revision, signal: research.controller.signal };
  }

  function current(context) { return context.revision === research.revision && !context.signal.aborted; }
  function failure(error, context) {
    if (!current(context) || error.name === "AbortError") return;
    el("subject").disabled = false;
    el("status").textContent = error.message; el("status").classList.add("error");
    el("retry").hidden = false; el("content").hidden = true;
  }

  async function loadDataset() {
    const context = begin();
    research.dataset = el("dataset").value;
    el("subject").disabled = true;
    research.page = 1; research.flagsPage = 1;
    try {
      const subjects = await request("subjects", new URLSearchParams({ dataset: research.dataset, page_size: 200 }), context.signal);
      if (!current(context)) return;
      options(el("subject"), subjects.items.map(item => [item.subject_id, `${item.subject_id} · ${item.campaign}`]), research.subject);
      research.subject = el("subject").value;
      el("subject").disabled = false;
      const dataset = research.datasets.find(item => item.source_code === research.dataset);
      options(el("entity"), (dataset?.entities || Object.keys(labels)).map(key => [key, labels[key]]), research.entity);
      research.entity = el("entity").value;
      renderSource(dataset);
      await loadData(context, true);
    } catch (error) { failure(error, context); }
  }

  async function loadData(context = begin(), refreshSummary = true) {
    try {
      research.subject = el("subject").value; research.entity = el("entity").value;
      if (!research.subject) {
        el("status").textContent = "Este conjunto no tiene participantes con mediciones estructuradas.";
        return;
      }
      if (refreshSummary) {
        const summary = await request("summary", parameters(), context.signal);
        if (!current(context)) return;
        research.summary = summary;
        research.metrics = summary.metrics.filter(item => item.entity === research.entity);
        const previous = research.metrics.find(item => item.metric === research.metric);
        const selected = previous?.metric || research.metrics.find(item => item.available > 0)?.metric || research.metrics[0]?.metric;
        options(el("metric"), research.metrics.map(item => [item.metric, `${item.label} (${item.unit})${item.available ? "" : " · Sin mediciones"}`]), selected);
        research.metric = el("metric").value;
      }
      renderCards();
      const [records, flags] = await Promise.all([
        request(research.entity.replaceAll("_", "-"), parameters({ metric: research.metric, page: research.page, page_size: 12 }), context.signal),
        request("quality-flags", parameters({ page: research.flagsPage, page_size: 5 }), context.signal),
      ]);
      if (!current(context)) return;
      const points = [];
      if (research.metric) {
        let page = 1, result;
        do {
          result = await request("series", parameters({ entity: research.entity, metric: research.metric, page, page_size: 200 }), context.signal);
          if (!current(context)) return;
          points.push(...result.items); page++;
        } while (page <= result.pages);
      }
      research.points = points;
      renderRecords(records); renderFlags(flags);
      el("content").hidden = false;
      const metric = research.metrics.find(item => item.metric === research.metric);
      const undated = Math.max(0, (metric?.records || 0) - (metric?.dated_records || 0));
      el("status").textContent = `${research.subject} · ${labels[research.entity]} · ${records.total} registro(s) originales${undated ? ` · ${undated} sin fecha: solo en tabla` : ""}.`;
      renderChart();
      research.loaded = true;
    } catch (error) { failure(error, context); }
  }

  function renderSource(dataset) {
    const box = el("source"); box.replaceChildren();
    if (!dataset) {
      for (const source of research.datasets) {
        const details = make("details"); details.append(make("summary", "", `${source.source_code} · Archivo y procedencia`),
          make("p", "muted", source.note), make("p", "muted", source.archive_filename), make("code", "", source.sha256));
        box.append(details);
      }
      return;
    }
    box.append(make("strong", "", dataset.source_code), make("p", "muted", dataset.selected_families), make("p", "", dataset.note));
    const details = make("details"); details.append(make("summary", "", "Archivo de origen y huella SHA-256"),
      make("p", "muted", dataset.archive_filename), make("code", "", dataset.sha256));
    box.append(details);
  }

  function renderCards() {
    const cards = el("cards"); cards.replaceChildren();
    let metrics = research.metrics.slice(0, 6);
    const chosen = research.metrics.find(item => item.metric === research.metric);
    if (chosen && !metrics.includes(chosen)) metrics = [...metrics.slice(0, 5), chosen];
    for (const item of metrics) {
      const card = make("button", "research-card"); card.type = "button";
      card.setAttribute("aria-pressed", String(item.metric === research.metric));
      card.append(make("span", "research-card-label", item.label), make("strong", "", item.available),
        make("span", "", `valores registrados · ${item.unit}${item.available ? "" : " · No disponible"}`));
      card.addEventListener("click", () => { el("metric").value = item.metric; changeMetric(); });
      cards.append(card);
    }
  }

  function renderRecords(result) {
    const body = el("results"); body.replaceChildren();
    const metric = research.metrics.find(item => item.metric === research.metric);
    el("value-heading").textContent = `${metric?.label || "Valor"} (${metric?.unit || "—"})`;
    el("record-count").textContent = `${result.total} registro(s)`;
    for (const record of result.items) {
      const tr = make("tr"), dateCell = make("td", "", record.date || "Fecha no disponible");
      if (record.measurement_time) dateCell.append(make("small", "", `${record.measurement_time} · hora local sin zona`));
      if (record.raw_date && record.raw_date !== record.date) dateCell.append(make("small", "", `Original: ${record.raw_date}`));
      const value = record[research.entity === "lab_results" ? "value" : research.metric];
      const quality = record.quality_flags || [];
      tr.append(dateCell, make("td", "", `${display(value)}${finite(value) ? ` ${metric?.unit || ""}` : ""}`),
        make("td", "", `${record.phase || "Sin fase"} / ${record.br_day ?? "—"}`),
        make("td", quality.length ? "research-flag-tag" : "muted", quality.length ? [...new Set(quality.map(item => flagText(item.flag)))].join(" · ") : "Sin bandera registrada"),
        make("td", "", `${record.source_file} · fila ${record.source_row}`));
      body.append(tr);
    }
    if (!result.items.length) {
      const tr = make("tr"), cell = make("td", "muted", "Sin registros para los filtros seleccionados. Los campos vacíos no equivalen a cero.");
      cell.colSpan = 5; tr.append(cell); body.append(tr);
    }
    paginate(result, "", research.page);
  }

  function paginate(result, prefix, page) {
    el(`${prefix}prev`).disabled = page <= 1;
    el(`${prefix}next`).disabled = page >= result.pages;
    el(`${prefix}page`).textContent = result.pages ? `Página ${page} de ${result.pages}` : "Sin resultados";
  }

  function renderFlags(result) {
    el("quality-count").textContent = `${result.total} bandera(s)`;
    el("quality-summary").textContent = result.total ? "Revisar los archivos originales antes de interpretar. Las banderas no constituyen un diagnóstico."
      : "No hay banderas registradas para esta selección; esto no acredita validación clínica.";
    const box = el("flags"); box.replaceChildren();
    for (const flag of result.items) {
      const item = make("article", "research-flag");
      item.append(make("strong", "", flagText(flag.flag)), make("p", "", flag.notes || "Revisar el registro original."),
        make("small", "", `${flag.source_date || "Fecha no disponible"} · ${flag.field_name || "Campo sin especificar"} · valor original: ${flag.original_value ?? "No disponible"}`),
        make("small", "", `${flag.source_file} · fila ${flag.source_row ?? "No disponible"}`));
      box.append(item);
    }
    paginate(result, "flags-", research.flagsPage);
  }

  function chartGeometry(points, width, height) {
    const valid = points.filter(item => /^\d{4}-\d{2}-\d{2}$/.test(item.date) && finite(item.value));
    if (!valid.length) return [];
    const values = valid.map(item => item.value);
    const times = points.map(item => Date.parse(`${item.date}T00:00:00Z`)).filter(Number.isFinite);
    const low = Math.min(...values), high = Math.max(...values), spread = high - low || Math.max(Math.abs(low) * .1, 1);
    const min = low - spread * .12, max = high + spread * .12;
    const first = Math.min(...times), last = Math.max(...times), span = last - first || 1;
    return points.map(item => ({ ...item,
      x: 65 + (Date.parse(`${item.date}T00:00:00Z`) - first) / span * Math.max(1, width - 95),
      y: finite(item.value) ? height - 38 - (item.value - min) / (max - min) * (height - 58) : null,
      min, max, first, last }));
  }

  function renderChart() {
    if (!research.initialized || document.body.dataset.view !== "research" || el("content").hidden) return;
    const canvas = el("chart"), box = canvas.getBoundingClientRect(), width = box.width || 600, height = box.height || 280;
    const scale = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
    const ctx = canvas.getContext("2d"); ctx.scale(scale, scale); ctx.clearRect(0, 0, width, height);
    const metric = research.metrics.find(item => item.metric === research.metric);
    el("chart-title").textContent = `${metric?.label || "Indicador"} · ${research.subject}`;
    el("chart-caption").textContent = `Fecha de estudio · ${metric?.unit || "Sin unidad"} · Los conflictos diarios y valores rechazados por la vista del proveedor se mantienen sin medición.`;
    const points = chartGeometry(research.points, width, height); research.plotted = points;
    el("chart-empty").hidden = points.length > 0;
    canvas.setAttribute("aria-label", `${metric?.label || "Indicador"}: ${points.filter(item => item.y !== null).length} valores diarios disponibles. Eje de fechas de estudio, sin hora UTC de captura. Resultados originales en la tabla inferior.`);
    if (!points.length) return;
    ctx.font = "11px system-ui"; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = 20 + (height - 58) * i / 4;
      ctx.strokeStyle = "#243547"; ctx.beginPath(); ctx.moveTo(62, y); ctx.lineTo(width - 25, y); ctx.stroke();
      ctx.fillStyle = "#9aacc1"; ctx.textAlign = "right";
      ctx.fillText(display(points[0].max - (points[0].max - points[0].min) * i / 4), 54, y + 4);
    }
    let connected = false;
    ctx.strokeStyle = "#5cdeed"; ctx.lineWidth = 2; ctx.beginPath();
    for (const point of points) {
      if (point.y === null || !Number.isFinite(point.x)) { connected = false; continue; }
      if (connected) ctx.lineTo(point.x, point.y); else ctx.moveTo(point.x, point.y);
      connected = true;
    }
    ctx.stroke();
    for (const point of points) {
      if (point.y === null || !Number.isFinite(point.x)) continue;
      ctx.fillStyle = point.quality_flags?.length ? "#f2b16b" : "#5cdeed";
      ctx.beginPath(); ctx.arc(point.x, point.y, point.quality_flags?.length ? 4 : 2.5, 0, Math.PI * 2); ctx.fill();
    }
    ctx.fillStyle = "#9aacc1"; ctx.textAlign = "left"; ctx.fillText(research.points[0]?.date || "", 65, height - 10);
    ctx.textAlign = "right"; ctx.fillText(research.points.at(-1)?.date || "", width - 25, height - 10);
    el("chart-detail").textContent = `${points.filter(item => item.y !== null).length} valores diarios · ${points.filter(item => item.y === null).length} fechas sin valor disponible. Consulta las banderas y los originales.`;
  }

  function changeMetric() {
    research.metric = el("metric").value; research.page = 1;
    void loadData(begin(), false);
  }

  function init() {
    if (research.initialized) return;
    research.initialized = true;
    el("dataset").addEventListener("change", () => { research.subject = ""; void loadDataset(); });
    for (const name of ["subject", "entity"]) el(name).addEventListener("change", () => {
      research.page = 1; research.flagsPage = 1; void loadData();
    });
    el("metric").addEventListener("change", changeMetric);
    el("filters").addEventListener("submit", event => { event.preventDefault(); research.page = 1; research.flagsPage = 1; void loadData(); });
    el("reset").addEventListener("click", () => { el("from").value = ""; el("to").value = ""; research.page = 1; research.flagsPage = 1; void loadData(); });
    el("retry").addEventListener("click", () => void show(true));
    for (const [button, field, delta] of [["prev", "page", -1], ["next", "page", 1], ["flags-prev", "flagsPage", -1], ["flags-next", "flagsPage", 1]]) {
      el(button).addEventListener("click", () => { research[field] = Math.max(1, research[field] + delta); void loadData(begin(), false); });
    }
    el("chart").addEventListener("mousemove", event => {
      const x = event.clientX - el("chart").getBoundingClientRect().left;
      const point = research.plotted.reduce((nearest, item) => !nearest || Math.abs(item.x - x) < Math.abs(nearest.x - x) ? item : nearest, null);
      if (point) el("chart-detail").textContent = `${point.date} · ${display(point.value)}${finite(point.value) ? ` ${research.metrics.find(item => item.metric === research.metric)?.unit || ""}` : ""}${point.quality_flags?.length ? ` · ${point.quality_flags.map(flagText).join("; ")}` : ""}`;
    });
    window.addEventListener("resize", renderChart);
  }

  async function show(retry = false) {
    init();
    if (research.loaded && !retry) { renderChart(); return; }
    const context = begin();
    try {
      const [datasets, subjects] = await Promise.all([
        request("datasets", new URLSearchParams({ page_size: 200 }), context.signal),
        request("subjects", new URLSearchParams({ page_size: 1 }), context.signal),
      ]);
      if (!current(context)) return;
      research.datasets = datasets.items;
      options(el("dataset"), [["", "Todos los conjuntos"], ...datasets.items.map(item => [item.source_code, `${item.source_code} · ${item.entities.map(key => labels[key]).join(" / ")}`])], research.dataset);
      el("catalog-count").textContent = `${datasets.total} conjuntos · ${subjects.total} participantes`;
      await loadDataset();
    } catch (error) { failure(error, context); }
  }

  return { show, renderChart, chartGeometry };
})();
