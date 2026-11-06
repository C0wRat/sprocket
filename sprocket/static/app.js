import { clamp, mapRect, migrateOrientation, toDisplay, toScanner } from './geometry.js';
import { BRANDS, filmType, films, firstStock, isStock, isos, stockName } from './films.js';
import { computeHistogram, drawHistogram } from './histogram.js';
import { MAX_SCALE, STEP, clampView, cropView, fitScale, fitView, isZoomed, layout, pan, zoomAt } from './zoom.js';

const $ = (id) => document.getElementById(id);
const el = {
  status: $('status'), statusText: $('status-text'),
  stage: $('stage'), frame: $('frame'), preview: $('preview'), empty: $('empty'),
  crop: $('crop'), cropLabel: $('crop-label'),
  alert: $('alert'), alertText: $('alert-text'), alertClose: $('alert-close'),
  jobbar: $('jobbar'), bar: $('bar'), jobText: $('job-text'), jobTime: $('job-time'), cancel: $('cancel'),
  film: $('film'), stockBrand: $('stock-brand'), stockFilm: $('stock-film'), stockIso: $('stock-iso'),
  adjustReset: $('adjust-reset'), adjustHint: $('adjust-hint'),
  preset: $('preset'), presetSave: $('preset-save'), presetActions: $('preset-actions'),
  presetRename: $('preset-rename'), presetDelete: $('preset-delete'),
  lock: $('lock'), lockHint: $('lock-hint'),
  histogramBox: $('histogram-box'), histogram: $('histogram'), clipping: $('clipping'),
  rotate: $('rotate'), mirror: $('mirror'), cropInfo: $('crop-info'), cropReset: $('crop-reset'),
  resolution: $('resolution'), resolutionOut: $('resolution-out'), resolutionTicks: $('resolution-ticks'),
  sizeHint: $('size-hint'), roll: $('roll'), frameNo: $('frame-no'),
  saveJpeg: $('save-jpeg'), saveTiff: $('save-tiff'), sharpen: $('sharpen'),
  btnPreview: $('btn-preview'), btnScan: $('btn-scan'), outputDir: $('output-dir'),
  scans: $('scans'), scansEmpty: $('scans-empty'), toast: $('toast'), theme: $('theme'),
  lightbox: $('lightbox'), lightboxStage: $('lightbox-stage'), lightboxImg: $('lightbox-img'),
  lightboxName: $('lightbox-name'), lightboxCount: $('lightbox-count'), lightboxFiles: $('lightbox-files'),
  lightboxNote: $('lightbox-note'), lightboxClose: $('lightbox-close'),
  lightboxPrev: $('lightbox-prev'), lightboxNext: $('lightbox-next'),
  zoombar: $('zoombar'), zoomLevel: $('zoom-level'), zoomIn: $('zoom-in'), zoomOut: $('zoom-out'),
  zoomFit: $('zoom-fit'), zoom100: $('zoom-100'), zoomCrop: $('zoom-crop'),
  scannerField: $('scanner-field'), scanner: $('scanner'), scannerHint: $('scanner-hint'),
};

const FILMS = ['color-negative', 'slide', 'bw-negative'];
const FILM_NAMES = { 'color-negative': 'colour negative', slide: 'slide', 'bw-negative': 'black & white negative' };
const FALLBACK_CAPS = { resolutions: [600, 1200, 2400, 3600, 7200], width_mm: 36.33, height_mm: 25 };
const STORAGE_KEY = 'sprocket.settings.v1';
// The Adjust sliders, -100 to 100. The last three do nothing for black & white film.
const SLIDERS = ['brightness', 'contrast', 'saturation', 'warmth', 'tint'];
const COLOUR_SLIDERS = ['saturation', 'warmth', 'tint'];
const sliders = Object.fromEntries(SLIDERS.map((name) => [name, { input: $(name), out: $(`${name}-out`) }]));
const DEFAULTS = {
  film: 'color-negative', stock: null, brightness: 0, contrast: 0, saturation: 0, warmth: 0, tint: 0,
  rotate: 0, mirror: false, crop: null,
  zoomCrop: false, // the preview follows the crop (zoom to crop)
  // How rotate and mirror combine: 2 = rotate, then mirror (see geometry.js).
  orientation: 2,
  // Locked colours: the levels of one frame, { film, roll, low: [r, g, b], high: [r, g, b] }.
  lock: null,
  resolution: 3600, roll: 'roll-01', frame: 1, saveJpeg: true, saveTiff: true, sharpen: 'off',
};
const SHARPEN = ['off', 'low', 'medium', 'high'];
// The colour theme is remembered on its own, so it applies before the settings load
// (see the script at the top of index.html). "system" follows the computer's setting.
const THEME_KEY = 'sprocket.theme';
const THEMES = ['system', 'light', 'dark', 'dracula', 'contrast', 'retro'];
const prefersLight = matchMedia('(prefers-color-scheme: light)');

const settings = loadSettings();
let status = null;
let shownPreview = null; // id of the preview scan on screen
let previewSize = null; // its pixel size, after rotation
let previewRequest = 0;
let previewUrl = null;
let tracked = null; // the job we're following: { id, state, sawResult }
let pollTimer = 0;
let drag = null;
let shownHistogram = null; // { hist, mono }, redrawn when the theme changes
let view = null; // zoom and pan of the preview (see zoom.js); null: fit the whole picture
let presets = []; // saved slider presets, from the server: { name, film, brightness, … }
let chosenPreset = ''; // the name of the preset last chosen or saved

// --- Settings (remembered by this browser) -----------------------------------

function loadSettings() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) ?? {};
  } catch { /* storage unavailable or corrupt */ }
  const s = { ...DEFAULTS, ...saved };
  if (!FILMS.includes(s.film)) s.film = DEFAULTS.film;
  if (!isStock(s.stock) || filmType(s.stock.brand, s.stock.film) !== s.film) s.stock = null;
  for (const name of SLIDERS) {
    if (!Number.isFinite(s[name])) s[name] = 0;
    s[name] = clamp(Math.round(s[name]), -100, 100);
  }
  if (![0, 90, 180, 270].includes(s.rotate)) s.rotate = 0;
  s.mirror = s.mirror === true;
  if (saved.orientation !== 2) Object.assign(s, migrateOrientation(s), { orientation: 2 });
  if (!isValidCrop(s.crop)) s.crop = null;
  s.zoomCrop = s.zoomCrop === true && Boolean(s.crop);
  if (!isValidLock(s.lock)) s.lock = null;
  if (!SHARPEN.includes(s.sharpen)) s.sharpen = DEFAULTS.sharpen;
  return s;
}

function isValidCrop(c) {
  return Boolean(c) && ['x0', 'y0', 'x1', 'y1'].every((k) => Number.isFinite(c[k]) && c[k] >= 0 && c[k] <= 1)
    && c.x1 - c.x0 >= 0.01 && c.y1 - c.y0 >= 0.01;
}

function isValidLock(l) {
  const raw = (v) => Number.isFinite(v) && v >= 0 && v <= 65535;
  return Boolean(l) && FILMS.includes(l.film) && [l.low, l.high].every((a) => Array.isArray(a) && a.length === 3 && a.every(raw))
    && l.low.every((v, i) => v <= l.high[i]);
}

// The lock applies only to the film type it was made for.
const activeLock = () => (settings.lock?.film === settings.film ? settings.lock : null);

function saveSettings() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch { /* storage unavailable: settings last until the page is closed */ }
}

// --- Theme ---------------------------------------------------------------------

function savedTheme() {
  try {
    const theme = localStorage.getItem(THEME_KEY);
    return THEMES.includes(theme) ? theme : 'system';
  } catch {
    return 'system';
  }
}

// Show the chosen theme. Every change (picker, the computer's setting) goes through here.
function applyTheme(choice = savedTheme()) {
  const theme = choice === 'system' ? (prefersLight.matches ? 'light' : 'dark') : choice;
  document.documentElement.dataset.theme = theme;
  el.theme.value = choice;
  if (shownHistogram) drawHistogram(el.histogram, shownHistogram.hist, shownHistogram.mono);
}

function setTheme(choice) {
  try {
    if (choice === 'system') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch { /* storage unavailable: the theme lasts until the page is closed */ }
  applyTheme(choice);
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

  renderScanners(s, running);
  renderJob(job);
  if (s.preview.id && s.preview.id !== shownPreview) {
    shownPreview = s.preview.id;
    renderPreview();
    renderLock();
  }
  follow(job);
}

// --- Choosing a scanner, when more than one is connected ----------------------------

let shownScanners = '';
const shortModel = (model) => model.replace(/^Plustek /, '');

function renderScanners(s, running) {
  const { scanners, chosen } = s;
  const chosenMissing = Boolean(chosen) && !scanners.some((d) => d.key === chosen);
  el.scannerField.hidden = !s.choosable || (scanners.length < 2 && !chosenMissing);
  el.scanner.disabled = running || scanners.length < 2;
  // Rebuild the list only when it changes, so polling doesn't close it while it's open.
  const key = scanners.map((d) => d.key).join('|');
  if (key !== shownScanners) {
    shownScanners = key;
    el.scanner.replaceChildren(...scanners.map((d) => new Option(`${shortModel(d.model)} · USB port ${d.port}`, d.key)));
  }
  if (s.scanner.key) el.scanner.value = s.scanner.key;
  el.scannerHint.hidden = !chosenMissing || !s.scanner.connected;
  if (chosenMissing && s.scanner.connected) {
    const [model, port] = chosen.split('@');
    el.scannerHint.textContent = `The ${shortModel(model)} on USB port ${port} isn’t connected, so the ${shortModel(s.scanner.model)} is used.`;
  }
}

async function chooseScanner() {
  try {
    const device = await api('/api/scanner', { method: 'PUT', body: JSON.stringify({ key: el.scanner.value }) });
    showToast(`Using the ${shortModel(device.model)} on USB port ${device.port}. Press Preview to see its frame.`);
  } catch (err) {
    showAlert(err.message);
  }
  poll();
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
  renderLock();
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
    ...adjustments(), rotate: settings.rotate, mirror: settings.mirror, v: shownPreview,
  });
  if (settings.crop) params.set('crop', cropParam(settings.crop));
  const lock = activeLock();
  if (lock) params.set('levels', [...lock.low, ...lock.high].join(','));
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
    // A picture of another shape (it was rotated) starts again from fitting the stage.
    if (previewSize?.w !== img.naturalWidth || previewSize?.h !== img.naturalHeight) view = null;
    previewSize = { w: img.naturalWidth, h: img.naturalHeight };
    el.empty.hidden = true;
    el.frame.hidden = false;
    el.zoombar.hidden = false;
    fitFrame();
    drawCrop();
    renderHistogram(img);
  } catch (err) {
    showAlert(err.message);
  }
}

const pixelReader = document.createElement('canvas');

// Histogram and clipping of the part of the preview that will be saved.
function renderHistogram(img) {
  const { naturalWidth: w, naturalHeight: h } = img;
  const r = displayCrop() ?? { x0: 0, y0: 0, x1: 1, y1: 1 };
  const x = Math.floor(r.x0 * w);
  const y = Math.floor(r.y0 * h);
  const cw = Math.max(1, Math.min(w - x, Math.ceil(r.x1 * w) - x));
  const ch = Math.max(1, Math.min(h - y, Math.ceil(r.y1 * h) - y));
  pixelReader.width = cw;
  pixelReader.height = ch;
  const ctx = pixelReader.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, x, y, cw, ch, 0, 0, cw, ch);
  const hist = computeHistogram(ctx.getImageData(0, 0, cw, ch).data);
  el.histogramBox.hidden = false;
  shownHistogram = { hist, mono: settings.film === 'bw-negative' };
  drawHistogram(el.histogram, hist, shownHistogram.mono);
  const amount = (label, fraction) => {
    const span = document.createElement('span');
    span.textContent = `${label} ${percentText(fraction)}`;
    span.classList.toggle('clip-warn', fraction > 0.01);
    return span;
  };
  el.clipping.replaceChildren('Clipped: ', amount('black', hist.black), ' · ', amount('white', hist.white));
}

function percentText(fraction) {
  const p = fraction * 100;
  if (p > 0 && p < 0.1) return '<0.1%';
  return `${p < 10 ? p.toFixed(1) : Math.round(p)}%`;
}

const schedulePreview = debounce(renderPreview, 120);

const cropParam = (crop) => ['x0', 'y0', 'x1', 'y1'].map((k) => crop[k].toFixed(4)).join(',');

// --- Zoom and pan ----------------------------------------------------------------
// The crop box is positioned in fractions of the frame, and pointer positions are
// measured on the frame, so the crop stays lined up however the frame is sized and moved.

// The stage, and the room for the picture when it fits (inside the stage's padding).
function stageBoxes() {
  const box = el.stage.getBoundingClientRect();
  const style = getComputedStyle(el.stage);
  return {
    stage: { w: box.width, h: box.height },
    avail: {
      w: Math.max(1, box.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)),
      h: Math.max(1, box.height - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)),
    },
  };
}

// Size and place the preview for the current zoom. When not zoomed in it fits the stage,
// also after the window is resized.
function fitFrame() {
  if (!previewSize) return;
  const { stage, avail } = stageBoxes();
  if (settings.zoomCrop && !settings.crop) {
    settings.zoomCrop = false; // the crop was reset: back to the whole frame
    saveSettings();
    view = null;
  }
  // Zoom to crop follows the crop, rotation, mirror and the size of the stage.
  if (settings.zoomCrop) view = cropView(displayCrop(), previewSize, avail, stage);
  else if (view) view = clampView(view, previewSize, avail, stage);
  const box = layout(view ?? fitView(previewSize, avail), previewSize, stage);
  Object.assign(el.frame.style, {
    width: `${Math.max(1, box.w)}px`, height: `${Math.max(1, box.h)}px`, left: `${box.left}px`, top: `${box.top}px`,
  });
  renderZoom();
}

function setView(next) {
  stopFollowingCrop();
  const { avail } = stageBoxes();
  view = isZoomed(next, previewSize, avail) ? next : null; // back to following the stage size
  fitFrame();
}

const stageCentre = () => {
  const { stage } = stageBoxes();
  return { x: stage.w / 2, y: stage.h / 2 };
};

function stagePoint(event) {
  const r = el.stage.getBoundingClientRect();
  return { x: event.clientX - r.left, y: event.clientY - r.top };
}

function zoomTo(scale, point = stageCentre()) {
  if (!previewSize || el.frame.hidden) return;
  const { stage, avail } = stageBoxes();
  setView(zoomAt(view ?? fitView(previewSize, avail), scale, point, previewSize, avail, stage));
}

const currentScale = () => view?.scale ?? fitScale(previewSize, stageBoxes().avail);

function zoomBy(factor, point) {
  if (previewSize) zoomTo(currentScale() * factor, point);
}

function zoomFit() {
  if (previewSize) setView(fitView(previewSize, stageBoxes().avail));
}

// Zoom to crop: the button and the C key both come here, so the button shows the state.
function setZoomCrop(on) {
  settings.zoomCrop = on && Boolean(settings.crop);
  saveSettings();
  if (!settings.zoomCrop) view = null; // back to the whole frame
  fitFrame();
  renderZoom();
}

// Zooming or moving by hand, or editing the crop, takes over from following the crop,
// starting from the view as it is.
function stopFollowingCrop() {
  if (!settings.zoomCrop) return;
  settings.zoomCrop = false;
  saveSettings();
  renderZoom();
}

function renderZoom() {
  el.zoomCrop.setAttribute('aria-pressed', String(settings.zoomCrop));
  el.zoomCrop.disabled = !settings.crop;
  if (!previewSize) return;
  const scale = currentScale();
  const fit = fitScale(previewSize, stageBoxes().avail);
  el.zoomLevel.value = `${Math.round(scale * 100)}%`;
  el.zoomIn.disabled = scale >= Math.max(fit, MAX_SCALE) * 0.999;
  el.zoomOut.disabled = el.zoomFit.disabled = !view;
  el.zoom100.disabled = Math.abs(scale - Math.max(1, fit)) < 0.001;
  el.stage.classList.toggle('zoomed', Boolean(view));
  el.stage.classList.toggle('pixels', scale >= 3); // close up, show the preview's real pixels
}

// Panning: drag with the middle button, or with the left button while Space is held.
let panning = null; // { pointerId, last: {x, y} }
let spaceHeld = false;
// Touch: two fingers pinch to zoom and move the picture.
const touches = new Map();
let pinch = null; // { view, distance, mid }

const touchList = () => [...touches.values()];
const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const midpoint = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

function bindZoom() {
  el.stage.addEventListener('wheel', (event) => {
    if (!previewSize || el.frame.hidden) return;
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1;
    // A trackpad pinch arrives as a wheel event with ctrlKey, in smaller steps.
    const speed = event.ctrlKey ? 0.01 : 0.0015;
    zoomBy(Math.exp(-event.deltaY * unit * speed), stagePoint(event));
  }, { passive: false });

  el.stage.addEventListener('pointerdown', (event) => {
    if (!previewSize || el.frame.hidden) return;
    if (event.pointerType === 'touch') {
      touches.set(event.pointerId, stagePoint(event));
      if (touches.size === 2) {
        cancelDrag(); // the first finger started a crop; it's a pinch after all
        const [a, b] = touchList();
        pinch = { view: view ?? fitView(previewSize, stageBoxes().avail), distance: distance(a, b), mid: midpoint(a, b) };
      }
      return;
    }
    if (event.button === 1 || (event.button === 0 && spaceHeld)) {
      panning = { pointerId: event.pointerId, last: stagePoint(event) };
      el.stage.setPointerCapture(event.pointerId);
      el.stage.classList.add('panning');
      event.preventDefault(); // no autoscroll or paste on middle-click
    }
  });

  el.stage.addEventListener('pointermove', (event) => {
    if (pinch && touches.has(event.pointerId)) {
      touches.set(event.pointerId, stagePoint(event));
      const [a, b] = touchList();
      const { stage, avail } = stageBoxes();
      const mid = midpoint(a, b);
      const zoomed = zoomAt(pinch.view, pinch.view.scale * (distance(a, b) / pinch.distance), pinch.mid, previewSize, avail, stage);
      setView(pan(zoomed, mid.x - pinch.mid.x, mid.y - pinch.mid.y, previewSize, avail, stage));
      return;
    }
    if (!panning || event.pointerId !== panning.pointerId) return;
    const point = stagePoint(event);
    const { stage, avail } = stageBoxes();
    if (view) setView(pan(view, point.x - panning.last.x, point.y - panning.last.y, previewSize, avail, stage));
    panning.last = point;
  });

  const release = (event) => {
    touches.delete(event.pointerId);
    if (touches.size < 2) pinch = null;
    if (panning?.pointerId === event.pointerId) {
      panning = null;
      el.stage.classList.remove('panning');
    }
  };
  el.stage.addEventListener('pointerup', release);
  el.stage.addEventListener('pointercancel', release);
  el.stage.addEventListener('auxclick', (event) => event.button === 1 && event.preventDefault());

  // Space turns a left drag into panning, unless a button or field has the focus (where
  // Space presses or types).
  document.addEventListener('keydown', (event) => {
    if (event.key !== ' ' || event.target.closest('button, input, select, textarea, a, [contenteditable]')) return;
    event.preventDefault(); // don't scroll the page
    if (!spaceHeld) {
      spaceHeld = true;
      el.stage.classList.add('space-pan');
    }
  });
  document.addEventListener('keyup', (event) => {
    if (event.key !== ' ') return;
    spaceHeld = false;
    el.stage.classList.remove('space-pan');
  });
  window.addEventListener('blur', () => {
    spaceHeld = false;
    el.stage.classList.remove('space-pan');
  });

  el.zoomIn.addEventListener('click', () => zoomBy(STEP));
  el.zoomOut.addEventListener('click', () => zoomBy(1 / STEP));
  el.zoomFit.addEventListener('click', zoomFit);
  el.zoom100.addEventListener('click', () => zoomTo(1));
  el.zoomCrop.addEventListener('click', () => setZoomCrop(!settings.zoomCrop));
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
  if (event.button !== 0 || spaceHeld || pinch) return; // panning or pinching instead
  stopFollowingCrop();
  const p = pointerPosition(event);
  const current = displayCrop();
  drag = current && inside(p, current) ? { mode: 'move', start: p, origin: current } : { mode: 'draw', start: p };
  el.frame.setPointerCapture(event.pointerId);
  event.preventDefault();
});

el.frame.addEventListener('pointermove', (event) => {
  if (!drag || pinch) return;
  drag.rect = dragRect(drag, pointerPosition(event));
  drawCrop(drag.rect);
});

// Forget a crop drag that turned out to be something else (a pinch).
function cancelDrag() {
  drag = null;
  drawCrop();
}

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

// The resolution slider steps through the scanner's resolutions: its value is an index.
let resolutions = [];

function renderResolutions(caps) {
  if (caps.resolutions.join(',') === resolutions.join(',')) return;
  resolutions = [...caps.resolutions];
  el.resolution.max = resolutions.length - 1;
  el.resolution.disabled = resolutions.length < 2;
  el.resolutionTicks.replaceChildren(...resolutions.map((r) => {
    const tick = document.createElement('span');
    tick.textContent = r;
    return tick;
  }));
  if (!resolutions.includes(Number(settings.resolution))) {
    settings.resolution = resolutions.reduce((a, b) => (Math.abs(b - 3600) < Math.abs(a - 3600) ? b : a));
  }
  renderResolution();
}

function renderResolution() {
  const index = resolutions.indexOf(Number(settings.resolution));
  el.resolution.value = index;
  el.resolutionOut.value = `${settings.resolution} dpi`;
  el.resolution.setAttribute('aria-valuetext', `${settings.resolution} dpi`);
  el.resolutionTicks.querySelectorAll('span').forEach((tick, i) => tick.classList.toggle('on', i === index));
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
  renderStock();
  renderSliders();
}

// The Brand / Film / ISO pickers. Each lists what the one before it allows.
function renderStock() {
  const { brand = '', film = '', iso = '' } = settings.stock ?? {};
  const fill = (select, values, chosen) => {
    select.replaceChildren(...values.map((v) => new Option(String(v), String(v))));
    select.value = String(chosen);
  };
  if (el.stockBrand.options.length === 1) el.stockBrand.append(...BRANDS.map((b) => new Option(b, b)));
  el.stockBrand.value = brand;
  fill(el.stockFilm, films(brand), film);
  fill(el.stockIso, isos(brand, film), iso);
  el.stockFilm.disabled = !brand;
  el.stockIso.disabled = isos(brand, film).length < 2;
  el.stockBrand.title = settings.stock ? `${stockName(settings.stock)}: ${FILM_NAMES[settings.film]} film` : 'The film’s brand. Any film: just choose the type above';
}

// Choose a stock (or null for any film). Its film type is chosen with it.
function setStock(stock) {
  settings.stock = stock;
  const film = stock ? filmType(stock.brand, stock.film) : settings.film;
  const filmChanged = film !== settings.film;
  settings.film = film;
  saveSettings();
  renderFilm();
  if (filmChanged) {
    renderLock();
    renderPreview();
  }
}

// Film type and slider values, as the server expects them.
function adjustments() {
  return { film: settings.film, ...Object.fromEntries(SLIDERS.map((name) => [name, settings[name]])) };
}

// A typed slider value: a whole number from -100 to 100, or null if it isn't a number yet.
function sliderValue(text) {
  const n = Number.parseFloat(text);
  return Number.isFinite(n) ? clamp(Math.round(n), -100, 100) : null;
}

function renderSliders() {
  const mono = settings.film === 'bw-negative';
  for (const name of SLIDERS) {
    const { input, out } = sliders[name];
    input.value = settings[name];
    // Leave the number alone while it's being typed, so "-" or "" isn't replaced mid-edit.
    if (document.activeElement !== out) out.value = settings[name];
    input.disabled = out.disabled = mono && COLOUR_SLIDERS.includes(name);
  }
  el.adjustReset.hidden = SLIDERS.every((name) => settings[name] === 0);
  renderPresets();
  el.adjustHint.textContent = mono
    ? 'Saturation, Warmth and Tint are for colour film.'
    : 'Double-click a slider to set it back to 0, or type a value.';
}

function setSliders(values) {
  Object.assign(settings, values);
  saveSettings();
  renderSliders();
  schedulePreview();
}

// --- Presets: named slider settings, kept on the server ---------------------------

const sameName = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();
// The sliders a film type uses: Saturation, Warmth and Tint do nothing for black & white.
const slidersFor = (film) => (film === 'bw-negative' ? SLIDERS.filter((s) => !COLOUR_SLIDERS.includes(s)) : SLIDERS);
const matches = (preset) => preset.film === settings.film && slidersFor(preset.film).every((s) => preset[s] === settings[s]);

// What a preset saves: the film type and the sliders it uses (the others as 0).
const presetValues = () => ({
  film: settings.film,
  ...Object.fromEntries(SLIDERS.map((s) => [s, slidersFor(settings.film).includes(s) ? settings[s] : 0])),
});

async function loadPresets() {
  try {
    ({ presets } = await api('/api/presets'));
  } catch {
    return; // keep what we have; the server will be polled again
  }
  renderPresets();
}

// The picker shows the preset the sliders are set to, if any, grouped by film type.
function renderPresets() {
  const current = presets.find((p) => matches(p) && sameName(p.name, chosenPreset)) ?? presets.find(matches);
  const groups = FILMS.map((film) => {
    const group = document.createElement('optgroup');
    group.label = FILM_NAMES[film][0].toUpperCase() + FILM_NAMES[film].slice(1);
    group.append(...presets.filter((p) => p.film === film).map((p) => new Option(p.name, p.name)));
    return group;
  }).filter((group) => group.children.length);
  el.preset.replaceChildren(new Option(presets.length ? 'Presets…' : 'No presets yet', ''), ...groups);
  el.preset.value = current?.name ?? '';
  el.presetActions.hidden = !current;
}

// A preset sets the sliders, and the film type it was made for. Locked colours stay as they are.
function applyPreset(name) {
  const preset = presets.find((p) => p.name === name);
  if (!preset) return;
  chosenPreset = preset.name;
  const filmChanged = preset.film !== settings.film;
  settings.film = preset.film;
  if (settings.stock && filmType(settings.stock.brand, settings.stock.film) !== settings.film) settings.stock = null;
  setSliders(Object.fromEntries(slidersFor(preset.film).map((s) => [s, preset[s]])));
  if (filmChanged) {
    renderFilm();
    renderLock();
  }
}

async function savePreset() {
  const name = window.prompt('Name for this preset, e.g. the film stock:', el.preset.value || chosenPreset)?.trim();
  if (!name) return;
  const existing = presets.find((p) => sameName(p.name, name));
  if (existing && !matches(existing) && !window.confirm(`Replace the preset “${existing.name}”?`)) return;
  try {
    const saved = await api(`/api/presets/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(presetValues()) });
    chosenPreset = saved.name;
    showToast(`Saved the preset “${saved.name}”.`);
  } catch (err) {
    showAlert(err.message);
  }
  loadPresets();
}

async function renamePreset() {
  const old = el.preset.value;
  if (!old) return;
  const name = window.prompt(`New name for “${old}”:`, old)?.trim();
  if (!name || name === old) return;
  try {
    chosenPreset = (await api(`/api/presets/${encodeURIComponent(old)}`, { method: 'PATCH', body: JSON.stringify({ name }) })).name;
  } catch (err) {
    showAlert(err.message);
  }
  loadPresets();
}

async function deletePreset() {
  const name = el.preset.value;
  if (!name || !window.confirm(`Delete the preset “${name}”? The sliders stay as they are.`)) return;
  try {
    await api(`/api/presets/${encodeURIComponent(name)}`, { method: 'DELETE' });
    showToast(`Deleted the preset “${name}”.`);
  } catch (err) {
    showAlert(err.message);
  }
  loadPresets();
}

// The number next to a slider can be typed in. The preview follows while typing; Enter
// or leaving the field tidies the number up, and Esc goes back to the value from before.
function bindSliderNumber(name) {
  const { out } = sliders[name];
  let before = 0;
  out.addEventListener('focus', () => { before = settings[name]; });
  out.addEventListener('input', () => {
    const value = sliderValue(out.value);
    if (value !== null && value !== settings[name]) setSliders({ [name]: value });
  });
  out.addEventListener('change', () => {
    setSliders({ [name]: sliderValue(out.value) ?? settings[name] });
    out.value = before = settings[name];
  });
  out.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      setSliders({ [name]: before });
      out.value = before;
      out.select();
      event.preventDefault();
    } else if (event.key === 'Enter') {
      out.value = before = settings[name];
      out.select();
    }
  });
}

let locking = false; // a lock request is on its way

// The button is pressed while a lock applies. A lock made for another film type is
// kept, but isn't used, and locking again replaces it.
function renderLock() {
  const lock = settings.lock;
  const active = activeLock();
  el.lock.setAttribute('aria-pressed', String(Boolean(active)));
  el.lock.title = active ? 'Unlock: measure the colours of each frame again' : 'Use the colours of this frame for the rest of the roll';
  el.lock.disabled = locking || (!active && !shownPreview); // locking needs a preview; unlocking doesn't
  let hint;
  if (active && lock.roll !== settings.roll) {
    hint = `Locked on roll “${lock.roll}”. Unlock if this is a different film.`;
  } else if (active) {
    hint = 'Every frame gets the colours of the frame you locked on. The sliders still work.';
  } else if (lock) {
    hint = `A lock for ${FILM_NAMES[lock.film]} film is kept but not used here. Locking replaces it.`;
  } else if (!shownPreview) {
    hint = 'Preview a frame with good colours, then lock them for the whole roll.';
  } else if (!settings.crop) {
    hint = 'Crop to the picture first, so the holder doesn’t count, then lock its colours for the rest of the roll.';
  } else {
    hint = 'Use this frame’s colours for the rest of the roll, so every frame matches.';
  }
  el.lockHint.textContent = hint;
  el.lockHint.classList.toggle('lock-warning', Boolean(lock) && (!active || lock.roll !== settings.roll));
}

async function toggleLock() {
  if (locking) return;
  if (activeLock()) {
    settings.lock = null;
  } else {
    // Remember what was measured, in case the film type or roll changes while waiting.
    const { film, roll } = settings;
    const params = new URLSearchParams({ film, preview: shownPreview });
    if (settings.crop) params.set('crop', cropParam(settings.crop));
    locking = true;
    renderLock();
    try {
      const { low, high } = await api(`/api/levels?${params}`);
      settings.lock = { film, roll, low, high };
    } catch (err) {
      showAlert(err.message);
      return;
    } finally {
      locking = false;
      renderLock();
    }
  }
  saveSettings();
  renderLock();
  renderPreview();
}

// Turn the picture by a multiple of 90°: clockwise if positive. The button and the R key both use this.
function rotateBy(degrees) {
  settings.rotate = (((settings.rotate + degrees) % 360) + 360) % 360;
  saveSettings();
  renderCropInfo();
  renderPreview(); // the crop box is redrawn once the rotated image arrives
}

// The button and the M key both go through here, so the button always shows the state.
function toggleMirror() {
  settings.mirror = !settings.mirror;
  renderMirror();
  saveSettings();
  renderPreview();
}

function renderMirror() {
  el.mirror.setAttribute('aria-pressed', String(settings.mirror));
}

function renderSharpen() {
  for (const b of el.sharpen.querySelectorAll('button')) {
    b.setAttribute('aria-checked', String(b.dataset.value === settings.sharpen));
    b.disabled = !settings.saveJpeg;
  }
}

function renderControls() {
  renderFilm();
  renderLock();
  renderMirror();
  el.roll.value = settings.roll;
  el.frameNo.value = settings.frame;
  el.saveJpeg.checked = settings.saveJpeg;
  el.saveTiff.checked = settings.saveTiff;
  renderSharpen();
  renderResolutions(FALLBACK_CAPS);
  renderCropInfo();
}

function bindControls() {
  for (const b of el.film.querySelectorAll('button')) {
    b.addEventListener('click', () => {
      settings.film = b.dataset.value;
      // A stock of another type no longer applies.
      if (settings.stock && filmType(settings.stock.brand, settings.stock.film) !== settings.film) settings.stock = null;
      saveSettings();
      renderFilm();
      renderLock();
      renderPreview();
    });
  }
  el.stockBrand.addEventListener('change', () => setStock(firstStock(el.stockBrand.value)));
  el.stockFilm.addEventListener('change', () => setStock(firstStock(settings.stock.brand, el.stockFilm.value)));
  el.stockIso.addEventListener('change', () => setStock({ ...settings.stock, iso: Number(el.stockIso.value) }));
  el.lock.addEventListener('click', toggleLock);
  for (const name of SLIDERS) {
    const { input } = sliders[name];
    input.addEventListener('input', () => setSliders({ [name]: Number(input.value) }));
    input.addEventListener('dblclick', () => setSliders({ [name]: 0 }));
    bindSliderNumber(name);
  }
  el.preset.addEventListener('change', () => applyPreset(el.preset.value));
  el.preset.addEventListener('focus', loadPresets); // pick up presets saved in another browser
  el.presetSave.addEventListener('click', savePreset);
  el.presetRename.addEventListener('click', renamePreset);
  el.presetDelete.addEventListener('click', deletePreset);
  el.adjustReset.addEventListener('click', () => setSliders(Object.fromEntries(SLIDERS.map((name) => [name, 0]))));
  el.rotate.addEventListener('click', () => rotateBy(90));
  el.mirror.addEventListener('click', toggleMirror);
  el.cropReset.addEventListener('click', () => {
    settings.crop = null;
    saveSettings();
    renderCropInfo();
    drawCrop();
    renderPreview();
  });
  el.resolution.addEventListener('input', () => {
    settings.resolution = resolutions[Number(el.resolution.value)] ?? settings.resolution;
    saveSettings();
    renderResolution();
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
    renderLock();
  });
  el.frameNo.addEventListener('change', () => {
    const n = Math.round(Number(el.frameNo.value));
    settings.frame = Number.isFinite(n) ? clamp(n, 0, 9999) : 1;
    el.frameNo.value = settings.frame;
    saveSettings();
  });
  el.saveJpeg.addEventListener('change', () => { settings.saveJpeg = el.saveJpeg.checked; saveSettings(); renderSharpen(); });
  el.saveTiff.addEventListener('change', () => { settings.saveTiff = el.saveTiff.checked; saveSettings(); });
  for (const b of el.sharpen.querySelectorAll('button')) {
    b.addEventListener('click', () => {
      settings.sharpen = b.dataset.value;
      saveSettings();
      renderSharpen();
    });
  }

  el.theme.addEventListener('change', () => setTheme(el.theme.value));
  prefersLight.addEventListener('change', () => applyTheme(el.theme.value));

  el.scanner.addEventListener('change', chooseScanner);
  el.btnPreview.addEventListener('click', () => startJob('preview'));
  el.btnScan.addEventListener('click', () => startJob('scan'));
  el.cancel.addEventListener('click', cancelJob);
  el.alertClose.addEventListener('click', hideAlert);

  bindLightbox();

  document.addEventListener('keydown', (event) => {
    if (el.lightbox.open) return; // the overlay has its own keys
    if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
    // Typing in a field shouldn't scan, but after moving a slider or ticking a box it should.
    if (event.target.closest('input:not([type=range], [type=checkbox]), select, textarea, [contenteditable]')) return;
    const key = event.key.toLowerCase();
    if (key === 'p') startJob('preview');
    else if (key === 's') startJob('scan');
    else if (key === 'm') toggleMirror();
    else if (key === 'r') rotateBy(event.shiftKey ? -90 : 90);
    else if (key === '+' || key === '=') zoomBy(STEP);
    else if (key === '-') zoomBy(1 / STEP);
    else if (key === '0') zoomFit();
    else if (key === '1') zoomTo(1);
    else if (key === 'c') setZoomCrop(!settings.zoomCrop);
    else return;
    event.preventDefault();
  });

  bindZoom();
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
      ...adjustments(),
      resolution: Number(settings.resolution),
      rotate: settings.rotate,
      mirror: settings.mirror,
      crop: settings.crop,
      levels: activeLock() && { low: settings.lock.low, high: settings.lock.high },
      stock: stockName(settings.stock),
      roll: settings.roll || 'roll',
      frame: Number(settings.frame) || 0,
      save_jpeg: settings.saveJpeg,
      save_tiff: settings.saveTiff,
      sharpen: settings.sharpen,
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
  shownScans = scans;
  el.scans.replaceChildren(...scans.map(scanCard));
  el.scansEmpty.hidden = scans.length > 0;
  if (el.lightbox.open) renderLightbox();
}

let shownScans = []; // the scans in the gallery, newest first

function scanCard(scan, index) {
  const card = document.createElement('figure');
  card.className = 'scan';
  const main = scan.files.jpeg ?? scan.files.tiff;
  const thumb = document.createElement('a');
  thumb.className = 'thumb';
  thumb.href = main.url;
  thumb.target = '_blank';
  thumb.rel = 'noopener';
  thumb.title = 'Open (Ctrl+click: new tab)';
  openOnPlainClick(thumb, index);
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
    else {
      a.target = '_blank';
      a.rel = 'noopener';
      openOnPlainClick(a, index);
    }
    files.append(a);
  }
  caption.append(name, files);
  card.append(thumb, caption);
  return card;
}

// --- Lightbox: a saved scan, full size, on top of the page -------------------------

let lightboxIndex = 0;
let lightboxOpener = null; // focused again when the lightbox closes

// A plain left click opens the lightbox. Middle-click, Ctrl/Cmd/Shift+click and "open
// in new tab" still follow the link, so a scan can always be opened in its own tab.
function openOnPlainClick(link, index) {
  link.addEventListener('click', (event) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    openLightbox(index, link);
  });
}

function openLightbox(index, opener) {
  lightboxIndex = index;
  lightboxOpener = opener;
  renderLightbox();
  el.lightbox.showModal(); // traps focus, makes the page inert and closes on Esc
  el.lightboxClose.focus();
}

function closeLightbox() {
  if (el.lightbox.open) el.lightbox.close();
}

function stepLightbox(delta) {
  const next = lightboxIndex + delta;
  if (next < 0 || next >= shownScans.length) return;
  lightboxIndex = next;
  renderLightbox();
}

function renderLightbox() {
  const scan = shownScans[lightboxIndex];
  if (!scan) {
    closeLightbox();
    return;
  }
  el.lightboxName.textContent = scan.name;
  el.lightboxName.title = `${scan.roll}/${scan.name}`;
  el.lightboxCount.textContent = `${lightboxIndex + 1} of ${shownScans.length}`;
  // Browsers can't show TIFFs, so a scan saved only as a raw TIFF shows its thumbnail.
  const src = scan.files.jpeg?.url ?? scan.thumb;
  el.lightboxImg.hidden = !src;
  if (src && el.lightboxImg.getAttribute('src') !== src) el.lightboxImg.src = src;
  el.lightboxImg.alt = scan.name;
  el.lightboxNote.hidden = Boolean(scan.files.jpeg);
  el.lightboxNote.textContent = src
    ? 'Only the raw TIFF was saved. This is its small preview.'
    : 'Only the raw TIFF was saved, which a browser can’t show. Download it below.';
  const links = [];
  for (const [kind, label] of [['jpeg', 'JPEG'], ['tiff', 'TIFF']]) {
    const file = scan.files[kind];
    if (!file) continue;
    const a = document.createElement('a');
    a.href = file.url;
    a.textContent = `${label} · ${megabytes(file.size)}`;
    if (kind === 'tiff') a.download = '';
    else { a.target = '_blank'; a.rel = 'noopener'; }
    links.push(a);
  }
  el.lightboxFiles.replaceChildren(...links);
  el.lightboxPrev.disabled = lightboxIndex === 0;
  el.lightboxNext.disabled = lightboxIndex === shownScans.length - 1;
}

function bindLightbox() {
  el.lightboxClose.addEventListener('click', closeLightbox);
  el.lightboxPrev.addEventListener('click', () => stepLightbox(-1));
  el.lightboxNext.addEventListener('click', () => stepLightbox(1));
  // A click on the dark area around the picture closes it.
  el.lightbox.addEventListener('click', (event) => {
    if (event.target === el.lightbox || event.target === el.lightboxStage) closeLightbox();
  });
  el.lightbox.addEventListener('keydown', (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    // Newest first: left goes to newer scans, right to older ones, as in the gallery.
    if (event.key === 'ArrowLeft') stepLightbox(-1);
    else if (event.key === 'ArrowRight') stepLightbox(1);
    else return;
    event.preventDefault();
  });
  el.lightbox.addEventListener('close', () => {
    lightboxOpener?.focus();
    lightboxOpener = null;
  });
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

applyTheme();
renderControls();
bindControls();
poll();
loadScans();
loadPresets();
