/* ========================================================================
   Fieldscope — Dictado local con QVAC ASR
   - Captura audio en el navegador.
   - Convierte a WAV PCM mono 16 kHz localmente.
   - Envía el WAV únicamente al backend local 127.0.0.1:8001.
   - El backend transcribe con QVAC WHISPER_TINY on-device.
   ======================================================================== */

(() => {
  "use strict";

  const VOICE_API = `${API_BASE}/api/voice/transcribe`;
  const MAX_RECORDING_MS = 60_000;

  let recording = false;
  let starting = false;
  let generation = 0;
  let controller = null;
  let processing = false;
  let stream = null;
  let audioContext = null;
  let sourceNode = null;
  let processorNode = null;
  let silentGain = null;
  let chunks = [];
  let inputSampleRate = 48_000;
  let autoStopTimer = null;

  let button = null;
  let input = null;

  function initVoiceCapture() {
    input = document.getElementById("chatInput");
    const form = document.getElementById("chatForm");

    if (!input || !form) return;

    button = document.getElementById("voiceButton");

    // Compatibilidad con versiones del frontend que todavía no traen botón.
    if (!button) {
      button = document.createElement("button");
      button.id = "voiceButton";
      button.type = "button";
      button.className = "voice-button voice-generated";
      button.setAttribute("aria-label", "Dictar observación con QVAC local");
      button.innerHTML = microphoneSvg();

      const sendButton = form.querySelector(".btn-send, .send-button, button[type='submit']");
      if (sendButton) {
        form.insertBefore(button, sendButton);
      } else {
        form.appendChild(button);
      }

      // Solo tocamos el layout si tuvimos que crear el botón nosotros.
      form.classList.add("voice-generated-layout");
    }

    button.classList.add("voice-button");
    button.disabled = false;
    button.removeAttribute("disabled");
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", "Dictar observación con QVAC local");
    button.title = "Dictar con QVAC local";

    // Si el botón solo tenía un emoji o estaba vacío, usa un SVG consistente.
    if (!button.querySelector("svg")) {
      button.innerHTML = microphoneSvg();
    }

    injectVoiceStyles();
    button.addEventListener("click", onVoiceClick);
  }

  async function onVoiceClick() {
    if (processing || starting || Capture.isBusy()) return;

    if (recording) {
      await stopRecordingAndTranscribe();
      return;
    }

    await startRecording();
  }

  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia) {
      notify(
        "Micrófono no disponible",
        "Este navegador no ofrece acceso al micrófono en esta página.",
        "error"
      );
      return;
    }

    if (!window.isSecureContext) {
      notify(
        "Contexto no seguro",
        "Abre Fieldscope desde http://127.0.0.1:5500 o localhost para usar el micrófono.",
        "error"
      );
      return;
    }

    starting = true;
    const attempt = ++generation;
    setButtonState("processing");
    try {
      const grantedStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });

      if (attempt !== generation) {
        grantedStream.getTracks().forEach(track => track.stop());
        return;
      }
      stream = grantedStream;
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      audioContext = new AudioContextClass();
      await audioContext.resume();
      if (attempt !== generation) return;

      inputSampleRate = audioContext.sampleRate;
      chunks = [];

      sourceNode = audioContext.createMediaStreamSource(stream);
      processorNode = audioContext.createScriptProcessor(4096, 1, 1);
      silentGain = audioContext.createGain();
      silentGain.gain.value = 0;

      processorNode.onaudioprocess = (event) => {
        if (!recording) return;
        const data = event.inputBuffer.getChannelData(0);
        chunks.push(new Float32Array(data));
      };

      sourceNode.connect(processorNode);
      processorNode.connect(silentGain);
      silentGain.connect(audioContext.destination);

      recording = true;
      setButtonState("recording");

      autoStopTimer = window.setTimeout(() => {
        if (recording) stopRecordingAndTranscribe();
      }, MAX_RECORDING_MS);

      notify(
        "Grabando",
        "Habla normalmente. Pulsa el micrófono otra vez para terminar.",
        "default"
      );
    } catch (error) {
      if (attempt !== generation) return;
      cleanupAudio();
      setButtonState("idle");

      const denied = error?.name === "NotAllowedError" || error?.name === "SecurityError";
      notify(
        denied ? "Permiso de micrófono bloqueado" : "No se pudo iniciar el micrófono",
        denied
          ? "Permite el micrófono para 127.0.0.1 en Chrome y vuelve a intentarlo."
          : error?.message || "Revisa el dispositivo de entrada de audio.",
        "error"
      );
    } finally {
      if (attempt === generation) {
        starting = false;
        if (!recording) setButtonState("idle");
      }
    }
  }

  async function stopRecordingAndTranscribe() {
    if (!recording) return;

    recording = false;
    if (autoStopTimer) {
      clearTimeout(autoStopTimer);
      autoStopTimer = null;
    }

    // Copiar el audio antes de destruir el grafo.
    const capturedChunks = chunks;
    const capturedRate = inputSampleRate;
    cleanupAudio();

    const merged = mergeFloat32(capturedChunks);
    const duration = merged.length / Math.max(capturedRate, 1);

    if (duration < 0.35) {
      setButtonState("idle");
      notify(
        "Grabación muy corta",
        "Mantén el micrófono activo un poco más y vuelve a hablar.",
        "error"
      );
      return;
    }

    processing = true;
    const attempt = generation;
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 150_000);
    setButtonState("processing");

    try {
      const mono16k = resampleLinear(merged, capturedRate, 16_000);
      const wavBlob = encodePcm16Wav(mono16k, 16_000);

      const response = await fetch(VOICE_API, {
        method: "POST",
        headers: {
          "Content-Type": "audio/wav",
        },
        body: wavBlob,
        signal: controller.signal,
      });

      const body = await readJsonSafely(response);

      if (!response.ok) {
        const detail = body?.detail;
        const message =
          (typeof detail === "object" && detail?.message) ||
          (typeof detail === "string" && detail) ||
          body?.message ||
          `HTTP ${response.status}`;

        const hint = typeof detail === "object" ? detail?.hint : null;
        throw new Error(hint ? `${message} ${hint}` : message);
      }

      if (attempt !== generation) return;
      const transcript = String(body?.text || "").trim();
      if (!transcript) {
        throw new Error("QVAC no devolvió una transcripción.");
      }

      // No se envía automáticamente: el usuario puede revisar antes de registrar.
      const previous = input.value.trim();
      input.value = previous ? `${previous} ${transcript}` : transcript;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();

      notify(
        "Dictado listo",
        "QVAC transcribió el audio localmente. Revisa el texto y envíalo cuando quieras.",
        "success"
      );
    } catch (error) {
      if (attempt !== generation) return;
      notify(
        "No se pudo transcribir",
        error?.message || "Revisa QVAC ASR en el backend.",
        "error"
      );
    } finally {
      clearTimeout(timeout);
      if (attempt === generation) {
        controller = null;
        processing = false;
        setButtonState("idle");
      }
    }
  }

  function cancel() {
    generation += 1;
    recording = false;
    starting = false;
    processing = false;
    controller?.abort();
    clearTimeout(autoStopTimer);
    cleanupAudio();
    setButtonState("idle");
  }

  window.FieldScopeVoice = { get active() { return recording || processing || starting; }, cancel };
  window.addEventListener("pagehide", cancel);
  window.addEventListener("fieldscope:leave-capture", cancel);
  window.addEventListener("fieldscope:capture-busy", () => {
    if (button) button.disabled = Capture.isBusy() || starting || processing;
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape") cancel(); });

  function cleanupAudio() {
    try {
      if (processorNode) processorNode.onaudioprocess = null;
      processorNode?.disconnect();
      sourceNode?.disconnect();
      silentGain?.disconnect();
    } catch (_) {}

    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
    }

    if (audioContext && audioContext.state !== "closed") {
      audioContext.close().catch(() => {});
    }

    stream = null;
    audioContext = null;
    sourceNode = null;
    processorNode = null;
    silentGain = null;
    chunks = [];
  }

  function setButtonState(state) {
    if (!button) return;

    button.classList.toggle("is-recording", state === "recording");
    button.classList.toggle("is-processing", state === "processing");
    button.disabled = state === "processing" || Capture.isBusy();
    const send = document.querySelector("#chatForm button[type=submit]");
    if (send) send.disabled = state !== "idle" || Capture.isBusy();
    const status = document.getElementById("voiceStatus");
    if (status) status.textContent = state === "recording" ? "Grabando · pulsa para detener · Esc para cancelar" : state === "processing" ? "Procesando audio local · Esc para cancelar" : "";

    if (state === "recording") {
      button.setAttribute("aria-pressed", "true");
      button.setAttribute("aria-label", "Detener grabación y transcribir");
      button.title = "Detener y transcribir con QVAC";
    } else if (state === "processing") {
      button.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-label", "QVAC está transcribiendo");
      button.title = "QVAC está transcribiendo…";
    } else {
      button.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-label", "Dictar observación con QVAC local");
      button.title = "Dictar con QVAC local";
    }
  }

  function mergeFloat32(parts) {
    const length = parts.reduce((sum, part) => sum + part.length, 0);
    const merged = new Float32Array(length);
    let offset = 0;

    for (const part of parts) {
      merged.set(part, offset);
      offset += part.length;
    }

    return merged;
  }

  function resampleLinear(buffer, inputRate, outputRate) {
    if (!buffer.length) return new Float32Array(0);
    if (inputRate === outputRate) return buffer;

    const newLength = Math.max(
      1,
      Math.round(buffer.length * outputRate / inputRate)
    );
    const output = new Float32Array(newLength);
    const ratio = inputRate / outputRate;

    for (let i = 0; i < newLength; i += 1) {
      const position = i * ratio;
      const left = Math.floor(position);
      const right = Math.min(left + 1, buffer.length - 1);
      const fraction = position - left;
      output[i] = buffer[left] * (1 - fraction) + buffer[right] * fraction;
    }

    return output;
  }

  function encodePcm16Wav(samples, sampleRate) {
    const bytesPerSample = 2;
    const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
    const view = new DataView(buffer);

    writeAscii(view, 0, "RIFF");
    view.setUint32(4, 36 + samples.length * bytesPerSample, true);
    writeAscii(view, 8, "WAVE");
    writeAscii(view, 12, "fmt ");
    view.setUint32(16, 16, true);       // PCM fmt chunk size
    view.setUint16(20, 1, true);        // PCM
    view.setUint16(22, 1, true);        // mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * bytesPerSample, true);
    view.setUint16(32, bytesPerSample, true);
    view.setUint16(34, 16, true);
    writeAscii(view, 36, "data");
    view.setUint32(40, samples.length * bytesPerSample, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      const pcm = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      view.setInt16(offset, pcm, true);
      offset += 2;
    }

    return new Blob([buffer], { type: "audio/wav" });
  }

  function writeAscii(view, offset, value) {
    for (let i = 0; i < value.length; i += 1) {
      view.setUint8(offset + i, value.charCodeAt(i));
    }
  }

  async function readJsonSafely(response) {
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  }

  function notify(title, message, variant = "default") {
    if (typeof window.showToast === "function") {
      window.showToast(title, message, variant);
      return;
    }

    // Fallback simple si el sistema de toasts aún no se inicializó.
    console[variant === "error" ? "error" : "log"](`[${title}] ${message}`);
  }

  function microphoneSvg() {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"
              fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
        <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"
              fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>
      </svg>`;
  }

  function injectVoiceStyles() {
    if (document.getElementById("fieldscopeVoiceStyles")) return;

    const style = document.createElement("style");
    style.id = "fieldscopeVoiceStyles";
    style.textContent = `
      .voice-button {
        width: 48px;
        height: 48px;
        align-self: end;
        display: grid;
        place-items: center;
        flex: 0 0 auto;
        border: 1px solid rgba(130, 163, 195, .16);
        border-radius: 14px;
        color: #8095aa;
        background: rgba(14, 27, 42, .82);
        box-shadow: inset 0 1px 0 rgba(255, 255, 255, .025);
        cursor: pointer;
        transition: .2s ease;
      }

      .voice-button:hover:not(:disabled) {
        color: #bfe2ff;
        border-color: rgba(83, 161, 255, .34);
        background: rgba(53, 121, 205, .10);
        transform: translateY(-1px);
      }

      .voice-button svg {
        width: 19px;
        height: 19px;
      }

      .voice-button.is-recording {
        color: #ff9aaa;
        border-color: rgba(255, 105, 128, .48);
        background: rgba(255, 85, 110, .10);
        box-shadow: 0 0 0 4px rgba(255, 85, 110, .055);
        animation: fieldscope-voice-pulse 1.15s ease-in-out infinite;
      }

      .voice-button.is-processing {
        color: #91d5ff;
        border-color: rgba(78, 170, 255, .35);
        background: rgba(65, 147, 255, .09);
      }

      .voice-button:disabled {
        opacity: .55;
        cursor: wait;
        transform: none;
      }

      .composer.voice-generated-layout {
        grid-template-columns: minmax(0, 1fr) 48px 48px;
      }

      @keyframes fieldscope-voice-pulse {
        0%, 100% { box-shadow: 0 0 0 3px rgba(255, 85, 110, .045); }
        50% { box-shadow: 0 0 0 7px rgba(255, 85, 110, .085); }
      }
    `;

    document.head.appendChild(style);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initVoiceCapture, { once: true });
  } else {
    initVoiceCapture();
  }
})();
