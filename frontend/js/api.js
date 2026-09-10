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

    const response = await fetch(`${API_BASE}${path}`, config);

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

  getCustomers({ skip = 0, limit = 200 } = {}) {
    return this._request(`/api/customers?skip=${skip}&limit=${limit}`);
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
