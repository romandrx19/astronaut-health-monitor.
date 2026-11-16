/* Exámenes episódicos. Las actualizaciones del simulador no generan ni recargan OCT. */
const OctModule = (() => {
  const model = { astronautId: null, exams: [], selected: null, loaded: false, loading: false,
    saving: false, generation: 0, comparisonRequest: 0, summaryRequest: 0, comparison: null,
    rows: [], rowSequence: 0, requestKey: null, zoom: 1, fit: 1, crewSignature: "", importRequest: 0, metricKey: null };
  const labels = { rnfl: "Grosor RNFL", trt: "Grosor retiniano total", macular: "Grosor macular", choroidal: "Grosor coroideo" };
  const phases = { preflight: "Preflight", inflight: "Inflight", postflight: "Postflight" };
  const qualities = { good: "Buena", poor: "Baja", ungradable: "No interpretable", unknown: "No informada" };
  const provenance = { manual_entry: "Transcripción manual", exported_report: "Informe exportado" };
  const reviews = { pending: "Pendiente", review_requested: "Revisión solicitada", reviewed: "Revisión registrada" };

  async function octRequest(path, options = {}) {
    const response = await fetch(path, { ...options, credentials: "same-origin",
      headers: { ...options.headers, "X-ARES-OCT-Token": $("oct-session-token").content } });
    let data;
    try { data = await response.json(); } catch (_) { throw new Error("La respuesta OCT no se pudo interpretar"); }
    if (!response.ok) throw new Error(data.error || `Error OCT ${response.status}`);
    return data;
  }

  function notify(message, error = false) {
    $("oct-status").textContent = message;
    $("oct-status").dataset.state = error ? "error" : "";
  }

  function number(value) {
    if (value == null || !Number.isFinite(value)) return "—";
    if (Math.abs(value) >= 1e6 || (value !== 0 && Math.abs(value) < .001)) return value.toExponential(4);
    return value.toLocaleString("es-PE", { maximumSignificantDigits: 6 });
  }

  function localInput(iso) {
    if (!iso) return "";
    const value = new Date(iso);
    if (!Number.isFinite(value.getTime())) throw new Error("Fecha OCT inválida");
    return new Date(value.getTime() - value.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
  }

  function isoInput(id, required = true) {
    const value = $(id).value;
    if (!value && !required) return null;
    const parsed = new Date(value);
    if (!value || !Number.isFinite(parsed.getTime())) throw new Error("Completa una fecha válida para el examen o la revisión");
    return parsed.toISOString();
  }

  function option(value, caption) {
    const element = node("option", "", caption); element.value = String(value); return element;
  }

  function controls() {
    const locked = model.saving || model.loading;
    for (const id of ["oct-astronaut", "oct-eye", "oct-exam-select", "oct-refresh", "oct-json-file", "oct-form-files",
      "oct-exam-submit", "oct-set-baseline", "oct-review-submit", "oct-attach-submit"]) $(id).disabled = locked;
    $("oct-exam-fields").disabled = model.saving;
    $("oct-review-fields").disabled = locked || !model.selected;
    for (const id of ["oct-set-baseline", "oct-review-submit", "oct-attach-submit"]) $(id).disabled = locked || !model.selected;
    $("oct-set-baseline").disabled ||= Boolean(model.selected?.is_baseline);
    $("oct-attach-files").disabled = locked || !model.selected;
    $("oct-exam-form").setAttribute("aria-busy", String(model.saving));
  }

  async function loadSummary() {
    const id = model.astronautId, requestId = ++model.summaryRequest;
    if (!id) return;
    try {
      const summary = await octRequest(`/api/astronauts/${id}/oct/summary`);
      if (id !== model.astronautId || requestId !== model.summaryRequest) return;
      if (summary.astronaut_id !== id) throw new Error("El resumen ocular pertenece a otro astronauta");
      const review = summary.last_review;
      $("ocular-medical-summary").textContent = [
        `Datos OCT de demostración · ${summary.exam_count} examen(es).`,
        `Último examen: ${summary.last_exam_date ? eventTime(summary.last_exam_date) : "sin registros"}.`,
        `Referencias basales marcadas: ${summary.baselines.length ? summary.baselines.map(item => `${item.eye} · #${item.id} · ${item.scan_protocol}`).join(" / ") : "ninguna"}.`,
        review ? `Última revisión registrada: ${eventTime(review.reviewed_at)} · ${review.reviewed_by}. ${review.review_notes || ""}` : "Sin revisión profesional registrada.",
        summary.latest_notes ? `Observaciones del último examen: ${summary.latest_notes}` : "",
      ].filter(Boolean).join("\n");
    } catch (error) {
      if (id === model.astronautId && requestId === model.summaryRequest) $("ocular-medical-summary").textContent = `No se pudo consultar salud ocular. ${error.message}`;
    }
  }

  function clearRegistration() {
    $("oct-exam-form").reset();
    $("oct-json-file").value = "";
    $("oct-measurement-fields").replaceChildren();
    model.rows = []; model.requestKey = null;
  }

  function syncContext() {
    const astronaut = state.crew.find(item => item.id === state.selectedId);
    if (!astronaut) return;
    const signature = JSON.stringify(state.crew.map(item => [item.id, item.name]));
    if (signature !== model.crewSignature) {
      $("oct-astronaut").replaceChildren(...state.crew.map(item => option(item.id, item.name)));
      model.crewSignature = signature;
    }
    $("oct-astronaut").value = String(astronaut.id);
    $("oct-record").textContent = `Astronauta #${astronaut.id}`;
    $("oct-form-astronaut").textContent = astronaut.name;
    if (model.astronautId === astronaut.id) return;
    const previous = model.astronautId;
    model.astronautId = astronaut.id;
    ++model.generation; ++model.comparisonRequest; ++model.importRequest;
    model.exams = []; model.selected = null; model.loaded = false; model.loading = false; model.comparison = null;
    clearRegistration();
    renderExamOptions(); renderSelected(); renderComparison();
    notify(previous ? `Exámenes de ${astronaut.name}. El borrador OCT anterior se descartó al cambiar de astronauta.` : "Registra o importa un examen OCT de demostración.");
    void loadSummary();
    if (navigationState.view === "oct") void loadExams();
  }

  async function show() {
    syncContext();
    if (!model.loaded && !model.loading) await loadExams();
    drawChart(); fitImage();
  }

  function renderExamOptions() {
    const eye = $("oct-eye").value || "OD";
    const visible = model.exams.filter(exam => exam.eye === eye);
    $("oct-exam-select").replaceChildren(...(visible.length ? visible.map(exam => option(exam.id,
      `${eventTime(exam.exam_date)} · ${phases[exam.mission_phase]}${exam.is_baseline ? " · Basal" : ""}`)) : [option("", "Sin exámenes para este ojo")]));
    $("oct-baseline-select").replaceChildren(option("", "Basal marcado compatible"),
      ...visible.filter(exam => exam.is_baseline).map(exam => option(exam.id, `#${exam.id} · ${exam.device_model || "Equipo pendiente"} · ${exam.scan_protocol || "Protocolo pendiente"}`)));
  }

  async function loadExams(selectedId = model.selected?.id, { afterSave = false } = {}) {
    if (!model.astronautId || (model.saving && !afterSave)) return;
    const id = model.astronautId, generation = ++model.generation;
    model.loading = true; controls();
    notify("Consultando exámenes OCT…");
    try {
      const result = await octRequest(`/api/astronauts/${id}/oct/exams`);
      if (id !== model.astronautId || generation !== model.generation) return;
      if (result.astronaut_id !== id || !Array.isArray(result.exams) || result.exams.some(exam => exam.astronaut_id !== id)) throw new Error("Los exámenes no corresponden al astronauta seleccionado");
      model.exams = result.exams; model.loaded = true;
      renderExamOptions();
      const visible = model.exams.filter(exam => exam.eye === $("oct-eye").value);
      model.selected = visible.find(exam => exam.id === selectedId) || visible[0] || null;
      renderSelected();
      await loadComparison();
      if (id === model.astronautId && generation === model.generation) notify(`${result.exams.length} examen(es) OCT de demostración registrados.`);
    } catch (error) {
      if (id === model.astronautId && generation === model.generation) notify(error.message, true);
    } finally {
      if (generation === model.generation) { model.loading = false; controls(); }
    }
  }

  function metadataLine(container, title, value) {
    container.append(node("dt", "", title), node("dd", "", value || "No informado"));
  }

  function renderSelected() {
    const exam = model.selected;
    $("oct-exam-select").value = String(exam?.id || "");
    $("oct-exam-phase").textContent = exam ? `${exam.eye} · ${phases[exam.mission_phase]} · #${exam.id}` : "Sin examen";
    $("oct-file-select").replaceChildren(...(exam?.files.length ? exam.files.map(file => option(file.id, file.original_name)) : [option("", "Sin imágenes importadas")]));
    $("oct-attach-files").value = "";
    const metadata = $("oct-exam-metadata"); metadata.replaceChildren();
    if (exam) {
      for (const [title, value] of [["Fecha UTC", eventTime(exam.exam_date)], ["Equipo", exam.device_model],
        ["Protocolo", exam.scan_protocol], ["Calidad declarada", qualities[exam.image_quality]],
        ["Procedencia", provenance[exam.provenance]], ["Documento", exam.source_document], ["Observaciones", exam.notes]]) metadataLine(metadata, title, value);
    }
    const measurements = $("oct-measurements"); measurements.replaceChildren();
    for (const item of exam?.measurements || []) {
      const card = node("article", "oct-measurement");
      card.append(node("h4", "", labels[item.metric] || item.metric), node("strong", "", `${number(item.value)} ${item.unit || "· unidad pendiente"}`),
        node("p", "", `Región: ${item.anatomical_region || "No informada"}`), node("p", "", `Método: ${item.measurement_method || "No informado"}`));
      measurements.append(card);
    }
    if (!measurements.children.length) measurements.append(node("p", "muted", "Este examen aún no contiene mediciones cuantitativas registradas."));
    $("oct-review-info").textContent = exam ? `${reviews[exam.review_status]}${exam.reviewed_at ? ` · ${eventTime(exam.reviewed_at)} · ${exam.reviewed_by}` : ""}` : "Sin revisión registrada.";
    $("oct-review-status").value = exam?.review_status || "pending";
    $("oct-reviewed-by").value = exam?.reviewed_by || "";
    $("oct-reviewed-at").value = localInput(exam?.reviewed_at);
    $("oct-review-notes").value = exam?.review_notes || "";
    showImage(); controls();
  }

  function showImage() {
    const file = model.selected?.files.find(item => String(item.id) === $("oct-file-select").value)
      || model.selected?.files[0];
    const image = $("oct-image");
    model.zoom = 1;
    image.hidden = true;
    $("oct-image-stage").hidden = !file;
    $("oct-image-empty").hidden = Boolean(file);
    $("oct-file-info").textContent = "";
    if (!file) {
      image.removeAttribute("src");
      $("oct-image-empty").textContent = "Aún no hay imágenes. Registra un examen y carga una exportación PNG, JPEG o TIFF de una sola página.";
      $("oct-zoom-label").textContent = "100 %";
      return;
    }
    $("oct-file-select").value = String(file.id);
    $("oct-file-info").textContent = `${file.file_type} · ${file.metadata.width} × ${file.metadata.height} px · ${file.metadata.preview_transform} · Vista previa hasta 2048 px. SHA-256: ${file.checksum}`;
    image.alt = `Examen OCT de demostración #${model.selected.id}, ${model.selected.eye}, ${eventTime(model.selected.exam_date)}`;
    image.src = file.content_url;
  }

  function fitImage() {
    const image = $("oct-image"), viewport = $("oct-image-viewport");
    if (!image.naturalWidth || !image.naturalHeight || navigationState.view !== "oct") return;
    model.fit = Math.min(viewport.clientWidth / image.naturalWidth, viewport.clientHeight / image.naturalHeight, 1);
    if (model.fit > 0) applyZoom();
  }

  function applyZoom() {
    const image = $("oct-image"), stage = $("oct-image-stage");
    const width = Math.round(image.naturalWidth * model.fit * model.zoom);
    const height = Math.round(image.naturalHeight * model.fit * model.zoom);
    image.style.width = `${width}px`; image.style.height = `${height}px`;
    stage.style.width = `${width}px`; stage.style.height = `${height}px`;
    $("oct-zoom-label").textContent = `${Math.round(model.zoom * 100)} %`;
  }

  async function loadComparison() {
    const id = model.astronautId, selectedId = model.selected?.id, requestId = ++model.comparisonRequest;
    model.comparison = null; renderComparison();
    if (!id || !selectedId) return;
    const params = new URLSearchParams({ eye: model.selected.eye, current_id: selectedId });
    for (const [key, field] of [["baseline_id", "oct-baseline-select"], ["from", "oct-period-from"], ["to", "oct-period-to"]]) {
      if ($(field).value) params.set(key, $(field).value);
    }
    try {
      const result = await octRequest(`/api/astronauts/${id}/oct/comparison?${params}`);
      if (id !== model.astronautId || selectedId !== model.selected?.id || requestId !== model.comparisonRequest) return;
      if (result.astronaut_id !== id || result.current_id !== selectedId) throw new Error("La comparación no corresponde al examen seleccionado");
      model.comparison = result; renderComparison();
    } catch (error) {
      if (requestId === model.comparisonRequest && id === model.astronautId) {
        $("oct-comparison-status").textContent = error.message; notify(error.message, true);
      }
    }
  }

  function renderComparison() {
    const result = model.comparison;
    $("oct-baseline-info").textContent = result?.baseline_id ? `Referencia basal #${result.baseline_id}. ${result.message}.` : "No existe un examen basal comparable";
    $("oct-comparison-status").textContent = result?.message || "No existe un examen basal comparable";
    const comparisons = result?.comparisons || [];
    $("oct-metric-select").replaceChildren(...(comparisons.length ? comparisons.map((item, index) =>
      option(index, `${item.label} · ${item.anatomical_region} · ${item.unit} · ${item.measurement_method}`)) : [option("", "Sin mediciones comparables")]));
    const selectedIndex = comparisons.findIndex(item => comparisonKey(item) === model.metricKey);
    $("oct-metric-select").value = comparisons.length ? String(Math.max(0, selectedIndex)) : "";
    if (comparisons.length) model.metricKey = comparisonKey(selectedComparison());
    const notices = $("oct-notices"); notices.replaceChildren();
    for (const item of result?.notices || []) {
      const notice = node("article", "oct-notice"); notice.dataset.kind = item.kind;
      notice.append(node("p", "", item.reason));
      if (item.metric) notice.append(node("p", "", `${labels[item.metric] || item.metric}: ${number(item.previous_value)} → ${number(item.current_value)} ${item.unit}. Diferencia: ${number(item.difference)} ${item.unit}.`));
      if (item.exam_date) notice.append(node("p", "muted", `${eventTime(item.exam_date)} · Calidad: ${qualities[item.quality] || item.quality}`));
      if (item.guidance) notice.append(node("p", "", item.guidance));
      if (item.validation_reference) notice.append(node("p", "muted", `Regla configurada: ${item.validation_reference}`));
      notices.append(notice);
    }
    renderTrend();
  }

  function selectedComparison() {
    const value = $("oct-metric-select").value;
    return value === "" ? null : model.comparison?.comparisons[Number(value)];
  }

  function comparisonKey(item) {
    return JSON.stringify([item.metric, item.unit, item.anatomical_region, item.measurement_method]);
  }

  function renderTrend() {
    const item = selectedComparison(), values = $("oct-comparison-values"), rows = $("oct-history-records");
    values.replaceChildren(); rows.replaceChildren();
    if (item) {
      for (const [label, value] of [["Basal", `${number(item.baseline_value)} ${item.unit}`], ["Actual", `${number(item.current_value)} ${item.unit}`],
        ["Diferencia actual − basal", `${number(item.difference)} ${item.unit}`], ["Magnitud de diferencia", `${number(item.absolute_difference)} ${item.unit}`],
        ["Diferencia porcentual", item.percent_difference == null ? "No calculable con este basal" : `${number(item.percent_difference)} %`]]) {
        const card = node("div"); card.append(node("span", "", label), node("strong", "", value)); values.append(card);
      }
      for (const point of item.points) {
        const row = node("tr");
        row.append(node("td", "", `#${point.exam_id} · ${eventTime(point.exam_date)}`), node("td", "", phases[point.mission_phase]),
          node("td", "", `${number(point.value)} ${item.unit}`), node("td", "", qualities[point.quality]), node("td", "", provenance[point.provenance]));
        rows.append(row);
      }
      $("oct-chart-caption").textContent = `${item.label} · ${item.anatomical_region} · ${item.measurement_method} · ${item.points.length} examen(es) comparables en el periodo. Basal: ${eventTime(item.baseline_date)}. Actual: ${eventTime(item.current_date)}.`;
    } else $("oct-chart-caption").textContent = "Los puntos corresponden a exámenes guardados. Elige un examen con un basal y mediciones comparables.";
    drawChart();
  }

  function drawChart() {
    const canvas = $("oct-chart"), rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
    const ctx = canvas.getContext("2d"); ctx.scale(ratio, ratio);
    const item = selectedComparison(), points = item?.points || [];
    if (!points.length) return;
    let low = Math.min(...points.map(point => point.value)), high = Math.max(...points.map(point => point.value));
    const padding = Math.max(1, Math.abs(low) * .02, (high - low) * .15);
    low -= padding; high += padding;
    if (!Number.isFinite(high - low)) { $("oct-chart-caption").textContent += " Los valores exceden la precisión del gráfico. Consulta la tabla."; return; }
    const time = point => new Date(point.exam_date).getTime();
    const start = time(points[0]), span = time(points.at(-1)) - start;
    const x = point => span ? 62 + (time(point) - start) / span * (rect.width - 80) : rect.width / 2;
    const y = point => 18 + (high - point.value) / (high - low) * (rect.height - 50);
    ctx.font = "11px system-ui"; ctx.textAlign = "right";
    for (let index = 0; index <= 4; index++) {
      const level = low + (high - low) * index / 4, top = 18 + (4 - index) / 4 * (rect.height - 50);
      ctx.strokeStyle = "#263751"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(62, top); ctx.lineTo(rect.width - 18, top); ctx.stroke();
      ctx.fillStyle = "#97aac7"; ctx.fillText(number(level), 54, top + 4);
    }
    ctx.strokeStyle = "#5dd9e8"; ctx.lineWidth = 2; ctx.beginPath();
    points.forEach((point, index) => index ? ctx.lineTo(x(point), y(point)) : ctx.moveTo(x(point), y(point))); ctx.stroke();
    for (const point of points) { ctx.fillStyle = point.exam_id === model.comparison.baseline_id ? "#ffbc70" : "#5dd9e8"; ctx.beginPath(); ctx.arc(x(point), y(point), 4, 0, Math.PI * 2); ctx.fill(); }
    ctx.fillStyle = "#97aac7"; ctx.textAlign = "left";
    ctx.fillText(new Date(points[0].exam_date).toLocaleDateString("es-PE", { timeZone: "UTC" }), 62, rect.height - 8);
    ctx.textAlign = "right";
    ctx.fillText(new Date(points.at(-1).exam_date).toLocaleDateString("es-PE", { timeZone: "UTC" }), rect.width - 18, rect.height - 8);
  }

  function addMeasurement(data = {}) {
    if (model.rows.length >= 50) { notify("El examen admite hasta 50 mediciones documentadas.", true); return; }
    const index = ++model.rowSequence, element = node("div", "oct-measurement-row"), row = { element };
    for (const [key, label, type, placeholder] of [["metric", "Indicador", "text", "rnfl"], ["value", "Valor", "number", ""],
      ["unit", "Unidad", "text", "µm"], ["anatomical_region", "Región anatómica", "text", "Región del informe"],
      ["measurement_method", "Método", "text", "Método documentado"]]) {
      const input = node("input"), caption = node("label", "", label);
      input.id = `oct-row-${index}-${key}`; input.type = type; input.step = "any";
      input.maxLength = key === "metric" ? 64 : key === "unit" ? 32 : 200;
      input.placeholder = placeholder; input.value = data[key] ?? "";
      if (key === "metric") input.setAttribute("list", "oct-metric-codes");
      caption.htmlFor = input.id; caption.append(input); element.append(caption); row[key] = input;
    }
    const remove = node("button", "secondary", "Quitar"); remove.type = "button";
    remove.setAttribute("aria-label", `Quitar medición ${index}`);
    remove.addEventListener("click", () => { model.rows = model.rows.filter(item => item !== row); element.remove(); });
    element.append(remove); model.rows.push(row); $("oct-measurement-fields").append(element);
  }

  function examPayload() {
    if (!$("oct-form-demo").checked) throw new Error("Confirma que los datos y archivos son de demostración");
    const measurements = [];
    for (const row of model.rows) {
      if (["metric", "value", "unit", "anatomical_region", "measurement_method"].every(key => row[key].value.trim() === "")) continue;
      if (["metric", "value"].some(key => row[key].value.trim() === "")) throw new Error("Completa el indicador y el valor de cada medición añadida");
      const value = Number(row.value.value);
      if (row.value.validity.badInput || !Number.isFinite(value)) throw new Error("Introduce un valor numérico finito");
      measurements.push({ metric: row.metric.value.trim(), value, unit: row.unit.value.trim(),
        anatomical_region: row.anatomical_region.value.trim(), measurement_method: row.measurement_method.value.trim() });
    }
    const result = { is_demo: true, eye: $("oct-form-eye").value, exam_date: isoInput("oct-form-date"),
      mission_phase: $("oct-form-phase").value, device_model: $("oct-form-device").value.trim(),
      scan_protocol: $("oct-form-protocol").value.trim(), image_quality: $("oct-form-quality").value,
      is_baseline: $("oct-form-baseline").checked, provenance: $("oct-form-provenance").value,
      source_document: $("oct-form-source").value.trim(), notes: $("oct-form-notes").value, measurements,
      review_status: $("oct-form-review-status").value, review_notes: $("oct-form-review-notes").value };
    if (result.review_status === "reviewed") { result.reviewed_by = $("oct-form-reviewed-by").value; result.reviewed_at = isoInput("oct-form-reviewed-at"); }
    return result;
  }

  function fileList(id) {
    const files = Array.from($(id).files || []);
    if (files.length > 5 || files.some(file => file.size > 10 * 1024 * 1024)) throw new Error("Selecciona hasta 5 imágenes, de 10 MiB como máximo cada una");
    return files;
  }

  async function saveExam(event) {
    event.preventDefault();
    if (model.saving || model.loading || !model.astronautId) return;
    const id = model.astronautId;
    let payload, files;
    try { payload = examPayload(); files = fileList("oct-form-files"); } catch (error) { notify(error.message, true); return; }
    const fingerprint = JSON.stringify([id, payload, files.map(file => [file.name, file.size, file.lastModified])]);
    if (model.requestKey?.fingerprint !== fingerprint) model.requestKey = { fingerprint,
      key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}` };
    const body = new FormData(); body.append("exam", JSON.stringify(payload));
    for (const file of files) body.append("files", file);
    ++model.importRequest;
    model.saving = true; controls(); renderCrew(); notify("Validando y guardando examen OCT…");
    try {
      const result = await octRequest(`/api/astronauts/${id}/oct/exams`, { method: "POST", headers: { "Idempotency-Key": model.requestKey.key }, body });
      if (id !== model.astronautId) return;
      clearRegistration(); $("oct-registration").open = false; $("oct-eye").value = result.exam.eye;
      await loadExams(result.exam.id, { afterSave: true }); await loadSummary();
      if (id !== model.astronautId) return;
      notify(result.created ? "Examen OCT de demostración guardado." : "El examen ya estaba guardado. Se recuperó el registro del reintento.");
      if (navigationState.view === "oct") $("oct-title").focus();
    } catch (error) { if (id === model.astronautId) notify(`${error.message}. El formulario conserva sus datos.`, true); }
    finally { model.saving = false; controls(); renderCrew(); }
  }

  async function importResults() {
    const file = $("oct-json-file").files?.[0], id = model.astronautId, requestId = ++model.importRequest;
    if (!file) return;
    try {
      if (file.size > 200 * 1024) throw new Error("El archivo JSON admite hasta 200 KiB");
      let data = JSON.parse(await file.text());
      if (id !== model.astronautId || requestId !== model.importRequest) return;
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Se requiere un objeto JSON de examen OCT");
      data = await octRequest("/api/oct/validate", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...data, provenance: "exported_report", source_document: data.source_document || file.name }) });
      if (id !== model.astronautId || requestId !== model.importRequest) return;
      const fieldMap = { eye: "eye", mission_phase: "phase", device_model: "device", scan_protocol: "protocol", image_quality: "quality",
        is_baseline: "baseline", notes: "notes", review_status: "review-status", reviewed_by: "reviewed-by", review_notes: "review-notes" };
      const examDate = localInput(data.exam_date), reviewedDate = localInput(data.reviewed_at);
      clearRegistration();
      for (const [key, suffix] of Object.entries(fieldMap)) {
        if (key === "is_baseline") $("oct-form-baseline").checked = data[key] === true;
        else if (data[key] != null) $(`oct-form-${suffix}`).value = String(data[key]);
      }
      $("oct-form-date").value = examDate; $("oct-form-reviewed-at").value = reviewedDate;
      $("oct-form-provenance").value = "exported_report"; $("oct-form-source").value = data.source_document || file.name;
      $("oct-form-demo").checked = false;
      for (const item of data.measurements) addMeasurement(item);
      $("oct-registration").open = true;
      notify("Resultados JSON cargados en el formulario. Revisa la procedencia, adjunta las imágenes y confirma los datos de demostración antes de guardar.");
    } catch (error) { if (id === model.astronautId && requestId === model.importRequest) notify(`No se pudo importar el JSON. ${error.message}`, true); }
  }

  async function saveExisting(changes) {
    if (model.saving || !model.selected) return;
    const id = model.astronautId, examId = model.selected.id;
    model.saving = true; controls(); renderCrew();
    try {
      await octRequest(`/api/oct/exams/${examId}?astronaut_id=${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes) });
      if (id !== model.astronautId) return;
      await loadExams(examId, { afterSave: true }); await loadSummary();
      if (id === model.astronautId) notify("Seguimiento del examen OCT guardado.");
    } catch (error) { if (id === model.astronautId) notify(error.message, true); }
    finally { model.saving = false; controls(); renderCrew(); }
  }

  async function attachImages(event) {
    event.preventDefault();
    if (model.saving || !model.selected) return;
    const id = model.astronautId, examId = model.selected.id;
    let files;
    try { files = fileList("oct-attach-files"); if (!files.length) throw new Error("Selecciona una imagen para adjuntar"); }
    catch (error) { notify(error.message, true); return; }
    const body = new FormData(); for (const file of files) body.append("files", file);
    model.saving = true; controls(); renderCrew(); notify("Validando imágenes…");
    try {
      await octRequest(`/api/oct/exams/${examId}/files?astronaut_id=${id}`, { method: "POST", body });
      if (id !== model.astronautId) return;
      await loadExams(examId, { afterSave: true });
      if (id === model.astronautId) notify("Imágenes asociadas al examen de demostración.");
    } catch (error) { if (id === model.astronautId) notify(`Archivo no interpretable o importación rechazada. ${error.message}`, true); }
    finally { model.saving = false; controls(); renderCrew(); }
  }

  function init() {
    $("open-oct-medical").addEventListener("click", () => showView("oct"));
    $("oct-new-exam").addEventListener("click", () => { $("oct-registration").open = true; $("oct-form-eye").focus(); });
    $("oct-astronaut").addEventListener("change", async () => { await selectAstronaut(Number($("oct-astronaut").value)); syncContext(); });
    $("oct-refresh").addEventListener("click", () => { void loadExams(); void loadSummary(); });
    $("oct-eye").addEventListener("change", () => {
      renderExamOptions(); model.selected = model.exams.find(exam => exam.eye === $("oct-eye").value) || null;
      renderSelected(); void loadComparison();
    });
    $("oct-exam-select").addEventListener("change", () => {
      model.selected = model.exams.find(exam => String(exam.id) === $("oct-exam-select").value) || null;
      renderSelected(); void loadComparison();
    });
    $("oct-file-select").addEventListener("change", showImage);
    $("oct-image").addEventListener("load", () => { $("oct-image").hidden = false; fitImage(); });
    $("oct-image").addEventListener("error", () => {
      if (!model.selected?.files.length) return;
      $("oct-image-stage").hidden = true; $("oct-image-empty").hidden = false;
      $("oct-image-empty").textContent = "La imagen no se pudo interpretar. Actualiza el examen o revisa su copia privada.";
    });
    for (const id of ["oct-fit", "oct-zoom-reset"]) $(id).addEventListener("click", () => { model.zoom = 1; fitImage(); $("oct-image-viewport").scrollTop = 0; $("oct-image-viewport").scrollLeft = 0; });
    $("oct-zoom-in").addEventListener("click", () => { model.zoom = Math.min(8, model.zoom * 1.25); applyZoom(); });
    $("oct-zoom-out").addEventListener("click", () => { model.zoom = Math.max(.25, model.zoom / 1.25); applyZoom(); });
    $("oct-compare").addEventListener("click", loadComparison);
    $("oct-metric-select").addEventListener("change", () => {
      const item = selectedComparison();
      if (item) model.metricKey = comparisonKey(item);
      renderTrend();
    });
    $("oct-add-measurement").addEventListener("click", () => addMeasurement());
    $("oct-exam-form").addEventListener("submit", saveExam);
    $("oct-json-file").addEventListener("change", importResults);
    $("oct-attach-form").addEventListener("submit", attachImages);
    $("oct-set-baseline").addEventListener("click", () => saveExisting({ is_baseline: true }));
    $("oct-review-form").addEventListener("submit", event => {
      event.preventDefault();
      const changes = { review_status: $("oct-review-status").value, review_notes: $("oct-review-notes").value };
      if (changes.review_status === "reviewed") {
        try { changes.reviewed_by = $("oct-reviewed-by").value; changes.reviewed_at = isoInput("oct-reviewed-at"); }
        catch (error) { notify(error.message, true); return; }
      }
      void saveExisting(changes);
    });
    window.addEventListener("resize", () => { drawChart(); fitImage(); });
    controls();
  }

  return { init, show, syncContext, get saving() { return model.saving; } };
})();
