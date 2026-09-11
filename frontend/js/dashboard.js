/* ========================================================================
   Fieldscope — Panorama / Dashboard
   ======================================================================== */

const Dashboard = (() => {
  const el = {};

  function init() {
    el.kpiRow = document.getElementById("kpiRow");
    el.modality = document.getElementById("chartModality");
    el.country = document.getElementById("chartCountry");
    el.age = document.getElementById("chartAge");
    el.seedBtn = document.getElementById("seedBtn");

    el.seedBtn.addEventListener("click", onSeed);
  }

  async function onSeed() {
    el.seedBtn.disabled = true;
    el.seedBtn.innerHTML = "Cargando datos…";

    try {
      const result = await Api.seed();
      if (!result.seeded) { toast("Dataset", result.message); return; }
      await refresh();

      window.dispatchEvent(
        new CustomEvent("fieldscope:data-changed", {
          detail: { source: "seed" },
        })
      );

      toast(
        "Dataset sintético cargado",
        "El panorama fue actualizado correctamente.",
        "success"
      );
    } catch (error) {
      toast(
        "No se pudieron cargar los datos",
        error.message,
        "error"
      );
    } finally {
      el.seedBtn.disabled = false;
      el.seedBtn.innerHTML = `
        <svg viewBox="0 0 24 24">
          <path d="M12 3v18M3 12h18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
        </svg>
        Cargar datos de ejemplo
      `;
    }
  }

  async function refresh() {
    el.kpiRow.innerHTML = kpiSkeleton();
    el.modality.innerHTML = loadingChart();
    el.country.innerHTML = loadingChart();
    el.age.innerHTML = loadingChart();

    try {
      const data = await Api.getDashboard();

      renderKpis(data);
      renderModality(data.by_modality || []);
      renderCountry(data.by_country || []);
      renderAge(data.by_age || []);
    } catch (error) {
      const html = `
        <div class="empty-state">
          <strong>No se pudo cargar el panorama</strong>
          <p>${escapeHtml(error.message)}</p>
        </div>
      `;

      el.kpiRow.innerHTML = html;
      el.modality.innerHTML = html;
      el.country.innerHTML = html;
      el.age.innerHTML = html;
    }
  }

  function renderKpis(data) {
    const cards = [
      {
        label: "Clientes observados",
        value: data.total_customers ?? 0,
        meta: "Customer coverage",
        icon: "customers",
      },
      {
        label: "Unidades instaladas",
        value: data.total_equipment ?? 0,
        meta: "Physical units",
        icon: "units",
      },
      {
        label: "Registros de equipo",
        value: data.total_equipment_categories ?? 0,
        meta: "Equipment records",
        icon: "records",
      },
      {
        label: "Observaciones capturadas",
        value: data.total_observations ?? 0,
        meta: "Field intelligence",
        icon: "observations",
      },
      {
        label: "Clientes incompletos",
        value: data.incomplete_customers ?? 0,
        meta: "Data quality",
        icon: "quality",
      },
      { label: "Clientes con tecnología de 7+ años", value: data.aging_technology_customers ?? 0, meta: "Revisión comercial", icon: "quality" },
      { label: "Clientes actualizados recientemente", value: data.recently_updated_customers ?? 0, meta: "Últimos 180 días", icon: "customers" },
      { label: "Registros por revalidar", value: (data.freshness?.aging || 0) + (data.freshness?.stale || 0) + (data.freshness?.unknown || 0), meta: "Actualidad de datos", icon: "quality" },
      { label: "Registros de alta confianza", value: (data.confidence || []).find(row => row.level === "High")?.total ?? 0, meta: "Calidad de evidencia", icon: "quality" },
    ];

    el.kpiRow.innerHTML = cards
      .map(
        (card) => `
          <article class="kpi-card">
            <div class="kpi-topline">
              <div class="kpi-icon">${iconSvg(card.icon)}</div>
              <span class="kpi-meta">${escapeHtml(card.meta)}</span>
            </div>

            <div class="kpi-value">${Number(card.value).toLocaleString("es-PA")}</div>
            <div class="kpi-label">${escapeHtml(card.label)}</div>
          </article>
        `
      )
      .join("");
  }

  function renderModality(rows) {
    renderBarList(
      el.modality,
      rows.map((row) => ({
        label: row.modality || "Sin dato",
        value: Number(row.total) || 0,
      }))
    );
  }

  function renderCountry(rows) {
    renderBarList(
      el.country,
      rows.map((row) => ({
        label: row.country || "Sin dato",
        value: Number(row.total) || 0,
      }))
    );
  }

  function renderBarList(container, items) {
    if (!items.length) {
      container.innerHTML = emptyChart("Sin datos todavía.");
      return;
    }

    const sorted = [...items].sort((a, b) => b.value - a.value);
    const max = Math.max(...sorted.map((item) => item.value), 1);

    container.innerHTML = sorted
      .map(
        (item) => `
          <div class="bar-row">
            <div class="bar-label" title="${escapeHtml(item.label)}">
              ${escapeHtml(item.label)}
            </div>

            <div class="bar-track">
              <div
                class="bar-fill"
                style="width:${Math.max((item.value / max) * 100, 2)}%"
              ></div>
            </div>

            <div class="bar-value">
              ${item.value.toLocaleString("es-PA")}
            </div>
          </div>
        `
      )
      .join("");
  }

  function renderAge(rows) {
    if (!rows.length) {
      el.age.innerHTML = emptyChart("Sin datos de antigüedad todavía.");
      return;
    }

    const buckets = [
      { label: "0–<4 años", min: 0, max: 4, value: 0 },
      { label: "4–<8 años", min: 4, max: 8, value: 0 },
      { label: "8–<12 años", min: 8, max: 12, value: 0 },
      { label: "12+ años", min: 12, max: Infinity, value: 0 },
      { label: "Sin dato", min: null, max: null, value: 0 },
    ];

    rows.forEach(({ age, total }) => {
      const numericTotal = Number(total) || 0;

      if (age === null || age === undefined) {
        buckets[4].value += numericTotal;
        return;
      }

      const numericAge = Number(age);

      const bucket = buckets.find(
        (item) =>
          item.min !== null &&
          numericAge >= item.min &&
          numericAge < item.max
      );

      if (bucket) bucket.value += numericTotal;
    });

    const max = Math.max(...buckets.map((bucket) => bucket.value), 1);

    el.age.innerHTML = `
      <div class="age-hist">
        ${buckets
          .map(
            (bucket) => `
              <div class="age-col">
                <div class="age-col-value">
                  ${bucket.value || ""}
                </div>

                <div
                  class="age-col-bar"
                  style="
                    height:${
                      bucket.value
                        ? Math.max((bucket.value / max) * 100, 5)
                        : 0
                    }%
                  "
                ></div>

                <div class="age-col-label">
                  ${escapeHtml(bucket.label)}
                </div>
              </div>
            `
          )
          .join("")}
      </div>
    `;
  }

  function kpiSkeleton() {
    return Array.from({ length: 5 })
      .map(
        () => `
          <article class="kpi-card">
            <div class="kpi-topline">
              <div class="kpi-icon"></div>
              <span class="kpi-meta">Cargando</span>
            </div>
            <div class="kpi-value">—</div>
            <div class="kpi-label">Consultando indicador</div>
          </article>
        `
      )
      .join("");
  }

  function loadingChart() {
    return `
      <div class="empty-state">
        <strong>Cargando visualización…</strong>
        <p>Consultando el dataset estructurado.</p>
      </div>
    `;
  }

  function emptyChart(message) {
    return `
      <div class="empty-state">
        <strong>Sin información disponible</strong>
        <p>${escapeHtml(message)}</p>
      </div>
    `;
  }

  function iconSvg(type) {
    const icons = {
      customers: `
        <svg viewBox="0 0 24 24">
          <circle cx="9" cy="8" r="3" fill="none" stroke="currentColor" stroke-width="1.6"/>
          <path d="M3.8 19c.5-4 2.2-6 5.2-6s4.7 2 5.2 6M16 6.5a2.6 2.6 0 0 1 0 5.1M16.5 13.3c2.2.5 3.3 2.3 3.7 5.7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
        </svg>
      `,
      units: `
        <svg viewBox="0 0 24 24">
          <rect x="4" y="4" width="16" height="16" rx="3" fill="none" stroke="currentColor" stroke-width="1.6"/>
          <path d="M8 9h8M8 13h5M8 17h7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
        </svg>
      `,
      records: `
        <svg viewBox="0 0 24 24">
          <path d="M6 3h9l4 4v14H6z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>
          <path d="M15 3v5h4M9 12h6M9 16h6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
        </svg>
      `,
      observations: `
        <svg viewBox="0 0 24 24">
          <path d="M4 5.5h16v10H9l-5 4v-14Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>
          <path d="M8 10h8M8 13h5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
        </svg>
      `,
      quality: `
        <svg viewBox="0 0 24 24">
          <path d="M12 3 4.5 6v5.5c0 4.3 2.5 7.4 7.5 9.5 5-2.1 7.5-5.2 7.5-9.5V6z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>
          <path d="m8.5 12 2.2 2.2 4.8-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      `,
    };

    return icons[type] || icons.records;
  }

  function toast(title, message, variant = "default") {
    if (typeof window.showToast === "function") {
      window.showToast(title, message, variant);
    }
  }

  function escapeHtml(value) {
    return String(value).replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char]
    );
  }

  return {
    init,
    refresh,
  };
})();
