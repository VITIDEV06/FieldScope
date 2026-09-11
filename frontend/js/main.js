/* ========================================================================
   Fieldscope — App shell, navegación y estado del sistema
   ======================================================================== */

(function () {
  const navItems = [...document.querySelectorAll(".nav-item")];
  const views = [...document.querySelectorAll(".view")];

  const labels = {
    home: "Home",
    capture: "Captura",
    customer: "Cliente 360",
    map: "Geografía",
    dashboard: "Panorama",
    installed: "Installed Base",
    queries: "Consultas",
    opportunities: "Oportunidades",
  };

  const loaded = new Set();

  const sidebar = document.getElementById("sidebar");
  const sidebarOverlay = document.getElementById("sidebarOverlay");
  const mobileMenu = document.getElementById("mobileMenu");
  const mobileClose = document.getElementById("mobileClose");

  function showView(name) {
    if (name !== "capture") window.dispatchEvent(new Event("fieldscope:leave-capture"));
    navItems.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === name);
    });

    views.forEach((view) => {
      view.classList.toggle("is-active", view.dataset.viewPanel === name);
    });

    const currentLabel = document.getElementById("currentViewLabel");
    if (currentLabel) currentLabel.textContent = labels[name] || name;

    if (name === "customer" && !loaded.has("customer")) {
      loaded.add("customer");
      Customer360.refresh();
    }

    if (name === "map" && !loaded.has("map")) {
      loaded.add("map");
      GeoMap.refresh();
    }

    if (name === "dashboard" && !loaded.has("dashboard")) {
      loaded.add("dashboard");
      Dashboard.refresh();
    }

    if (name === "installed") {
      loaded.add("installed");
      InstalledBase.refresh();
    }

    if (name === "opportunities") {
      loaded.add("opportunities");
      InstalledBase.refreshOpportunities();
    }

    closeSidebar();
  }

  window.showView = showView;

  navItems.forEach((button) => {
    button.addEventListener("click", () => {
      showView(button.dataset.view);
    });
  });

  document.addEventListener("click", (event) => {
    const go = event.target.closest("[data-go]");
    if (go) showView(go.dataset.go);
  });

  function openSidebar() {
    sidebar?.classList.add("is-open");
    sidebarOverlay?.classList.add("is-open");
  }

  function closeSidebar() {
    sidebar?.classList.remove("is-open");
    sidebarOverlay?.classList.remove("is-open");
  }

  mobileMenu?.addEventListener("click", openSidebar);
  mobileClose?.addEventListener("click", closeSidebar);
  sidebarOverlay?.addEventListener("click", closeSidebar);

  async function checkSystemStatus() {
    const dot = document.getElementById("apiStatusDot");
    const apiLabel = document.getElementById("apiStatusLabel");
    const aiLabel = document.getElementById("aiModeLabel");
    const livePill = document.getElementById("livePill");
    const globalStatus = document.getElementById("globalStatusText");

    try {
      const health = await Api.health();

      dot?.classList.add("is-ok");
      dot?.classList.remove("is-down");

      if (apiLabel) {
        apiLabel.textContent = "API conectada";
      }

      livePill?.classList.remove("is-down", "is-fallback");

      if (health.qvac_ready) {
        if (aiLabel) {
          aiLabel.textContent = `${health.model || "QVAC"} · On-device`;
        }

        if (globalStatus) {
          globalStatus.textContent = "QVAC local · Listo";
        }
      } else {
        if (aiLabel) {
          aiLabel.textContent = "QVAC no está listo";
        }

        if (globalStatus) {
          globalStatus.textContent = "Inferencia local pendiente";
        }

        livePill?.classList.add("is-fallback");
      }
    } catch (error) {
      dot?.classList.add("is-down");
      dot?.classList.remove("is-ok");

      if (apiLabel) apiLabel.textContent = "API sin conexión";
      if (aiLabel) aiLabel.textContent = "Revisa el backend en :8001";
      if (globalStatus) globalStatus.textContent = "Backend desconectado";

      livePill?.classList.remove("is-fallback");
      livePill?.classList.add("is-down");
    }
  }

  function updateClock() {
    const now = new Date();

    const today = document.getElementById("todayLabel");
    const clock = document.getElementById("clockLabel");

    if (today) {
      today.textContent = now.toLocaleDateString("es-PA", {
        weekday: "short",
        day: "2-digit",
        month: "short",
      });
    }

    if (clock) {
      clock.textContent = now.toLocaleTimeString("es-PA", {
        hour: "2-digit",
        minute: "2-digit",
      });
    }
  }

  function showToast(title, message, variant = "default") {
    const stack = document.getElementById("toastStack");
    if (!stack) return;

    const toast = document.createElement("div");
    toast.className = `toast ${variant}`;

    toast.innerHTML = `
      <strong>${escapeHtml(title)}</strong>
      <span>${escapeHtml(message)}</span>
    `;

    stack.appendChild(toast);

    window.setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(6px)";
    }, 3600);

    window.setTimeout(() => {
      toast.remove();
    }, 4000);
  }

  window.showToast = showToast;

  function confirmAction({
    title = "Eliminar información",
    message = "¿Deseas continuar?",
    confirmText = "Eliminar",
    safetyNote = "El equipamiento instalado no se modificará.",
  } = {}) {
    const modal = document.getElementById("confirmModal");
    const titleNode = document.getElementById("confirmTitle");
    const messageNode = document.getElementById("confirmMessage");
    const safetyNode = document.getElementById("confirmSafetyNote");
    const accept = document.getElementById("confirmAccept");
    const cancel = document.getElementById("confirmCancel");

    if (!modal || !accept || !cancel) {
      return Promise.resolve(window.confirm(message));
    }

    titleNode.textContent = title;
    messageNode.textContent = message;
    safetyNode.textContent = safetyNote;
    accept.textContent = confirmText;

    const previousFocus = document.activeElement;
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");

    window.setTimeout(() => cancel.focus(), 0);

    return new Promise((resolve) => {
      let settled = false;

      const finish = (value) => {
        if (settled) return;
        settled = true;

        modal.classList.remove("is-open");
        modal.setAttribute("aria-hidden", "true");

        accept.removeEventListener("click", onAccept);
        cancel.removeEventListener("click", onCancel);
        modal.removeEventListener("click", onBackdrop);
        document.removeEventListener("keydown", onKeydown);

        previousFocus?.focus();
        resolve(value);
      };

      const onAccept = () => finish(true);
      const onCancel = () => finish(false);

      const onBackdrop = (event) => {
        if (event.target.matches("[data-confirm-close]")) {
          finish(false);
        }
      };

      const onKeydown = (event) => {
        if (event.key === "Tab") {
          const focusable = [...modal.querySelectorAll("button:not([disabled])")];
          const first = focusable[0], last = focusable.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
        if (event.key === "Escape") {
          finish(false);
        }
      };

      accept.addEventListener("click", onAccept);
      cancel.addEventListener("click", onCancel);
      modal.addEventListener("click", onBackdrop);
      document.addEventListener("keydown", onKeydown);
    });
  }

  window.confirmAction = confirmAction;

  function wireDataRefresh() {
    window.addEventListener("fieldscope:data-changed", async () => {
      if (loaded.has("customer")) {
        await Customer360.refresh();
      }

      if (loaded.has("map")) {
        await GeoMap.refresh({ preservePath: true });
      }

      if (loaded.has("dashboard")) {
        await Dashboard.refresh();
      }

      if (loaded.has("installed")) {
        await InstalledBase.refresh();
      }

      if (loaded.has("opportunities")) {
        await InstalledBase.refreshOpportunities();
      }

      await checkSystemStatus();
    });
  }

  function wireSearchShortcut() {
    document.addEventListener("keydown", (event) => {
      const isShortcut =
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "k";

      if (!isShortcut) return;

      event.preventDefault();
      showView("customer");

      const input = document.getElementById("customerSearch");
      window.setTimeout(() => input?.focus(), 80);
    });
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

  document.addEventListener("DOMContentLoaded", () => {
    Capture.init();
    Customer360.init();
    GeoMap.init();
    Dashboard.init();
    InstalledBase.init();

    wireDataRefresh();
    wireSearchShortcut();

    updateClock();
    checkSystemStatus();

    window.setInterval(updateClock, 30_000);
    window.setInterval(checkSystemStatus, 15_000);
  });
})();
