/* ========================================================================
   Fieldscope — API client
   Backend actual del proyecto: http://127.0.0.1:8001
   ======================================================================== */

const API_BASE = "http://127.0.0.1:8001";

const Api = {
  async _request(path, options = {}) {
    const config = {
      headers: { "Content-Type": "application/json" },
      ...options,
    };

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), path.endsWith("/process") ? 150_000 : 20_000);
    let response;
    try { response = await fetch(`${API_BASE}${path}`, { ...config, signal: controller.signal }); }
    catch (error) { throw new Error(error.name === "AbortError" ? "La solicitud excedió el tiempo límite. Comprueba el backend." : "No se pudo conectar al backend local."); }
    finally { clearTimeout(timeout); }

    if (!response.ok) {
      let detail = response.statusText || `HTTP ${response.status}`;

      try {
        const body = await response.json();
        const rawDetail = body.detail ?? body.message ?? body;
        detail = typeof rawDetail === "string"
          ? rawDetail
          : JSON.stringify(rawDetail);
      } catch (_) {}

      throw new Error(detail);
    }

    return response.json();
  },

  ping() {
    return this._request("/");
  },

  health() {
    return this._request("/api/health");
  },

  processObservation({ text, submittedBy, sessionId }) {
    return this._request("/api/observations/process", {
      method: "POST",
      body: JSON.stringify({
        text,
        submitted_by: submittedBy,
        session_id: sessionId || null,
      }),
    });
  },

  confirmObservation(sessionId) {
    return this._request("/api/observations/confirm", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId }),
    });
  },

  cancelPendingObservation(sessionId) {
    return this._request(`/api/observations/pending/${sessionId}`, { method: "DELETE" });
  },

  getInstalledBase(filters = {}) {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") params.set(key, value);
    });
    return this._request(`/api/installed-base?${params.toString()}`);
  },

  queryLocal(query) {
    return this._request("/api/queries/local", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
  },

  getOpportunities(minAge = 7) {
    return this._request(`/api/opportunities?min_age=${encodeURIComponent(minAge)}`);
  },

  getCapabilities() {
    return this._request("/api/capabilities");
  },

  async getCustomers() {
    const customers = [];
    let page;
    do {
      page = await this._request(`/api/customers?skip=${customers.length}&limit=1000`);
      customers.push(...page);
    } while (page.length === 1000);
    return customers;
  },

  getCustomerDetail(customerId) {
    return this._request(`/api/customers/${customerId}`);
  },

  updateCustomerCountry(customerId, country) {
    return this._request(`/api/customers/${customerId}/country`, {
      method: "PATCH",
      body: JSON.stringify({ country }),
    });
  },

  getGeographyCatalog() {
    return this._request("/api/geography/catalog");
  },

  getCustomCountries() {
    return this._request("/api/geography/custom-countries");
  },

  addCustomCountry(countryName, region) {
    return this._request("/api/geography/custom-countries", {
      method: "POST",
      body: JSON.stringify({
        country_name: countryName,
        region,
      }),
    });
  },

  getDashboard() {
    return this._request("/api/analytics/dashboard");
  },

  deleteObservation(observationId) {
    return this._request(`/api/observations/${observationId}`, {
      method: "DELETE",
    });
  },


  seed() {
    return this._request("/api/seed", { method: "POST" });
  },
};
