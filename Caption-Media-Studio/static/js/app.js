(() => {
  "use strict";

  const state = {
    currentMedia: null,
    studioMedia: null,
    voiceMedia: null,
    audioMedia: null,
    videoMedia: null,
    segments: [],
    voiceSegments: [],
    layers: [],
    selectedLayer: -1,
    undoStack: [],
    duration: 0,
    recorder: null,
    recordedChunks: [],
    recordStartedAt: 0,
    recordTimer: null,
    mediaInfo: null,
  };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const LANG_LABELS = { hi: "Hindi", en: "English", ur: "Urdu" };

  function showToast(message, type = "info", timeout = 4200) {
    const region = $("#toastRegion");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    region.append(toast);
    window.setTimeout(() => toast.remove(), timeout);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) throw new Error(body?.error || `Request failed (${response.status}).`);
    return body;
  }

  function jsonOptions(body) {
    return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  }

  function setView(name) {
    const view = $(`#view-${name}`);
    if (!view) return;
    $$(".view").forEach((item) => item.classList.toggle("active", item === view));
    $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.nav === name));
    $("#currentPage").textContent = view.dataset.title || "Workspace";
    $("#sidebar").classList.remove("open");
    if (name === "history") refreshHistory();
    if (name === "settings") refreshDiagnostics();
    if (name === "dashboard") refreshDashboard();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function formatTime(seconds) {
    const safe = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const secs = Math.floor(safe % 60);
    const millis = Math.round((safe % 1) * 1000);
    return `${hours ? `${String(hours).padStart(2, "0")}:` : ""}${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(Number(seconds))) return "—";
    const value = Math.max(0, Math.floor(Number(seconds)));
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(Number(bytes))) return "—";
    const size = Number(bytes);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  }

  function setProgress(elementId, percent, message) {
    const wrapper = $(`#${elementId}`);
    if (!wrapper) return;
    wrapper.classList.remove("hidden");
    const bar = $("span", wrapper);
    const text = $("small", wrapper);
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    if (text) text.textContent = message || "Uploading…";
  }

  function uploadMedia(file, target, progressId, nameId) {
    return new Promise((resolve, reject) => {
      if (!file) return reject(new Error("Choose a media file first."));
      const request = new XMLHttpRequest();
      const data = new FormData();
      data.append("file", file);
      setProgress(progressId, 0, "Uploading…");
      request.open("POST", "/api/upload");
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) setProgress(progressId, event.loaded / event.total * 100, `Uploading ${Math.round(event.loaded / event.total * 100)}%`);
      });
      request.addEventListener("load", () => {
        $(`#${progressId}`)?.classList.add("hidden");
        let payload;
        try { payload = JSON.parse(request.responseText); } catch { payload = {}; }
        if (request.status < 200 || request.status >= 300) {
          reject(new Error(payload.error || `Upload failed (${request.status}).`));
          return;
        }
        state[target] = payload;
        const name = $(`#${nameId}`);
        if (name) name.textContent = `${payload.filename} · ${formatBytes(payload.size)}`;
        if (target === "currentMedia") {
          $("#captionFileName").textContent = `${payload.filename} · ${formatBytes(payload.size)}`;
          $("#dashboardFileName").textContent = `${payload.filename} · ${formatBytes(payload.size)}`;
        }
        showToast(`${payload.filename} uploaded.`, "success");
        resolve(payload);
      });
      request.addEventListener("error", () => {
        $(`#${progressId}`)?.classList.add("hidden");
        reject(new Error("Upload failed. Check your connection to the local server."));
      });
      request.addEventListener("abort", () => {
        $(`#${progressId}`)?.classList.add("hidden");
        reject(new Error("Upload cancelled."));
      });
      request.send(data);
    });
  }

  function uploadFromInput(inputId, target, progressId, nameId, onReady) {
    const input = $(`#${inputId}`);
    input.addEventListener("change", async () => {
      const file = input.files?.[0];
      if (!file) return;
      try {
        const uploaded = await uploadMedia(file, target, progressId, nameId);
        onReady?.(uploaded);
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  function createActionStatus(container, message, type = "") {
    container.className = `job-status${type ? ` ${type}` : ""}`;
    container.classList.remove("hidden");
    container.replaceChildren();
    const text = document.createElement("span");
    text.textContent = message;
    container.append(text);
  }

  async function pollJob(jobId, statusElement, onComplete) {
    let active = true;
    let interval;
    const poll = async () => {
      if (!active) return;
      try {
        const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
        const status = `${job.message || job.state}${job.progress ? ` · ${job.progress}%` : ""}`;
        createActionStatus(statusElement, status, job.state === "failed" ? "error" : job.state === "completed" ? "complete" : "");
        if (job.state === "completed") {
          active = false;
          window.clearInterval(interval);
          onComplete?.(job.result);
        } else if (job.state === "failed" || job.state === "cancelled") {
          active = false;
          window.clearInterval(interval);
          if (job.error) {
            const detail = document.createElement("div");
            detail.textContent = job.error;
            statusElement.append(detail);
          }
        }
      } catch (error) {
        active = false;
        window.clearInterval(interval);
        createActionStatus(statusElement, error.message, "error");
      }
    };
    await poll();
    if (active) interval = window.setInterval(poll, 1200);
  }

  async function startJob(url, payload, statusId, done) {
    const status = $(`#${statusId}`);
    try {
      createActionStatus(status, "Adding job to the local queue…");
      const response = await api(url, jsonOptions(payload));
      pollJob(response.job_id, status, done);
      return response.job_id;
    } catch (error) {
      createActionStatus(status, error.message, "error");
      showToast(error.message, "error");
      return null;
    }
  }

  function pushUndo() {
    state.undoStack.push(JSON.stringify(state.segments));
    if (state.undoStack.length > 30) state.undoStack.shift();
    $("#undoCaptions").disabled = state.undoStack.length === 0;
  }

  function renderTranscript(target = $("#transcriptEditor"), segments = state.segments, editable = true) {
    target.replaceChildren();
    if (!segments.length) {
      target.className = "transcript-editor empty-note";
      target.textContent = "No transcript lines yet.";
      return;
    }
    target.className = "transcript-editor";
    segments.forEach((segment, index) => {
      const row = document.createElement("div");
      row.className = "caption-row";
      const time = document.createElement("div");
      time.className = "caption-time";
      const timeLabel = document.createElement("span");
      timeLabel.textContent = formatTime(segment.start);
      time.append(timeLabel);
      const wrap = document.createElement("div");
      wrap.className = "caption-edit-wrap";
      if (editable) {
        const textarea = document.createElement("textarea");
        textarea.value = segment.text || "";
        textarea.rows = Math.max(1, Math.min(4, Math.ceil(textarea.value.length / 72)));
        textarea.setAttribute("aria-label", `Caption line ${index + 1}`);
        textarea.addEventListener("focus", pushUndo, { once: true });
        textarea.addEventListener("input", () => {
          if (!segment.original_text) segment.original_text = segment.text;
          segment.text = textarea.value;
          state.segments[index] = segment;
          let original = $(".original-text", wrap);
          if (segment.original_text && !original) {
            original = document.createElement("small");
            original.className = "original-text";
            wrap.append(original);
          }
          if (original) original.textContent = `Original: ${segment.original_text || ""}`;
          updateCaptionPreview();
        });
        wrap.append(textarea);
      } else {
        const text = document.createElement("div");
        text.textContent = segment.text || "";
        wrap.append(text);
      }
      if (segment.original_text && segment.original_text !== segment.text) {
        const original = document.createElement("small");
        original.className = "original-text";
        original.textContent = `Original: ${segment.original_text}`;
        wrap.append(original);
      }
      if (editable) {
        const timeInputs = document.createElement("div");
        timeInputs.className = "caption-time-controls";
        [["In", "start"], ["Out", "end"]].forEach(([label, key]) => {
          const field = document.createElement("label");
          field.textContent = label;
          const input = document.createElement("input");
          input.type = "number";
          input.min = "0";
          input.step = "0.01";
          input.value = Number(segment[key] || 0).toFixed(2);
          input.setAttribute("aria-label", `Caption ${index + 1} ${label} time in seconds`);
          input.addEventListener("focus", pushUndo, { once: true });
          input.addEventListener("change", () => {
            const value = Math.max(0, Number(input.value) || 0);
            if (key === "end" && value < Number(segment.start || 0)) {
              input.value = Number(segment.start || 0).toFixed(2);
              segment.end = Number(segment.start || 0);
            } else if (key === "start" && value > Number(segment.end || 0)) {
              segment.start = value;
              segment.end = value;
              const endInput = $$(".caption-time-controls input", timeInputs)[1];
              if (endInput) endInput.value = value.toFixed(2);
            } else {
              segment[key] = value;
            }
            timeLabel.textContent = formatTime(segment.start);
            updateCaptionPreview();
          });
          field.append(input);
          timeInputs.append(field);
        });
        time.append(timeInputs);
      }
      row.append(time, wrap);
      target.append(row);
    });
  }

  function setTranscript(transcript, fromVoice = false) {
    const segments = Array.isArray(transcript?.segments) ? transcript.segments : [];
    if (fromVoice) {
      state.voiceSegments = segments.map((segment) => ({ ...segment }));
      renderTranscript($("#voiceTranscript"), state.voiceSegments, false);
      $("#voiceExportTxt").disabled = false;
      $("#voiceExportSrt").disabled = false;
      $("#voiceExportVtt").disabled = false;
      return;
    }
    pushUndo();
    state.segments = segments.map((segment) => ({
      ...segment,
      original_text: segment.original_text ?? segment.text ?? "",
    }));
    renderTranscript();
    updateCaptionPreview();
    const language = transcript.language ? ` · ${LANG_LABELS[transcript.language] || transcript.language}` : "";
    showToast(`Transcript ready${language}.`, "success");
  }

  function captionStyle() {
    const preset = $("#captionPreset").value;
    const presets = {
      shorts: { font: "Arial", size: 38, color: "#ffffff", highlight: "#a891ff", outline: 4, bold: true, position: "bottom", bgOpacity: 25 },
      reels: { font: "Arial", size: 36, color: "#ffffff", highlight: "#ff71b8", outline: 3, bold: true, position: "bottom", bgOpacity: 30 },
      gaming: { font: "Arial", size: 40, color: "#ffffff", highlight: "#65f0c2", outline: 5, bold: true, position: "center", bgOpacity: 15 },
      podcast: { font: "Georgia", size: 30, color: "#ffffff", highlight: "#ffca7b", outline: 2, bold: false, position: "bottom", bgOpacity: 65 },
      cinematic: { font: "Georgia", size: 28, color: "#ffffff", highlight: "#f0d99b", outline: 2, bold: false, position: "bottom", bgOpacity: 0 },
      minimal: { font: "Arial", size: 27, color: "#ffffff", highlight: "#ffffff", outline: 2, bold: false, position: "bottom", bgOpacity: 35 },
      karaoke: { font: "Arial", size: 37, color: "#ffffff", highlight: "#69f1da", outline: 4, bold: true, position: "bottom", bgOpacity: 20 },
    };
    const chosen = presets[preset];
    if (chosen) {
      $("#captionFont").value = chosen.font;
      $("#captionSize").value = chosen.size;
      $("#captionColor").value = chosen.color;
      $("#highlightColor").value = chosen.highlight;
      $("#captionOutline").value = chosen.outline;
      $("#captionBold").checked = chosen.bold;
      $("#captionPosition").value = chosen.position;
      $("#captionOpacity").value = chosen.bgOpacity;
    }
    const preview = $("#captionPreview");
    const sample = $(".caption-preview-text", preview);
    if (!sample) return;
    const active = $("#activeHighlight").checked;
    const sampleText = state.segments[0]?.text || "Your caption appears here";
    const words = sampleText.split(/\s+/);
    const wordLimit = Math.max(1, Number($("#maxWords").value) || 7);
    const display = words.slice(0, wordLimit);
    sample.replaceChildren();
    display.forEach((word, index) => {
      if (index) sample.append(document.createTextNode(" "));
      const part = document.createElement("span");
      part.textContent = word;
      if (active && index === 0) part.style.color = $("#highlightColor").value;
      sample.append(part);
    });
    sample.style.fontFamily = $("#captionFont").value;
    sample.style.fontSize = `${$("#captionSize").value}px`;
    sample.style.color = $("#captionColor").value;
    sample.style.fontWeight = $("#captionBold").checked ? "700" : "400";
    sample.style.fontStyle = $("#captionItalic").checked ? "italic" : "normal";
    sample.style.textAlign = $("#captionAlign").value;
    sample.style.textShadow = `0 2px ${$("#captionShadow").value}px #000`;
    sample.style.outline = `${$("#captionOutline").value}px transparent`;
    sample.style.lineHeight = $("#captionSpacing").value;
    const opacity = Math.max(0, Math.min(100, Number($("#captionOpacity").value))) / 100;
    const color = $("#captionBg").value;
    const rgb = color.match(/[a-f\d]{2}/gi)?.map((part) => parseInt(part, 16)) || [16, 17, 26];
    sample.style.backgroundColor = `rgba(${rgb.join(",")},${opacity})`;
    sample.style.alignSelf = $("#captionPosition").value === "top" ? "flex-start" : $("#captionPosition").value === "center" ? "center" : "flex-end";
    sample.style.marginTop = $("#captionPosition").value === "top" ? "0" : "";
    sample.style.marginBottom = $("#captionPosition").value === "center" ? "0" : "";
    sample.dataset.highlight = active ? $("#highlightColor").value : "";
    $("#captionSizeValue").textContent = `${$("#captionSize").value} px`;
  }

  function visibleSegments() {
    const delay = Number($("#captionTiming").value || 0) / 1000;
    return state.segments.map((segment) => {
      const start = Math.max(0, Number(segment.start) + delay);
      const end = Math.max(start + .01, Number(segment.end) + delay);
      return {
        ...segment,
        start,
        end,
        words: (segment.words || []).map((word) => {
          const wordStart = Math.max(0, Number(word.start) + delay);
          return {
            ...word,
            start: wordStart,
            end: Math.max(wordStart + .01, Number(word.end) + delay),
          };
        }),
      };
    });
  }

  async function exportSegments(format, variant) {
    if (!state.segments.length) {
      showToast("Generate or add caption lines before exporting.", "error");
      return;
    }
    try {
      const result = await api("/api/subtitles/export", jsonOptions({
        segments: visibleSegments(),
        format,
        variant,
        word_timestamps: $("#exportWordTimes").checked,
        max_words: Number($("#maxWords").value),
        max_lines: Number($("#captionLines").value),
      }));
      downloadResult(result);
      showToast(`${format.toUpperCase()} export is ready.`, "success");
      refreshHistory();
    } catch (error) { showToast(error.message, "error"); }
  }

  function downloadResult(result) {
    if (!result?.download_url) return;
    const link = document.createElement("a");
    link.href = result.download_url;
    link.download = result.filename || "";
    document.body.append(link);
    link.click();
    link.remove();
  }

  function renderLayers() {
    const list = $("#layerList");
    const timeline = $("#layerTimeline");
    list.replaceChildren();
    timeline.replaceChildren();
    if (!state.layers.length) {
      list.className = "layer-list empty-note";
      list.textContent = "Add a layer to start designing.";
      updateOverlayPreview();
      return;
    }
    list.className = "layer-list";
    const duration = Math.max(1, state.duration || 0, ...state.layers.map((layer) => Number(layer.end) || 0), 60);
    $("#timelineDuration").textContent = formatDuration(duration);
    state.layers.forEach((layer, index) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `layer-row${index === state.selectedLayer ? " active" : ""}`;
      const color = document.createElement("i");
      color.className = "layer-color";
      color.style.background = layer.color || "#a891ff";
      const content = document.createElement("span");
      content.className = "layer-row-main";
      const title = document.createElement("b");
      title.textContent = layer.text || `Layer ${index + 1}`;
      const sub = document.createElement("small");
      sub.textContent = `${formatTime(layer.start)}–${formatTime(layer.end)} · ${layer.animation}`;
      content.append(title, sub);
      row.append(color, content);
      row.addEventListener("click", () => selectLayer(index));
      list.append(row);

      const track = document.createElement("div");
      track.className = "timeline-track";
      const bar = document.createElement("div");
      bar.className = "timeline-bar";
      const start = Math.max(0, Number(layer.start) || 0);
      const end = Math.max(start, Number(layer.end) || 0);
      bar.style.left = `${Math.min(99, start / duration * 100)}%`;
      bar.style.width = `${Math.max(1, Math.min(100, (end - start) / duration * 100))}%`;
      bar.textContent = layer.text || `Layer ${index + 1}`;
      bar.title = `${formatTime(start)} - ${formatTime(end)}`;
      bar.addEventListener("click", () => selectLayer(index));
      track.append(bar);
      timeline.append(track);
    });
    updateOverlayPreview();
  }

  function selectedLayer() {
    return state.layers[state.selectedLayer] || null;
  }

  function selectLayer(index) {
    if (index < 0 || index >= state.layers.length) return;
    state.selectedLayer = index;
    const layer = selectedLayer();
    $("#selectedLayerLabel").textContent = `Layer ${index + 1} · ${layer.type || "Normal text"}`;
    const values = {
      layerText: layer.text, layerType: layer.type || "Normal text",
      layerAnimation: layer.animation || "none", layerFont: layer.font || "Sans",
      layerFontSize: layer.font_size ?? 48, layerColor: layer.color || "#ffffff",
      layerOpacity: layer.opacity ?? 100, layerOutline: layer.outline ?? 2,
      layerRotation: layer.rotation ?? 0, layerStart: layer.start ?? 0,
      layerEnd: layer.end ?? 5, layerAnimationDuration: layer.animation_duration ?? 0.5,
      layerLetterSpacing: layer.letter_spacing ?? 0,
      layerAlign: layer.alignment || "center",
      layerLineSpacing: layer.line_spacing ?? 1.2,
      layerShadow: layer.shadow ?? 4,
      layerBold: Boolean(layer.bold), layerItalic: Boolean(layer.italic),
      layerBackground: Boolean(layer.background), layerGradient: Boolean(layer.gradient),
      layerRounded: Boolean(layer.rounded), layerGlow: Boolean(layer.glow),
    };
    Object.entries(values).forEach(([id, value]) => { $(`#${id}`).value = value; });
    $$("[id^='layer']").filter((element) => element.matches("input,textarea,select")).forEach((element) => { element.disabled = false; });
    ["layerBold", "layerItalic", "layerBackground", "layerGradient", "layerRounded", "layerGlow"].forEach((id) => { $(`#${id}`).checked = values[id]; });
    renderLayers();
  }

  function readLayerControls() {
    const layer = selectedLayer();
    if (!layer) return;
    layer.text = $("#layerText").value;
    layer.type = $("#layerType").value;
    layer.animation = $("#layerAnimation").value;
    layer.font = $("#layerFont").value;
    layer.font_size = Number($("#layerFontSize").value) || 48;
    layer.color = $("#layerColor").value;
    layer.opacity = Number($("#layerOpacity").value);
    layer.outline = Number($("#layerOutline").value);
    layer.rotation = Number($("#layerRotation").value) || 0;
    layer.start = Math.max(0, Number($("#layerStart").value) || 0);
    layer.end = Math.max(layer.start + 0.1, Number($("#layerEnd").value) || layer.start + 5);
    layer.animation_duration = Number($("#layerAnimationDuration").value) || .5;
    layer.letter_spacing = Number($("#layerLetterSpacing").value) || 0;
    layer.alignment = $("#layerAlign").value;
    layer.line_spacing = Number($("#layerLineSpacing").value) || 1.2;
    layer.shadow = Number($("#layerShadow").value) || 0;
    layer.bold = $("#layerBold").checked;
    layer.italic = $("#layerItalic").checked;
    layer.background = $("#layerBackground").checked;
    layer.gradient = $("#layerGradient").checked;
    layer.rounded = $("#layerRounded").checked;
    layer.glow = $("#layerGlow").checked;
    renderLayers();
  }

  function animationClass(animation) {
    if (animation === "fade" || animation.startsWith("fade_")) return "fade";
    if (animation === "zoom" || animation === "zoom_out" || animation === "scale" || animation === "pop") return "zoom";
    if (animation === "pulse") return "blink";
    if (animation === "glow") return "glow";
    if (animation === "karaoke") return "karaoke";
    if (animation.startsWith("slide_")) return animation.replace("_", "-");
    if (animation === "bounce") return "bounce";
    if (animation === "shake") return "shake";
    if (animation === "typewriter" || animation === "letter_by_letter" || animation === "word_by_word") return "typewriter";
    return "";
  }

  function updateOverlayPreview() {
    const container = $("#overlayPreview");
    if (!container) return;
    container.replaceChildren();
    state.layers.forEach((layer, index) => {
      const text = document.createElement("div");
      text.className = `overlay-text${layer.background ? " boxed" : ""}${layer.gradient ? " gradient" : ""}${layer.italic ? " italic" : ""}`;
      if (index === state.selectedLayer) text.classList.add(animationClass(layer.animation));
      text.textContent = layer.text || "Text layer";
      text.style.left = `${(Number.isFinite(Number(layer.x)) ? Number(layer.x) : .5) * 100}%`;
      text.style.top = `${(Number.isFinite(Number(layer.y)) ? Number(layer.y) : .5) * 100}%`;
      const alignment = layer.alignment || "center";
      const horizontal = alignment === "left" ? "0" : alignment === "right" ? "-100%" : "-50%";
      text.style.transform = `translate(${horizontal}, -50%) rotate(${Number(layer.rotation) || 0}deg)`;
      text.style.color = layer.gradient ? "transparent" : (layer.color || "#ffffff");
      text.style.opacity = Math.max(0, Math.min(100, Number(layer.opacity ?? 100))) / 100;
      text.style.fontSize = `${Math.max(8, Math.min(96, Number(layer.font_size) || 48)) * .45}px`;
      text.style.fontFamily = layer.font || "Arial";
      text.style.fontWeight = layer.bold ? "700" : "500";
      text.style.letterSpacing = `${Number(layer.letter_spacing) || 0}px`;
      text.style.webkitTextStroke = `${Math.min(5, Number(layer.outline) || 0) / 2}px #171722`;
      const shadowAmount = Number.isFinite(Number(layer.shadow)) ? Number(layer.shadow) : 4;
      text.style.textShadow = layer.glow || layer.animation === "glow"
        ? `0 0 ${Math.max(4, shadowAmount)}px ${layer.color || "#fff"}`
        : `0 2px ${shadowAmount}px #000`;
      text.style.lineHeight = String(layer.line_spacing || 1.2);
      text.style.animationDuration = `${Math.max(.15, Number(layer.animation_duration) || .5)}s`;
      text.style.background = layer.background ? "#11121a99" : "";
      text.style.borderRadius = layer.rounded ? "9px" : "0";
      text.dataset.layer = String(index);
      text.addEventListener("pointerdown", beginDragLayer);
      container.append(text);
    });
    syncPreviewTime();
  }

  function beginDragLayer(event) {
    const target = event.currentTarget;
    const index = Number(target.dataset.layer);
    const container = $("#overlayPreview");
    target.setPointerCapture(event.pointerId);
    const move = (moveEvent) => {
      const rect = container.getBoundingClientRect();
      state.layers[index].x = Math.max(0, Math.min(1, (moveEvent.clientX - rect.left) / rect.width));
      state.layers[index].y = Math.max(0, Math.min(1, (moveEvent.clientY - rect.top) / rect.height));
      target.style.left = `${state.layers[index].x * 100}%`;
      target.style.top = `${state.layers[index].y * 100}%`;
    };
    const up = () => {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", up);
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", up);
  }

  function syncPreviewTime() {
    const time = $("#studioVideo").currentTime || 0;
    $$(".overlay-text", $("#overlayPreview")).forEach((element) => {
      const layer = state.layers[Number(element.dataset.layer)];
      const active = layer && time >= Number(layer.start || 0) && time <= Number(layer.end || Infinity);
      element.style.visibility = active ? "visible" : "hidden";
    });
  }

  function addLayer(source) {
    const layer = source ? { ...source, x: source.x ?? .5, y: source.y ?? .5 } : {
      text: "Your text here", type: "Normal text", animation: "fade",
      font: "Sans", font_size: 48, color: "#ffffff", opacity: 100,
      outline: 2, rotation: 0, start: 0, end: 5, animation_duration: .5,
      letter_spacing: 0, alignment: "center", line_spacing: 1.2, shadow: 4,
      bold: true, italic: false, background: false, rounded: false, glow: false,
      gradient: false, x: .5, y: .75,
    };
    state.layers.push(layer);
    selectLayer(state.layers.length - 1);
  }

  function setVideoPreview(uploaded) {
    const video = $("#studioVideo");
    video.src = uploaded.url;
    $("#videoPlaceholder").classList.add("hidden");
    video.classList.remove("hidden");
    video.load();
  }

  async function renderVideo() {
    if (!state.studioMedia) return showToast("Upload a video in Text Animation Studio first.", "error");
    if (!state.layers.length && !$("#renderSubtitles").checked) return showToast("Add a text layer or enable subtitle rendering first.", "error");
    await startJob("/api/video/render", {
      file_id: state.studioMedia.file_id,
      layers: state.layers,
      segments: $("#renderSubtitles").checked ? visibleSegments() : [],
      burn_subtitles: $("#renderSubtitles").checked,
      caption_style: getCaptionStyleForRender(),
    }, "renderJob", (result) => {
      addOutputLink($("#renderJob"), result);
      refreshHistory();
    });
  }

  function addOutputLink(container, result) {
    if (!result?.file_id) return;
    const link = document.createElement("a");
    link.href = `/api/files/${encodeURIComponent(result.file_id)}`;
    link.download = result.filename || "";
    link.textContent = ` Download ${result.filename || "output"}`;
    container.append(link);
    showToast("Output is ready for download.", "success");
  }

  async function getMediaInfo(media) {
    if (!media) throw new Error("Upload a file first.");
    return api("/api/media/info", jsonOptions({ file_id: media.file_id }));
  }

  function renderMediaInfo(info) {
    const container = $("#mediaInfo");
    container.className = "info-grid";
    container.replaceChildren();
    [
      ["Duration", formatDuration(info.duration)],
      ["Size", formatBytes(info.size)],
      ["Container", info.format || "Unknown"],
      ["Video stream", info.video ? "Available" : "None"],
      ["Audio stream", info.audio ? "Available" : "None"],
      ["Streams", (info.streams || []).map((item) => item.codec).filter(Boolean).join(", ") || "—"],
    ].forEach(([label, value]) => {
      const item = document.createElement("div");
      item.className = "info-item";
      const small = document.createElement("small");
      small.textContent = label;
      const b = document.createElement("b");
      b.textContent = value;
      item.append(small, b);
      container.append(item);
    });
  }

  async function runAudioOperation(endpoint, payload, statusId) {
    const media = state.audioMedia;
    if (!media) return showToast("Upload an audio or video source first.", "error");
    const status = $(`#${statusId}`);
    const response = await startJob(endpoint, { file_id: media.file_id, ...payload }, statusId, (result) => {
      addOutputLink(status, result);
      refreshHistory();
    });
    return response;
  }

  async function refreshHistory() {
    try {
      const result = await api("/api/history");
      renderHistory(result.files || [], result.jobs || []);
    } catch (error) {
      $("#historyList").textContent = error.message;
    }
  }

  function renderHistory(files, jobItems) {
    const list = $("#historyList");
    list.replaceChildren();
    if (!files.length) {
      list.className = "history-list empty-note";
      list.textContent = "No generated files yet.";
    } else {
      list.className = "history-list";
      files.forEach((file) => list.append(createFileRow(file, true)));
    }
    const jobs = $("#jobHistory");
    jobs.replaceChildren();
    if (!jobItems.length) {
      jobs.className = "job-history empty-note";
      jobs.textContent = "No jobs yet.";
      return;
    }
    jobs.className = "job-history";
    jobItems.forEach((job) => {
      const row = document.createElement("div");
      row.className = "job-item";
      const kind = document.createElement("span");
      kind.className = "job-kind";
      kind.textContent = job.kind;
      const message = document.createElement("span");
      message.className = "job-message";
      message.textContent = `${job.message}${job.error ? ` · ${job.error}` : ""}`;
      const badge = document.createElement("span");
      badge.className = `job-state ${job.state}`;
      badge.textContent = job.state;
      row.append(kind, message, badge);
      if (job.state === "queued") {
        const cancel = document.createElement("button");
        cancel.className = "button button-quiet";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", async () => {
          try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); refreshHistory(); }
          catch (error) { showToast(error.message, "error"); }
        });
        row.append(cancel);
      }
      jobs.append(row);
    });
  }

  function createFileRow(file, allowDelete) {
    const row = document.createElement("div");
    row.className = "history-item";
    const icon = document.createElement("span");
    icon.className = "file-icon";
    icon.textContent = file.extension || "FILE";
    const meta = document.createElement("span");
    meta.className = "file-meta";
    const name = document.createElement("b");
    name.textContent = file.filename;
    const date = document.createElement("small");
    date.textContent = new Date(file.modified * 1000).toLocaleString();
    meta.append(name, date);
    const size = document.createElement("span");
    size.className = "history-size";
    size.textContent = formatBytes(file.size);
    const actions = document.createElement("span");
    actions.className = "file-actions";
    const download = document.createElement("a");
    download.href = `/api/files/${encodeURIComponent(file.file_id)}`;
    download.download = file.filename;
    download.textContent = "↓";
    download.setAttribute("aria-label", `Download ${file.filename}`);
    actions.append(download);
    if (allowDelete) {
      const remove = document.createElement("button");
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Delete ${file.filename}`);
      remove.addEventListener("click", () => confirmAction("Delete generated file?", `Delete ${file.filename}? This cannot be undone.`, async () => {
        try { await api(`/api/files/${encodeURIComponent(file.file_id)}`, { method: "DELETE" }); showToast("File deleted.", "success"); refreshHistory(); }
        catch (error) { showToast(error.message, "error"); }
      }));
      actions.append(remove);
    }
    row.append(icon, meta, size, actions);
    return row;
  }

  function confirmAction(title, message, action) {
    $("#confirmTitle").textContent = title;
    $("#confirmMessage").textContent = message;
    $("#confirmBackdrop").classList.remove("hidden");
    const close = () => {
      $("#confirmBackdrop").classList.add("hidden");
      $("#confirmOkay").removeEventListener("click", yes);
      $("#confirmCancel").removeEventListener("click", no);
    };
    const yes = async () => { close(); await action(); };
    const no = close;
    $("#confirmOkay").addEventListener("click", yes);
    $("#confirmCancel").addEventListener("click", no);
  }

  async function refreshDiagnostics() {
    try {
      const [health, settings] = await Promise.all([api("/api/health"), api("/api/settings")]);
      renderServices(health, $("#diagnosticsList"));
      const list = $("#settingsList");
      list.replaceChildren();
      const values = [
        ["Python version", health.python],
        ["Bind address", `${settings.host}:${settings.port}`],
        ["Upload size limit", `${settings.max_upload_mb} MB`],
        ["Queued job limit", settings.max_queued_jobs],
        ["Default Whisper model", settings.whisper_default_model],
        ["Whisper compute type", settings.whisper_compute_type],
        ["Background workers", health.queue_worker_count],
        ["Available memory", settings.memory?.available_mb == null ? "Unavailable" : `${settings.memory.available_mb} MB`],
        ["Physical memory", settings.memory?.total_mb == null ? "Unavailable" : `${settings.memory.total_mb} MB`],
      ];
      values.forEach(([label, value]) => {
        const row = document.createElement("div");
        row.className = "setting-row";
        const name = document.createElement("span");
        name.textContent = label;
        const val = document.createElement("b");
        val.textContent = String(value);
        row.append(name, val);
        list.append(row);
      });
      $$("[data-max-upload]").forEach((item) => { item.textContent = settings.max_upload_mb; });
      const chip = $("#serverStatus");
      chip.classList.toggle("ok", true);
      chip.classList.remove("error");
      chip.lastChild.textContent = "Local server ready";
    } catch (error) {
      renderServices({}, $("#diagnosticsList"));
      const chip = $("#serverStatus");
      chip.classList.add("error");
      chip.classList.remove("ok");
      chip.lastChild.textContent = "Server unavailable";
      showToast(error.message, "error");
    }
  }

  function renderServices(health, target) {
    if (!target) return;
    target.replaceChildren();
    const items = [
      ["Flask dashboard", true],
      ["FFmpeg", health.ffmpeg],
      ["ffprobe", health.ffprobe],
      ["Faster-Whisper", health.faster_whisper],
      ["Edge-TTS", health.edge_tts],
    ];
    items.forEach(([name, ready]) => {
      const row = document.createElement("div");
      row.className = "service-row";
      const label = document.createElement("span");
      label.textContent = name;
      const status = document.createElement("span");
      status.className = `service-state${ready ? "" : " off"}`;
      status.textContent = ready ? "Available" : "Not installed";
      row.append(label, status);
      target.append(row);
    });
  }

  async function refreshDashboard() {
    await refreshDiagnostics();
    try {
      const { files } = await api("/api/history");
      const target = $("#recentFiles");
      target.replaceChildren();
      const recent = files.slice(0, 4);
      if (!recent.length) {
        target.className = "recent-list empty-note";
        target.textContent = "Your exports will appear here.";
      } else {
        target.className = "recent-list";
        recent.forEach((file) => target.append(createFileRow(file, false)));
      }
    } catch (error) {
      $("#recentFiles").textContent = error.message;
    }
  }

  async function startTranscription(media, voice = false) {
    if (!media) return showToast(voice ? "Record or upload an audio clip first." : "Upload an audio or video file first.", "error");
    const statusId = voice ? "voiceJob" : "captionJob";
    let language = $(`#${voice ? "voiceLanguage" : "captionLanguage"}`).value;
    if (!voice && language === "custom") {
      language = $("#customLanguage").value.trim().toLowerCase();
      if (!/^[a-z]{2,3}$/.test(language)) return showToast("Enter a two- or three-letter Whisper language code.", "error");
    }
    const model = $(`#${voice ? "voiceModel" : "whisperModel"}`).value;
    const endpoint = voice ? "/api/voice/transcribe" : "/api/transcribe";
    return startJob(endpoint, {
      file_id: media.file_id,
      language,
      model,
      correct: voice ? true : $("#enableCorrection").checked,
      word_timestamps: voice ? true : $("#wordTimestamps").checked,
    }, statusId, (transcript) => {
      setTranscript(transcript, voice);
      if (voice) $("#voiceTranscript").scrollIntoView({ behavior: "smooth", block: "center" });
      refreshHistory();
    });
  }

  async function applyCorrection() {
    if (!state.segments.length) return showToast("There are no caption lines to correct.", "error");
    try {
      pushUndo();
      const result = await api("/api/subtitles/correct", jsonOptions({
        segments: state.segments,
        enabled: $("#runCorrection").checked,
      }));
      state.segments = result.segments;
      renderTranscript();
      showToast(result.enabled ? "Correction applied. Original dialogue is preserved." : "Correction disabled; original text restored.", "success");
    } catch (error) {
      state.undoStack.pop();
      $("#undoCaptions").disabled = state.undoStack.length === 0;
      showToast(error.message, "error");
    }
  }

  async function exportVoice(format) {
    if (!state.voiceSegments.length) return;
    try {
      const result = await api("/api/subtitles/export", jsonOptions({
        segments: state.voiceSegments,
        format,
        variant: "corrected",
        word_timestamps: true,
        max_words: 7,
        max_lines: 2,
      }));
      downloadResult(result);
      refreshHistory();
    } catch (error) { showToast(error.message, "error"); }
  }

  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      return showToast("This browser does not support microphone recording. Upload an audio file instead.", "error");
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = ["audio/ogg;codecs=opus", "audio/webm;codecs=opus"].find((item) => MediaRecorder.isTypeSupported(item));
      state.recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      state.recordedChunks = [];
      state.recorder.addEventListener("dataavailable", (event) => { if (event.data.size) state.recordedChunks.push(event.data); });
      state.recorder.addEventListener("stop", async () => {
        stream.getTracks().forEach((track) => track.stop());
        const type = state.recorder.mimeType || "audio/ogg";
        const extension = type.includes("webm") ? "webm" : "ogg";
        const recording = new File(state.recordedChunks, `voice-recording.${extension}`, { type });
        $("#recordStatus").textContent = "Uploading recording…";
        try {
          state.voiceMedia = await uploadMedia(recording, "voiceMedia", "voiceProgress", "voiceFileName");
          $("#recordStatus").textContent = "Recording uploaded";
          startTranscription(state.voiceMedia, true);
        } catch (error) {
          $("#recordStatus").textContent = "Recording could not be uploaded";
          showToast(error.message, "error");
        }
      });
      state.recorder.start();
      state.recordStartedAt = Date.now();
      $("#recordStatus").textContent = "Recording…";
      $(".record-display").classList.add("recording");
      $("#startRecording").disabled = true;
      $("#stopRecording").disabled = false;
      state.recordTimer = window.setInterval(() => {
        $("#recordTimer").textContent = formatDuration((Date.now() - state.recordStartedAt) / 1000);
      }, 500);
    } catch (error) { showToast(`Microphone access failed: ${error.message}`, "error"); }
  }

  function stopRecording() {
    if (state.recorder?.state === "recording") state.recorder.stop();
    window.clearInterval(state.recordTimer);
    $("#stopRecording").disabled = true;
    $("#startRecording").disabled = false;
    $(".record-display").classList.remove("recording");
  }

  function bindEvents() {
    $$("[data-nav]").forEach((element) => element.addEventListener("click", (event) => {
      event.preventDefault();
      setView(element.dataset.nav);
    }));
    $("#menuToggle").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
    $("#refreshButton").addEventListener("click", refreshDiagnostics);
    $("#refreshHistory").addEventListener("click", refreshHistory);
    uploadFromInput("dashboardUpload", "currentMedia", "dashboardProgress", "dashboardFileName");
    uploadFromInput("captionUpload", "currentMedia", "captionProgress", "captionFileName");
    uploadFromInput("studioUpload", "studioMedia", "studioProgress", "studioFileName", setVideoPreview);
    uploadFromInput("voiceUpload", "voiceMedia", "voiceProgress", "voiceFileName");
    uploadFromInput("audioUpload", "audioMedia", "audioProgress", "audioFileName");
    uploadFromInput("videoUpload", "videoMedia", "videoProgress", "videoFileName");

    $("#transcribeButton").addEventListener("click", () => startTranscription(state.currentMedia));
    $("#captionLanguage").addEventListener("change", () => {
      $("#customLanguageWrap").classList.toggle("hidden", $("#captionLanguage").value !== "custom");
    });
    $("#whisperModel").addEventListener("change", () => {
      if (["small", "medium"].includes($("#whisperModel").value)) {
        showToast("Larger models can need substantial free RAM. The server checks available memory before loading; it will not silently switch models.", "info", 6500);
      }
    });
    $("#correctButton").addEventListener("click", applyCorrection);
    $("#undoCaptions").addEventListener("click", () => {
      const previous = state.undoStack.pop();
      if (!previous) return;
      state.segments = JSON.parse(previous);
      renderTranscript();
      $("#undoCaptions").disabled = state.undoStack.length === 0;
      updateCaptionPreview();
    });
    $("#addCaption").addEventListener("click", () => {
      pushUndo();
      const end = state.segments.at(-1)?.end || 0;
      state.segments.push({ start: end, end: end + 2, text: "", original_text: "", words: [] });
      renderTranscript();
      $("textarea", $("#transcriptEditor")?.lastElementChild)?.focus();
    });
    $("#exportSubtitle").addEventListener("click", () => exportSegments($("#subtitleFormat").value, $("#subtitleVariant").value));
    $("#downloadOriginal").addEventListener("click", () => exportSegments($("#subtitleFormat").value, "original"));
    $$("#captionFont,#captionSize,#captionColor,#highlightColor,#captionBg,#captionOutline,#captionShadow,#captionPosition,#captionAlign,#captionLines,#captionTiming,#captionSpacing,#captionOpacity,#captionBold,#captionItalic,#activeHighlight,#maxWords")
      .forEach((element) => element.addEventListener("input", updateCaptionPreview));
    $("#captionPreset").addEventListener("change", () => { captionStyle(); updateCaptionPreview(false); });
    $("#burnCaptions").addEventListener("click", () => {
      if (!state.currentMedia) return showToast("Upload a video before rendering.", "error");
      if (!state.segments.length) return showToast("Generate or add captions before rendering.", "error");
      startJob("/api/video/render", {
        file_id: state.currentMedia.file_id,
        layers: [],
        segments: visibleSegments(),
        burn_subtitles: true,
        caption_style: getCaptionStyleForRender(),
      }, "captionJob", (result) => { addOutputLink($("#captionJob"), result); refreshHistory(); });
    });
    $("#addLayer").addEventListener("click", () => addLayer());
    $("#duplicateLayer").addEventListener("click", () => {
      if (!selectedLayer()) return;
      addLayer(selectedLayer());
    });
    $("#deleteLayer").addEventListener("click", () => {
      if (!selectedLayer()) return;
      state.layers.splice(state.selectedLayer, 1);
      state.selectedLayer = Math.min(state.selectedLayer, state.layers.length - 1);
      renderLayers();
      if (state.selectedLayer >= 0) selectLayer(state.selectedLayer);
      else {
        $("#selectedLayerLabel").textContent = "Select a text layer to edit.";
        $$("[id^='layer']").filter((element) => element.matches("input,textarea,select")).forEach((element) => { element.disabled = true; });
      }
    });
    $$("[id^='layer']").filter((element) => element.matches("input,textarea,select")).forEach((element) => {
      element.addEventListener("input", readLayerControls);
      element.addEventListener("change", readLayerControls);
    });
    $("#renderVideo").addEventListener("click", renderVideo);
    $("#previewAnimation").addEventListener("click", () => {
      updateOverlayPreview();
      $$(".overlay-text", $("#overlayPreview")).forEach((element) => {
        const animation = animationClass(state.layers[Number(element.dataset.layer)]?.animation);
        element.classList.remove("blink", "fade", "zoom", "glow", "karaoke", "slide-left", "slide-right", "slide-up", "slide-down", "bounce", "shake", "typewriter");
        if (animation) {
          void element.offsetWidth;
          element.classList.add(animation);
        }
      });
    });
    $("#studioVideo").addEventListener("loadedmetadata", () => {
      state.duration = $("#studioVideo").duration || 0;
      $("#videoDurationLabel").textContent = `Duration ${formatDuration(state.duration)}`;
      renderLayers();
    });
    $("#studioVideo").addEventListener("timeupdate", syncPreviewTime);
    $("#voiceTranscribe").addEventListener("click", () => startTranscription(state.voiceMedia, true));
    $("#startRecording").addEventListener("click", startRecording);
    $("#stopRecording").addEventListener("click", stopRecording);
    $("#sendVoiceToCaptions").addEventListener("click", () => {
      if (!state.voiceSegments.length) return showToast("Transcribe a recording first.", "error");
      pushUndo();
      state.segments = state.voiceSegments.map((segment) => ({ ...segment }));
      renderTranscript();
      setView("captions");
    });
    $("#voiceExportTxt").addEventListener("click", () => exportVoice("txt"));
    $("#voiceExportSrt").addEventListener("click", () => exportVoice("srt"));
    $("#voiceExportVtt").addEventListener("click", () => exportVoice("vtt"));
    $("#ttsGenerate").addEventListener("click", () => startJob("/api/tts", {
      text: $("#ttsText").value,
      voice: $("#ttsVoice").value,
      rate: $("#ttsRate").value,
      pitch: $("#ttsPitch").value,
    }, "ttsJob", (result) => {
      addOutputLink($("#ttsJob"), result);
      if (result?.file_id) {
        $("#ttsPreview").src = `/api/files/${encodeURIComponent(result.file_id)}/preview`;
        $("#ttsPreview").classList.remove("hidden");
      }
      refreshHistory();
    }));
    $("#mediaInfoButton").addEventListener("click", async () => {
      try { state.mediaInfo = await getMediaInfo(state.audioMedia); renderMediaInfo(state.mediaInfo); }
      catch (error) { showToast(error.message, "error"); }
    });
    $("#extractAudioButton").addEventListener("click", () => runAudioOperation("/api/audio/extract", {
      format: $("#extractFormat").value, bitrate: $("#extractBitrate").value,
    }, "extractJob"));
    $("#convertAudioButton").addEventListener("click", () => runAudioOperation("/api/audio/convert", {
      format: $("#convertFormat").value, bitrate: $("#convertBitrate").value,
    }, "convertJob"));
    $("#trimAudioButton").addEventListener("click", () => runAudioOperation("/api/audio/trim", {
      start: $("#trimStart").value, end: $("#trimEnd").value, format: $("#trimFormat").value,
    }, "trimJob"));
    $("#videoToMp3").addEventListener("click", () => runVideoExtract("mp3"));
    $("#videoToWav").addEventListener("click", () => runVideoExtract("wav"));
  }

  function getCaptionStyleForRender() {
    return {
      font: $("#captionFont").value.trim(),
      font_size: Number($("#captionSize").value),
      font_color: $("#captionColor").value,
      highlight_color: $("#highlightColor").value,
      background_color: $("#captionBg").value,
      outline: Number($("#captionOutline").value),
      shadow: Number($("#captionShadow").value),
      bold: $("#captionBold").checked,
      italic: $("#captionItalic").checked,
      alignment: $("#captionAlign").value,
      position: $("#captionPosition").value,
      line_spacing: Number($("#captionSpacing").value),
      background_opacity: Number($("#captionOpacity").value),
      active_word_highlight: $("#activeHighlight").checked,
      max_words: Number($("#maxWords").value),
      max_lines: Number($("#captionLines").value),
    };
  }

  async function runVideoExtract(format) {
    if (!state.videoMedia) return showToast("Upload a video first.", "error");
    await startJob("/api/audio/extract", {
      file_id: state.videoMedia.file_id, format, bitrate: "192k",
    }, "videoToolJob", (result) => { addOutputLink($("#videoToolJob"), result); refreshHistory(); });
  }

  function updateCaptionPreview(markCustom = true) {
    if (markCustom && $("#captionPreset").value !== "custom") $("#captionPreset").value = "custom";
    captionStyle();
  }

  function initialize() {
    $("#todayLabel").textContent = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(new Date());
    bindEvents();
    updateCaptionPreview();
    refreshDashboard();
    refreshHistory();
    setInterval(() => {
      if ($("#view-history").classList.contains("active")) refreshHistory();
    }, 5000);
  }

  initialize();
})();
