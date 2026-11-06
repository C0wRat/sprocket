// Run with: node --test tests/geometry.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { mapRect, migrateOrientation, toDisplay, toScanner } from '../sprocket/static/geometry.js';

// Same table as ORIENTATIONS in tests/test_imaging.py, which checks imaging.orient():
// where scanner point (0.2, 0.1) lands on screen.
const ORIENTATIONS = [
  [0, false, [0.2, 0.1]], [90, false, [0.9, 0.2]], [180, false, [0.8, 0.9]], [270, false, [0.1, 0.8]],
  [0, true, [0.8, 0.1]], [90, true, [0.1, 0.2]], [180, true, [0.2, 0.9]], [270, true, [0.9, 0.8]],
];

const near = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 1e-9);

test('toDisplay matches the server', () => {
  for (const [rotate, mirror, expected] of ORIENTATIONS) {
    assert.ok(near(toDisplay([0.2, 0.1], rotate, mirror), expected), `rotate ${rotate}, mirror ${mirror}`);
  }
});

test('toScanner undoes toDisplay', () => {
  for (const [rotate, mirror] of ORIENTATIONS) {
    for (const point of [[0.2, 0.1], [0.7, 0.4], [0, 1]]) {
      assert.ok(near(toScanner(toDisplay(point, rotate, mirror), rotate, mirror), point));
    }
  }
});

test('mirror flips left-right on screen at every rotation', () => {
  for (const rotate of [0, 90, 180, 270]) {
    const [x, y] = toDisplay([0.2, 0.1], rotate);
    assert.ok(near(toDisplay([0.2, 0.1], rotate, true), [1 - x, y]), `rotate ${rotate}`);
  }
});

test('mapRect keeps rectangles ordered', () => {
  const rect = mapRect({ x0: 0.1, y0: 0.2, x1: 0.5, y1: 0.6 }, (p) => toDisplay(p, 90, true));
  assert.ok(near([rect.x0, rect.y0, rect.x1, rect.y1], [0.2, 0.1, 0.6, 0.5]), JSON.stringify(rect));
});

// The old order: mirror left-right in scanner coordinates, then rotate.
function oldToDisplay([x, y], rotate, mirror) {
  return toDisplay([mirror ? 1 - x : x, y], rotate, false);
}

test('migrated settings show the picture as before', () => {
  for (const rotate of [0, 90, 180, 270]) {
    for (const mirror of [false, true]) {
      const migrated = migrateOrientation({ rotate, mirror });
      for (const point of [[0.2, 0.1], [0.7, 0.4]]) {
        assert.ok(
          near(toDisplay(point, migrated.rotate, migrated.mirror), oldToDisplay(point, rotate, mirror)),
          `rotate ${rotate}, mirror ${mirror}`,
        );
      }
    }
  }
});
