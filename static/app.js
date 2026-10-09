// DANY AI STUDIO HUB - Frontend Logic (v2.3)
"use strict";

// ---------------------------------------------------------------------------
// Cliente de API con token de sesión (el servidor solo acepta llamadas de esta página)
// ---------------------------------------------------------------------------
let SESSION_TOKEN = null;

async function getToken(force = false) {
  if (SESSION_TOKEN && !force) return SESSION_TOKEN;
  const res = await fetch("/api/session", { cache: "no-store" });
  if (!res.ok) throw new Error("No se pudo iniciar sesión con el servidor local");
  SESSION_TOKEN = (await res.json()).token;
  return SESSION_TOKEN;
}

async function api(path, { method = "GET", body } = {}, retry = true) {
  const token = await getToken();
  const opts = { method, headers: { "X-Studio-Token": token }, cache: "no-store" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401 && retry) {
    await getToken(true); // el servidor se reinició: pedir token nuevo y reintentar una vez
    return api(path, { method, body }, false);
  }
  let data = {};
  try { data = await res.json(); } catch (_) { /* respuesta sin JSON */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function uploadAudioForMastering(file, retry = true) {
  const token = await getToken();
  const res = await fetch("/api/master_audio_upload", {
    method: "POST",
    headers: {
      "X-Studio-Token": token,
      "X-File-Name": encodeURIComponent(file.name),
      "Content-Type": "application/octet-stream",
    },
    body: file,
    cache: "no-store",
  });
  if (res.status === 401 && retry) {
    await getToken(true);
    return uploadAudioForMastering(file, false);
  }
  let data = {};
  try { data = await res.json(); } catch (_) { /* respuesta sin JSON */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  fetchSystemInfo();
  initMusicPresets();
  initAcestepRunner();
  initVideoPrompts();
  initColabBridge();
  initMastering();
  initLlmPanel();
  initAudioLibrary();
  initVisualStudio();
  initSceneShort();
  initYue2Controls();
});

// ---------------------------------------------------------------------------
// TABS
// ---------------------------------------------------------------------------
function initTabs() {
  const navBtns = document.querySelectorAll(".nav-btn");
  const tabPanels = document.querySelectorAll(".tab-panel");
  navBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      navBtns.forEach((b) => b.classList.remove("active"));
      tabPanels.forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = $(btn.getAttribute("data-tab"));
      if (panel) panel.classList.add("active");
    });
  });
}

// ---------------------------------------------------------------------------
// TELEMETRÍA
// ---------------------------------------------------------------------------
async function fetchSystemInfo() {
  try {
    const data = await api("/api/system_info");
    const gpu = data.gpu;
    $("gpuName").textContent = gpu.name || "Modo Nube";
    $("gpuVram").textContent = `VRAM: ${gpu.free_gb || 0} / ${gpu.total_gb || 0} GB (${gpu.tier})`;

    const bridgeStatus = $("bridgeStatus");
    const colabStatusText = $("colabStatusText");
    if ($("bridgePath")) $("bridgePath").textContent = data.paths.colab_bridge || "Bridge no configurado";

    if (data.paths.bridge_available) {
      bridgeStatus.textContent = "Sincronizado ✅";
      bridgeStatus.style.color = "#00f7ff";
      if (colabStatusText) colabStatusText.textContent = "Conectado y listo en " + data.paths.colab_bridge;
    } else {
      bridgeStatus.textContent = "No conectado ⚠️";
      bridgeStatus.style.color = "#ffaa00";
      if (colabStatusText) {
        colabStatusText.textContent = data.paths.colab_bridge
          ? "No se encuentra " + data.paths.colab_bridge + ". Se requiere Google Drive de escritorio."
          : "Configura paths.colab_bridge_dir en config.json.";
      }
    }
    addDetectedLoras(data.loras || []);
  } catch (err) {
    console.warn("Error obteniendo telemetría:", err);
    $("gpuName").textContent = "Sin conexión con el servidor";
  }
}

function addDetectedLoras(names) {
  const select = $("loraPreset");
  if (!select) return;
  const norm = (v) => v.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  const existing = new Set(Array.from(select.options).map((o) => norm(o.value)));
  names.forEach((name) => {
    if (existing.has(norm(name))) return; // "deep horizon" y la carpeta deep_horizon son la misma LoRA
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = `${name} (detectada en lora_models)`;
    select.insertBefore(opt, select.querySelector('option[value="none"]'));
  });
}

// ---------------------------------------------------------------------------
// Utilidades compartidas (tamaños, fechas, streaming de media autenticado)
// ---------------------------------------------------------------------------
function formatBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${units[i]}`;
}

function formatDate(ts) {
  if (!ts) return "";
  try { return new Date(ts * 1000).toLocaleString(); } catch (_) { return ""; }
}

// Carga un audio/video protegido por token en un <audio>/<video> vía blob
// (el elemento nativo no puede mandar cabeceras propias en su atributo src).
async function loadMediaBlobInto(el, streamPath, absolutePath, metaEl, label, autoplay = true) {
  if (!el) return false;
  // Si el usuario cambia rápido de archivo, la respuesta vieja se descarta (gana la última petición).
  const seq = (el._loadSeq = (el._loadSeq || 0) + 1);
  el.pause();
  if (el.src && el.src.startsWith("blob:")) URL.revokeObjectURL(el.src);
  el.removeAttribute("src");
  el.load();
  if (metaEl) metaEl.textContent = `[...] Cargando "${label || absolutePath}"...`;
  try {
    const url = `${streamPath}?path=${encodeURIComponent(absolutePath)}`;
    let res = await fetch(url, { headers: { "X-Studio-Token": await getToken() }, cache: "no-store" });
    if (res.status === 401) { // el servidor se reinició: pedir token nuevo y reintentar una vez
      res = await fetch(url, { headers: { "X-Studio-Token": await getToken(true) }, cache: "no-store" });
    }
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    if (seq !== el._loadSeq) return false;
    el.preload = "auto";
    el.src = URL.createObjectURL(blob);
    const shown = label || absolutePath;
    if (metaEl) metaEl.textContent = autoplay ? shown : `${shown}  ·  listo, pulsa ▶`;
    if (autoplay) {
      try {
        await el.play();
      } catch (_) {
        if (metaEl && seq === el._loadSeq) metaEl.textContent = `${shown}  ·  pulsa ▶ para reproducir`;
      }
    }
    return true;
  } catch (err) {
    if (metaEl && seq === el._loadSeq) metaEl.textContent = `[ERROR] ${err.message}`;
    return false;
  }
}

// Carga una imagen en un <img> vía Blob autenticado con fallback directo
async function loadImageBlobInto(el, streamPath, absolutePath, metaEl, label) {
  if (!el || !absolutePath) return false;
  if (el.src && el.src.startsWith("blob:")) URL.revokeObjectURL(el.src);
  if (metaEl) metaEl.textContent = `[...] Cargando imagen: ${label || absolutePath}...`;
  try {
    const token = await getToken();
    const url = `${streamPath}?path=${encodeURIComponent(absolutePath)}&token=${encodeURIComponent(token)}`;
    let res = await fetch(url, { headers: { "X-Studio-Token": token }, cache: "no-store" });
    if (res.status === 401) {
      const newToken = await getToken(true);
      res = await fetch(`${streamPath}?path=${encodeURIComponent(absolutePath)}&token=${encodeURIComponent(newToken)}`, {
        headers: { "X-Studio-Token": newToken }, cache: "no-store"
      });
    }
    if (!res.ok) {
      el.src = `${streamPath}?path=${encodeURIComponent(absolutePath)}`;
      if (metaEl) metaEl.textContent = label || `Guardada en: ${absolutePath}`;
      return true;
    }
    const blob = await res.blob();
    el.src = URL.createObjectURL(blob);
    if (metaEl) metaEl.textContent = label || `Guardada en: ${absolutePath}`;
    return true;
  } catch (err) {
    el.src = `${streamPath}?path=${encodeURIComponent(absolutePath)}`;
    if (metaEl) metaEl.textContent = label || `Guardada en: ${absolutePath}`;
    return true;
  }
}

// Texto legible del error que reporta el propio <audio>/<video> (MediaError).
function mediaErrorText(el) {
  const e = el && el.error;
  if (!e) return "error desconocido";
  const names = {
    1: "reproducción abortada",
    2: "error de red al leer el archivo",
    3: "el navegador no pudo decodificar el archivo",
    4: "formato no soportado por el navegador",
  };
  return names[e.code] || e.message || "error desconocido";
}

// Compara rutas de Windows ignorando mayúsculas y el tipo de barra.
function samePath(a, b) {
  const n = (p) => String(p || "").replace(/\//g, "\\").toLowerCase();
  return n(a) !== "" && n(a) === n(b);
}

// Rellena un <select> con pistas de audio (usado por la Biblioteca de Audio y por el
// selector de "canción de referencia" en la pestaña Vídeo, a partir de la misma llamada).
function populateAudioSelect(selectEl, tracks, includeNone) {
  if (!selectEl) return;
  const prev = selectEl.value;
  selectEl.textContent = "";
  if (includeNone) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Ninguna (sin condicionar por audio)";
    selectEl.appendChild(opt);
  }
  tracks.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t.path;
    opt.textContent = `${t.name}${t.ext} · ${formatBytes(t.size)}`;
    selectEl.appendChild(opt);
  });
  if (tracks.some((t) => t.path === prev)) selectEl.value = prev;
}

// ---------------------------------------------------------------------------
// MÚSICA: presets (vienen del servidor), compositor IA, generador TOML
// ---------------------------------------------------------------------------
let lastCliCommand = "";
let lastTomlPath = "";
let lastMeta = { bpm: null, key: "", genre: "" }; // BPM/tonalidad/genero del preset o de la IA; se borra si editas el caption a mano

// Contexto de la "canción actual" que usan tanto el prompt de vídeo IA como el planificador de escenas.
function songContext() {
  const useSong = $("videoUseSongContext") ? $("videoUseSongContext").checked : true;
  const promptArea = $("videoPrompt");
  if (!useSong) return { title: "", genre: "", caption: promptArea ? promptArea.value : "", lyrics: "", bpm: null, key: "" };
  return {
    title: $("trackTitle") ? $("trackTitle").value : "",
    caption: $("musicCaption") ? $("musicCaption").value : "",
    lyrics: $("musicLyrics") ? $("musicLyrics").value : "",
    genre: lastMeta.genre || "",
    bpm: lastMeta.bpm,
    key: lastMeta.key || "",
  };
}

function initMusicPresets() {
  const loraSelect = $("loraPreset");
  const captionInput = $("musicCaption");
  const lyricsInput = $("musicLyrics");
  const out = $("musicOutput");
  let presets = {};

  function applyPreset() {
    const p = presets[loraSelect.value];
    if (!p) return;
    captionInput.value = p.caption;
    lyricsInput.value = p.lyrics;
    $("loraScale").value = p.lora_scale;
    lastMeta = { bpm: p.bpm, key: p.key, genre: p.genre || "" };
  }

  api("/api/presets")
    .then((data) => { presets = data; applyPreset(); })
    .catch((err) => { out.textContent = `[ERROR] No se pudieron cargar los presets: ${err.message}`; });

  loraSelect.addEventListener("change", applyPreset);
  captionInput.addEventListener("input", () => { lastMeta = { bpm: null, key: "", genre: "" }; });

  // Generar TOML
  $("btnBuildMusicConfig").addEventListener("click", async () => {
    out.textContent = "[...] Generando configuración TOML para ACE-Step 1.5...";
    const payload = {
      title: $("trackTitle").value,
      model_tier: $("musicEngine").value,
      duration: parseFloat($("musicDuration").value),
      lora_name: loraSelect.value === "none" ? "" : loraSelect.value,
      lora_scale: parseFloat($("loraScale").value),
      caption: captionInput.value,
      lyrics: lyricsInput.value,
      instrumental: false,
      seed: 42,
    };
    if (lastMeta.bpm) payload.bpm = lastMeta.bpm;
    if (lastMeta.key) payload.key = lastMeta.key;

    try {
      const data = await api("/api/generate_music_config", { method: "POST", body: payload });
      lastCliCommand = data.cli_command || `python cli.py -c "${data.toml_path}"`;
      lastTomlPath = data.toml_path || "";
      if ($("btnRunAcestepNow")) $("btnRunAcestepNow").disabled = !lastTomlPath;
      let text = `[ÉXITO] Archivo de configuración creado:\n${data.toml_path}\n\nEjecuta en tu terminal:\n${lastCliCommand}\n\n(o pulsa «▶️ Ejecutar Ahora» para lanzarlo sin salir de la app)\n\nDuración: ${data.duration}s | Motor: ${data.model_tier}`;
      text += data.lora_path ? `\nLoRA cargada desde: ${data.lora_path}` : "\nLoRA: ninguna";
      (data.warnings || []).forEach((w) => { text += `\n[AVISO] ${w}`; });
      if ((data.lyrics_removed || []).length) {
        text += `\n[LETRA LIMPIA] Se quitaron ${data.lyrics_removed.length} línea(s) técnicas:`;
        data.lyrics_removed.forEach((l) => { text += `\n  - línea ${l.line}: ${l.text}`; });
      }
      out.textContent = text;
    } catch (err) {
      out.textContent = `[ERROR] No se pudo crear la configuración: ${err.message}`;
    }
  });

  // Copiloto IA
  const btnCopilot = $("btnRunAICopilot");
  btnCopilot.addEventListener("click", async () => {
    const idea = $("aiIdeaInput").value.trim();
    if (!idea) {
      alert("Por favor escribe una idea o estilo para que la IA la desarrolle.");
      return;
    }
    btnCopilot.disabled = true;
    out.textContent = `[IA CO-PILOT] Componiendo: "${idea}"...\n(si es la primera vez, el modelo local puede tardar en cargarse)`;
    try {
      const data = await api("/api/ai/compose", { method: "POST", body: { idea } });
      $("trackTitle").value = data.title;
      captionInput.value = data.caption;
      lyricsInput.value = data.lyrics;
      $("videoPrompt").value = data.video_prompt;
      loraSelect.value = data.lora_name || "none";
      $("loraScale").value = data.lora_scale;
      lastMeta = { bpm: data.bpm, key: data.key, genre: data.genre || "" };

      const origin = data.source === "llm"
        ? `Modelo conectado: ${data.llm_model}`
        : "Plantilla de respaldo" + (data.llm_error ? ` (el modelo falló: ${data.llm_error})` : " (no hay modelo conectado; conéctalo en ⚙️ Modelos)");
      let text = `[IA ÉXITO] ${origin}\n\nGénero: ${data.genre}\nBPM: ${data.bpm} | Tonalidad: ${data.key}\nLoRA sugerida: ${data.lora_name || "ninguna"} (escala ${data.lora_scale})`;
      if (data.source !== "llm" && data.matched === false) {
        text += "\n[AVISO] Ninguna palabra clave coincidió: se usó la plantilla narrativa por defecto.";
      }
      if ((data.lyrics_removed || []).length) text += `\n[LETRA LIMPIA] Se quitaron ${data.lyrics_removed.length} línea(s) técnicas.`;
      out.textContent = text;
    } catch (err) {
      out.textContent = `[IA ERROR] ${err.message}`;
    } finally {
      btnCopilot.disabled = false;
    }
  });

  // Copiar comando CLI (el real, generado por el servidor)
  $("btnCopyMusicCLI").addEventListener("click", () => {
    if (!lastCliCommand) {
      alert("Primero pulsa «Generar Configuración TOML».");
      return;
    }
    navigator.clipboard.writeText(lastCliCommand);
    alert("Comando CLI copiado al portapapeles:\n" + lastCliCommand);
  });
}

// ---------------------------------------------------------------------------
// v2.3 - Mejora #3: Ejecutar ACE-Step localmente sin salir de la app
// ---------------------------------------------------------------------------
function initAcestepRunner() {
  const btn = $("btnRunAcestepNow");
  const out = $("musicOutput");
  if (!btn) return;

  let pollTimer = null;
  function stopPolling() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } }

  async function poll(jobId) {
    try {
      const data = await api(`/api/acestep/status?job_id=${encodeURIComponent(jobId)}`);
      if (data.status === "DONE") {
        stopPolling();
        btn.disabled = false;
        out.textContent = data.output_file
          ? `[LISTO] ACE-Step terminó. Audio nuevo:\n${data.output_file}\n(ya deberías verlo en la Biblioteca de Audio)\n\n--- LOG (final) ---\n${(data.log || "").slice(-1500)}`
          : `[LISTO] ACE-Step terminó, pero no se detectó el archivo de audio nuevo.\n\n--- LOG ---\n${(data.log || "").slice(-1500)}`;
        if (typeof reloadAudioLibrary === "function") reloadAudioLibrary(data.output_file);
      } else if (data.status === "ERROR") {
        stopPolling();
        btn.disabled = false;
        out.textContent = `[ERROR] ACE-Step terminó con código ${data.returncode}.\n\n--- LOG (final) ---\n${(data.log || "").slice(-2000)}`;
      } else {
        out.textContent = `[EN PROCESO] ACE-Step está generando (puede tardar varios minutos)...\n\n--- LOG (en vivo) ---\n${(data.log || "").slice(-1500)}`;
        pollTimer = setTimeout(() => poll(jobId), 4000);
      }
    } catch (err) {
      out.textContent = `[ERROR] No se pudo consultar el estado de ACE-Step: ${err.message}`;
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", async () => {
    if (!lastTomlPath) {
      alert("Primero pulsa «Generar Configuración TOML».");
      return;
    }
    btn.disabled = true;
    stopPolling();
    out.textContent = "[...] Lanzando ACE-Step en segundo plano...";
    try {
      const payload = {
        toml_path: lastTomlPath,
        title: $("trackTitle") ? $("trackTitle").value : "",
        genre: lastMeta.genre || "",
        bpm: lastMeta.bpm,
        key: lastMeta.key || "",
        caption: $("musicCaption") ? $("musicCaption").value : "",
      };
      const data = await api("/api/acestep/run", { method: "POST", body: payload });
      out.textContent = `[LANZADO] job_id: ${data.job_id}\nGenerando en segundo plano (puede tardar varios minutos, no cierres el servidor)...`;
      poll(data.job_id);
    } catch (err) {
      out.textContent = `[ERROR] ${err.message}`;
      btn.disabled = false;
    }
  });
}

// ---------------------------------------------------------------------------
// v2.2/v2.3: BIBLIOTECA DE AUDIO (reproducir, masterizar y reusar contexto)
// ---------------------------------------------------------------------------
let reloadAudioLibrary = null;
let reloadVideoLibrary = null;

function initAudioLibrary() {
  const select = $("audioTrackSelect");
  const player = $("audioPlayer");
  const meta = $("audioPlayerMeta");
  const libraryStatus = $("audioLibraryStatus");
  const btnRefresh = $("btnRefreshAudioLibrary");
  const btnMaster = $("btnMasterSelectedTrack");
  const btnMasterLocalFile = $("btnMasterLocalFile");
  const masterFileInput = $("audioMasterFileInput");
  const masterStatus = $("audioMasterStatus");
  const btnUseForVideo = $("btnUseTrackForVideo");
  if (!select || !player) return;

  meta.style.whiteSpace = "pre-wrap";
  if (masterStatus) masterStatus.style.whiteSpace = "pre-wrap";

  let tracksByPath = {};
  let loadedPath = null; // pista que ya está cargada en el reproductor

  // Si el navegador no puede decodificar el archivo, lo decimos en vez de quedarnos mudos.
  player.addEventListener("error", () => {
    if (!player.getAttribute("src")) return;
    meta.textContent = `[ERROR] ${mediaErrorText(player)}`;
  });

  function fillEmpty(text) {
    select.textContent = "";
    const opt = document.createElement("option");
    opt.textContent = text;
    select.appendChild(opt);
  }

  function selectedLabel() {
    const o = select.options[select.selectedIndex];
    return o ? o.textContent : "";
  }

  function updateUseForVideoVisibility() {
    if (!btnUseForVideo) return;
    const track = tracksByPath[select.value];
    btnUseForVideo.style.display = track && track.meta ? "" : "none";
  }

  // Carga la pista elegida en el reproductor. Sin esto, el ▶ no hacía nada hasta cambiar
  // de pista: el evento "change" no salta para la que ya viene seleccionada por defecto.
  function loadSelected(autoplay) {
    const path = select.value;
    if (!tracksByPath[path]) return;
    loadedPath = path;
    loadMediaBlobInto(player, "/api/audio/stream", path, meta, selectedLabel(), autoplay);
  }

  // opts.selectPath: pista a seleccionar tras refrescar (la recién generada o masterizada)
  async function loadList(opts = {}) {
    const wanted = opts.selectPath || select.value;
    if (libraryStatus) libraryStatus.textContent = "Actualizando lista de pistas...";
    try {
      const data = await api("/api/audio/list");
      tracksByPath = {};
      select.textContent = "";
      if (!data.tracks || !data.tracks.length) {
        fillEmpty("No se encontraron pistas de audio");
        if (libraryStatus) libraryStatus.textContent = "No se encontraron pistas de audio.";
        meta.textContent = "Genera o masteriza una pista, o añade su carpeta en config.json -> paths.extra_audio_roots.";
        if (btnUseForVideo) btnUseForVideo.style.display = "none";
        if ($("refAudioSelect")) populateAudioSelect($("refAudioSelect"), [], true);
        loadedPath = null;
        return;
      }
      data.tracks.forEach((t) => {
        tracksByPath[t.path] = t;
        const opt = document.createElement("option");
        opt.value = t.path;
        const metaTag = t.meta && t.meta.genre ? ` · ${t.meta.genre}` : "";
        opt.textContent = `${t.name}${t.ext} · ${formatBytes(t.size)} · ${formatDate(t.mtime)}${metaTag}`;
        select.appendChild(opt);
      });
      if (libraryStatus) libraryStatus.textContent = `${data.tracks.length} pistas cargadas.`;
      const match = data.tracks.find((t) => samePath(t.path, wanted));
      if (match) select.value = match.path;
      if ($("refAudioSelect")) populateAudioSelect($("refAudioSelect"), data.tracks, true);
      updateUseForVideoVisibility();
      if (opts.selectPath || !samePath(loadedPath, select.value)) loadSelected(false);
    } catch (err) {
      if (libraryStatus) libraryStatus.textContent = `No se pudo actualizar la lista: ${err.message}`;
    }
  }

  select.addEventListener("change", () => {
    loadSelected(true);
    updateUseForVideoVisibility();
  });

  if (btnMaster) {
    btnMaster.addEventListener("click", async () => {
      const path = select.value;
      if (!tracksByPath[path]) { alert("Selecciona antes una pista."); return; }
      btnMaster.disabled = true;
      masterStatus.textContent = "[...] Masterizando (2 pasadas EBU R128 con FFmpeg)...";
      try {
        const d = await api("/api/master_audio", { method: "POST", body: { input_file: path } });
        masterStatus.textContent = `[ÉXITO] ${d.output_file}\n${d.target_lufs} LUFS · TP ${d.true_peak} dBTP · ${d.bitrate}`;
        await loadList({ selectPath: d.output_file });
      } catch (err) {
        masterStatus.textContent = `[ERROR] ${err.message}`;
      } finally {
        btnMaster.disabled = false;
      }
    });
  }

  if (btnMasterLocalFile && masterFileInput) {
    btnMasterLocalFile.addEventListener("click", () => masterFileInput.click());
    masterFileInput.addEventListener("change", async () => {
      const file = masterFileInput.files && masterFileInput.files[0];
      if (!file) return;
      if (file.size > 1024 ** 3) {
        masterStatus.textContent = "[ERROR] El archivo supera el límite de 1 GB.";
        masterFileInput.value = "";
        return;
      }

      btnMasterLocalFile.disabled = true;
      if (btnMaster) btnMaster.disabled = true;
      masterStatus.textContent = `[... ] Subiendo y masterizando ${file.name} (EBU R128)...`;
      try {
        const data = await uploadAudioForMastering(file);
        masterStatus.textContent = `[ÉXITO] ${data.output_file}\n${data.target_lufs} LUFS · TP ${data.true_peak} dBTP · ${data.bitrate}`;
        await loadList({ selectPath: data.output_file });
      } catch (err) {
        masterStatus.textContent = `[ERROR] ${err.message}`;
      } finally {
        btnMasterLocalFile.disabled = false;
        if (btnMaster) btnMaster.disabled = false;
        masterFileInput.value = "";
      }
    });
  }

  if (btnUseForVideo) {
    btnUseForVideo.addEventListener("click", () => {
      const track = tracksByPath[select.value];
      if (!track || !track.meta) return;
      if ($("trackTitle") && track.meta.title) $("trackTitle").value = track.meta.title;
      if ($("musicCaption") && track.meta.caption) $("musicCaption").value = track.meta.caption;
      lastMeta = { bpm: track.meta.bpm, key: track.meta.key, genre: track.meta.genre };
      const videoTabBtn = document.querySelector('.nav-btn[data-tab="tab-video"]');
      if (videoTabBtn) videoTabBtn.click();
    });
  }

  btnRefresh.addEventListener("click", () => loadList({}));
  reloadAudioLibrary = (selectPath) => loadList({ selectPath });
  loadList({});
}

// ---------------------------------------------------------------------------
// VÍDEO
// ---------------------------------------------------------------------------
function initVideoPrompts() {
  const styleSelect = $("videoStyle");
  const promptArea = $("videoPrompt");

  const videoPrompts = {
    outlaw_rock: "Cinematic 9:16 vertical shot, dramatic camera dolly in, vintage dark muscle car accelerating on rain-slicked asphalt at night, amber headlights cutting through mist, neon billboard reflections on wet metal, cinematic film grain, ultra-sharp textures, beat sync cuts every 2 seconds, no text, photorealistic.",
    cyber_cockpit: "First-person POV cockpit view of a futuristic sports car driving through a midnight cyber city, illuminated digital dashboard with glowing cyan dials, speed blurred rain on the windshield, aggressive turns, smoke and sparks, anamorphic lens flare, photorealistic DLSS 5 texture.",
    acoustic_soul: "Slow cinematic pan around a vintage wooden acoustic guitar leaning against an aged studio chair, warm golden volumetric dust particles floating in sunlight rays, rich oak wood grain, soft bokeh background, deep shadows, melancholic atmosphere.",
  };

  if (styleSelect && promptArea) {
    const updatePrompt = () => { promptArea.value = videoPrompts[styleSelect.value] || ""; };
    styleSelect.addEventListener("change", updatePrompt);
    updatePrompt();
  }

  const btnGen = $("btnGenerateVideoPrompt");
  if (btnGen && promptArea) {
    btnGen.addEventListener("click", async () => {
      const interval = $("beatSyncInterval")?.value || "2";
      const dlss = $("dlssMode")?.value || "off";
      let prompt = promptArea.value.trim().replace(/,?\s*beat sync cuts every [\d.,]+ seconds?/i, "");
      prompt = prompt.replace(/[.\s]+$/, "") + `, beat sync cuts every ${interval} seconds, DLSS 5 enhancer ${dlss}.`;
      promptArea.value = prompt;
      try {
        await navigator.clipboard.writeText(prompt);
        btnGen.textContent = "✅ Prompt actualizado y copiado";
      } catch (_) {
        btnGen.textContent = "✅ Prompt actualizado";
      }
      setTimeout(() => { btnGen.textContent = "🎬 Generar Prompt y Config ComfyUI"; }, 2500);
    });
  }

  const btnOpenComfy = $("btnOpenComfy");
  if (btnOpenComfy) {
    btnOpenComfy.addEventListener("click", () => {
      window.open("http://127.0.0.1:8188", "_blank", "noopener");
    });
  }
}

// ---------------------------------------------------------------------------
// v2.2/v2.3: PROMPT DE VÍDEO CON IA + GENERACIÓN DE UN CLIP CON MINIMAX H3
// ---------------------------------------------------------------------------
// ESTUDIO VISUAL: PORTADAS IA (KREA-2 TURBO) & VÍDEO CINEMÁTICO (LTX-2.5 / MINIMAX)
// ---------------------------------------------------------------------------
let lastGeneratedCoverPath = "";

function initVisualStudio() {
  // --- SECCIÓN A: PORTADAS IA CON KREA-2 TURBO ---
  const btnGenCover = $("btnGenerateCover");
  const btnCoverRefresh = $("btnCoverRefresh");
  const coverTerminal = $("coverTerminal");
  const coverOutput = $("coverOutput");
  const coverLibSelect = $("coverLibrarySelect");
  const btnRefreshCoverLib = $("btnRefreshCoverLibrary");
  const coverPreviewContainer = $("coverPreviewContainer");
  const coverPreviewImg = $("coverPreviewImg");
  const coverPreviewMeta = $("coverPreviewMeta");
  const coverDownloadLink = $("coverDownloadLink");
  const btnUseAsVideoInit = $("btnUseAsVideoInit");
  const btnClearInitImage = $("btnClearInitImage");
  const videoInitialImage = $("videoInitialImage");
  const videoInitImgPreviewContainer = $("videoInitImgPreviewContainer");
  const videoInitImgPreview = $("videoInitImgPreview");

  let coverPollTimer = null;

  function updateInitImagePreview(path) {
    if (!videoInitImgPreviewContainer || !videoInitImgPreview) return;
    const clean = (path || "").trim();
    if (!clean) {
      videoInitImgPreviewContainer.style.display = "none";
      videoInitImgPreview.removeAttribute("src");
      return;
    }
    videoInitImgPreviewContainer.style.display = "block";
    loadImageBlobInto(videoInitImgPreview, "/api/image/stream", clean, null, "Imagen inicial para vídeo");
  }

  function displaySelectedCover(path) {
    if (!path) return;
    lastGeneratedCoverPath = path;
    if (coverPreviewContainer && coverPreviewImg) {
      coverPreviewContainer.style.display = "block";
      const filename = path.split(/[\\/]/).pop();
      loadImageBlobInto(coverPreviewImg, "/api/image/stream", path, coverPreviewMeta, `Portada: ${filename}`);
      if (coverDownloadLink) {
        coverDownloadLink.href = `/api/image/stream?path=${encodeURIComponent(path)}`;
        coverDownloadLink.download = filename;
      }
    }
  }

  async function loadCoverLibrary(wantedPath = null) {
    if (!coverLibSelect) return;
    coverLibSelect.textContent = "";
    try {
      const data = await api("/api/cover/list");
      if (!data.covers || !data.covers.length) {
        const opt = document.createElement("option");
        opt.textContent = "No hay portadas generadas aún";
        coverLibSelect.appendChild(opt);
        return;
      }
      data.covers.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.path;
        opt.textContent = `${c.name}${c.ext} · ${formatBytes(c.size)} · ${formatDate(c.mtime)}`;
        coverLibSelect.appendChild(opt);
      });

      let chosen = wantedPath;
      if (!chosen) {
        const exists = data.covers.find((c) => c.path === lastGeneratedCoverPath);
        chosen = exists ? exists.path : data.covers[0].path;
      }
      coverLibSelect.value = chosen;
      displaySelectedCover(chosen);
    } catch (err) {
      const opt = document.createElement("option");
      opt.textContent = "Error al listar portadas: " + err.message;
      coverLibSelect.appendChild(opt);
    }
  }

  if (coverLibSelect) {
    coverLibSelect.addEventListener("change", () => {
      displaySelectedCover(coverLibSelect.value);
    });
  }
  if (btnRefreshCoverLib) {
    btnRefreshCoverLib.addEventListener("click", () => loadCoverLibrary());
  }

  async function pollCoverStatus(promptId) {
    try {
      const data = await api(`/api/cover/status?prompt_id=${encodeURIComponent(promptId)}`);
      if (data.status === "DONE") {
        if (coverPollTimer) clearTimeout(coverPollTimer);
        coverPollTimer = null;
        if (btnGenCover) btnGenCover.disabled = false;
        lastGeneratedCoverPath = data.image_path || "";
        if (coverOutput) {
          coverOutput.textContent = `[ÉXITO] Portada generada con Krea-2 Turbo:\n${lastGeneratedCoverPath}`;
        }
        await loadCoverLibrary(lastGeneratedCoverPath);
      } else if (data.status === "ERROR") {
        if (coverPollTimer) clearTimeout(coverPollTimer);
        coverPollTimer = null;
        if (btnGenCover) btnGenCover.disabled = false;
        if (coverOutput) {
          coverOutput.textContent = `[ERROR EN COMFYUI] ${data.message || "Fallo en la generación de portada."}`;
        }
      } else {
        if (coverOutput) {
          coverOutput.textContent = `[EN PROCESO] ${data.message || "Generando portada en GPU con Krea-2 Turbo..."}`;
        }
        coverPollTimer = setTimeout(() => pollCoverStatus(promptId), 2500);
      }
    } catch (err) {
      if (coverPollTimer) clearTimeout(coverPollTimer);
      coverPollTimer = null;
      if (btnGenCover) btnGenCover.disabled = false;
      if (coverOutput) coverOutput.textContent = `[ERROR] ${err.message}`;
    }
  }

  if (btnGenCover) {
    btnGenCover.addEventListener("click", async () => {
      const idea = ($("coverIdea") ? $("coverIdea").value : "").trim();
      if (!idea) {
        alert("Escribe una idea, título o descripción para la portada.");
        return;
      }
      btnGenCover.disabled = true;
      if (coverOutput) coverOutput.textContent = "[...] Enviando petición a ComfyUI (Krea-2 Turbo)...";
      try {
        const payload = {
          idea,
          genre: $("coverGenre") ? $("coverGenre").value : "auto",
          aspect_ratio: $("coverAspect") ? $("coverAspect").value : "square",
          guidance_scale: $("coverGuidance") ? parseFloat($("coverGuidance").value) : 1.0,
          steps: $("coverSteps") ? parseInt($("coverSteps").value, 10) : 8,
          seed: $("coverSeed") && $("coverSeed").value ? parseInt($("coverSeed").value, 10) : undefined,
          use_llm: $("coverUseLLM") ? $("coverUseLLM").checked : true,
        };
        const res = await api("/api/cover/generate", { method: "POST", body: payload });
        if (coverOutput) {
          coverOutput.textContent = `[EN COLA] prompt_id: ${res.prompt_id}\nFormato: ${res.aspect_ratio} · semilla ${res.seed}\nEsperando a ComfyUI...`;
        }
        pollCoverStatus(res.prompt_id);
      } catch (err) {
        if (coverOutput) coverOutput.textContent = `[ERROR] ${err.message}`;
        btnGenCover.disabled = false;
      }
    });
  }

  if (btnUseAsVideoInit) {
    btnUseAsVideoInit.addEventListener("click", () => {
      const path = (coverLibSelect && coverLibSelect.value) ? coverLibSelect.value : lastGeneratedCoverPath;
      if (!path) {
        alert("Primero selecciona o genera una portada.");
        return;
      }
      if (videoInitialImage) {
        videoInitialImage.value = path;
        updateInitImagePreview(path);
        videoInitialImage.scrollIntoView({ behavior: "smooth", block: "center" });
        videoInitialImage.style.borderColor = "#00f7ff";
        setTimeout(() => { videoInitialImage.style.borderColor = ""; }, 2000);
      }
    });
  }

  if (videoInitialImage) {
    videoInitialImage.addEventListener("input", () => {
      updateInitImagePreview(videoInitialImage.value.trim());
    });
    videoInitialImage.addEventListener("change", () => {
      updateInitImagePreview(videoInitialImage.value.trim());
    });
  }

  if (btnClearInitImage && videoInitialImage) {
    btnClearInitImage.addEventListener("click", () => {
      videoInitialImage.value = "";
      updateInitImagePreview("");
    });
  }

  if (btnCoverRefresh) {
    btnCoverRefresh.addEventListener("click", () => {
      if (coverOutput) coverOutput.textContent = "[INFO] Refrescando biblioteca de portadas...";
      loadCoverLibrary();
    });
  }

  // --- SECCIÓN B: DIRECTOR DE VÍDEO CINEMÁTICO (LTX-2.5 & MINIMAX) ---
  const btnAiPrompt = $("btnAiVideoPrompt");
  const btnGenerateVideo = $("btnGenerateVideoNow") || $("btnGenerateMinimaxH3");
  const promptArea = $("videoPrompt");
  const videoOutput = $("videoOutput") || $("h3Output");
  const videoPlayer = $("videoPlayer") || $("h3VideoPlayer");
  const videoMeta = $("videoPlayerMeta") || $("h3VideoMeta");
  const libSelect = $("videoLibrarySelect");
  const btnRefreshLib = $("btnRefreshVideoLibrary");

  if (btnAiPrompt) {
    btnAiPrompt.addEventListener("click", async () => {
      const ctx = songContext();
      if (!ctx.genre && !ctx.caption) {
        alert("Escribe primero un caption/género en la pestaña Musical, o ingresa un prompt manual.");
        return;
      }
      btnAiPrompt.disabled = true;
      const prev = btnAiPrompt.textContent;
      btnAiPrompt.textContent = "[...] Pensando prompt de movimiento...";
      try {
        const data = await api("/api/ai/video_prompt", { method: "POST", body: ctx });
        if (promptArea) promptArea.value = data.video_prompt;
        const origin = data.source === "llm" ? `Modelo: ${data.llm_model}` : "Plantilla cinematográfica";
        if (videoOutput) videoOutput.textContent = `[IA PROMPT] ${origin}

${data.video_prompt}`;
      } catch (err) {
        if (videoOutput) videoOutput.textContent = `[ERROR IA] ${err.message}`;
      } finally {
        btnAiPrompt.disabled = false;
        btnAiPrompt.textContent = prev;
      }
    });
  }

  let videoPollTimer = null;
  function stopVideoPolling() {
    if (videoPollTimer) { clearTimeout(videoPollTimer); videoPollTimer = null; }
  }

  async function pollVideoStatus(promptId) {
    try {
      const data = await api(`/api/video/status?prompt_id=${encodeURIComponent(promptId)}`);
      if (data.status === "DONE") {
        stopVideoPolling();
        if (btnGenerateVideo) btnGenerateVideo.disabled = false;
        if (data.videos && data.videos.length) {
          const mainVideo = data.videos[0];
          if (videoOutput) {
            videoOutput.textContent = `[ÉXITO] Vídeo renderizado correctamente:\n${data.videos.join("\n")}`;
          }
          loadMediaBlobInto(videoPlayer, "/api/video/stream", mainVideo, videoMeta, "Vídeo recién generado");
        } else {
          if (videoOutput) videoOutput.textContent = "[LISTO] Render completado por ComfyUI.";
        }
        loadVideoLibrary();
      } else if (data.status === "ERROR") {
        stopVideoPolling();
        if (btnGenerateVideo) btnGenerateVideo.disabled = false;
        if (videoOutput) {
          videoOutput.textContent = `[ERROR EN COMFYUI] ${data.message || "Fallo en la ejecución del flujo de vídeo."}`;
        }
      } else if (data.status === "RUNNING") {
        if (videoOutput) {
          videoOutput.textContent = `[RENDERIZANDO] En GPU RTX 5080... ${data.message || ""}`;
        }
        videoPollTimer = setTimeout(() => pollVideoStatus(promptId), 3000);
      } else {
        if (videoOutput) videoOutput.textContent = `[ESTADO: ${data.status}] ${data.message || "Esperando turno..."}`;
        videoPollTimer = setTimeout(() => pollVideoStatus(promptId), 3500);
      }
    } catch (err) {
      stopVideoPolling();
      if (btnGenerateVideo) btnGenerateVideo.disabled = false;
      if (videoOutput) videoOutput.textContent = `[ERROR] No se pudo consultar ComfyUI: ${err.message}`;
    }
  }

  if (btnGenerateVideo) {
    btnGenerateVideo.addEventListener("click", async () => {
      const prompt = promptArea ? promptArea.value.trim() : "";
      if (!prompt) {
        alert("Escribe o genera primero un prompt de vídeo cinemático.");
        return;
      }
      btnGenerateVideo.disabled = true;
      stopVideoPolling();
      if (videoOutput) videoOutput.textContent = "[...] Enviando trabajo a ComfyUI...";
      try {
        const engine = $("videoEngine") ? $("videoEngine").value : "ltx-2.5";
        const aspect = $("videoAspect") ? $("videoAspect").value : "9:16";
        const duration = $("videoDuration") ? parseFloat($("videoDuration").value) : 2.0;
        const initImg = videoInitialImage ? videoInitialImage.value.trim() : "";
        const refAudio = $("refAudioSelect") ? $("refAudioSelect").value : "";

        const payload = {
          engine,
          prompt,
          title: $("trackTitle") ? $("trackTitle").value : "clip",
          duration_seconds: duration,
          aspect_ratio: aspect,
          initial_image: initImg,
          reference_audio_path: refAudio,
        };

        const res = await api("/api/video/generate", { method: "POST", body: payload });
        const engineLabel = engine === "ltx-2.5" ? "LTX-2.5 22B (Ultra rápido RTX 5080)" : "MiniMax H3";
        if (videoOutput) {
          videoOutput.textContent =
            `[EN COLA] Motor: ${engineLabel}
` +
            `Prompt ID: ${res.prompt_id} · Duración: ${res.duration_seconds}s · Formato: ${res.aspect_ratio}
` +
            (initImg ? `· Condicionado con portada: ${initImg}
` : "") +
            `Esperando a ComfyUI en GPU local...`;
        }
        pollVideoStatus(res.prompt_id);
      } catch (err) {
        if (videoOutput) videoOutput.textContent = `[ERROR] ${err.message}`;
        btnGenerateVideo.disabled = false;
      }
    });
  }

  async function loadVideoLibrary() {
    if (!libSelect) return;
    libSelect.textContent = "";
    try {
      const data = await api("/api/video/list");
      if (!data.videos || !data.videos.length) {
        const opt = document.createElement("option");
        opt.textContent = "No hay vídeos generados aún";
        libSelect.appendChild(opt);
        return;
      }
      data.videos.forEach((v) => {
        const opt = document.createElement("option");
        opt.value = v.path;
        opt.textContent = `${v.name}${v.ext} · ${formatBytes(v.size)} · ${formatDate(v.mtime)}`;
        libSelect.appendChild(opt);
      });
    } catch (err) {
      const opt = document.createElement("option");
      opt.textContent = "Error: " + err.message;
      libSelect.appendChild(opt);
    }
  }

  if (libSelect) {
    libSelect.addEventListener("change", () => {
      const label = libSelect.options[libSelect.selectedIndex] ? libSelect.options[libSelect.selectedIndex].textContent : "";
      loadMediaBlobInto(videoPlayer, "/api/video/stream", libSelect.value, videoMeta, label);
    });
  }
  if (btnRefreshLib) btnRefreshLib.addEventListener("click", loadVideoLibrary);
  reloadVideoLibrary = loadVideoLibrary;
  loadVideoLibrary().then(() => {
    if (!libSelect || libSelect.selectedIndex < 0) return;
    const opt = libSelect.options[libSelect.selectedIndex];
    if (opt && opt.getAttribute("value")) {
      loadMediaBlobInto(videoPlayer, "/api/video/stream", opt.value, videoMeta, opt.textContent, false);
    }
  });
  loadCoverLibrary();
}


// ---------------------------------------------------------------------------
// v2.3 - Mejora #2: Planificador de escenas -> Short completo (varios clips
// H3 generados en secuencia y unidos con FFmpeg)
// ---------------------------------------------------------------------------
function renderSceneCards(scenes) {
  const container = $("scenesContainer");
  if (!container) return;
  container.textContent = "";
  scenes.forEach((s, i) => {
    const card = document.createElement("div");
    card.className = "player-box";
    card.dataset.index = String(i);

    const title = document.createElement("div");
    title.className = "player-meta";
    title.textContent = `Escena ${i + 1}`;

    const promptArea = document.createElement("textarea");
    promptArea.className = "textarea-field scene-prompt";
    promptArea.rows = 2;
    promptArea.value = s.prompt;

    const durRow = document.createElement("div");
    durRow.className = "form-row";
    const durGroup = document.createElement("div");
    durGroup.className = "form-group";
    const durLabel = document.createElement("label");
    durLabel.textContent = "Duración (s, 5-15):";
    const durInput = document.createElement("input");
    durInput.type = "number";
    durInput.min = "5";
    durInput.max = "15";
    durInput.step = "1";
    durInput.className = "input-field scene-duration";
    durInput.value = Math.round(s.duration_seconds);
    durGroup.appendChild(durLabel);
    durGroup.appendChild(durInput);
    durRow.appendChild(durGroup);

    const status = document.createElement("div");
    status.className = "player-meta scene-status";
    status.textContent = "Pendiente";

    card.appendChild(title);
    card.appendChild(promptArea);
    card.appendChild(durRow);
    card.appendChild(status);
    container.appendChild(card);
  });
}

function pollMinimaxUntilDone(promptId, statusEl) {
  return new Promise((resolve, reject) => {
    const check = async () => {
      try {
        const data = await api(`/api/video/minimax_status?prompt_id=${encodeURIComponent(promptId)}`);
        if (data.status === "DONE") {
          if (data.videos && data.videos.length) resolve(data.videos[0]);
          else reject(new Error("ComfyUI terminó pero no se encontró el archivo de vídeo"));
        } else if (data.status === "UNKNOWN") {
          reject(new Error(data.message || "Trabajo desconocido para ComfyUI"));
        } else {
          if (statusEl) statusEl.textContent = `[EN PROCESO] ${data.status}${data.message ? " - " + data.message : ""}`;
          setTimeout(check, 4000);
        }
      } catch (err) {
        reject(err);
      }
    };
    check();
  });
}

async function generateShortSequentially() {
  const container = $("scenesContainer");
  const out = $("shortOutput");
  const btn = $("btnGenerateShort");
  if (!container || !out || !btn) return;
  const cards = Array.from(container.querySelectorAll("[data-index]"));
  if (!cards.length) {
    alert("Primero pulsa «Planificar Escenas con IA».");
    return;
  }

  btn.disabled = true;
  const refAudio = $("refAudioSelect") ? $("refAudioSelect").value : "";
  const quality = $("h3Quality") ? $("h3Quality").value : "native";
  const titleBase = $("trackTitle") ? $("trackTitle").value : "short";
  const clipPaths = [];

  for (const card of cards) {
    const idx = Number(card.dataset.index);
    const promptEl = card.querySelector(".scene-prompt");
    const durEl = card.querySelector(".scene-duration");
    const statusEl = card.querySelector(".scene-status");
    const prompt = promptEl.value.trim();
    const duration = parseFloat(durEl.value);

    if (!prompt) {
      statusEl.textContent = "[ERROR] Prompt vacío";
      out.textContent = `[ERROR] La escena ${idx + 1} no tiene prompt.`;
      btn.disabled = false;
      return;
    }

    statusEl.textContent = "[...] Enviando a ComfyUI...";
    out.textContent = `[ESCENA ${idx + 1}/${cards.length}] Generando con MiniMax H3...`;
    try {
      const payload = { prompt, duration_seconds: duration, quality, title: `${titleBase}_scene${idx + 1}` };
      if (refAudio) payload.reference_audio_path = refAudio;
      const data = await api("/api/video/minimax_generate", { method: "POST", body: payload });
      statusEl.textContent = `[EN PROCESO] prompt_id ${data.prompt_id}`;
      const clipPath = await pollMinimaxUntilDone(data.prompt_id, statusEl);
      statusEl.textContent = `[LISTO] ${clipPath}`;
      clipPaths.push(clipPath);
    } catch (err) {
      statusEl.textContent = `[ERROR] ${err.message}`;
      out.textContent = `[ERROR] Fallo en la escena ${idx + 1}: ${err.message}`;
      btn.disabled = false;
      return;
    }
  }

  out.textContent = `[UNIENDO] ${clipPaths.length} clips con FFmpeg...`;
  try {
    const concatPayload = { clips: clipPaths, title: titleBase };
    const useRealAudio = $("useRealAudioInShort") ? $("useRealAudioInShort").checked : false;
    if (useRealAudio && refAudio) concatPayload.audio_path = refAudio;
    const result = await api("/api/video/concat", { method: "POST", body: concatPayload });
    out.textContent = `[LISTO] Short completo (${result.clips_used} escenas${result.audio_used ? ", audio real" : ""}):\n${result.output_file}`;
    loadMediaBlobInto($("h3VideoPlayer"), "/api/video/stream", result.output_file, $("h3VideoMeta"), "Short completo");
    if (typeof reloadVideoLibrary === "function") reloadVideoLibrary();
  } catch (err) {
    out.textContent = `[ERROR] No se pudieron unir los clips: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

function initSceneShort() {
  const btnPlan = $("btnPlanScenes");
  const btnGenerate = $("btnGenerateShort");
  const out = $("shortOutput");
  if (!btnPlan || !btnGenerate) return;

  btnPlan.addEventListener("click", async () => {
    const ctx = songContext();
    if (!ctx.genre && !ctx.caption) {
      alert("Escribe primero un caption/género en la pestaña Musical, o desmarca «usar contexto de la canción».");
      return;
    }
    btnPlan.disabled = true;
    out.textContent = "[...] Planificando escenas...";
    try {
      const payload = {
        ...ctx,
        num_scenes: parseInt($("sceneCount").value, 10),
        scene_seconds: parseFloat($("sceneSeconds").value),
      };
      const data = await api("/api/ai/video_scenes", { method: "POST", body: payload });
      renderSceneCards(data.scenes);
      const origin = data.source === "llm"
        ? `Modelo conectado: ${data.llm_model}`
        : "Plantilla de respaldo (variaciones de cámara automáticas; conecta un modelo en ⚙️ Modelos para un arco narrativo a medida)";
      out.textContent = `[PLAN LISTO] ${origin}\n${data.scenes.length} escenas. Revisa/edita los prompts y pulsa «Generar Short Completo».`;
      btnGenerate.disabled = false;
    } catch (err) {
      out.textContent = `[ERROR] ${err.message}`;
    } finally {
      btnPlan.disabled = false;
    }
  });

  btnGenerate.addEventListener("click", generateShortSequentially);
}

// ---------------------------------------------------------------------------
// COLAB BRIDGE (trabajos firmados)
// ---------------------------------------------------------------------------
function initColabBridge() {
  const btnDispatch = $("btnDispatchColab");
  const btnRefresh = $("btnRefreshColabJobs");
  const jobsListDiv = $("colabJobsList");
  const logOutput = $("colabLogOutput");
  let requireConfirm = true;

  api("/api/colab/policy").then((p) => {
    requireConfirm = p.require_confirm;
    const hint = $("colabPolicyHint");
    if (hint) {
      hint.textContent = `Solo se aceptan: ${p.allowed_executables.join(" / ")} <script.py> [args]. Scripts permitidos: ${p.allowed_scripts.join(", ")}. Cada trabajo se firma (HMAC), caduca a los ${Math.round(p.max_age_s / 60)} min y Colab verifica la firma antes de ejecutar.`;
    }
  }).catch(() => {});

  async function loadJobs() {
    try {
      const data = await api("/api/colab/jobs");
      jobsListDiv.textContent = "";
      if (!data.available) {
        jobsListDiv.textContent = "Colab Bridge no disponible (¿Google Drive de escritorio cerrado?).";
        return;
      }
      if (!data.jobs.length) {
        jobsListDiv.textContent = "No hay trabajos registrados en Colab Bridge.";
        return;
      }
      data.jobs.forEach((job) => {
        const pill = document.createElement("div");
        pill.className = "job-pill";
        pill.textContent = `${job.id} · ${job.status}`;
        pill.addEventListener("click", () => monitorJob(job.id, pill));
        jobsListDiv.appendChild(pill);
      });
    } catch (err) {
      jobsListDiv.textContent = "Error al conectar con Colab Bridge: " + err.message;
    }
  }

  async function monitorJob(jobId, element) {
    document.querySelectorAll(".job-pill").forEach((p) => p.classList.remove("active"));
    if (element) element.classList.add("active");
    logOutput.textContent = `[CONSULTANDO] Estado del trabajo ${jobId}...`;
    try {
      const data = await api(`/api/colab/status?id=${encodeURIComponent(jobId)}`);
      const reason = data.result && data.result.reason ? `\nMotivo: ${data.result.reason}` : "";
      logOutput.textContent = `[ESTADO: ${data.status}]${reason}\n\n--- REGISTRO ---\n${data.log || "Sin salida de registro aún."}`;
    } catch (err) {
      logOutput.textContent = `[ERROR] No se pudo leer el log de ${jobId}: ${err.message}`;
    }
  }

  btnDispatch.addEventListener("click", async () => {
    const cmd = $("colabCommand").value.trim();
    if (!cmd) {
      alert("Por favor escribe un comando para enviar a Colab.");
      return;
    }
    if (requireConfirm && !confirm("¿Enviar este trabajo a Colab?\n\n" + cmd + "\n\nSe ejecutará en la máquina de Google Colab con tu Drive montado.")) {
      return;
    }
    try {
      const data = await api("/api/colab/dispatch", { method: "POST", body: { command: cmd, confirm: true } });
      alert(`Trabajo firmado y enviado con ID:\n${data.job_id}\n\nColab lo verificará y lo procesará.`);
      loadJobs();
    } catch (err) {
      alert("No se envió el trabajo: " + err.message);
    }
  });

  btnRefresh.addEventListener("click", loadJobs);
  loadJobs();
}

// ---------------------------------------------------------------------------
// MASTERIZACIÓN EBU R128
// ---------------------------------------------------------------------------
function initMastering() {
  const btn = $("btnExecuteMaster");
  const input = $("masterInputFile");
  const log = $("masterLog");

  btn.addEventListener("click", async () => {
    const file = input.value.trim();
    if (!file) {
      alert("Introduce la ruta del archivo de audio.");
      return;
    }
    btn.disabled = true;
    log.textContent = "[...] Masterizando (2 pasadas EBU R128) con FFmpeg. Puede tardar unos segundos...";
    try {
      const d = await api("/api/master_audio", { method: "POST", body: { input_file: file } });
      log.textContent =
        `[ÉXITO] Masterización finalizada:\nArchivo: ${d.output_file}\n` +
        `Objetivo: ${d.target_lufs} LUFS | True Peak <= ${d.true_peak} dBTP | ${d.bitrate}\n` +
        `Original medido: ${d.measured.input_lufs} LUFS | TP ${d.measured.input_true_peak} dBTP | LRA ${d.measured.input_lra}`;
    } catch (err) {
      log.textContent = `[ERROR] ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
}

// ---------------------------------------------------------------------------
// CONEXIÓN MANUAL DE MODELO (LLM)
// ---------------------------------------------------------------------------
function initLlmPanel() {
  if (!$("llmProvider")) return;
  const status = $("llmStatus");
  const DEFAULT_URLS = {
    ollama: "http://127.0.0.1:11434",
    nim: "https://integrate.api.nvidia.com/v1",
    openai: "http://127.0.0.1:1234/v1",
  };

  const readForm = () => ({
    provider: $("llmProvider").value,
    base_url: $("llmBaseUrl").value.trim(),
    model: $("llmModel").value.trim(),
    temperature: parseFloat($("llmTemperature").value),
    api_key: $("llmApiKey").value,
    enabled: $("llmEnabled").checked,
  });

  function fillForm(cfg) {
    $("llmProvider").value = cfg.provider || "ollama";
    $("llmBaseUrl").value = cfg.base_url || DEFAULT_URLS[cfg.provider] || DEFAULT_URLS.ollama;
    $("llmModel").value = cfg.model || (cfg.provider === "nim" ? "nvidia/nemotron-3-ultra-550b-a55b" : "");
    $("llmTemperature").value = cfg.temperature;
    $("llmEnabled").checked = !!cfg.enabled;
    $("llmApiKey").value = "";
    if (cfg.provider === "nim") {
      $("llmApiKey").placeholder = cfg.has_api_key
        ? "(detectada en entorno/secrets; deja vacío para usarla)"
        : "NVIDIA_API_KEY (deja vacío si está en secrets/entorno)";
    } else {
      $("llmApiKey").placeholder = cfg.has_api_key ? "(guardada; escribe otra para reemplazarla)" : "solo si tu servidor la exige";
    }
    status.textContent = cfg.enabled && cfg.model
      ? `Conector ACTIVO: ${cfg.provider.toUpperCase()} · ${cfg.model} · ${cfg.base_url}`
      : "Conector desactivado: el compositor usa plantillas de respaldo.";
  }

  api("/api/llm/config").then(fillForm).catch((err) => { status.textContent = "Error: " + err.message; });

  $("llmProvider").addEventListener("change", () => {
    const prov = $("llmProvider").value;
    const current = $("llmBaseUrl").value.trim();
    if (!current || Object.values(DEFAULT_URLS).includes(current)) {
      $("llmBaseUrl").value = DEFAULT_URLS[prov] || DEFAULT_URLS.ollama;
    }
    if (prov === "nim" && !$("llmModel").value.trim()) {
      $("llmModel").value = "nvidia/nemotron-3-ultra-550b-a55b";
    }
  });

  $("btnLlmDetect").addEventListener("click", async () => {
    status.textContent = "[...] Buscando modelos en el servidor...";
    try {
      const data = await api("/api/llm/models", { method: "POST", body: readForm() });
      const list = $("llmModelList");
      list.textContent = "";
      data.models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m;
        list.appendChild(opt);
      });
      status.textContent = data.models.length
        ? `[OK] ${data.models.length} modelo(s) encontrados. Elige uno en el campo «Modelo»:\n${data.models.join("\n")}`
        : "[AVISO] El servidor responde pero no lista modelos. Escribe el nombre a mano.";
    } catch (err) {
      status.textContent = `[ERROR] ${err.message}`;
    }
  });

  $("btnLlmTest").addEventListener("click", async () => {
    status.textContent = "[...] Probando el modelo (puede tardar si tiene que cargarse en memoria)...";
    try {
      const d = await api("/api/llm/test", { method: "POST", body: readForm() });
      status.textContent = `[OK] ${d.model} respondió en ${d.latency_ms} ms:\n${d.reply}`;
    } catch (err) {
      status.textContent = `[ERROR] ${err.message}`;
    }
  });

  $("btnLlmSave").addEventListener("click", async () => {
    try {
      const cfg = await api("/api/llm/config", { method: "POST", body: readForm() });
      fillForm(cfg);
      status.textContent += "\n[GUARDADO]";
    } catch (err) {
      status.textContent = `[ERROR] ${err.message}`;
    }
  });
}

// ---------------------------------------------------------------------------
// v2.6 - YuE2 (Lyrics-to-Song) + NVIDIA NIM
// ---------------------------------------------------------------------------
(function initYue2() {
  const $ = (id) => document.getElementById(id);
  const statusEl = $("yue2Status");
  const monitorEl = $("yue2Monitor");
  const libEl = $("yue2Library");
  let yue2PollTimer = null;
  let yue2JobId = null;

  async function audioStreamUrl(p) {
    const token = await getToken();
    return `/api/audio/stream?path=${encodeURIComponent(p)}&token=${encodeURIComponent(token)}`;
  }

  async function refreshStatus() {
    try {
      const s = await api("/api/yue2/status");
      if (statusEl) {
        statusEl.textContent = s.available
          ? `[OK] YuE2 listo | Python: ${s.python_available ? "OK" : "FALTA"} | Cot: ${s.cot} | Trabajos en curso: ${s.running_jobs.length}`
          : `[OFFLINE] YuE2 no encontrado en ${s.dir} (falta generate_16gb.py)`;
      }
    } catch (err) {
      if (statusEl) statusEl.textContent = `[ERROR] ${err.message}`;
    }
  }

  async function refreshLibrary() {
    if (!libEl) return;
    try {
      const s = await api("/api/yue2/status");
      const outDir = (s.output_dir || "").replace(/\\/g, "/").toLowerCase();
      const tracks = await api("/api/audio/list");
      const token = await getToken();
      const yue = (tracks.tracks || []).filter((t) => {
        const p = (t.path || "").replace(/\\/g, "/").toLowerCase();
        return p && (outDir ? p.startsWith(outDir) : p.includes("/yue2/outputs/"));
      });
      libEl.innerHTML = "";
      if (!yue.length) {
        libEl.innerHTML = '<div style="color:var(--text-muted); padding:8px 0;">Sin audios generados con YuE2 todavía en outputs/.</div>';
        return;
      }
      for (const t of yue.slice(0, 10)) {
        const div = document.createElement("div");
        div.className = "track-item";
        div.style.cssText = "margin-bottom:14px; padding:12px; background:rgba(255,255,255,0.03); border-radius:8px; border:1px solid rgba(0, 247, 255, 0.15);";

        const label = document.createElement("div");
        label.style.cssText = "font-weight:600; font-size:0.95rem; margin-bottom:8px; color:#00f7ff; display:flex; justify-content:space-between; align-items:center;";
        const sizeMb = (t.size / (1024 * 1024)).toFixed(1);
        label.innerHTML = `<span>🎵 ${t.name}</span> <span style="font-size:0.78rem; color:#aaa; font-weight:normal;">${sizeMb} MB · ${t.ext.toUpperCase()}</span>`;

        const audio = document.createElement("audio");
        audio.controls = true;
        audio.preload = "metadata";
        audio.style.cssText = "width:100%; height:40px; margin-top:4px;";
        audio.src = `/api/audio/stream?path=${encodeURIComponent(t.path)}&token=${encodeURIComponent(token)}`;

        const btnRow = document.createElement("div");
        btnRow.style.cssText = "margin-top:8px; display:flex; gap:8px;";

        const btnMaster = document.createElement("button");
        btnMaster.className = "btn btn-secondary";
        btnMaster.style.cssText = "font-size:0.8rem; padding:4px 10px;";
        btnMaster.textContent = "🎚️ Masterizar EBU R128 (-11 LUFS)";
        btnMaster.addEventListener("click", async () => {
          btnMaster.disabled = true;
          btnMaster.textContent = "⏳ Masterizando...";
          try {
            const mRes = await api("/api/master_audio", { method: "POST", body: { input_file: t.path } });
            alert(`Masterización completada con éxito:\n${mRes.output_file}`);
            refreshLibrary();
          } catch (e) {
            alert(`Error al masterizar: ${e.message}`);
          } finally {
            btnMaster.disabled = false;
            btnMaster.textContent = "🎚️ Masterizar EBU R128 (-11 LUFS)";
          }
        });

        btnRow.appendChild(btnMaster);
        div.appendChild(label);
        div.appendChild(audio);
        div.appendChild(btnRow);
        libEl.appendChild(div);
      }
    } catch (err) {
      if (libEl) libEl.textContent = `[ERROR] ${err.message}`;
    }
  }

  function startPoll(jobId) {
    yue2JobId = jobId;
    if (yue2PollTimer) clearInterval(yue2PollTimer);
    yue2PollTimer = setInterval(async () => {
      try {
        const s = await api(`/api/yue2/job?job_id=${encodeURIComponent(jobId)}`);
        const head = s.status === "RUNNING"
          ? `[RUNNING] ${s.elapsed_s || 0}s transcurridos...`
          : s.status === "DONE"
            ? `[DONE] Audio: ${s.output_file || "(no localizado)"}`
            : `[ERROR] rc=${s.returncode}`;
        if (monitorEl) monitorEl.textContent = head + "\n" + (s.log || "").split("\n").slice(-6).join("\n");
        if (s.status !== "RUNNING") {
          clearInterval(yue2PollTimer);
          yue2PollTimer = null;
          refreshStatus();
          refreshLibrary();
          if (s.status === "DONE" && s.output_file && typeof window.playYue2Track === "function") {
            window.playYue2Track(s.output_file);
          }
        }
      } catch (err) {
        if (monitorEl) monitorEl.textContent = `[ERROR] ${err.message}`;
        clearInterval(yue2PollTimer);
        yue2PollTimer = null;
      }
    }, 5000);
  }

  const btnYue2AiCompose = $("btnYue2AiCompose");
  const btnYue2AiComposeAndRun = $("btnYue2AiComposeAndRun");
  const yue2AiStatusEl = $("yue2AiStatus");

  if (btnYue2AiCompose) {
    btnYue2AiCompose.addEventListener("click", async () => {
      const idea = ($("yue2AiIdea") ? $("yue2AiIdea").value : "").trim();
      const genre = $("yue2AiGenre") ? $("yue2AiGenre").value : "auto";
      const lang = $("yue2AiLang") ? $("yue2AiLang").value : "es";
      if (!idea && genre === "auto") {
        alert("Por favor escribe una idea o temática para que la IA diseñe la canción.");
        return;
      }
      btnYue2AiCompose.disabled = true;
      if (btnYue2AiComposeAndRun) btnYue2AiComposeAndRun.disabled = true;
      if (yue2AiStatusEl) {
        yue2AiStatusEl.style.display = "block";
        yue2AiStatusEl.textContent = "[...] Generando prompt de estilo y letra con IA para YuE 2...";
      }
      const t0 = Date.now();
      try {
        const res = await api("/api/yue2/compose", {
          method: "POST",
          body: { idea, genre, lang },
        });
        if ($("yue2Style")) $("yue2Style").value = res.style || "";
        if ($("yue2Lyrics")) $("yue2Lyrics").value = res.lyrics || "";
        if ($("yue2Output")) $("yue2Output").value = res.output || res.title || "yue2_song";
        if ($("yue2Cot") && res.cot) $("yue2Cot").value = res.cot;
        const dt = ((Date.now() - t0) / 1000).toFixed(1);
        const origin = res.source === "llm"
          ? `Modelo: ${res.llm_model} (${res.provider})`
          : `Plantilla inteligente` + (res.llm_error ? ` (el modelo falló: ${res.llm_error})` : "");
        if (yue2AiStatusEl) {
          yue2AiStatusEl.textContent =
            `[ÉXITO IA] ${origin} en ${dt}s\n` +
            `Título: ${res.title} | Género: ${res.genre} | ${res.bpm} BPM | ${res.key}\n` +
            `Modo CoT: ${res.cot}\n` +
            `✅ Estilo y letra listos abajo. Puedes editarlos o pulsar «▶️ Generar con YuE2 (16GB FP8)».`;
        }
      } catch (err) {
        if (yue2AiStatusEl) yue2AiStatusEl.textContent = `[ERROR IA] ${err.message}`;
      } finally {
        btnYue2AiCompose.disabled = false;
        if (btnYue2AiComposeAndRun) btnYue2AiComposeAndRun.disabled = false;
      }
    });
  }

  if (btnYue2AiComposeAndRun) {
    btnYue2AiComposeAndRun.addEventListener("click", async () => {
      const idea = ($("yue2AiIdea") ? $("yue2AiIdea").value : "").trim();
      const genre = $("yue2AiGenre") ? $("yue2AiGenre").value : "auto";
      const lang = $("yue2AiLang") ? $("yue2AiLang").value : "es";
      if (!idea && genre === "auto") {
        alert("Por favor escribe una idea o temática para que la IA diseñe y lance la canción.");
        return;
      }
      btnYue2AiComposeAndRun.disabled = true;
      if (btnYue2AiCompose) btnYue2AiCompose.disabled = true;
      if (yue2AiStatusEl) {
        yue2AiStatusEl.style.display = "block";
        yue2AiStatusEl.textContent = "[...] Pipeline YuE 2: 1/2 Componiendo estilo y letra con IA...";
      }
      try {
        const res = await api("/api/yue2/compose_and_run", {
          method: "POST",
          body: { idea, genre, lang },
        });
        const comp = res.composed || {};
        if ($("yue2Style")) $("yue2Style").value = comp.style || "";
        if ($("yue2Lyrics")) $("yue2Lyrics").value = comp.lyrics || "";
        if ($("yue2Output")) $("yue2Output").value = comp.output || comp.title || "yue2_song";
        if ($("yue2Cot") && comp.cot) $("yue2Cot").value = comp.cot;

        if (yue2AiStatusEl) {
          yue2AiStatusEl.textContent =
            `[PIPELINE OK] 2/2 Lanzado a GPU en segundo plano (Job ${res.job_id})\n` +
            `Estilo: ${comp.style}\n` +
            `Letra cargada y sintetizando en RTX 5080 (FP8 16GB). Siguiendo en Monitor YuE2...`;
        }
        if (monitorEl) monitorEl.textContent = `[LANZADO] job ${res.job_id}\nIniciando síntesis YuE 2 en RTX 5080...`;
        startPoll(res.job_id);
      } catch (err) {
        if (yue2AiStatusEl) yue2AiStatusEl.textContent = `[ERROR PIPELINE] ${err.message}`;
      } finally {
        btnYue2AiComposeAndRun.disabled = false;
        if (btnYue2AiCompose) btnYue2AiCompose.disabled = false;
      }
    });
  }

  if ($("btnRunYue2")) {
    $("btnRunYue2").addEventListener("click", async () => {
      try {
        const payload = {
          style: $("yue2Style").value.trim(),
          lyrics: $("yue2Lyrics").value.trim(),
          cot: $("yue2Cot").value,
          output: $("yue2Output").value.trim() || "yue2_song",
          lora_name: $("yue2LoraSelect") ? $("yue2LoraSelect").value : "",
          lora_scale: $("yue2LoraScale") ? parseFloat($("yue2LoraScale").value) : 1.0,
          reference_audio_path: $("yue2RefAudioSelect") ? $("yue2RefAudioSelect").value : "",
        };
        const res = await api("/api/yue2/run", { method: "POST", body: payload });
        if (monitorEl) monitorEl.textContent = `[LANZADO] job ${res.job_id}\nEsperando primer poll...`;
        startPoll(res.job_id);
      } catch (err) {
        if (monitorEl) monitorEl.textContent = `[ERROR] ${err.message}`;
      }
    });
  }

  if ($("btnNimTest")) {
    $("btnNimTest").addEventListener("click", async () => {
      const el = $("nimStatus");
      el.textContent = "Probando conexion con NVIDIA NIM...";
      try {
        const res = await api("/api/nim/test");
        if (res.connected && res.inference_ok) {
          el.textContent = `[OK] NIM conectado | ${res.models_count} modelos | Test: ${res.test_model} | Inferencia OK (${res.latency_ms} ms)`;
        } else if (res.connected) {
          el.textContent = `[PARCIAL] NIM conectado (${res.models_count} modelos) pero inferencia fallo: ${res.inference_error || "?"}`;
        } else {
          el.textContent = `[OFFLINE] ${res.error || "Sin conexion"}`;
        }
      } catch (err) {
        el.textContent = `[ERROR] ${err.message}`;
      }
    });
  }

// Refrescar al entrar en la pestana YuE2 y al inicio
  refreshStatus();
  refreshLibrary();

  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.tab === "tab-yue2") {
        refreshStatus();
        refreshLibrary();
      }
    });
  });
})();


// ---------------------------------------------------------------------------
// YUE 2: LORAS, REFERENCIA DE AUDIO Y REPRODUCTOR DEDICADO
// ---------------------------------------------------------------------------
let currentYue2TrackPath = "";

async function initYue2Controls() {
  const loraSelect = $("yue2LoraSelect");
  const refAudioSelect = $("yue2RefAudioSelect");
  const audioPlayer = $("yue2AudioPlayer");
  const audioMeta = $("yue2AudioPlayerMeta");
  const btnMaster = $("btnYue2MasterTrack");
  const btnSendCover = $("btnYue2SendToCover");
  const btnSendVideo = $("btnYue2SendToVideo");

  // Cargar LoRAs disponibles
  if (loraSelect) {
    try {
      const loras = await api("/api/yue2/loras");
      loraSelect.innerHTML = '<option value="">Sin LoRA (Modelo base YuE2 puro)</option>';
      let lastCat = "";
      let groupEl = null;
      (loras || []).forEach((l) => {
        if (l.category !== lastCat) {
          lastCat = l.category;
          groupEl = document.createElement("optgroup");
          groupEl.label = `--- ${l.category} ---`;
          loraSelect.appendChild(groupEl);
        }
        const opt = document.createElement("option");
        opt.value = l.path;
        opt.textContent = `${l.name} (${l.filename})`;
        if (groupEl) groupEl.appendChild(opt);
        else loraSelect.appendChild(opt);
      });
    } catch (e) {
      console.warn("No se pudieron cargar LoRAs de YuE2:", e);
    }
  }

  // Cargar audios de referencia de la biblioteca
  async function loadYue2RefAudios() {
    if (!refAudioSelect) return;
    try {
      const data = await api("/api/audio/list");
      refAudioSelect.innerHTML = '<option value="">Ninguno (Generación desde cero)</option>';
      (data.tracks || []).slice(0, 50).forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t.path;
        opt.textContent = `${t.name} · ${formatBytes(t.size)}`;
        refAudioSelect.appendChild(opt);
      });
    } catch (e) {
      console.warn("No se pudieron cargar audios para YuE2:", e);
    }
  }
  loadYue2RefAudios();

  // Función para reproducir un track de YuE2
  window.playYue2Track = function(filepath) {
    if (!filepath) return;
    currentYue2TrackPath = filepath;
    if (audioPlayer) {
      loadMediaBlobInto(audioPlayer, "/api/audio/stream", filepath, audioMeta, "Pista YuE 2 recién sintetizada", true);
    }
    if (btnMaster) btnMaster.disabled = false;
    if (btnSendCover) btnSendCover.disabled = false;
    if (btnSendVideo) btnSendVideo.disabled = false;
  };

  // Botón Masterizar
  if (btnMaster) {
    btnMaster.addEventListener("click", async () => {
      if (!currentYue2TrackPath) return;
      btnMaster.disabled = true;
      const prevText = btnMaster.textContent;
      btnMaster.textContent = "⏳ Masterizando a -11 LUFS...";
      try {
        const res = await api("/api/master_audio", {
          method: "POST",
          body: { input_file: currentYue2TrackPath },
        });
        alert(`¡Masterización completada!
Archivo: ${res.output_file}
Volumen: ${res.integrated_lufs} LUFS`);
        window.playYue2Track(res.output_file);
      } catch (err) {
        alert("Error al masterizar: " + err.message);
      } finally {
        btnMaster.disabled = false;
        btnMaster.textContent = prevText;
      }
    });
  }

  // Botón Enviar a Portada
  if (btnSendCover) {
    btnSendCover.addEventListener("click", () => {
      const ideaInput = $("coverIdea");
      const titleVal = $("yue2Output") ? $("yue2Output").value : "cancion";
      const styleVal = $("yue2Style") ? $("yue2Style").value : "";
      if (ideaInput) {
        ideaInput.value = `Portada oficial para canción ${titleVal}: ${styleVal}`.slice(0, 300);
      }
      // Cambiar a pestaña Visual
      const navBtns = document.querySelectorAll(".nav-btn");
      const tabPanels = document.querySelectorAll(".tab-panel");
      navBtns.forEach((b) => b.classList.remove("active"));
      tabPanels.forEach((p) => p.classList.remove("active"));
      const targetBtn = document.querySelector('.nav-btn[data-tab="tab-video"]');
      if (targetBtn) targetBtn.classList.add("active");
      const targetPanel = $("tab-video");
      if (targetPanel) targetPanel.classList.add("active");
    });
  }

  // Botón Enviar a Vídeo
  if (btnSendVideo) {
    btnSendVideo.addEventListener("click", () => {
      const refAudio = $("refAudioSelect");
      if (refAudio && currentYue2TrackPath) {
        let found = false;
        for (let opt of refAudio.options) {
          if (opt.value === currentYue2TrackPath) {
            opt.selected = true;
            found = true;
            break;
          }
        }
        if (!found) {
          const newOpt = document.createElement("option");
          newOpt.value = currentYue2TrackPath;
          newOpt.textContent = `YuE2 Pista: ${currentYue2TrackPath.split(/[\\/]/).pop()}`;
          newOpt.selected = true;
          refAudio.appendChild(newOpt);
        }
      }
      // Cambiar a pestaña Visual
      const navBtns = document.querySelectorAll(".nav-btn");
      const tabPanels = document.querySelectorAll(".tab-panel");
      navBtns.forEach((b) => b.classList.remove("active"));
      tabPanels.forEach((p) => p.classList.remove("active"));
      const targetBtn = document.querySelector('.nav-btn[data-tab="tab-video"]');
      if (targetBtn) targetBtn.classList.add("active");
      const targetPanel = $("tab-video");
      if (targetPanel) targetPanel.classList.add("active");
    });
  }
}
