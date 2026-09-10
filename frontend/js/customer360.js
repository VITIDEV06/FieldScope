/* ========================================================================
   Fieldscope — Cliente 360
   ======================================================================== */

const Customer360 = (() => {
  const el = {};

  let customers = [];
  let activeId = null;

  function init() {
    el.list = document.getElementById("customerList");
    el.detail = document.getElementById("customerDetail");
    el.search = document.getElementById("customerSearch");

    el.search.addEventListener("input", () => {
      renderList(filterCustomers(el.search.value));
    });

    el.list.addEventListener("click", (event) => {
      const row = event.target.closest(".customer-row");
      if (!row) return;

      selectCustomer(Number(row.dataset.id));
    });

    el.detail.addEventListener("click", async (event) => {
      const deleteObservationButton = event.target.closest(
        "[data-delete-observation]"
      );

      if (deleteObservationButton) {
        await deleteObservation(
          Number(deleteObservationButton.dataset.deleteObservation)
        );
        return;
      }

    });
  }

  async function refresh() {
    el.list.innerHTML = loadingList();

    try {
      customers = await Api.getCustomers();
      renderList(customers);

      if (activeId) {
        const stillExists = customers.some((customer) => customer.id === activeId);

        if (stillExists) {
          await selectCustomer(activeId, { silent: true });
        } else {
          activeId = null;
        }
      }

      if (!activeId && customers.length) {
        await selectCustomer(customers[0].id, { silent: true });
      }
    } catch (error) {
      el.list.innerHTML = errorBlock(
        "No se pudo cargar el directorio",
        error.message
      );
    }
  }

  function filterCustomers(term) {
    const value = term.trim().toLowerCase();

    if (!value) return customers;

    return customers.filter((customer) => {
      return (
        customer.name.toLowerCase().includes(value) ||
        (customer.city || "").toLowerCase().includes(value) ||
        (customer.country || "").toLowerCase().includes(value)
      );
    });
  }

  function renderList(list) {
    if (!list.length) {
      el.list.innerHTML = `
        <div class="empty-state">
          <strong>Sin resultados</strong>
          <p>No encontramos clientes que coincidan con la búsqueda.</p>
        </div>
      `;
      return;
    }

    el.list.innerHTML = list
      .map(
        (customer) => `
          <button
            class="customer-row ${customer.id === activeId ? "is-active" : ""}"
            data-id="${customer.id}"
            type="button"
          >
            <div class="customer-row-name">${escapeHtml(customer.name)}</div>
            <div class="customer-row-loc">${escapeHtml(locationLabel(customer))}</div>
          </button>
        `
      )
      .join("");
  }

  function locationLabel(customer) {
    return (
      [customer.city, customer.country].filter(Boolean).join(", ") ||
      "Ubicación sin especificar"
    );
  }

  async function selectCustomer(id, { silent = false } = {}) {
    activeId = id;

    [...el.list.querySelectorAll(".customer-row")].forEach((row) => {
      row.classList.toggle("is-active", Number(row.dataset.id) === id);
    });

    if (!silent) {
      el.detail.innerHTML = `
        <div class="empty-state empty-state-large">
          <strong>Cargando cliente…</strong>
          <p>Consolidando parque instalado e historial.</p>
        </div>
      `;
    }

    try {
      const data = await Api.getCustomerDetail(id);
      renderDetail(data);
    } catch (error) {
      el.detail.innerHTML = errorBlock(
        "No se pudo cargar el cliente",
        error.message
      );
    }
  }

  function renderDetail({ customer, equipment, observations }) {
    const sortedObservations = [...observations].sort(
      (a, b) => new Date(b.submitted_at) - new Date(a.submitted_at)
    );

    const categories = equipment.length;

    const totalUnits = equipment.reduce(
      (sum, item) => sum + Math.max(Number(item.quantity) || 1, 1),
      0
    );

    const highConfidence = equipment.filter(
      (item) => item.confidence === "High"
    ).length;

    el.detail.innerHTML = `
      <div class="detail-head">
        <h2>${escapeHtml(customer.name)}</h2>

        <div class="loc">
          ${escapeHtml(locationLabel(customer))}
          · cliente desde ${formatDate(customer.created_at)}
        </div>

        <div class="detail-summary">
          <div class="detail-stat">
            <strong>${totalUnits}</strong>
            <span>unidades instaladas</span>
          </div>

          <div class="detail-stat">
            <strong>${categories}</strong>
            <span>categorías de equipo</span>
          </div>

          <div class="detail-stat">
            <strong>${sortedObservations.length}</strong>
            <span>observaciones</span>
          </div>

          <div class="detail-stat">
            <strong>${highConfidence}/${categories || 0}</strong>
            <span>alta confianza</span>
          </div>
        </div>
      </div>

      <div class="detail-section-title">
        Equipamiento conocido · ${categories} categorías · ${totalUnits} unidades
      </div>

      <div class="equipment-grid">
        ${
          equipment.length
            ? equipment.map(equipmentCard).join("")
            : `
              <div class="empty-state">
                <strong>Sin equipamiento reportado</strong>
                <p>Aún no hay equipos asociados a este cliente.</p>
              </div>
            `
        }
      </div>

      <div class="detail-section-title">
        Historial de observaciones · ${sortedObservations.length}
      </div>

      <div class="timeline">
        ${
          sortedObservations.length
            ? sortedObservations.map(observationItem).join("")
            : `
              <div class="empty-state">
                <strong>Sin historial</strong>
                <p>Las observaciones futuras aparecerán aquí.</p>
              </div>
            `
        }
      </div>
    `;
  }

  function equipmentCard(eq) {
    return `
      <div class="equipment-card">
        <div class="equipment-card-head">
          <span class="equipment-card-modality">
            ${escapeHtml(eq.modality || "Sin modalidad")}
          </span>

          ${confidenceBadge(eq.confidence)}
        </div>

        <div class="equipment-card-row">
          <span>Fabricante</span>
          <span>${escapeHtml(eq.manufacturer || "—")}</span>
        </div>

        <div class="equipment-card-row">
          <span>Modelo</span>
          <span>${escapeHtml(eq.model || "—")}</span>
        </div>

        <div class="equipment-card-row">
          <span>Cantidad</span>
          <span>${eq.quantity ?? "—"}</span>
        </div>

        <div class="equipment-card-row">
          <span>Edad estimada</span>
          <span>${eq.estimated_age ? `${eq.estimated_age} años` : "—"}</span>
        </div>

        <div class="equipment-card-row">
          <span>Reportado</span>
          <span>${eq.times_reported ?? 0}×</span>
        </div>
      </div>
    `;
  }

  function observationItem(observation) {
    return `
      <div class="timeline-item" data-observation-id="${observation.id}">
        <div class="timeline-item-main">
          <div class="timeline-text">
            ${escapeHtml(observation.raw_text || "Observación sin texto")}
          </div>

          <div class="timeline-meta">
            <span>${escapeHtml(observation.submitted_by || "Field Employee")}</span>
            <span>·</span>
            <span>${formatDate(observation.submitted_at)}</span>
            ${confidenceBadge(observation.confidence)}
          </div>
        </div>

        <button
          class="timeline-delete-button"
          data-delete-observation="${observation.id}"
          type="button"
          title="Eliminar esta observación"
          aria-label="Eliminar esta observación del historial"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 7h14M9 7V4h6v3M8 10v7M12 10v7M16 10v7M6.5 7l.8 13h9.4l.8-13"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linecap="round"
                  stroke-linejoin="round"/>
          </svg>
        </button>
      </div>
    `;
  }

  async function deleteObservation(observationId) {
    const confirmed = await askConfirmation({
      title: "Eliminar observación",
      message:
        "Esta observación desaparecerá permanentemente del historial de este cliente.",
      confirmText: "Eliminar observación",
      safetyNote:
        "El cliente, los equipos instalados y sus cantidades permanecerán sin cambios.",
    });

    if (!confirmed) return;

    const button = el.detail.querySelector(
      `[data-delete-observation="${observationId}"]`
    );

    if (button) button.disabled = true;

    try {
      await Api.deleteObservation(observationId);

      toast(
        "Observación eliminada",
        "El registro histórico fue borrado. El installed base se conservó.",
        "success"
      );

      await selectCustomer(activeId, { silent: true });

      window.dispatchEvent(
        new CustomEvent("fieldscope:data-changed", {
          detail: { source: "delete-observation" },
        })
      );
    } catch (error) {
      if (button) button.disabled = false;

      toast(
        "No se pudo eliminar",
        error.message,
        "error"
      );
    }
  }

  function askConfirmation(options) {
    if (typeof window.confirmAction === "function") {
      return window.confirmAction(options);
    }

    return Promise.resolve(
      window.confirm(options.message || "¿Deseas continuar?")
    );
  }

  function toast(title, message, variant = "default") {
    if (typeof window.showToast === "function") {
      window.showToast(title, message, variant);
    }
  }

  function observationStateBadge(state) {
    const value = state || "Reportado";

    return `
      <span class="observation-state-badge ${escapeHtml(value)}">
        ${escapeHtml(value)}
      </span>
    `;
  }

  function confidenceBadge(level) {
    const value = level || "Unknown";

    const label =
      {
        High: "Alta",
        Medium: "Media",
        Low: "Baja",
        Unknown: "Desconocida",
      }[value] || value;

    return `
      <span class="confidence-badge ${escapeHtml(value)}">
        <span class="confidence-dot"></span>
        ${escapeHtml(label)}
      </span>
    `;
  }

  function formatDate(iso) {
    if (!iso) return "—";

    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";

    return date.toLocaleDateString("es-PA", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  }

  function loadingList() {
    return `
      <div class="empty-state">
        <strong>Cargando clientes…</strong>
        <p>Consultando Customer Installed Base.</p>
      </div>
    `;
  }

  function errorBlock(title, message) {
    return `
      <div class="empty-state">
        <strong>${escapeHtml(title)}</strong>
        <p>${escapeHtml(message)}</p>
      </div>
    `;
  }

  async function open(id) {
    if (!customers.length) {
      await refresh();
    }

    el.search.value = "";
    renderList(customers);
    await selectCustomer(id);
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
    open,
  };
})();
