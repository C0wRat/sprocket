// The histogram of the preview inside the crop, for judging exposure and clipping.

// The preview is a JPEG, so clipped areas come back a level or two off pure black or white.
export const BLACK = 2; // a channel at or below this counts as clipped to black
export const WHITE = 253; // and at or above this, to white

// Count the pixels of RGBA data (as from getImageData) per level and channel.
export function computeHistogram(rgba) {
  const r = new Uint32Array(256);
  const g = new Uint32Array(256);
  const b = new Uint32Array(256);
  let black = 0;
  let white = 0;
  for (let i = 0; i < rgba.length; i += 4) {
    const R = rgba[i];
    const G = rgba[i + 1];
    const B = rgba[i + 2];
    r[R] += 1;
    g[G] += 1;
    b[B] += 1;
    if (R <= BLACK || G <= BLACK || B <= BLACK) black += 1;
    if (R >= WHITE || G >= WHITE || B >= WHITE) white += 1;
  }
  const pixels = rgba.length / 4;
  return { r, g, b, pixels, black: pixels ? black / pixels : 0, white: pixels ? white / pixels : 0 };
}

// The colours of the channels come from the theme (--hist-r and so on in style.css).
function colours(canvas) {
  const style = getComputedStyle(canvas);
  const get = (name) => style.getPropertyValue(name).trim();
  return { r: get('--hist-r'), g: get('--hist-g'), b: get('--hist-b'), grey: get('--hist-grey') };
}

// Draw the channels on top of each other; where they overlap they add up to white.
export function drawHistogram(canvas, hist, mono = false) {
  const palette = colours(canvas);
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  const channels = mono ? [[hist.g, palette.grey]] : [[hist.r, palette.r], [hist.g, palette.g], [hist.b, palette.b]];
  // Scale to the highest level that isn't pure black or white, so a clipped spike
  // doesn't flatten everything else; spikes are drawn full height.
  let peak = 1;
  for (const [bins] of channels) for (let i = 1; i < 255; i += 1) peak = Math.max(peak, bins[i]);
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = mono ? 0.8 : 0.85;
  for (const [bins, colour] of channels) {
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let i = 0; i < 256; i += 1) ctx.lineTo((i / 255) * w, h - Math.min(1, bins[i] / peak) * (h - 2));
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = colour;
    ctx.fill();
  }
}
