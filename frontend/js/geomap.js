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

  /*
   * 195 estados incluidos en el catálogo operativo:
   * 193 miembros de Naciones Unidas + Palestina + Ciudad del Vaticano.
   *
   * Algunos países transcontinentales pueden clasificarse de maneras
   * distintas según el contexto. Para este proyecto se usa una sola
   * agrupación operativa y el usuario puede crear/ajustar países manualmente.
   */
  const COUNTRIES_BY_REGION = {
    "Norteamérica": [
      "Canadá",
      "Estados Unidos",
    ],

    "Centroamérica y Caribe": [
      "Antigua y Barbuda",
      "Bahamas",
      "Barbados",
      "Belice",
      "Costa Rica",
      "Cuba",
      "Dominica",
      "El Salvador",
      "Granada",
      "Guatemala",
      "Haití",
      "Honduras",
      "Jamaica",
      "México",
      "Nicaragua",
      "Panamá",
      "República Dominicana",
      "San Cristóbal y Nieves",
      "Santa Lucía",
      "San Vicente y las Granadinas",
      "Trinidad y Tobago",
    ],

    "Sudamérica": [
      "Argentina",
      "Bolivia",
      "Brasil",
      "Chile",
      "Colombia",
      "Ecuador",
      "Guyana",
      "Paraguay",
      "Perú",
      "Surinam",
      "Uruguay",
      "Venezuela",
    ],

    "Europa": [
      "Albania",
      "Alemania",
      "Andorra",
      "Austria",
      "Bélgica",
      "Bielorrusia",
      "Bosnia y Herzegovina",
      "Bulgaria",
      "Chipre",
      "Croacia",
      "Dinamarca",
      "Eslovaquia",
      "Eslovenia",
      "España",
      "Estonia",
      "Finlandia",
      "Francia",
      "Grecia",
      "Hungría",
      "Irlanda",
      "Islandia",
      "Italia",
      "Letonia",
      "Liechtenstein",
      "Lituania",
      "Luxemburgo",
      "Macedonia del Norte",
      "Malta",
      "Moldavia",
      "Mónaco",
      "Montenegro",
      "Noruega",
      "Países Bajos",
      "Polonia",
      "Portugal",
      "Reino Unido",
      "República Checa",
      "Rumania",
      "Rusia",
      "San Marino",
      "Serbia",
      "Suecia",
      "Suiza",
      "Ucrania",
      "Ciudad del Vaticano",
    ],

    "Asia": [
      "Afganistán",
      "Bangladés",
      "Bután",
      "Brunéi",
      "Camboya",
      "China",
      "Corea del Norte",
      "Corea del Sur",
      "Filipinas",
      "India",
      "Indonesia",
      "Japón",
      "Kazajistán",
      "Kirguistán",
      "Laos",
      "Malasia",
      "Maldivas",
      "Mongolia",
      "Myanmar",
      "Nepal",
      "Pakistán",
      "Singapur",
      "Sri Lanka",
      "Tailandia",
      "Tayikistán",
      "Timor Oriental",
      "Turkmenistán",
      "Uzbekistán",
      "Vietnam",
    ],

    "Medio Oriente": [
      "Arabia Saudita",
      "Armenia",
      "Azerbaiyán",
      "Baréin",
      "Emiratos Árabes Unidos",
      "Georgia",
      "Irak",
      "Irán",
      "Israel",
      "Jordania",
      "Kuwait",
      "Líbano",
      "Omán",
      "Palestina",
      "Catar",
      "Siria",
      "Turquía",
      "Yemen",
    ],

    "África": [
      "Angola",
      "Argelia",
      "Benín",
      "Botsuana",
      "Burkina Faso",
      "Burundi",
      "Cabo Verde",
      "Camerún",
      "Chad",
      "Comoras",
      "Costa de Marfil",
      "Egipto",
      "Eritrea",
      "Esuatini",
      "Etiopía",
      "Gabón",
      "Gambia",
      "Ghana",
      "Guinea",
      "Guinea-Bisáu",
      "Guinea Ecuatorial",
      "Kenia",
      "Lesoto",
      "Liberia",
      "Libia",
      "Madagascar",
      "Malaui",
      "Malí",
      "Marruecos",
      "Mauricio",
      "Mauritania",
      "Mozambique",
      "Namibia",
      "Níger",
      "Nigeria",
      "República Centroafricana",
      "República del Congo",
      "República Democrática del Congo",
      "Ruanda",
      "Santo Tomé y Príncipe",
      "Senegal",
      "Seychelles",
      "Sierra Leona",
      "Somalia",
      "Sudáfrica",
      "Sudán",
      "Sudán del Sur",
      "Tanzania",
      "Togo",
      "Túnez",
      "Uganda",
      "Yibuti",
      "Zambia",
      "Zimbabue",
    ],

    "Oceanía": [
      "Australia",
      "Fiyi",
      "Islas Marshall",
      "Islas Salomón",
      "Kiribati",
      "Micronesia",
      "Nauru",
      "Nueva Zelanda",
      "Palaos",
      "Papúa Nueva Guinea",
      "Samoa",
      "Tonga",
      "Tuvalu",
      "Vanuatu",
    ],

    "Otras regiones": [],
  };

  /*
   * Alias frecuentes para que la información guardada por Captura
   * se normalice al mismo país mostrado en la interfaz.
   */
  const COUNTRY_ALIASES = {
    "canada": "Canadá",
    "canadá": "Canadá",
    "united states": "Estados Unidos",
    "united states of america": "Estados Unidos",
    "usa": "Estados Unidos",
    "us": "Estados Unidos",
    "estados unidos": "Estados Unidos",

    "mexico": "México",
    "méxico": "México",
    "panama": "Panamá",
    "panamá": "Panamá",
    "dominican republic": "República Dominicana",
    "republica dominicana": "República Dominicana",
    "república dominicana": "República Dominicana",

    "brazil": "Brasil",
    "brasil": "Brasil",
    "peru": "Perú",
    "perú": "Perú",

    "spain": "España",
    "españa": "España",
    "germany": "Alemania",
    "alemania": "Alemania",
    "france": "Francia",
    "francia": "Francia",
    "italy": "Italia",
    "italia": "Italia",
    "united kingdom": "Reino Unido",
    "uk": "Reino Unido",
    "reino unido": "Reino Unido",
    "netherlands": "Países Bajos",
    "paises bajos": "Países Bajos",
    "países bajos": "Países Bajos",
    "czech republic": "República Checa",
    "czechia": "República Checa",
    "russia": "Rusia",
    "rusia": "Rusia",

    "japan": "Japón",
    "japon": "Japón",
    "japón": "Japón",
    "south korea": "Corea del Sur",
    "north korea": "Corea del Norte",
    "philippines": "Filipinas",
    "thailand": "Tailandia",
    "vietnam": "Vietnam",

    "saudi arabia": "Arabia Saudita",
    "united arab emirates": "Emiratos Árabes Unidos",
    "uae": "Emiratos Árabes Unidos",
    "qatar": "Catar",
    "turkey": "Turquía",
    "turkiye": "Turquía",
    "türkiye": "Turquía",

    "egypt": "Egipto",
    "south africa": "Sudáfrica",
    "morocco": "Marruecos",
    "ivory coast": "Costa de Marfil",
    "democratic republic of the congo": "República Democrática del Congo",
    "republic of the congo": "República del Congo",

    "new zealand": "Nueva Zelanda",
    "papua new guinea": "Papúa Nueva Guinea",
    "solomon islands": "Islas Salomón",
    "marshall islands": "Islas Marshall",
  };

  /*
   * Indexar automáticamente todos los nombres canónicos en español.
   */
  Object.values(COUNTRIES_BY_REGION)
    .flat()
    .forEach((country) => {
      COUNTRY_ALIASES[normalize(country)] = country;
    });

  function init() {
    el.breadcrumb = document.getElementById("mapBreadcrumb");
    el.grid = document.getElementById("mapGrid");

    el.grid.addEventListener("click", onGridClick);
    el.grid.addEventListener("submit", onGridSubmit);
  }

  async function refresh({ preservePath = false } = {}) {
    el.grid.innerHTML = loadingState("Cargando inteligencia geográfica…");

    try {
      const [customerRows, customRows] = await Promise.all([
        Api.getCustomers(),
        Api.getCustomCountries(),
      ]);

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
      .toLowerCase();
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
        if (typeof Customer360?.refresh === "function") {
          Customer360.refresh();
        }

        if (typeof Dashboard?.refresh === "function") {
          Dashboard.refresh();
        }

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
