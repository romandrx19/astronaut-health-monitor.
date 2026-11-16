/* El servidor coordina generación, ritmo y pausa entre todas las ventanas. */
const SimulationControl = (() => {
  let revision = -1;
  let ready = false;
  let changing = false;
  let polling = false;

  function apply(status) {
    if (!status || !Number.isInteger(status.revision) || status.revision < revision) return;
    revision = status.revision;
    ready = true;
    state.running = status.running === true;
    $("toggle").textContent = state.running ? "⏸ Pausar simulación" : "▶ Reanudar simulación";
    syncNavigationContext();
  }

  async function refresh() {
    apply(await api("/api/simulation", { cache: "no-store" }));
  }

  async function setRunning(running) {
    if (changing) throw new Error("Espera a que termine el cambio de simulación");
    changing = true;
    $("toggle").disabled = true;
    try {
      apply(await api("/api/simulation", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ running }) }));
    } finally {
      changing = false;
      $("toggle").disabled = false;
    }
  }

  async function toggle() {
    try { await setRunning(!state.running); }
    catch (error) { setConnection(false, error.message); }
  }

  async function poll() {
    if (polling || changing || state.saving || medicalState.saving) return;
    polling = true;
    try {
      await refresh();
      if (ready && state.running) await simulateOnce(true);
      else await refreshData();
    } catch (error) { setConnection(false, error.message); }
    finally { polling = false; }
  }

  return { apply, setRunning, refresh, toggle, poll };
})();
