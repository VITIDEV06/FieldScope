/* ========================================================================
   Fieldscope — Captura conversacional
   ======================================================================== */

const Capture = (() => {
  let sessionId = null;
  let busy = false;
  let resetTimer = null;
  let readyToConfirm = false;

  const el = {};

  function init() {
    el.log = document.getElementById("chatLog");
    el.form = document.getElementById("chatForm");
    el.input = document.getElementById("chatInput");
    el.reporter = document.getElementById("reporterName");
    el.suggestions = document.getElementById("chatSuggestions");
    el.extractBody = document.getElementById("extractBody");
    el.confirmActions = document.getElementById("confirmActions");
    el.confirmButton = document.getElementById("confirmObservation");
    el.cancelButton = document.getElementById("cancelObservation");
    el.voiceButton = document.getElementById("voiceButton");

    renderEmptyChat();

    el.form.addEventListener("submit", onSubmit);

    el.input.addEventListener("input", autoGrow);

    el.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        el.form.requestSubmit();
      }
    });

    el.confirmButton?.addEventListener("click", confirmPending);
    el.cancelButton?.addEventListener("click", cancelPending);

    el.suggestions.addEventListener("click", (event) => {
      const button = event.target.closest(".chip");
      if (!button) return;

      el.input.value = button.dataset.fill || "";
      el.input.focus();
      autoGrow();
    });
  }

  function autoGrow() {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 120)}px`;
  }

  function renderEmptyChat() {
    el.log.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">
          <svg viewBox="0 0 24 24">
            <path d="M5 5.5h14v9H9l-4 3.5V5.5Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
            <path d="M8.5 9.5h7M8.5 12h4.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </div>
        <strong>Comienza con una observación</strong>
        <p>Escribe lo que viste en la visita. El sistema extraerá cliente, ubicación, equipo, cantidad, edad y fabricante.</p>
      </div>
    `;
  }

  async function onSubmit(event) {
    event.preventDefault();

    const text = el.input.value.trim();
    if (!text || busy || window.FieldScopeVoice?.active) return;
    clearTimeout(resetTimer);
    resetTimer = null;

    clearEmptyState();

    appendMessage("user", text);

    el.input.value = "";
    autoGrow();
    setBusy(true);

    const thinkingId = appendMessage("system", "Analizando observación…", {
      pending: true,
    });

    try {
      const response = await Api.processObservation({
        text,
        submittedBy: el.reporter.value.trim() || "Field Employee",
        sessionId,
      });

      replaceMessage(thinkingId, buildResponseMessage(response));

      if (response.status === "needs_more_info") {
        sessionId = response.session_id;
        renderExtract(response.extracted_so_far);
        showConfirmation(false);
      }

      if (response.status === "ready_for_confirmation") {
        sessionId = response.session_id;
        renderExtract(response.extracted);
        showConfirmation(true);
        appendMessage("system", "Datos listos para revisión. Confirma para guardarlos en Installed Base.");
      }

    if (response.status === "success") {
        sessionId = null;
        renderExtract(successToExtractShape(response));

        window.dispatchEvent(
          new CustomEvent("fieldscope:data-changed", {
            detail: { source: "capture" },
          })
        );

        toast(
          "Registro completado",
          `${response.customer?.name || "Cliente"} se ha registrado correctamente.`,
          "success"
        );

        // Muestra el mensaje final un instante y luego prepara un chat nuevo.
        resetConversationAfterSuccess();
      }

      if (response.status === "error") {
        showConfirmation(false);
        sessionId = response.session_id;
        renderExtract(response.extracted);
        toast("No se pudo completar", response.message || "Revisa la información.", "error");
      }
    } catch (error) {
      replaceMessage(thinkingId, {
        text: `No se pudo procesar la observación: ${error.message}`,
        variant: "error",
      });

      el.input.value = text;
      autoGrow();
      toast("No se pudo procesar", error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  function clearEmptyState() {
    const empty = el.log.querySelector(".empty-state, .chat-empty");
    if (empty) el.log.innerHTML = "";
  }

  function buildResponseMessage(response) {
    if (response.status === "ready_for_confirmation") {
      return { text: response.message, variant: "default" };
    }
    if (response.status === "needs_more_info") {
      return {
        text: response.follow_up,
        variant: "default",
      };
    }

    if (response.status === "success") {
      const equipment = response.equipment || [];
      const categoryCount = equipment.length;

      const totalUnits = equipment.reduce(
        (sum, eq) => sum + Math.max(Number(eq.quantity) || 0, 0),
        0
      );

      return {
        text:
          `Registrado para ${response.customer.name}. ` +
          `${totalUnits} ${totalUnits === 1 ? "unidad" : "unidades"} ` +
          `en ${categoryCount} ${categoryCount === 1 ? "categoría" : "categorías"} de equipo.`,
        variant: "success",
        meta:
          `Estado: ${response.observation_state || "Reportado"} · ` +
          `Confianza: ${translateConfidence(response.observation_confidence)}` +
          (response.contains_duplicate_update
            ? " · coincidencia con registro previo"
            : ""),
      };
    }

    return {
      text: response.message || "No se pudo completar la observación.",
      variant: "error",
    };
  }

  function setBusy(value) {
    busy = value;

    const button = el.form.querySelector(".btn-send");
    button.disabled = value;

    el.input.disabled = value;
    el.confirmButton.disabled = value || !readyToConfirm;
    el.cancelButton.disabled = value;
    window.dispatchEvent(new CustomEvent("fieldscope:capture-busy", { detail: value }));
  }

  function appendMessage(role, text, options = {}) {
    const id = `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;

    const node = document.createElement("div");
    node.className = `msg msg-${role}`;
    node.id = id;
    node.dataset.pending = options.pending ? "1" : "0";
    node.textContent = text;

    el.log.appendChild(node);
    scrollToBottom();

    return id;
  }

  function replaceMessage(id, { text, variant = "default", meta }) {
    const node = document.getElementById(id);
    if (!node) return;

    node.textContent = text;
    node.dataset.pending = "0";

    if (variant === "error") node.classList.add("is-error");
    if (variant === "success") node.classList.add("is-success");

    if (meta) {
      const metaNode = document.createElement("span");
      metaNode.className = "msg-meta";
      metaNode.textContent = meta;
      node.appendChild(metaNode);
    }

    scrollToBottom();
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      el.log.scrollTop = el.log.scrollHeight;
    });
  }

  function resetConversationAfterSuccess() {
    sessionId = null;
    showConfirmation(false);

    resetTimer = window.setTimeout(() => {
      if (busy || sessionId || el.input.value.trim() || window.FieldScopeVoice?.active) return;
      // Limpia completamente la conversación para un nuevo registro.
      el.log.innerHTML = "";
      renderEmptyChat();

      // Limpia el panel de datos extraídos.
      renderExtract(null);

      // Deja el cuadro de texto listo para el siguiente hospital.
      el.input.value = "";
      autoGrow();
      el.input.focus();
    }, 1400);
  }

  function showConfirmation(show) {
    if (!el.confirmActions) return;
    readyToConfirm = show;
    el.confirmActions.hidden = !sessionId;
    el.confirmButton.disabled = busy || !show;
  }

  async function confirmPending() {
    if (!sessionId || busy) return;
    setBusy(true);
    if (el.confirmButton) el.confirmButton.disabled = true;
    try {
      const response = await Api.confirmObservation(sessionId);
      appendMessage("system", buildResponseMessage(response).text, { pending: false });
      renderExtract(successToExtractShape(response));
      window.dispatchEvent(new CustomEvent("fieldscope:data-changed", { detail: { source: "capture" } }));
      toast("Registro completado", `${response.customer?.name || "Cliente"} se guardó localmente.`, "success");
      resetConversationAfterSuccess();
    } catch (error) {
      toast("No se pudo guardar", error.message, "error");
    } finally {
      if (el.confirmButton) el.confirmButton.disabled = false;
      setBusy(false);
    }
  }

  async function cancelPending() {
    if (!sessionId || busy) return;
    setBusy(true);
    try { await Api.cancelPendingObservation(sessionId); }
    catch (error) { toast("No se pudo descartar", error.message, "error"); return; }
    finally { setBusy(false); }
    clearTimeout(resetTimer);
    window.FieldScopeVoice?.cancel();
    sessionId = null;
    showConfirmation(false);
    el.log.innerHTML = "";
    renderEmptyChat();
    renderExtract(null);
    toast("Captura descartada", "No se guardó información en Installed Base.");
  }

  function successToExtractShape(response) {
    return {
      customer_name: response.customer?.name,
      city: response.customer?.city,
      country: response.customer?.country,
      equipment: (response.equipment || []).map((equipment) => ({
        modality: equipment.modality,
        manufacturer: equipment.manufacturer,
        model: equipment.model,
        estimated_age: equipment.estimated_age,
        quantity: equipment.quantity,
      })),
    };
  }

  function translateConfidence(value) {
    return (
      {
        High: "alta",
        Medium: "media",
        Low: "baja",
        Unknown: "desconocida",
      }[value] || value || "desconocida"
    );
  }

  function fieldRow(label, value) {
    const missing =
      value === null ||
      value === undefined ||
      value === "" ||
      value === "Desconocido";

    return `
      <div class="extract-field">
        <div class="label">${escapeHtml(label)}</div>
        <div class="value ${missing ? "is-missing" : ""}">
          ${missing ? "Sin especificar" : escapeHtml(value)}
        </div>
      </div>
    `;
  }

  function renderExtract(data) {
    const fields = data ? [data.customer_name, data.city, data.country,
      ...(data.equipment || []).flatMap(eq => [eq.modality, eq.quantity, eq.manufacturer, eq.model, eq.estimated_age])] : [];
    const known = fields.filter(value => value != null && value !== "" && value !== "Desconocido").length;
    document.getElementById("qualityLabel").textContent = fields.length ? `${known}/${fields.length} campos conocidos` : "Sin datos";
    document.getElementById("qualityProgress").style.width = fields.length ? `${100 * known / fields.length}%` : "0%";
    if (!data) {
      el.extractBody.innerHTML = emptyExtract();
      return;
    }

    const equipment = data.equipment || [];
    const categoryCount = equipment.length;

    const totalUnits = equipment.reduce(
      (sum, eq) => sum + Math.max(Number(eq.quantity) || 0, 0),
      0
    );

    el.extractBody.innerHTML = `
      ${fieldRow("Cliente", data.customer_name)}
      ${fieldRow("Ciudad", data.city)}
      ${fieldRow("País", data.country)}

      <div class="extract-field">
        <div class="label">Cobertura identificada</div>
        <div class="value">
          ${categoryCount} ${categoryCount === 1 ? "categoría" : "categorías"}
          ·
          ${totalUnits} ${totalUnits === 1 ? "unidad conocida" : "unidades conocidas"}
        </div>
      </div>

      ${
        equipment.length
          ? equipment.map(equipmentCard).join("")
          : `<p class="extract-empty">Ningún equipo identificado todavía.</p>`
      }
    `;
  }

  function emptyExtract() {
    return `
      <div class="empty-state">
        <div class="empty-icon">
          <svg viewBox="0 0 24 24">
            <path d="M12 3v18M3 12h18" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
          </svg>
        </div>
        <strong>Aún no hay datos estructurados</strong>
        <p>Inicia una conversación para ver aquí cliente, ubicación, cantidades y equipos.</p>
      </div>
    `;
  }

  function equipmentCard(eq) {
    const details = [
      eq.manufacturer,
      eq.model,
      eq.estimated_age != null ? `${eq.estimated_age} años` : null,
    ]
      .filter(Boolean)
      .join(" · ");

    return `
      <div class="eq-card">
        <div class="eq-card-top">
          <span class="eq-card-modality">
            ${escapeHtml(eq.modality || "Sin modalidad")}
          </span>

          <span class="eq-card-details">
            ${eq.quantity ? `× ${escapeHtml(eq.quantity)}` : ""}
          </span>
        </div>

        <div class="eq-card-details">
          ${details ? escapeHtml(details) : "Fabricante, modelo y edad sin confirmar"}
        </div>
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

  return { init, isBusy: () => busy };
})();
