(() => {
  const root = document.getElementById("elpriser");
  const urls = { day: root.dataset.urlDay, days: root.dataset.urlDays, profile: root.dataset.urlProfile };
  const $ = (id) => document.getElementById(id);

  // --- tilstand (gemmes i adressen, så et valg kan deles) -------------------------------------
  const params = new URLSearchParams(location.search);
  const gridAvailable = root.dataset.gridAvailable === "1";
  const state = {
    grid: params.get("nettarif") === null ? gridAvailable : gridAvailable && params.get("nettarif") === "1",
    area: ["DK1", "DK2"].includes(params.get("omraade")) ? params.get("omraade") : "DK1",
    date: /^\d{4}-\d{2}-\d{2}$/.test(params.get("dato") || "") ? params.get("dato") : root.dataset.today,
    hourly: params.get("opl") === "time",
    vat: params.get("moms") === "1",
    days: [7, 30, 90].includes(Number(params.get("dage"))) ? Number(params.get("dage")) : 30,
  };

  const fmt = (value, digits = 1) =>
    value == null ? "–" : value.toLocaleString("da-DK", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const unitText = (data) => data.unit + (data.grid && data.grid.enabled ? " inkl. nettarif m.m." : "");
  const showGridNote = (data) => {
    const note = $("grid-note");
    const text = state.grid && data.grid && !data.grid.enabled ? data.grid.reason : "";
    note.textContent = text || "";
    note.classList.toggle("d-none", !text);
  };
  const showError = (message) => {
    const box = $("error-box");
    box.textContent = message || "";
    box.classList.toggle("d-none", !message);
  };

  function addDays(iso, delta) {
    const d = new Date(iso + "T12:00:00Z"); // midt på dagen: undgår sommertids-fælder
    d.setUTCDate(d.getUTCDate() + delta);
    return d.toISOString().slice(0, 10);
  }

  async function getJson(url, query) {
    const response = await fetch(`${url}?${new URLSearchParams(query)}`, { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Fejl ${response.status} ved hentning af priser.`);
    return data;
  }

  // --- grafer ---------------------------------------------------------------------------------
  const charts = {};
  const cache = {};

  function baseOptions(unit) {
    const text = css("--bs-body-color"), grid = css("--bs-border-color");
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: text, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 }, grid: { display: false } },
        y: { ticks: { color: text }, grid: { color: grid }, title: { display: true, text: unit, color: text } },
      },
    };
  }

  function draw(name, canvasId, config) {
    if (charts[name]) charts[name].destroy();
    charts[name] = new Chart($(canvasId), config);
  }

  function tercileColors(values) {
    const sorted = [...values].sort((a, b) => a - b);
    const low = sorted[Math.floor((sorted.length - 1) / 3)];
    const high = sorted[Math.floor(((sorted.length - 1) * 2) / 3)];
    const [green, amber, red] = [css("--bs-success"), css("--bs-warning"), css("--bs-danger")];
    return values.map((v) => (v <= low ? green : v >= high ? red : amber));
  }

  function renderDay() {
    const data = cache.day;
    if (!data) return;
    const points = data.points;
    $("day-empty").classList.toggle("d-none", data.has_data);
    $("day-unit").textContent = unitText(data) + (data.vat ? ", inkl. moms" : "");
    const weekday = new Date(data.date + "T12:00:00Z").toLocaleDateString("da-DK", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
    $("day-title").textContent = `Pris pr. ${data.step_minutes >= 60 ? "time" : data.step_minutes + ". minut"} – ${weekday} (${data.area})`;

    const values = points.map((p) => p.v);
    const stacked = !!(data.grid && data.grid.enabled && points.length && points[0].spot !== undefined);
    const currentIndex = data.current ? points.findIndex((p) => p.t === data.current.t) : -1;
    const body = css("--bs-body-color");
    const common = { categoryPercentage: 1, barPercentage: 0.92 };
    const outline = {
      borderColor: values.map((_, i) => (i === currentIndex ? body : "transparent")),
      borderWidth: values.map((_, i) => (i === currentIndex ? 2 : 0)),
    };
    // Med nettarif stables spotprisen (farvet efter dagens samlede pris) og nettarif m.m. (grå).
    const datasets = stacked
      ? [
          { label: "Spotpris", data: points.map((p) => p.spot), backgroundColor: tercileColors(values), stack: "pris", ...common, ...outline },
          { label: "Nettarif m.m.", data: points.map((p) => p.grid), backgroundColor: css("--bs-gray-500"), stack: "pris", ...common },
        ]
      : [{ data: values, backgroundColor: tercileColors(values), ...common, ...outline }];
    const options = baseOptions(unitText(data));
    if (stacked) { options.scales.x.stacked = true; options.scales.y.stacked = true; }
    draw("day", "chart-day", {
      type: "bar",
      data: { labels: points.map((p) => p.label), datasets },
      options: {
        ...options,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => ` ${ctx.dataset.label ? ctx.dataset.label + " " : ""}${fmt(ctx.parsed.y, 2)} ${data.unit}`,
              footer: (items) => (stacked ? `I alt ${fmt(points[items[0].dataIndex].v, 2)} ${data.unit}` : ""),
            },
          },
        },
      },
    });
    $("legend-grid").classList.toggle("d-none", !stacked);
    showGridNote(data);

    $("day-table").innerHTML = points.map((p) => `<tr><td>${p.label}</td><td class="text-end">${fmt(p.v, 2)}</td></tr>`).join("");

    const sub = (point) => (point ? `kl. ${point.label}` : " ");
    $("stat-now").textContent = fmt(data.current && data.current.v);
    $("stat-now-sub").textContent = data.current ? `${data.unit} · kl. ${data.current.label}` : data.unit;
    $("stat-min").textContent = fmt(data.min && data.min.v);
    $("stat-min-sub").textContent = sub(data.min);
    $("stat-max").textContent = fmt(data.max && data.max.v);
    $("stat-max-sub").textContent = sub(data.max);
    $("stat-avg").textContent = fmt(data.avg);
    const w = data.cheapest_window;
    $("stat-window").textContent = w ? `${w.from}–${w.to}` : "–";
    $("stat-window-sub").textContent = w ? `i snit ${fmt(w.avg)} ${data.unit}` : " ";
  }

  function renderDays() {
    const data = cache.days;
    if (!data) return;
    $("days-empty").classList.toggle("d-none", data.days.length > 0);
    const values = data.days.map((d) => d.avg);
    draw("days", "chart-days", {
      type: "bar",
      data: {
        labels: data.days.map((d) => d.date.slice(8) + "/" + d.date.slice(5, 7)),
        datasets: [{ data: values, backgroundColor: tercileColors(values), categoryPercentage: 1, barPercentage: 0.9 }],
      },
      options: {
        ...baseOptions(unitText(data)),
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => data.days[items[0].dataIndex].date,
              label: (ctx) => ` Gns. ${fmt(ctx.parsed.y, 2)} ${data.unit}`,
              afterLabel: (ctx) => {
                const d = data.days[ctx.dataIndex];
                return ` Laveste ${fmt(d.min, 2)} · højeste ${fmt(d.max, 2)}`;
              },
            },
          },
        },
      },
    });
  }

  function renderProfile() {
    const data = cache.profile;
    if (!data) return;
    $("profile-empty").classList.toggle("d-none", data.hours.length > 0);
    draw("profile", "chart-profile", {
      type: "line",
      data: {
        labels: data.hours.map((h) => h.label),
        datasets: [{
          data: data.hours.map((h) => h.v),
          borderColor: css("--bs-primary"),
          backgroundColor: css("--bs-primary"),
          tension: 0.25,
          pointRadius: 2,
        }],
      },
      options: {
        ...baseOptions(unitText(data)),
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { title: (items) => `kl. ${items[0].label}`, label: (ctx) => ` Gns. ${fmt(ctx.parsed.y, 2)} ${data.unit}` } },
        },
      },
    });
  }

  // --- indlæsning -----------------------------------------------------------------------------
  const common = () => ({ omraade: state.area, moms: state.vat ? "1" : "0", ...(state.grid && { nettarif: "1" }) });

  function syncControls() {
    document.querySelectorAll("#area-group [data-area]").forEach((b) => b.classList.toggle("active", b.dataset.area === state.area));
    document.querySelectorAll("#period-group [data-days]").forEach((b) => b.classList.toggle("active", Number(b.dataset.days) === state.days));
    $("day-input").value = state.date;
    $("opt-hourly").checked = state.hourly;
    $("opt-vat").checked = state.vat;
    if ($("opt-grid")) $("opt-grid").checked = state.grid;
    const query = new URLSearchParams({ omraade: state.area, dato: state.date, dage: state.days, ...(state.hourly && { opl: "time" }), ...(state.vat && { moms: "1" }), ...(gridAvailable && { nettarif: state.grid ? "1" : "0" }) });
    history.replaceState(null, "", `?${query}`);
  }

  let dayRequest = 0;
  async function loadDay() {
    const ticket = ++dayRequest; // ignorér forældede svar, hvis man klikker hurtigt
    try {
      const data = await getJson(urls.day, { ...common(), dato: state.date, opl: state.hourly ? "time" : "raa" });
      if (ticket !== dayRequest) return;
      cache.day = data;
      showError("");
      renderDay();
    } catch (error) {
      showError(error.message);
    }
  }

  async function loadTrends() {
    try {
      const [days, profile] = await Promise.all([
        getJson(urls.days, { ...common(), dage: state.days }),
        getJson(urls.profile, { ...common(), dage: state.days }),
      ]);
      cache.days = days;
      cache.profile = profile;
      renderDays();
      renderProfile();
    } catch (error) {
      showError(error.message);
    }
  }

  const reloadAll = () => { syncControls(); loadDay(); loadTrends(); };
  const setDate = (iso) => { state.date = iso; syncControls(); loadDay(); };

  document.querySelectorAll("#area-group [data-area]").forEach((b) => b.addEventListener("click", () => { state.area = b.dataset.area; reloadAll(); }));
  document.querySelectorAll("#period-group [data-days]").forEach((b) => b.addEventListener("click", () => { state.days = Number(b.dataset.days); syncControls(); loadTrends(); }));
  $("day-prev").addEventListener("click", () => setDate(addDays(state.date, -1)));
  $("day-next").addEventListener("click", () => setDate(addDays(state.date, 1)));
  $("day-today").addEventListener("click", () => setDate(root.dataset.today));
  $("day-tomorrow").addEventListener("click", () => setDate(addDays(root.dataset.today, 1)));
  $("day-input").addEventListener("change", (e) => e.target.value && setDate(e.target.value));
  $("opt-hourly").addEventListener("change", (e) => { state.hourly = e.target.checked; syncControls(); loadDay(); });
  $("opt-vat").addEventListener("change", (e) => { state.vat = e.target.checked; reloadAll(); });
  if ($("opt-grid")) $("opt-grid").addEventListener("change", (e) => { state.grid = e.target.checked; reloadAll(); });

  // Tema-skift: tegn graferne igen med de nye farver.
  new MutationObserver(() => { renderDay(); renderDays(); renderProfile(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-bs-theme"] });

  reloadAll();
})();
