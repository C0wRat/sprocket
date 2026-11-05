// Run with: node --test tests/*.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { computeHistogram } from '../sprocket/static/histogram.js';

const rgba = (pixels) => new Uint8ClampedArray(pixels.flatMap(([r, g, b]) => [r, g, b, 255]));

test('counts each channel', () => {
  const hist = computeHistogram(rgba([[0, 128, 255], [10, 128, 250], [10, 20, 30]]));
  assert.equal(hist.pixels, 3);
  assert.equal(hist.r[10], 2);
  assert.equal(hist.g[128], 2);
  assert.equal(hist.b[255], 1);
  for (const channel of [hist.r, hist.g, hist.b]) assert.equal(channel.reduce((a, v) => a + v, 0), 3);
});

test('measures clipping in any channel, allowing for JPEG noise', () => {
  const hist = computeHistogram(rgba([[1, 100, 100], [100, 100, 254], [3, 100, 252], [0, 0, 255]]));
  assert.equal(hist.black, 2 / 4); // the first and the last pixel
  assert.equal(hist.white, 2 / 4); // the second and the last
});

test('an empty crop has no clipping', () => {
  const hist = computeHistogram(new Uint8ClampedArray(0));
  assert.deepEqual([hist.pixels, hist.black, hist.white], [0, 0, 0]);
});
