// Crop rectangles are stored in *scanner* coordinates: fractions (0-1) of the full
// scan area as the scanner sees it. They are drawn on the oriented preview, which the
// server mirrors left-right first and then rotates clockwise (orient() in
// sprocket/imaging.py). These helpers convert points between the two.

export const clamp = (value, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, value));

const normal = (rotate) => ((rotate % 360) + 360) % 360;

export function toDisplay([x, y], rotate = 0, mirror = false) {
  if (mirror) x = 1 - x;
  switch (normal(rotate)) {
    case 90: return [1 - y, x];
    case 180: return [1 - x, 1 - y];
    case 270: return [y, 1 - x];
    default: return [x, y];
  }
}

export function toScanner([x, y], rotate = 0, mirror = false) {
  let point;
  switch (normal(rotate)) {
    case 90: point = [y, 1 - x]; break;
    case 180: point = [1 - x, 1 - y]; break;
    case 270: point = [1 - y, x]; break;
    default: point = [x, y];
  }
  if (mirror) point[0] = 1 - point[0];
  return point;
}

// Map a rectangle {x0, y0, x1, y1} through a point function, keeping it ordered.
export function mapRect(rect, fn) {
  const [ax, ay] = fn([rect.x0, rect.y0]);
  const [bx, by] = fn([rect.x1, rect.y1]);
  return { x0: Math.min(ax, bx), y0: Math.min(ay, by), x1: Math.max(ax, bx), y1: Math.max(ay, by) };
}
