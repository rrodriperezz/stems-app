// Stems Web — multitrack player with Web Audio (sample-perfect sync between stems).
// Speed via time-stretch + pitch shift (SoundTouchJS): tempo and pitch are independent.
import { PitchShifter } from "/static/vendor/soundtouch.js";

const STRETCH_BUFFER = 4096;   // SoundTouch ScriptProcessor size (power of 2)
const WAVE_BARS = 600;         // waveform resolution
const $ = (sel, el = document) => el.querySelector(sel);
const stemList = song => song.stems || ["vocals", "drums", "bass", "other"];
const clampPitch = n => Math.max(-7, Math.min(7, n));

// ---------- upload (one or many) ----------
const dropzone = $("#dropzone");
const fileInput = $("#file-input");
const uploadMsg = $("#upload-msg");

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("dragover", e => { e.preventDefault(); dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", e => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  handleFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", () => {
  handleFiles(fileInput.files);
  fileInput.value = "";
});

// ---------- process from a YouTube link ----------
const urlInput = $("#url-input");
const urlBtn = $("#url-btn");

async function processUrl() {
  const url = (urlInput.value || "").trim();
  if (!url) return;
  urlBtn.disabled = true;
  uploadMsg.textContent = "Resolving link…";
  const fd = new FormData();
  fd.append("url", url);
  fd.append("model", $("#model-select").value);
  fd.append("quality", $("#quality-select").value);
  try {
    const res = await fetch("/api/upload-url", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    urlInput.value = "";
    uploadMsg.textContent = "Queued — downloading from YouTube";
    setTimeout(() => (uploadMsg.textContent = ""), 4000);
    refresh();
  } catch (e) {
    uploadMsg.textContent = "Error: " + e.message;
  } finally {
    urlBtn.disabled = false;
  }
}
urlBtn.addEventListener("click", processUrl);
urlInput.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); processUrl(); } });

function handleFiles(files) {
  const arr = [...files].filter(Boolean);
  if (arr.length) uploadMany(arr);
}

async function uploadOne(file) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("model", $("#model-select").value);
  fd.append("quality", $("#quality-select").value);
  const res = await fetch("/api/upload", { method: "POST", body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
}

async function uploadMany(files) {
  let ok = 0;
  for (let i = 0; i < files.length; i++) {
    const tag = files.length > 1 ? `(${i + 1}/${files.length}) ` : "";
    uploadMsg.textContent = `Uploading ${tag}${files[i].name}…`;
    try { await uploadOne(files[i]); ok++; }
    catch (e) { uploadMsg.textContent = `Error with ${files[i].name}: ${e.message}`; }
  }
  if (ok) {
    uploadMsg.textContent = files.length > 1 ? `${ok}/${files.length} queued` : "Queued";
    setTimeout(() => (uploadMsg.textContent = ""), 4000);
    refresh();
  }
}

// ---------- list / polling ----------
let lastDoneIds = "";
let songMeta = {};               // id -> meta (for sorting/filtering)

async function refresh() {
  let data;
  try {
    data = await (await fetch("/api/songs")).json();
  } catch { return; }

  $("#device-badge").textContent = data.device === "cuda" ? "GPU · ROCm" : "CPU";

  const proc = $("#processing-list");
  proc.innerHTML = "";
  for (const j of data.processing) {
    const div = document.createElement("div");
    div.className = "job" + (j.status === "error" ? " error" : "");
    let phase = j.status === "error" ? ("Error: " + j.error) : (j.phase || "queued");
    if (j.status === "processing" && j.eta_sec) phase += ` · ~${fmtTime(j.eta_sec)} left`;
    div.innerHTML = `
      <div class="jname">${esc(j.name || j.id)}</div>
      <div class="jphase">${esc(phase)}</div>
      ${j.status === "processing" ? `<div class="progress-track"><div class="progress-fill" style="width:${j.progress || 3}%"></div></div>` : ""}`;
    if (j.status === "queued") {
      const x = document.createElement("button");
      x.className = "job-cancel"; x.textContent = "✕"; x.title = "Cancel (remove from queue)";
      x.onclick = () => cancelJob(j.id);
      div.appendChild(x);
    }
    proc.appendChild(div);
  }

  // re-render the library only if the set changed (do not clobber the open player)
  const ids = JSON.stringify(data.done.map(s => s.id));
  if (ids !== lastDoneIds) {
    lastDoneIds = ids;
    renderSongs(data.done);
  }
}

async function cancelJob(sid) {
  try {
    const res = await fetch(`/api/jobs/${sid}/cancel`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    refresh();
  } catch (e) { alert("Could not cancel: " + e.message); }
}

function renderSongs(songs) {
  const list = $("#songs-list");
  songMeta = {};
  list.innerHTML = "";
  for (const s of songs) {
    songMeta[s.id] = s;
    const div = document.createElement("div");
    div.className = "song";
    div.dataset.id = s.id;
    const mdl = s.model || "";
    const badgeCls = mdl === "bs_roformer_sw" ? "sw" : mdl.includes("_ft") ? "ft" : "";
    div.innerHTML = `
      <div class="song-head">
        <button class="play-btn" title="Open player">▶</button>
        <span class="song-title">${esc(s.name)}</span>
        <span class="model-badge ${badgeCls}">${esc(s.model)}</span>
        <span class="song-meta">${fmtTime(s.duration)} · proc. ${esc(s.time_sec)}s</span>
        <button class="del-btn" title="Delete">🗑</button>
      </div>
      <div class="player" hidden></div>`;
    $(".song-head", div).addEventListener("click", () => togglePlayer(div, s));
    $(".del-btn", div).addEventListener("click", e => { e.stopPropagation(); deleteSong(div, s); });
    list.appendChild(div);
  }
  applyLibrary();
}

// filter (search) + sort — works on the existing DOM (does not destroy open players)
function applyLibrary() {
  const list = $("#songs-list");
  const q = ($("#lib-search").value || "").trim().toLowerCase();
  const sort = $("#lib-sort").value;
  const nodes = [...list.querySelectorAll(".song")];

  let visible = 0;
  for (const n of nodes) {
    const m = songMeta[n.dataset.id];
    const hit = !q || (m && (m.name || "").toLowerCase().includes(q));
    n.hidden = !hit;
    if (hit) visible++;
  }

  const key = {
    recent:   (a, b) => (songMeta[b.dataset.id]?.processed_at || "").localeCompare(songMeta[a.dataset.id]?.processed_at || ""),
    name:     (a, b) => (songMeta[a.dataset.id]?.name || "").localeCompare(songMeta[b.dataset.id]?.name || ""),
    duration: (a, b) => (songMeta[b.dataset.id]?.duration || 0) - (songMeta[a.dataset.id]?.duration || 0),
  }[sort] || (() => 0);
  nodes.sort(key).forEach(n => list.appendChild(n));   // reorder without recreating

  const empty = $("#lib-empty");
  empty.hidden = !(nodes.length && visible === 0);
  empty.textContent = q ? "No results for that search." : "No results.";
  if (!nodes.length) list.innerHTML = '<p class="empty">Nothing yet. Drop a file above.</p>';
}

$("#lib-search").addEventListener("input", applyLibrary);
$("#lib-sort").addEventListener("change", applyLibrary);

async function deleteSong(songDiv, song) {
  if (!confirm(`Borrar "${song.name}" y sus stems?`)) return;
  if (P && P.song && P.song.id === song.id) { stopPlayback(); P = null; }
  try {
    const res = await fetch(`/api/songs/${song.id}`, { method: "DELETE" });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    songDiv.remove();
    delete songMeta[song.id];
    try { localStorage.removeItem(stateKey(song.id)); } catch {}
    lastDoneIds = "";   // force a re-render on the next refresh
    applyLibrary();
  } catch (e) {
    alert("Could not delete: " + e.message);
  }
}

// ---------- per-song state persistence (localStorage) ----------
const stateKey = sid => "stems:st:" + sid;
function loadState(sid) {
  try { return JSON.parse(localStorage.getItem(stateKey(sid))) || {}; } catch { return {}; }
}
function saveState() {
  if (!P || !P.song) return;
  const s = {
    vol: P.vol, muted: P.muted, solo: P.solo, masterVol: P.masterVol,
    rate: P.rate, pitch: P.pitch, loopA: P.loopA, loopB: P.loopB, offset: currentPos(),
  };
  try { localStorage.setItem(stateKey(P.song.id), JSON.stringify(s)); } catch {}
  P.lastSave = performance.now();
}

// ---------- multitrack player ----------
let ctx = null;                 // shared AudioContext
let P = null;                   // active player state

function newPlayerState(song, panel) {
  const vol = {}, muted = {};
  for (const s of stemList(song)) { vol[s] = 1; muted[s] = false; }
  return {
    song, panel, buffers: {}, gains: {}, sources: null, masterGain: null,
    vol, muted, masterVol: 1, rate: 1, pitch: 0,
    loopA: null, loopB: null, peaks: null, lastSave: 0,
    solo: null, playing: false, offset: 0, startedAt: 0, duration: 0, raf: 0,
  };
}

// apply the saved state (volumes/mute/solo/loop/rate/pitch) to P
function applySavedState(saved) {
  if (saved.vol) for (const k in P.vol) if (typeof saved.vol[k] === "number") P.vol[k] = saved.vol[k];
  if (saved.muted) for (const k in P.muted) P.muted[k] = !!saved.muted[k];
  if (saved.solo && P.muted.hasOwnProperty(saved.solo)) P.solo = saved.solo;
  if (typeof saved.masterVol === "number") P.masterVol = saved.masterVol;
  if (typeof saved.rate === "number") P.rate = saved.rate;
  if (typeof saved.pitch === "number") P.pitch = clampPitch(saved.pitch);
  if (typeof saved.loopA === "number") P.loopA = saved.loopA;
  if (typeof saved.loopB === "number") P.loopB = saved.loopB;
}

// Current position in seconds, adjusted for playback speed.
function currentPos() {
  if (!P) return 0;
  return P.playing ? P.offset + (ctx.currentTime - P.startedAt) * P.rate : P.offset;
}

async function togglePlayer(songDiv, song) {
  const panel = $(".player", songDiv);
  if (!panel.hidden) {           // close
    saveState();
    stopPlayback();
    panel.hidden = true;
    P = null;
    return;
  }
  // close any other open player
  if (P) saveState();
  stopPlayback();
  document.querySelectorAll(".player").forEach(p => (p.hidden = true));
  P = newPlayerState(song, panel);
  panel.hidden = false;
  panel.innerHTML = '<p class="loading">Loading stems…</p>';

  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  try {
    const my = P;
    await Promise.all(stemList(song).map(async stem => {
      const res = await fetch(`/songs/${song.id}/${stem}.mp3`);
      if (!res.ok) throw new Error(`${stem}.mp3 not found`);
      const buf = await ctx.decodeAudioData(await res.arrayBuffer());
      my.buffers[stem] = buf;
    }));
    if (P !== my) return;        // the user opened another song while this one was loading
    P.duration = Math.max(...stemList(song).map(s => P.buffers[s].duration));

    const saved = loadState(song.id);
    applySavedState(saved);
    computePeaks(P);
    buildPlayerUI();
    const start = (typeof saved.offset === "number" && saved.offset < P.duration - 0.4)
      ? saved.offset : 0;
    startPlayback(start);        // opening = play right away, from where it left off
  } catch (e) {
    panel.innerHTML = `<p class="loading">Error loading stems: ${esc(e.message)}</p>`;
  }
}

function buildPlayerUI() {
  const sid = P.song.id;
  const pitchOpts = [];
  for (let n = 7; n >= -7; n--) pitchOpts.push(`<option value="${n}"${n === P.pitch ? " selected" : ""}>${n > 0 ? "+" + n : n}</option>`);
  P.panel.innerHTML = `
    <div class="transport">
      <button id="btn-toggle">⏸</button>
      <button id="btn-stop">⏹</button>
      <span class="time" id="time-label">0:00 / ${fmtTime(P.duration)}</span>
      <div class="seek" id="seek">
        <canvas class="wave" id="wave"></canvas>
        <div class="loop-region" id="loop-region" hidden></div>
        <div class="seek-fill" id="seek-fill"></div>
      </div>
    </div>
    <div class="controls-row">
      <div class="ctl">
        <span class="ctl-label">Loop A-B</span>
        <button class="lbtn" id="loop-a" title="Set start (key A)">A</button>
        <button class="lbtn" id="loop-b" title="Set end (key B)">B</button>
        <button class="lbtn" id="loop-clear" title="Clear loop (key C)">✕</button>
      </div>
      <div class="ctl ctl-rate" title="Time-stretch: changes speed without changing pitch">
        <span class="ctl-label">Speed</span>
        <input type="range" id="rate-slider" min="0.5" max="1.5" step="0.05" value="${P.rate}">
        <span class="rate-val" id="rate-val">${P.rate.toFixed(2)}×</span>
        <button class="lbtn rate-reset" id="rate-reset" title="Reset to 1×">1×</button>
      </div>
      <div class="ctl" title="Pitch shift: changes pitch in semitones without changing speed">
        <span class="ctl-label">Pitch</span>
        <select id="pitch-select">${pitchOpts.join("")}</select>
      </div>
      <span class="help" id="kbd-help" tabindex="0" data-tip="Keyboard shortcuts (with the player open):
• Space: play / pause
• ← →: back / forward 5s  (Shift = 1s)
• A / B: set loop start / end
• C: clear loop
• 1-9: mute stem N
• + / −: pitch up / down">⌨</span>
      <a class="dl-zip" href="/api/songs/${sid}/download.zip" title="Download all stems (zip)">⬇ zip</a>
    </div>
    <div class="stem-row master-row">
      <span class="stem-name">Master</span>
      <input type="range" class="vol" id="master-vol" min="0" max="100" value="${Math.round(P.masterVol * 100)}">
      <span class="vol-val" id="vv-master">${Math.round(P.masterVol * 100)}</span>
    </div>
    ${stemList(P.song).map(stem => `
      <div class="stem-row stem-${stem}">
        <span class="stem-name">${stem}</span>
        <button class="sbtn ${P.muted[stem] ? "active-m" : ""}" data-m="${stem}" title="Mute">M</button>
        <button class="sbtn ${P.solo === stem ? "active-s" : ""}" data-s="${stem}" title="Solo">S</button>
        <input type="range" class="vol" data-v="${stem}" min="0" max="100" value="${Math.round(P.vol[stem] * 100)}">
        <span class="vol-val" id="vv-${stem}">${Math.round(P.vol[stem] * 100)}</span>
        <a class="dl-stem" href="/songs/${sid}/${stem}.mp3" download="${sid}-${stem}.mp3" title="Download ${stem}">⬇</a>
      </div>`).join("")}`;

  $("#btn-toggle", P.panel).onclick = () => (P.playing ? pausePlayback() : startPlayback(P.offset));
  $("#btn-stop", P.panel).onclick = () => { pausePlayback(); P.offset = 0; updateTimeUI(0); saveState(); };

  $("#master-vol", P.panel).oninput = e => {
    P.masterVol = e.target.value / 100;
    $("#vv-master", P.panel).textContent = e.target.value;
    applyMaster(); saveState();
  };
  $("#rate-slider", P.panel).oninput = e => setRate(parseFloat(e.target.value));
  $("#rate-reset", P.panel).onclick = () => setRate(1);
  $("#pitch-select", P.panel).onchange = e => setPitch(parseInt(e.target.value, 10));
  $("#loop-a", P.panel).onclick = () => { P.loopA = currentPos(); updateLoopUI(); saveState(); };
  $("#loop-b", P.panel).onclick = () => { P.loopB = currentPos(); updateLoopUI(); saveState(); };
  $("#loop-clear", P.panel).onclick = () => { P.loopA = P.loopB = null; updateLoopUI(); saveState(); };
  const seek = $("#seek", P.panel);
  const posFromEvent = e => {
    const r = seek.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    return frac * P.duration;
  };
  seek.addEventListener("pointerdown", e => {
    if (!P) return;
    P.seeking = true;
    seek.setPointerCapture(e.pointerId);
    const t = posFromEvent(e);
    setSeekVisual(t); updateTimeLabelOnly(t);
  });
  seek.addEventListener("pointermove", e => {
    if (!P || !P.seeking) return;
    const t = posFromEvent(e);
    setSeekVisual(t); updateTimeLabelOnly(t);
  });
  const endSeek = e => {
    if (!P || !P.seeking) return;
    P.seeking = false;
    try { seek.releasePointerCapture(e.pointerId); } catch {}
    seekTo(posFromEvent(e));
  };
  seek.addEventListener("pointerup", endSeek);
  seek.addEventListener("pointercancel", endSeek);

  P.panel.querySelectorAll("[data-m]").forEach(b => (b.onclick = () => toggleMute(b.dataset.m)));
  P.panel.querySelectorAll("[data-s]").forEach(b => (b.onclick = () => {
    const s = b.dataset.s;
    P.solo = P.solo === s ? null : s;
    P.panel.querySelectorAll("[data-s]").forEach(x =>
      x.classList.toggle("active-s", x.dataset.s === P.solo));
    applyGains(); saveState();
  }));
  P.panel.querySelectorAll("[data-v]").forEach(r => (r.oninput = () => {
    const s = r.dataset.v;
    P.vol[s] = r.value / 100;
    $(`#vv-${s}`, P.panel).textContent = r.value;
    applyGains(); saveState();
  }));

  updateLoopUI();
  requestAnimationFrame(() => drawWave(P));
}

function toggleMute(s) {
  if (!P) return;
  P.muted[s] = !P.muted[s];
  const btn = P.panel && P.panel.querySelector(`[data-m="${s}"]`);
  if (btn) btn.classList.toggle("active-m", P.muted[s]);
  applyGains(); saveState();
}

function effectiveGain(stem) {
  if (P.solo && P.solo !== stem) return 0;
  if (P.muted[stem]) return 0;
  return P.vol[stem];
}

function applyGains() {
  if (!P) return;
  for (const stem of stemList(P.song))
    if (P.gains[stem]) P.gains[stem].gain.value = effectiveGain(stem);
}

function applyMaster() {
  if (P && P.masterGain) P.masterGain.gain.value = P.masterVol;
}

// Change speed live. Re-anchors offset/startedAt so the position does not jump.
function setRate(rate) {
  if (!P) return;
  if (P.playing) { P.offset = currentPos(); P.startedAt = ctx.currentTime; }
  P.rate = rate;
  if (P.sources) for (const s of Object.values(P.sources)) s.tempo = rate;
  const sl = $("#rate-slider", P.panel); if (sl) sl.value = String(rate);
  const rv = $("#rate-val", P.panel); if (rv) rv.textContent = rate.toFixed(2) + "×";
  saveState();
}

// Change pitch (semitones) without touching speed.
function setPitch(semi) {
  if (!P) return;
  P.pitch = clampPitch(semi);
  if (P.sources) for (const s of Object.values(P.sources)) s.pitchSemitones = P.pitch;
  const sel = $("#pitch-select", P.panel); if (sel) sel.value = String(P.pitch);
  saveState();
}

function updateLoopUI() {
  if (!P || !P.panel) return;
  $("#loop-a", P.panel).classList.toggle("active", P.loopA != null);
  $("#loop-b", P.panel).classList.toggle("active", P.loopB != null);
  const reg = $("#loop-region", P.panel);
  if (P.loopA != null && P.loopB != null && P.duration) {
    const lo = Math.min(P.loopA, P.loopB), hi = Math.max(P.loopA, P.loopB);
    reg.style.left = (lo / P.duration) * 100 + "%";
    reg.style.width = ((hi - lo) / P.duration) * 100 + "%";
    reg.hidden = false;
  } else {
    reg.hidden = true;
  }
}

function startPlayback(offset) {
  if (!P || P.playing) return;
  if (ctx.state === "suspended") ctx.resume();
  const master = ctx.createGain();          // main bus: stems -> master -> output
  master.gain.value = P.masterVol;
  master.connect(ctx.destination);
  P.masterGain = master;
  P.sources = {};
  for (const stem of stemList(P.song)) {
    // one PitchShifter per stem; they start in the same quantum -> they stay in sync
    const shifter = new PitchShifter(ctx, P.buffers[stem], STRETCH_BUFFER);
    shifter.tempo = P.rate;                       // speed without changing pitch
    shifter.pitchSemitones = P.pitch;             // pitch without changing speed
    if (P.duration) shifter.percentagePlayed = offset / P.duration;
    const g = ctx.createGain();
    g.gain.value = effectiveGain(stem);
    shifter.connect(g);
    g.connect(master);
    P.sources[stem] = shifter;
    P.gains[stem] = g;
  }
  P.offset = offset;
  P.startedAt = ctx.currentTime;
  P.playing = true;
  $("#btn-toggle", P.panel).textContent = "⏸";
  tick();
}

function pausePlayback() {
  if (!P || !P.playing) return;
  P.offset = currentPos();
  stopSources();
  P.playing = false;
  cancelAnimationFrame(P.raf);
  if (P.panel) $("#btn-toggle", P.panel).textContent = "▶";
  saveState();
}

function stopSources() {
  if (P && P.sources) for (const s of Object.values(P.sources)) { try { s.disconnect(); } catch {} }
  if (P) P.sources = null;
}

function stopPlayback() {
  if (!P) return;
  cancelAnimationFrame(P.raf);
  stopSources();
  P.playing = false;
}

function seekTo(t) {
  if (!P) return;
  const wasPlaying = P.playing;
  if (wasPlaying) { stopSources(); P.playing = false; cancelAnimationFrame(P.raf); }
  P.offset = Math.max(0, Math.min(t, P.duration));
  updateTimeUI(P.offset);
  if (wasPlaying) startPlayback(P.offset);
  saveState();
}

function tick() {
  if (!P || !P.playing) return;
  const pos = currentPos();
  if (P.loopA != null && P.loopB != null) {   // A-B loop: jump back to the start of the range
    const lo = Math.min(P.loopA, P.loopB), hi = Math.max(P.loopA, P.loopB);
    if (pos >= hi) { seekTo(lo); return; }
  }
  if (pos >= P.duration) {       // end of the song
    pausePlayback();
    P.offset = 0;
    updateTimeUI(0);
    return;
  }
  updateTimeUI(pos);
  if (performance.now() - P.lastSave > 2500) saveState();   // save the position every ~2.5s
  P.raf = requestAnimationFrame(tick);
}

function updateTimeLabelOnly(pos) {
  if (!P || !P.panel) return;
  $("#time-label", P.panel).textContent = `${fmtTime(pos)} / ${fmtTime(P.duration)}`;
}

function setSeekVisual(pos) {
  if (!P || !P.panel) return;
  const fill = $("#seek-fill", P.panel);
  if (fill) fill.style.width = (P.duration ? (pos / P.duration) * 100 : 0) + "%";
}

function updateTimeUI(pos) {
  if (!P || !P.panel) return;
  updateTimeLabelOnly(pos);
  if (!P.seeking) setSeekVisual(pos);
}

// ---------- waveform ----------
function computePeaks(P) {
  const stems = stemList(P.song);
  const len = Math.max(...stems.map(s => P.buffers[s].length));
  const n = Math.min(WAVE_BARS, len);
  const block = Math.max(1, Math.floor(len / n));
  const step = Math.max(1, block >> 7);          // downsample so it stays fast
  const peaks = new Float32Array(n);
  for (const s of stems) {
    const b = P.buffers[s];
    const ch0 = b.getChannelData(0);
    const ch1 = b.numberOfChannels > 1 ? b.getChannelData(1) : ch0;
    const L = b.length;
    for (let i = 0; i < n; i++) {
      let mx = 0;
      const start = i * block, end = Math.min(L, start + block);
      for (let j = start; j < end; j += step) {
        const v = Math.abs((ch0[j] + ch1[j]) * 0.5);
        if (v > mx) mx = v;
      }
      if (mx > peaks[i]) peaks[i] = mx;           // the loudest stem per bucket
    }
  }
  let max = 0;
  for (const v of peaks) if (v > max) max = v;
  if (max > 0) for (let i = 0; i < n; i++) peaks[i] /= max;
  P.peaks = peaks;
}

function drawWave(P) {
  if (!P || !P.panel || !P.peaks) return;
  const cv = $("#wave", P.panel);
  if (!cv) return;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);
  const n = P.peaks.length, bw = w / n, mid = h / 2;
  const grad = g.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(241,236,223,.30)");
  grad.addColorStop(.5, "rgba(241,236,223,.16)");
  grad.addColorStop(1, "rgba(241,236,223,.30)");
  g.fillStyle = grad;
  for (let i = 0; i < n; i++) {
    const ph = Math.max(1.5, P.peaks[i] * h * 0.92);
    g.fillRect(i * bw, mid - ph / 2, Math.max(1, bw * 0.66), ph);
  }
}

let resizeRAF = 0;
window.addEventListener("resize", () => {
  cancelAnimationFrame(resizeRAF);
  resizeRAF = requestAnimationFrame(() => { if (P) drawWave(P); });
});

// ---------- keyboard shortcuts ----------
document.addEventListener("keydown", e => {
  if (!P || !P.panel || P.panel.hidden) return;
  const tag = e.target && e.target.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  const k = e.key;
  if (k === " ") { e.preventDefault(); P.playing ? pausePlayback() : startPlayback(P.offset); }
  else if (k === "ArrowRight") { e.preventDefault(); seekTo(currentPos() + (e.shiftKey ? 1 : 5)); }
  else if (k === "ArrowLeft") { e.preventDefault(); seekTo(currentPos() - (e.shiftKey ? 1 : 5)); }
  else if (k === "a" || k === "A") { P.loopA = currentPos(); updateLoopUI(); saveState(); }
  else if (k === "b" || k === "B") { P.loopB = currentPos(); updateLoopUI(); saveState(); }
  else if (k === "c" || k === "C") { P.loopA = P.loopB = null; updateLoopUI(); saveState(); }
  else if (k === "+" || k === "=") { setPitch(P.pitch + 1); }
  else if (k === "-" || k === "_") { setPitch(P.pitch - 1); }
  else if (/^[1-9]$/.test(k)) {
    const stems = stemList(P.song), idx = +k - 1;
    if (idx < stems.length) toggleMute(stems[idx]);
  }
});

window.addEventListener("beforeunload", saveState);

// ---------- utils ----------
function fmtTime(s) {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function esc(t) {
  const d = document.createElement("div");
  d.textContent = t ?? "";
  return d.innerHTML;
}

refresh();
setInterval(refresh, 2000);
