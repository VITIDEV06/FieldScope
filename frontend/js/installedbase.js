const InstalledBase = (() => {
  const el = {};

  function init() {
    el.table = document.getElementById("installedBaseTable");
    el.apply = document.getElementById("applyInstalledFilters");
    el.queryForm = document.getElementById("localQueryForm");
    el.queryInput = document.getElementById("localQueryInput");
    el.queryResults = document.getElementById("localQueryResults");
    el.opportunities = document.getElementById("opportunityList");
    el.apply?.addEventListener("click", refresh);
    el.queryForm?.addEventListener("submit", runQuery);
  }

  function filters() {
    return {
      customer: document.getElementById("filterCustomer")?.value.trim(),
      country: document.getElementById("filterCountry")?.value.trim(),
      city: document.getElementById("filterCity")?.value.trim(),
      modality: document.getElementById("filterModality")?.value.trim(),
      manufacturer: document.getElementById("filterManufacturer")?.value.trim(),
      min_age: document.getElementById("filterMinAge")?.value,
      state: document.getElementById("filterState")?.value,
    };
  }

  async function refresh() {
    if (!el.table) return;
    el.table.innerHTML = "<p>Cargando Installed Base…</p>";
    try {
      const data = await Api.getInstalledBase(filters());
      el.table.innerHTML = renderTable(data.items || []);
    } catch (error) {
      el.table.innerHTML = `<p class="status-warning">${escapeHtml(error.message)}</p>`;
    }
  }

  async function runQuery(event) {
    event.preventDefault();
    const query = el.queryInput.value.trim();
    if (!query) return;
    el.queryResults.innerHTML = "<p>Consultando datos locales…</p>";
    try {
      const data = await Api.queryLocal(query);
      el.queryResults.innerHTML = `
        <div class="local-query-meta">Parser local determinístico · ${data.count} resultado(s) · ${escapeHtml((data.applied_filters || []).join(" · ") || "sin filtros detectados")}</div>
        ${renderTable((data.items || []).map(item => ({ ...item, customer: { name: item.customer, city: item.city, country: item.country } })))}
      `;
    } catch (error) {
      el.queryResults.innerHTML = `<p class="status-warning">${escapeHtml(error.message)}</p>`;
    }
  }

  async function refreshOpportunities() {
    if (!el.opportunities) return;
    el.opportunities.innerHTML = "<p>Cargando oportunidades…</p>";
    try {
      const data = await Api.getOpportunities(7);
      const rows = data.items || [];
      el.opportunities.innerHTML = rows.length ? rows.map(item => {
        const eq = item.equipment;
        return `<article class="panel opportunity-card">
          <div class="eyebrow">${escapeHtml(eq.modality)}</div>
          <h2>${escapeHtml(item.customer)}</h2>
          <p>${escapeHtml([item.city, item.country].filter(Boolean).join(", ") || "Ubicación sin especificar")}</p>
          <div class="opportunity-metric"><strong>${escapeHtml(eq.estimated_age)} años</strong><span>${escapeHtml(eq.observation_state)} · confianza ${escapeHtml(eq.confidence || "Unknown")}</span></div>
          <div class="status-warning">${escapeHtml(item.message)}</div>
        </article>`;
      }).join("") : `<div class="panel simple-panel"><p>No hay equipos con antigüedad de 7 años o más.</p></div>`;
    } catch (error) {
      el.opportunities.innerHTML = `<p class="status-warning">${escapeHtml(error.message)}</p>`;
    }
  }

  function renderTable(items) {
    if (!items.length) return `<div class="empty-state"><strong>Sin resultados</strong><p>No hay equipos que coincidan con los filtros.</p></div>`;
    return `<table class="data-table"><thead><tr><th>Cliente</th><th>Equipo</th><th>Cant.</th><th>Marca</th><th>Modelo</th><th>Antigüedad</th><th>Estado</th><th>Confianza</th><th>Verificación</th></tr></thead><tbody>${items.map(item => {
      const customer = item.customer || {};
      const stale = ({fresh: "Reciente", aging: "Revalidar pronto", stale: "Requiere revalidación", unknown: "Sin fecha de observación"})[item.freshness] || "Sin fecha";
      return `<tr><td><strong>${escapeHtml(customer.name || "—")}</strong><small>${escapeHtml([customer.city, customer.country].filter(Boolean).join(", ") || "")}</small></td><td>${escapeHtml(item.modality || "—")}</td><td>${escapeHtml(item.quantity ?? "—")}</td><td>${escapeHtml(item.manufacturer || "Desconocido")}</td><td>${escapeHtml(item.model || "Desconocido")}</td><td>${item.estimated_age == null ? "Desconocida" : `${escapeHtml(item.estimated_age)} años`}</td><td>${escapeHtml(item.observation_state || "Reportado")}</td><td>${escapeHtml(item.confidence || "Unknown")}</td><td>${stale}<small>${item.last_verified ? new Date(item.last_verified).toLocaleDateString("es-PA") : "Sin fecha"}</small></td></tr>`;
    }).join("")}</tbody></table>`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  }

  return { init, refresh, refreshOpportunities };
})();
