/* ========================================================================
   Fieldscope — Geografía global administrable
   Región → País → Ciudad → Cliente

   NUEVO:
   1. Todas las regiones principales están disponibles.
   2. Todos los países del catálogo aparecen en su región.
   3. Dentro de una región puedes agregar manualmente un país/territorio.
   4. Si un cliente quedó SIN PAÍS durante Captura:
      - entras a la región correcta;
      - eliges "Asignar cliente sin país";
      - seleccionas el cliente;
      - seleccionas el país;
      - se actualiza el cliente en la base de datos.
   5. Los países manuales quedan guardados en SQLite mediante la API.
   ======================================================================== */

const GeoMap = (() => {
  const el = {};

  let customers = [];
  let customCountries = [];
  let path = [];
  let managementMode = null;

  const REGION_ORDER = [
    "Norteamérica",
    "Centroamérica y Caribe",
    "Sudamérica",
    "Europa",
    "Asia",
    "Medio Oriente",
    "África",
    "Oceanía",
    "Otras regiones",
  ];

  let COUNTRIES_BY_REGION = {};
  let COUNTRY_ALIASES = {};

  function init() {
    el.breadcrumb = document.getElementById("mapBreadcrumb");
    el.grid = document.getElementById("mapGrid");

    el.grid.addEventListener("click", onGridClick);
    el.grid.addEventListener("submit", onGridSubmit);
  }

  async function refresh({ preservePath = false } = {}) {
    el.grid.innerHTML = loadingState("Cargando inteligencia geográfica…");

    try {
      const [customerRows, customRows, catalog] = await Promise.all([
        Api.getCustomers(),
        Api.getCustomCountries(),
        Api.getGeographyCatalog(),
      ]);

      COUNTRIES_BY_REGION = catalog.regions;
      COUNTRY_ALIASES = Object.fromEntries(Object.entries(catalog.aliases).map(([key, value]) => [normalize(key), value]));
      Object.values(COUNTRIES_BY_REGION).flat().forEach(country => { COUNTRY_ALIASES[normalize(country)] = country; });
      customers = customerRows;
      customCountries = customRows;

      if (!preservePath) {
        path = [];
        managementMode = null;
      }

      render();
    } catch (error) {
      el.grid.innerHTML = errorState(
        "No se pudo conectar con la API",
        error.message
      );
    }
  }

  function normalize(value) {
    return String(value || "")
      .trim()
      .toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }

  function isMissingCountry(value) {
    const normalized = normalize(value);

    return (
      !normalized ||
      normalized === "desconocido" ||
      normalized === "unknown" ||
      normalized === "sin especificar" ||
      normalized === "sin país" ||
      normalized === "sin pais"
    );
  }

  function customCountryFor(value) {
    const normalized = normalize(value);

    return customCountries.find(
      (row) => normalize(row.country_name) === normalized
    );
  }

  function canonicalCountry(country) {
    if (isMissingCountry(country)) {
      return "Sin país";
    }

    const custom = customCountryFor(country);
    if (custom) return custom.country_name;

    return COUNTRY_ALIASES[normalize(country)] || String(country).trim();
  }

  function regionForCountry(country) {
    if (isMissingCountry(country)) {
      return null;
    }

    const custom = customCountryFor(country);
    if (custom) return custom.region;

    const canonical = canonicalCountry(country);

    for (const region of REGION_ORDER) {
      if ((COUNTRIES_BY_REGION[region] || []).includes(canonical)) {
        return region;
      }
    }

    return "Otras regiones";
  }

  function unassignedCustomers() {
    return customers.filter((customer) => isMissingCountry(customer.country));
  }

  function countriesForRegion(region) {
    const predefined = COUNTRIES_BY_REGION[region] || [];

    const custom = customCountries
      .filter((row) => row.region === region)
      .map((row) => row.country_name);

    return [...new Set([...predefined, ...custom])].sort((a, b) =>
      a.localeCompare(b, "es", { sensitivity: "base" })
    );
  }

  function scopedCustomers() {
    return customers.filter((customer) => {
      if (isMissingCountry(customer.country)) {
        return false;
      }

      return path.every((step) => {
        if (step.level === "region") {
          return regionForCountry(customer.country) === step.value;
        }

        if (step.level === "country") {
          return canonicalCountry(customer.country) === step.value;
        }

        if (step.level === "city") {
          return (customer.city || "Sin ciudad") === step.value;
        }

        return true;
      });
    });
  }

  function currentLevel() {
    return ["region", "country", "city", "customer"][path.length];
  }

  function currentRegion() {
    return path.find((step) => step.level === "region")?.value || null;
  }

  function render() {
    renderBreadcrumb();

    const scope = scopedCustomers();
    const level = currentLevel();

    if (level === "region") {
      renderAllRegions();
      return;
    }

    if (level === "country") {
      renderAllCountriesForCurrentRegion();
      return;
    }

    if (level === "city") {
      renderCities(scope);
      return;
    }

    renderCustomers(scope);
  }

  /* ======================================================================
     NIVEL 1 — REGIONES
     ====================================================================== */
  function renderAllRegions() {
    const cards = REGION_ORDER
      .map((region) => {
        const regionCustomers = customers.filter(
          (customer) => regionForCountry(customer.country) === region
        );

        const countriesWithData = new Set(
          regionCustomers
            .map((customer) => canonicalCountry(customer.country))
            .filter((country) => country && country !== "Sin país")
        );

        return {
          region,
          customers: regionCustomers.length,
          countriesWithData: countriesWithData.size,
          catalogCountries: countriesForRegion(region).length,
        };
      })
      /*
       * "Otras regiones" solo aparece si realmente hay un país sin
       * clasificación. Evita mostrar una tarjeta vacía.
       */
      .filter(
        (item) =>
          item.region !== "Otras regiones" ||
          item.countriesWithData > 0
      );

    const pending = unassignedCustomers().length;

    el.grid.innerHTML = `
      ${
        pending
          ? `
            <div class="geo-pending-banner">
              <div>
                <span class="geo-pending-kicker">Ubicación pendiente</span>
                <strong>
                  ${pending} cliente${pending === 1 ? "" : "s"} sin país
                </strong>
                <p>
                  Entra a la región correcta para asignarle un país.
                </p>
              </div>

              <div class="geo-pending-count">${pending}</div>
            </div>
          `
          : ""
      }

      ${cards
        .map(
          (item) => `
            <button
              class="map-card"
              data-level="region"
              data-value="${escapeHtml(item.region)}"
              type="button"
            >
              <div class="map-card-title">
                ${escapeHtml(item.region)}
              </div>

              <div class="map-card-loc">
                ${
                  item.region === "Otras regiones"
                    ? `${item.countriesWithData} país${
                        item.countriesWithData === 1 ? "" : "es"
                      } pendiente${
                        item.countriesWithData === 1 ? "" : "s"
                      } de clasificación`
                    : `${item.catalogCountries} países disponibles`
                }
              </div>

              <div class="map-card-stats">
                <div class="map-card-stat">
                  <b>${item.customers}</b>
                  cliente${item.customers === 1 ? "" : "s"}
                  ${
                    item.countriesWithData
                      ? ` · ${item.countriesWithData} país${
                          item.countriesWithData === 1 ? "" : "es"
                        } con datos`
                      : ""
                  }
                </div>
              </div>
            </button>
          `
        )
        .join("")}
    `;
  }

  /* ======================================================================
     NIVEL 2 — PAÍSES DE UNA REGIÓN
     ====================================================================== */
  function renderAllCountriesForCurrentRegion() {
    const region = currentRegion();

    if (!region) {
      renderAllRegions();
      return;
    }

    let countryNames = countriesForRegion(region);

    /*
     * Para "Otras regiones", mostrar los países encontrados en clientes
     * que no encajan en el catálogo.
     */
    if (region === "Otras regiones") {
      countryNames = [
        ...new Set(
          customers
            .filter(
              (customer) =>
                !isMissingCountry(customer.country) &&
                regionForCountry(customer.country) === "Otras regiones"
            )
            .map((customer) => canonicalCountry(customer.country))
        ),
      ].sort((a, b) =>
        a.localeCompare(b, "es", { sensitivity: "base" })
      );
    }

    const unassigned = unassignedCustomers();

    el.grid.innerHTML = `
      ${renderRegionManagement(region, unassigned, countryNames)}

      ${
        countryNames.length
          ? countryNames
              .map((country) => {
                const countryCustomers = customers.filter(
                  (customer) =>
                    regionForCountry(customer.country) === region &&
                    canonicalCountry(customer.country) === country
                );

                const cities = new Set(
                  countryCustomers
                    .map((customer) => customer.city)
                    .filter(Boolean)
                );

                return `
                  <button
                    class="map-card"
                    data-level="country"
                    data-value="${escapeHtml(country)}"
                    type="button"
                  >
                    <div class="map-card-title">
                      ${escapeHtml(country)}
                    </div>

                    <div class="map-card-loc">
                      ${
                        cities.size
                          ? `${cities.size} ${
                              cities.size === 1
                                ? "ciudad registrada"
                                : "ciudades registradas"
                            }`
                          : "Sin ciudades registradas todavía"
                      }
                    </div>

                    <div class="map-card-stats">
                      <div class="map-card-stat">
                        <b>${countryCustomers.length}</b>
                        cliente${
                          countryCustomers.length === 1 ? "" : "s"
                        }
                      </div>
                    </div>
                  </button>
                `;
              })
              .join("")
          : `
            <div class="empty-state geo-full-span">
              <strong>No hay países en esta categoría</strong>
              <p>
                Usa “Agregar país” para crear uno manualmente.
              </p>
            </div>
          `
      }
    `;
  }

  function renderRegionManagement(region, unassigned, countryNames) {
    return `
      <section class="geo-management geo-full-span">
        <div class="geo-management-head">
          <div>
            <span class="geo-management-kicker">Gestión manual</span>
            <h3>${escapeHtml(region)}</h3>
            <p>
              Agrega un país si no aparece en el catálogo o asigna aquí
              un cliente que quedó sin país.
            </p>
          </div>

          <div class="geo-management-actions">
            <button
              class="geo-action-button"
              data-geo-action="add-country"
              type="button"
            >
              ${plusIcon()}
              Agregar país
            </button>

            ${
              unassigned.length
                ? `
                  <button
                    class="geo-action-button geo-action-button-primary"
                    data-geo-action="assign-customer"
                    type="button"
                  >
                    ${pinIcon()}
                    Asignar cliente sin país
                    <span class="geo-action-count">${unassigned.length}</span>
                  </button>
                `
                : `
                  <span class="geo-all-assigned">
                    ${checkIcon()}
                    Todos los clientes tienen país
                  </span>
                `
            }
          </div>
        </div>

        ${renderManagementBody(region, unassigned, countryNames)}
      </section>
    `;
  }

  function renderManagementBody(region, unassigned, countryNames) {
    if (managementMode === "add-country") {
      return `
        <form class="geo-inline-form" data-geo-form="add-country">
          <div class="geo-inline-copy">
            <strong>Agregar país o territorio</strong>
            <span>
              Se guardará permanentemente dentro de ${escapeHtml(region)}.
            </span>
          </div>

          <label class="geo-field">
            <span>Nombre del país</span>
            <input
              type="text"
              name="country_name"
              placeholder="Ej.: Kosovo"
              autocomplete="off"
              required
            />
          </label>

          <div class="geo-form-actions">
            <button
              class="geo-form-button geo-form-button-secondary"
              data-geo-action="cancel-management"
              type="button"
            >
              Cancelar
            </button>

            <button
              class="geo-form-button geo-form-button-primary"
              type="submit"
            >
              Guardar país
            </button>
          </div>
        </form>
      `;
    }

    if (managementMode === "assign-customer") {
      if (!unassigned.length) {
        managementMode = null;
        return "";
      }

      return `
        <form class="geo-inline-form" data-geo-form="assign-customer">
          <div class="geo-inline-copy">
            <strong>Asignar ubicación</strong>
            <span>
              El cliente pasará a ${escapeHtml(region)} mediante el país
              que selecciones.
            </span>
          </div>

          <label class="geo-field">
            <span>Cliente sin país</span>
            <select name="customer_id" required>
              <option value="">Selecciona un cliente…</option>
              ${unassigned
                .map(
                  (customer) => `
                    <option value="${customer.id}">
                      ${escapeHtml(customer.name)}
                      ${
                        customer.city
                          ? ` — ${escapeHtml(customer.city)}`
                          : ""
                      }
                    </option>
                  `
                )
                .join("")}
            </select>
          </label>

          <label class="geo-field">
            <span>País dentro de ${escapeHtml(region)}</span>
            <select name="country" required>
              <option value="">Selecciona un país…</option>
              ${countryNames
                .map(
                  (country) => `
                    <option value="${escapeHtml(country)}">
                      ${escapeHtml(country)}
                    </option>
                  `
                )
                .join("")}
            </select>
          </label>

          ${
            countryNames.length
              ? ""
              : `
                <div class="geo-form-warning">
                  Primero agrega un país a esta región.
                </div>
              `
          }

          <div class="geo-form-actions">
            <button
              class="geo-form-button geo-form-button-secondary"
              data-geo-action="cancel-management"
              type="button"
            >
              Cancelar
            </button>

            <button
              class="geo-form-button geo-form-button-primary"
              type="submit"
              ${countryNames.length ? "" : "disabled"}
            >
              Asignar país
            </button>
          </div>
        </form>
      `;
    }

    return "";
  }

  /* ======================================================================
     NIVEL 3 — CIUDADES
     ====================================================================== */
  function renderCities(scope) {
    const groups = new Map();

    scope.forEach((customer) => {
      const city = customer.city || "Sin ciudad";

      if (!groups.has(city)) {
        groups.set(city, []);
      }

      groups.get(city).push(customer);
    });

    if (!groups.size) {
      const country =
        path.find((step) => step.level === "country")?.value ||
        "este país";

      el.grid.innerHTML = `
        <div class="empty-state geo-full-span">
          <strong>
            Aún no hay clientes registrados en ${escapeHtml(country)}
          </strong>
          <p>
            Cuando registres o asignes un hospital de este país, su ciudad
            aparecerá aquí automáticamente.
          </p>
        </div>
      `;
      return;
    }

    const sorted = [...groups.entries()].sort((a, b) =>
      String(a[0]).localeCompare(String(b[0]), "es", {
        sensitivity: "base",
      })
    );

    el.grid.innerHTML = sorted
      .map(
        ([city, list]) => `
          <button
            class="map-card"
            data-level="city"
            data-value="${escapeHtml(city)}"
            type="button"
          >
            <div class="map-card-title">
              ${escapeHtml(city)}
            </div>

            <div class="map-card-loc">
              Explora los clientes registrados en esta ciudad.
            </div>

            <div class="map-card-stats">
              <div class="map-card-stat">
                <b>${list.length}</b>
                cliente${list.length === 1 ? "" : "s"}
              </div>
            </div>
          </button>
        `
      )
      .join("");
  }

  /* ======================================================================
     NIVEL 4 — CLIENTES
     ====================================================================== */
  function renderCustomers(scope) {
    if (!scope.length) {
      el.grid.innerHTML = errorState(
        "Sin clientes en esta ciudad",
        "No hay observaciones disponibles para este nivel."
      );
      return;
    }

    el.grid.innerHTML = scope
      .map(
        (customer) => `
          <button
            class="map-card"
            data-level="customer"
            data-value="${customer.id}"
            type="button"
          >
            <div class="map-card-title">
              ${escapeHtml(customer.name)}
            </div>

            <div class="map-card-loc">
              ${escapeHtml(
                [customer.city, canonicalCountry(customer.country)]
                  .filter(Boolean)
                  .join(", ") || "Ubicación sin especificar"
              )}
            </div>

            <div class="map-card-stats">
              <div class="map-card-stat">
                <b>360°</b>
                abrir cliente
              </div>
            </div>
          </button>
        `
      )
      .join("");
  }

  /* ======================================================================
     INTERACCIONES
     ====================================================================== */
  function onGridClick(event) {
    const action = event.target.closest("[data-geo-action]");

    if (action) {
      const name = action.dataset.geoAction;

      if (name === "add-country") {
        managementMode =
          managementMode === "add-country" ? null : "add-country";
        render();
        focusManagementInput();
        return;
      }

      if (name === "assign-customer") {
        managementMode =
          managementMode === "assign-customer"
            ? null
            : "assign-customer";
        render();
        return;
      }

      if (name === "cancel-management") {
        managementMode = null;
        render();
        return;
      }
    }

    const card = event.target.closest(".map-card");
    if (!card) return;

    const { level, value } = card.dataset;

    if (level === "customer") {
      window.showView("customer");
      Customer360.open(Number(value));
      return;
    }

    managementMode = null;
    path.push({ level, value });
    render();
  }

  async function onGridSubmit(event) {
    const form = event.target.closest("[data-geo-form]");
    if (!form) return;

    event.preventDefault();

    const formType = form.dataset.geoForm;
    const submitButton = form.querySelector('button[type="submit"]');

    if (formType === "add-country") {
      const countryName = form.elements.country_name.value.trim();
      const region = currentRegion();

      if (!countryName || !region) return;

      setButtonBusy(submitButton, "Guardando…");

      try {
        await Api.addCustomCountry(countryName, region);

        customCountries = await Api.getCustomCountries();

        managementMode = null;
        render();

        toast(
          "País agregado",
          `${countryName} fue agregado a ${region}.`,
          "success"
        );
      } catch (error) {
        setButtonReady(submitButton, "Guardar país");

        toast(
          "No se pudo agregar el país",
          error.message,
          "error"
        );
      }

      return;
    }

    if (formType === "assign-customer") {
      const customerId = Number(form.elements.customer_id.value);
      const country = form.elements.country.value;
      const region = currentRegion();

      if (!customerId || !country || !region) return;

      const customer = customers.find(
        (row) => row.id === customerId
      );

      setButtonBusy(submitButton, "Asignando…");

      try {
        await Api.updateCustomerCountry(customerId, country);

        /*
         * Refrescar los datos manteniendo al usuario dentro de la región.
         */
        customers = await Api.getCustomers();
        customCountries = await Api.getCustomCountries();

        managementMode = null;
        render();

        /*
         * También refrescar las otras vistas para que Customer 360 y
         * Panorama tengan la ubicación actualizada.
         */
        window.dispatchEvent(new CustomEvent("fieldscope:data-changed", { detail: { source: "geography" } }));

        toast(
          "Ubicación asignada",
          `${customer?.name || "El cliente"} ahora pertenece a ${country}, ${region}.`,
          "success"
        );
      } catch (error) {
        setButtonReady(submitButton, "Asignar país");

        toast(
          "No se pudo asignar el país",
          error.message,
          "error"
        );
      }
    }
  }

  function setButtonBusy(button, text) {
    if (!button) return;

    button.disabled = true;
    button.dataset.originalText = button.textContent;
    button.textContent = text;
  }

  function setButtonReady(button, fallbackText) {
    if (!button) return;

    button.disabled = false;
    button.textContent =
      button.dataset.originalText || fallbackText;
  }

  function focusManagementInput() {
    window.setTimeout(() => {
      el.grid
        .querySelector('[data-geo-form="add-country"] input')
        ?.focus();
    }, 50);
  }

  /* ======================================================================
     BREADCRUMB
     ====================================================================== */
  function renderBreadcrumb() {
    const crumbs = [
      { label: "Todas las regiones", index: 0 },
      ...path.map((step, index) => ({
        label: step.value,
        index: index + 1,
      })),
    ];

    el.breadcrumb.innerHTML = crumbs
      .map((crumb, index) => {
        const isLast = index === crumbs.length - 1;

        const separator =
          index > 0
            ? `<span class="crumb-sep">/</span>`
            : "";

        return `
          ${separator}

          <button
            data-index="${crumb.index}"
            class="${isLast ? "is-current" : ""}"
            type="button"
          >
            ${escapeHtml(crumb.label)}
          </button>
        `;
      })
      .join("");

    el.breadcrumb.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        path = path.slice(0, Number(button.dataset.index));
        managementMode = null;
        render();
      });
    });
  }

  /* ======================================================================
     SMALL UI HELPERS
     ====================================================================== */
  function plusIcon() {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 5v14M5 12h14"
              fill="none"
              stroke="currentColor"
              stroke-width="1.7"
              stroke-linecap="round"/>
      </svg>
    `;
  }

  function pinIcon() {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 21s6-5.2 6-11a6 6 0 1 0-12 0c0 5.8 6 11 6 11Z"
              fill="none"
              stroke="currentColor"
              stroke-width="1.6"
              stroke-linejoin="round"/>
        <circle cx="12" cy="10" r="2.1"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"/>
      </svg>
    `;
  }

  function checkIcon() {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m6 12 4 4 8-9"
              fill="none"
              stroke="currentColor"
              stroke-width="1.7"
              stroke-linecap="round"
              stroke-linejoin="round"/>
      </svg>
    `;
  }

  function loadingState(message) {
    return `
      <div class="empty-state geo-full-span">
        <strong>${escapeHtml(message)}</strong>
        <p>Consultando clientes, países y ubicaciones.</p>
      </div>
    `;
  }

  function errorState(title, message) {
    return `
      <div class="empty-state geo-full-span">
        <strong>${escapeHtml(title)}</strong>
        <p>${escapeHtml(message)}</p>
      </div>
    `;
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
