// Zooming and panning the preview. A view is { scale, cx, cy }: screen (CSS) pixels per
// preview pixel, and the point of the picture at the centre of the stage, as fractions
// (0-1) of its width and height. The crop box is drawn in fractions of the picture, so
// it stays lined up whatever the view.

// The most the preview can be enlarged: screen pixels per preview pixel. The preview is
// only 600 dpi, so beyond this there's nothing more to see.
export const MAX_SCALE = 8;
// Each zoom button press or key press zooms by this much.
export const STEP = 1.25;

// The scale at which the whole picture fits in `avail` (the stage minus its padding).
export function fitScale(image, avail) {
  return Math.max(1e-6, Math.min(avail.w / image.w, avail.h / image.h));
}

export const fitView = (image, avail) => ({ scale: fitScale(image, avail), cx: 0.5, cy: 0.5 });

// Keep the scale between fitting and MAX_SCALE, and the picture over the stage: an axis
// that is smaller than the stage is centred, a larger one can't be pulled off screen.
export function clampView(view, image, avail, stage) {
  const fit = fitScale(image, avail);
  const scale = Math.min(Math.max(view.scale, fit), Math.max(fit, MAX_SCALE));
  const axis = (c, size, room) => {
    if (size <= room) return 0.5;
    const half = room / 2 / size;
    return Math.min(Math.max(c, half), 1 - half);
  };
  return {
    scale,
    cx: axis(view.cx, image.w * scale, stage.w),
    cy: axis(view.cy, image.h * scale, stage.h),
  };
}

// Where the picture goes on the stage: its size and its top-left corner, in pixels.
export function layout(view, image, stage) {
  const w = image.w * view.scale;
  const h = image.h * view.scale;
  return { w, h, left: stage.w / 2 - view.cx * w, top: stage.h / 2 - view.cy * h };
}

// Zoom to `scale`, keeping the part of the picture under `point` (stage pixels) in place.
export function zoomAt(view, scale, point, image, avail, stage) {
  const before = layout(view, image, stage);
  const fx = (point.x - before.left) / before.w;
  const fy = (point.y - before.top) / before.h;
  const next = clampView({ ...view, scale }, image, avail, stage);
  const w = image.w * next.scale;
  const h = image.h * next.scale;
  // Solve for the centre that puts (fx, fy) back under the point.
  const cx = (stage.w / 2 - (point.x - fx * w)) / w;
  const cy = (stage.h / 2 - (point.y - fy * h)) / h;
  return clampView({ scale: next.scale, cx, cy }, image, avail, stage);
}

// Move the picture by (dx, dy) stage pixels, as when dragging it.
export function pan(view, dx, dy, image, avail, stage) {
  const w = image.w * view.scale;
  const h = image.h * view.scale;
  return clampView({ ...view, cx: view.cx - dx / w, cy: view.cy - dy / h }, image, avail, stage);
}

// The view that fills the room for the picture with `rect` (fractions of the picture,
// as drawn), centred on it. Near an edge of the picture it shifts to keep the stage
// covered, which still shows all of the rectangle.
export function cropView(rect, image, avail, stage) {
  const w = Math.max(1e-6, (rect.x1 - rect.x0) * image.w);
  const h = Math.max(1e-6, (rect.y1 - rect.y0) * image.h);
  const view = { scale: Math.min(avail.w / w, avail.h / h), cx: (rect.x0 + rect.x1) / 2, cy: (rect.y0 + rect.y1) / 2 };
  return clampView(view, image, avail, stage);
}

// Is the view zoomed in beyond fitting the whole picture?
export const isZoomed = (view, image, avail) => view.scale > fitScale(image, avail) * 1.001;
