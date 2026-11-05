import { clamp, mapRect, toDisplay, toScanner } from './geometry.js';

const $ = (id) => document.getElementById(id);
const el = {
  status: $('status'), statusText: $('status-text'),
  stage: $('stage'), frame: $('frame'), preview: $('preview'), empty: $('empty'),
  crop: $('crop'), cropLabel: $('crop-label'),
  alert: $('alert'), alertText: $('alert-text'), alertClose: $('alert-close'),
  jobbar: $('jobbar'), bar: $('bar'), jobText: $('job-text'), jobTime: $('job-time'), cancel: $('cancel'),
  film: $('film'), brightness: $('brightness'), brightnessOut: $('brightness-out'),
  rotate: $('rotate'), mirror: $('mirror'), cropInfo: $('crop-info'), cropReset: $('crop-reset'),
  resolution: $('resolution'), sizeHint: $('size-hint'), roll: $('roll'), frameNo: $('frame-no'),
  saveJpeg: $('save-jpeg'), saveTiff: $('save-tiff'),
  btnPreview: $('btn-preview'), btnScan: $('btn-scan'), outputDir: $('output-dir'),
  scans: $('scans'), scansEmpty: $('scans-empty'), toast: $('toast'),
};

const FILMS = ['color-negative', 'slide', 'bw-negative'];
const FALLBACK_CAPS = { resolutions: [600, 1200, 2400, 3600, 7200], width_mm: 36.33, height_mm: 25 };
const STORAGE_KEY = 'sprocket.settings.v1';
const DEFAULTS = {
  film: 'color-negative', brightness: 0, rotate: 0, mirror: false, crop: null,
  resolution: 3600, roll: 'roll-01', frame: 1, saveJpeg: true, saveTiff: true,
};

const settings = loadSettings();
let status = null;
let shownPreview = null; // id of the preview scan on screen
let previewSize = null; // its pixel size, after rotation
let previewRequest = 0;
let previewUrl = null;
let tracked = null; // the job we're following: { id, state, sawResult }
let pollTimer = 0;
let drag = null;

// --- Settings (remembered by this browser) -----------------------------------

function loadSettings() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) ?? {};
  } catch { /* storage unavailable or corrupt */ }
  const s = { ...DEFAULTS, ...saved };
  if (!FILMS.includes(s.film)) s.film = DEFAULTS.film;
  if (![0, 90, 180, 270].includes(s.rotate)) s.rotate = 0;
  if (!isValidCrop(s.crop)) s.crop = null;
  return s;
}

function isValidCrop(c) {
  return Boolean(c) && ['x0', 'y0', 'x1', 'y1'].every((k) => Number.isFinite(c[k]) && c[k] >= 0 && c[k] <= 1)
    && c.x1 - c.x0 >= 0.01 && c.y1 - c.y0 >= 0.01;
}

function saveSettings() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch { /* storage unavailable: settings last until the page is closed */ }
}

// --- Server -------------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json' } });
  let body = null;
  try {
    body = await res.json();
  } catch { /* empty or not JSON */ }
  if (!res.ok) {
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ')
      : `Request failed (${res.status} ${res.statusText})`;
    throw new Error(message);
  }
  return body;
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    applyStatus(await api('/api/status'));
  } catch {
    status = null;
    setPill('err', 'Can’t reach Sprocket. Is it still running?');
    el.btnPreview.disabled = el.btnScan.disabled = true;
  }
  pollTimer = setTimeout(poll, status?.job?.state === 'running' ? 400 : 2000);
}

function applyStatus(s) {
  status = s;
  renderResolutions(s.capabilities);
  el.outputDir.textContent = `Saving to ${s.output_dir}`;

  const job = s.job;
  const running = job?.state === 'running';
  if (running && job.phase === 'finishing') setPill('busy', 'Parking the film holder…');
  else if (running) setPill('busy', job.kind === 'preview' ? 'Previewing…' : 'Scanning…');
  else if (s.scanner.connected) setPill('ok', `${s.scanner.model} · ready`);
  else setPill('err', 'Scanner not found. Check the USB cable and that it’s switched on.');
  el.btnPreview.disabled = el.btnScan.disabled = running || !s.scanner.connected;

  renderJob(job);
  if (s.preview.id && s.preview.id !== shownPreview) {
    shownPreview = s.preview.id;
    renderPreview();
  }
  follow(job);
}

function setPill(tone, text) {
  el.status.dataset.tone = tone;
  el.statusText.textContent = text;
}

// Notice when the job we're following saves its files or ends.
function follow(job) {
  if (!job) return;
  if (!tracked || tracked.id !== job.id) {
    tracked = { id: job.id, state: job.state, sawResult: job.state !== 'running' };
    if (job.state !== 'running') return; // finished before this page saw it
  }
  if (job.kind === 'scan' && job.result && !tracked.sawResult) {
    tracked.sawResult = true;
    scanSaved(job.result);
  }
  if (tracked.state === 'running' && job.state !== 'running') {
    if (job.state === 'error') showAlert(job.error);
    if (job.state === 'cancelled') showToast(job.kind === 'preview' ? 'Preview cancelled.' : 'Scan cancelled.');
  }
  tracked.state = job.state;
}

function scanSaved(result) {
  showToast(`Saved ${result.name}. Move the holder to the next frame and press Preview.`);
  settings.roll = result.roll;
  settings.frame = result.next_frame;
  el.roll.value = settings.roll;
  el.frameNo.value = settings.frame;
  saveSettings();
  loadScans();
}

function renderJob(job) {
  // While the holder is parked the picture is already in, so keep it uncovered for
  // cropping; the status in the top bar says what's happening.
  const running = job?.state === 'running' && job.phase !== 'finishing';
  el.jobbar.hidden = !running;
  if (!running) return;
  const what = job.kind === 'preview' ? 'Preview' : 'Scan';
  const percent = Math.round(job.progress * 100);
  const text = job.cancelling ? 'Cancelling… the scanner is parking the film holder.'
    : {
      starting: job.attempt > 1
        ? `${what}: the scanner didn’t start; trying again…`
        : `${what}: getting ready (moving the film, calibrating)…`,
      scanning: `${what}: scanning, ${percent}%`,
      processing: job.kind === 'preview' ? 'Preview: processing…' : 'Scan: saving files…',
    }[job.phase] ?? `${what}…`;
  el.jobText.textContent = text;
  el.jobTime.textContent = job.eta != null ? `about ${duration(job.eta)} left` : duration(job.elapsed);
  const indeterminate = job.phase === 'starting' || job.cancelling;
  el.bar.classList.toggle('indeterminate', indeterminate);
  el.bar.style.width = indeterminate ? '' : `${job.phase === 'scanning' ? percent : 100}%`;
  el.cancel.hidden = job.cancelling || !['starting', 'scanning'].includes(job.phase);
}

// --- Preview --------------------------------------------------------------------

async function renderPreview() {
  if (!shownPreview) return;
  const id = ++previewRequest;
  const params = new URLSearchParams({
    film: settings.film, brightness: settings.brightness, rotate: settings.rotate,
    mirror: settings.mirror, v: shownPreview,
  });
  if (settings.crop) {
    params.set('crop', ['x0', 'y0', 'x1', 'y1'].map((k) => settings.crop[k].toFixed(4)).join(','));
  }
  try {
    const res = await fetch(`/api/preview.jpg?${params}`);
    if (res.status === 404) return; // the server restarted and has no preview
    if (!res.ok) throw new Error(`Couldn’t load the preview (${res.status}).`);
    const url = URL.createObjectURL(await res.blob());
    const img = new Image();
    img.src = url;
    await img.decode().catch(() => {});
    if (id !== previewRequest) {
      URL.revokeObjectURL(url); // a newer request is on its way
      return;
    }
    el.preview.src = url;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = url;
    previewSize = { w: img.naturalWidth, h: img.naturalHeight };
    el.empty.hidden = true;
    el.frame.hidden = false;
    fitFrame();
    drawCrop();
  } catch (err) {
    showAlert(err.message);
  }
}

const schedulePreview = debounce(renderPreview, 120);

// Size the preview to fit the stage, keeping its aspect ratio.
function fitFrame() {
  if (!previewSize) return;
  const box = el.stage.getBoundingClientRect();
  const style = getComputedStyle(el.stage);
  const availW = box.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const availH = box.height - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
  const ratio = previewSize.w / previewSize.h;
  let w = availW;
  let h = w / ratio;
  if (h > availH) {
    h = availH;
    w = h * ratio;
  }
  el.frame.style.width = `${Math.max(1, Math.floor(w))}px`;
  el.frame.style.height = `${Math.max(1, Math.floor(h))}px`;
}

// --- Crop ------------------------------------------------------------------------

const displayCrop = () => settings.crop && mapRect(settings.crop, (p) => toDisplay(p, settings.rotate, settings.mirror));
const toScannerRect = (rect) => mapRect(rect, (p) => toScanner(p, settings.rotate, settings.mirror));
const percent = (v) => `${(v * 100).toFixed(3)}%`;

// Size of a crop (scanner coordinates; null = whole area) in mm, as displayed.
function cropSizeMM(crop) {
  const caps = status?.capabilities ?? FALLBACK_CAPS;
  const c = crop ?? { x0: 0, y0: 0, x1: 1, y1: 1 };
  let w = (c.x1 - c.x0) * caps.width_mm;
  let h = (c.y1 - c.y0) * caps.height_mm;
  if (settings.rotate % 180) [w, h] = [h, w];
  return { w, h };
}

function drawCrop(rect = displayCrop()) {
  el.crop.hidden = !rect;
  if (!rect) return;
  Object.assign(el.crop.style, {
    left: percent(rect.x0), top: percent(rect.y0),
    width: percent(rect.x1 - rect.x0), height: percent(rect.y1 - rect.y0),
  });
  const { w, h } = cropSizeMM(toScannerRect(rect));
  el.cropLabel.textContent = `${w.toFixed(1)} × ${h.toFixed(1)} mm`;
}

function pointerPosition(event) {
  const r = el.frame.getBoundingClientRect();
  return [clamp((event.clientX - r.left) / r.width), clamp((event.clientY - r.top) / r.height)];
}

const inside = ([x, y], r) => x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;

function dragRect(d, [x, y]) {
  if (d.mode === 'draw') {
    const [sx, sy] = d.start;
    return { x0: Math.min(sx, x), y0: Math.min(sy, y), x1: Math.max(sx, x), y1: Math.max(sy, y) };
  }
  const w = d.origin.x1 - d.origin.x0;
  const h = d.origin.y1 - d.origin.y0;
  const x0 = clamp(d.origin.x0 + x - d.start[0], 0, 1 - w);
  const y0 = clamp(d.origin.y0 + y - d.start[1], 0, 1 - h);
  return { x0, y0, x1: x0 + w, y1: y0 + h };
}

el.frame.addEventListener('pointerdown', (event) => {
  if (event.button !== 0) return;
  const p = pointerPosition(event);
  const current = displayCrop();
  drag = current && inside(p, current) ? { mode: 'move', start: p, origin: current } : { mode: 'draw', start: p };
  el.frame.setPointerCapture(event.pointerId);
  event.preventDefault();
});

el.frame.addEventListener('pointermove', (event) => {
  if (!drag) return;
  drag.rect = dragRect(drag, pointerPosition(event));
  drawCrop(drag.rect);
});

function endDrag() {
  if (!drag) return;
  const rect = drag.rect;
  drag = null;
  if (!rect || rect.x1 - rect.x0 < 0.03 || rect.y1 - rect.y0 < 0.03) {
    drawCrop(); // just a click: keep the crop as it was
    return;
  }
  settings.crop = toScannerRect(rect);
  saveSettings();
  renderCropInfo();
  schedulePreview(); // levels are measured inside the crop
}

el.frame.addEventListener('pointerup', endDrag);
el.frame.addEventListener('pointercancel', endDrag);

function renderCropInfo() {
  if (settings.crop) {
    const { w, h } = cropSizeMM(settings.crop);
    el.cropInfo.textContent = `Crop: ${w.toFixed(1)} × ${h.toFixed(1)} mm`;
  } else {
    el.cropInfo.textContent = 'Crop: full frame';
  }
  el.cropReset.hidden = !settings.crop;
  renderSizeHint();
}

// --- Controls ----------------------------------------------------------------------

let shownResolutions = '';

function renderResolutions(caps) {
  const key = caps.resolutions.join(',');
  if (key === shownResolutions) return;
  shownResolutions = key;
  el.resolution.replaceChildren(...caps.resolutions.map((r) => new Option(`${r} dpi`, r)));
  if (!caps.resolutions.includes(Number(settings.resolution))) {
    settings.resolution = caps.resolutions.reduce((a, b) => (Math.abs(b - 3600) < Math.abs(a - 3600) ? b : a));
  }
  el.resolution.value = settings.resolution;
  renderSizeHint();
}

function renderSizeHint() {
  const { w, h } = cropSizeMM(settings.crop);
  const dpi = Number(settings.resolution);
  const pw = Math.round((w / 25.4) * dpi);
  const ph = Math.round((h / 25.4) * dpi);
  el.sizeHint.textContent = `${pw.toLocaleString()} × ${ph.toLocaleString()} px · raw TIFF ≈ ${megabytes(pw * ph * 6)}`;
}

function renderFilm() {
  for (const b of el.film.querySelectorAll('button')) b.setAttribute('aria-checked', String(b.dataset.value === settings.film));
}

function renderControls() {
  renderFilm();
  el.brightness.value = settings.brightness;
  el.brightnessOut.value = settings.brightness;
  el.mirror.setAttribute('aria-pressed', String(settings.mirror));
  el.roll.value = settings.roll;
  el.frameNo.value = settings.frame;
  el.saveJpeg.checked = settings.saveJpeg;
  el.saveTiff.checked = settings.saveTiff;
  renderResolutions(FALLBACK_CAPS);
  renderCropInfo();
}

function bindControls() {
  for (const b of el.film.querySelectorAll('button')) {
    b.addEventListener('click', () => {
      settings.film = b.dataset.value;
      saveSettings();
      renderFilm();
      renderPreview();
    });
  }
  el.brightness.addEventListener('input', () => {
    settings.brightness = Number(el.brightness.value);
    el.brightnessOut.value = settings.brightness;
    saveSettings();
    schedulePreview();
  });
  el.brightness.addEventListener('dblclick', () => {
    el.brightness.value = 0;
    el.brightness.dispatchEvent(new Event('input'));
  });
  el.rotate.addEventListener('click', () => {
    settings.rotate = (settings.rotate + 90) % 360;
    saveSettings();
    renderCropInfo();
    renderPreview(); // the crop box is redrawn once the rotated image arrives
  });
  el.mirror.addEventListener('click', () => {
    settings.mirror = !settings.mirror;
    el.mirror.setAttribute('aria-pressed', String(settings.mirror));
    saveSettings();
    renderPreview();
  });
  el.cropReset.addEventListener('click', () => {
    settings.crop = null;
    saveSettings();
    renderCropInfo();
    drawCrop();
    renderPreview();
  });
  el.resolution.addEventListener('change', () => {
    settings.resolution = Number(el.resolution.value);
    saveSettings();
    renderSizeHint();
  });
  el.roll.addEventListener('change', async () => {
    const typed = el.roll.value.trim() || 'roll';
    try {
      const { roll, frame } = await api(`/api/next-frame?roll=${encodeURIComponent(typed)}`);
      settings.roll = roll;
      settings.frame = frame;
    } catch {
      settings.roll = typed;
    }
    el.roll.value = settings.roll;
    el.frameNo.value = settings.frame;
    saveSettings();
  });
  el.frameNo.addEventListener('change', () => {
    const n = Math.round(Number(el.frameNo.value));
    settings.frame = Number.isFinite(n) ? clamp(n, 0, 9999) : 1;
    el.frameNo.value = settings.frame;
    saveSettings();
  });
  el.saveJpeg.addEventListener('change', () => { settings.saveJpeg = el.saveJpeg.checked; saveSettings(); });
  el.saveTiff.addEventListener('change', () => { settings.saveTiff = el.saveTiff.checked; saveSettings(); });

  el.btnPreview.addEventListener('click', () => startJob('preview'));
  el.btnScan.addEventListener('click', () => startJob('scan'));
  el.cancel.addEventListener('click', cancelJob);
  el.alertClose.addEventListener('click', hideAlert);

  document.addEventListener('keydown', (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
    if (event.target.closest('input, select, textarea, [contenteditable]')) return;
    const key = event.key.toLowerCase();
    if (key === 'p') startJob('preview');
    else if (key === 's') startJob('scan');
    else return;
    event.preventDefault();
  });

  new ResizeObserver(fitFrame).observe(el.stage);
}

// --- Jobs ----------------------------------------------------------------------------

async function startJob(kind) {
  if ((kind === 'scan' ? el.btnScan : el.btnPreview).disabled) return;
  if (kind === 'scan' && !settings.saveJpeg && !settings.saveTiff) {
    showAlert('Choose at least one file type to save: JPEG or raw TIFF.');
    return;
  }
  hideAlert();
  el.btnPreview.disabled = el.btnScan.disabled = true;
  try {
    const body = kind === 'scan' ? JSON.stringify({
      film: settings.film,
      resolution: Number(settings.resolution),
      brightness: settings.brightness,
      rotate: settings.rotate,
      mirror: settings.mirror,
      crop: settings.crop,
      roll: settings.roll || 'roll',
      frame: Number(settings.frame) || 0,
      save_jpeg: settings.saveJpeg,
      save_tiff: settings.saveTiff,
    }) : undefined;
    const job = await api(`/api/${kind}`, { method: 'POST', body });
    tracked = { id: job.id, state: job.state, sawResult: false };
  } catch (err) {
    showAlert(err.message);
  }
  poll();
}

async function cancelJob() {
  try {
    await api('/api/cancel', { method: 'POST' });
  } catch (err) {
    showAlert(err.message);
  }
  poll();
}

// --- Recent scans ------------------------------------------------------------------

async function loadScans() {
  let scans;
  try {
    ({ scans } = await api('/api/scans'));
  } catch {
    return;
  }
  el.scans.replaceChildren(...scans.map(scanCard));
  el.scansEmpty.hidden = scans.length > 0;
}

function scanCard(scan) {
  const card = document.createElement('figure');
  card.className = 'scan';
  const main = scan.files.jpeg ?? scan.files.tiff;
  const thumb = document.createElement('a');
  thumb.className = 'thumb';
  thumb.href = main.url;
  thumb.target = '_blank';
  thumb.rel = 'noopener';
  if (scan.thumb) {
    const img = document.createElement('img');
    img.src = scan.thumb;
    img.alt = scan.name;
    img.loading = 'lazy';
    thumb.append(img);
  } else {
    thumb.textContent = scan.files.jpeg ? 'JPEG' : 'TIFF';
  }
  const caption = document.createElement('figcaption');
  const name = document.createElement('span');
  name.className = 'scan-name';
  name.textContent = scan.name;
  name.title = `${scan.roll}/${scan.name}`;
  const files = document.createElement('span');
  files.className = 'scan-files';
  for (const [kind, label] of [['jpeg', 'JPEG'], ['tiff', 'TIFF']]) {
    const file = scan.files[kind];
    if (!file) continue;
    const a = document.createElement('a');
    a.href = file.url;
    a.textContent = label;
    a.title = megabytes(file.size);
    if (kind === 'tiff') a.download = '';
    else { a.target = '_blank'; a.rel = 'noopener'; }
    files.append(a);
  }
  caption.append(name, files);
  card.append(thumb, caption);
  return card;
}

// --- Messages and helpers -----------------------------------------------------------

function showAlert(message) {
  el.alertText.textContent = message;
  el.alert.hidden = false;
}

function hideAlert() {
  el.alert.hidden = true;
}

let toastTimer = 0;
function showToast(message) {
  el.toast.textContent = message;
  el.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, 6000);
}

function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function megabytes(bytes) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

function debounce(fn, ms) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

renderControls();
bindControls();
poll();
loadScans();
