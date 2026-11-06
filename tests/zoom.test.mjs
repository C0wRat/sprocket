// Run with: node --test tests/zoom.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { MAX_SCALE, clampView, cropView, fitScale, fitView, isZoomed, layout, pan, zoomAt } from '../sprocket/static/zoom.js';

const image = { w: 860, h: 590 }; // a 600 dpi preview of the 8100's scan area
const stage = { w: 1000, h: 700 };
const avail = { w: 944, h: 644 }; // the stage minus 28 px of padding on each side

const close = (a, b, tolerance = 1e-6) => Math.abs(a - b) < tolerance;

test('fitting puts the whole picture in the middle of the stage', () => {
  const view = fitView(image, avail);
  assert.ok(close(view.scale, Math.min(944 / 860, 644 / 590)));
  const box = layout(view, image, stage);
  assert.ok(close(box.left + box.w / 2, stage.w / 2));
  assert.ok(close(box.top + box.h / 2, stage.h / 2));
  assert.ok(box.w <= avail.w + 1e-9 && box.h <= avail.h + 1e-9);
  assert.equal(isZoomed(view, image, avail), false);
});

test('zooming keeps the point under the cursor in place', () => {
  const start = fitView(image, avail);
  const cursor = { x: 300, y: 250 };
  const before = layout(start, image, stage);
  const fx = (cursor.x - before.left) / before.w;
  const fy = (cursor.y - before.top) / before.h;
  const view = zoomAt(start, start.scale * 3, cursor, image, avail, stage);
  const after = layout(view, image, stage);
  assert.ok(close(after.left + fx * after.w, cursor.x));
  assert.ok(close(after.top + fy * after.h, cursor.y));
  assert.ok(isZoomed(view, image, avail));
});

test('zoom stays between fitting and the maximum', () => {
  const start = fitView(image, avail);
  const centre = { x: 500, y: 350 };
  assert.ok(close(zoomAt(start, 0.01, centre, image, avail, stage).scale, fitScale(image, avail)));
  assert.equal(zoomAt(start, 1000, centre, image, avail, stage).scale, MAX_SCALE);
});

test('the picture can be panned but not pulled off the stage', () => {
  const zoomed = clampView({ scale: 4, cx: 0.5, cy: 0.5 }, image, avail, stage);
  const moved = pan(zoomed, 100, -50, image, avail, stage);
  assert.ok(close(moved.cx, 0.5 - 100 / (860 * 4)));
  assert.ok(close(moved.cy, 0.5 + 50 / (590 * 4)));
  const far = pan(zoomed, 1e6, 1e6, image, avail, stage);
  const box = layout(far, image, stage);
  assert.ok(close(box.left, 0) && close(box.top, 0), JSON.stringify(box)); // top-left corner reached, no gap
});

test('an axis smaller than the stage stays centred', () => {
  const wide = { w: 2000, h: 200 };
  const view = clampView({ scale: 0.6, cx: 0.9, cy: 0.1 }, wide, avail, stage);
  assert.equal(view.cy, 0.5); // 120 px tall in a 700 px stage
  assert.ok(view.cx < 0.9); // 1200 px wide: can pan, but only so far
});

// Where a rectangle (fractions of the picture) lands on the stage, in pixels.
function onStage(rect, view) {
  const box = layout(view, image, stage);
  return { x0: box.left + rect.x0 * box.w, y0: box.top + rect.y0 * box.h, x1: box.left + rect.x1 * box.w, y1: box.top + rect.y1 * box.h };
}

test('zooming to a crop fills the room for the picture with it', () => {
  const rect = { x0: 0.3, y0: 0.4, x1: 0.6, y1: 0.6 };
  const view = cropView(rect, image, avail, stage);
  const box = onStage(rect, view);
  // As big as fits in the padded area, and in the middle of the stage.
  assert.ok(close(box.x1 - box.x0, avail.w) || close(box.y1 - box.y0, avail.h), JSON.stringify(box));
  assert.ok(box.x1 - box.x0 <= avail.w + 1e-6 && box.y1 - box.y0 <= avail.h + 1e-6);
  assert.ok(close((box.x0 + box.x1) / 2, stage.w / 2) && close((box.y0 + box.y1) / 2, stage.h / 2));
});

test('a crop at the edge of the picture is still all on the stage', () => {
  const rect = { x0: 0, y0: 0.8, x1: 0.25, y1: 1 };
  const box = onStage(rect, cropView(rect, image, avail, stage));
  assert.ok(box.x0 >= -1e-6 && box.y0 >= -1e-6 && box.x1 <= stage.w + 1e-6 && box.y1 <= stage.h + 1e-6, JSON.stringify(box));
});

test('a tiny crop zooms no further than the maximum', () => {
  assert.equal(cropView({ x0: 0.5, y0: 0.5, x1: 0.501, y1: 0.501 }, image, avail, stage).scale, MAX_SCALE);
});
