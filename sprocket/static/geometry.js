// Crop rectangles are stored in *scanner* coordinates: fractions (0-1) of the full
// scan area as the scanner sees it. They are drawn on the oriented preview, which the
// server rotates clockwise first and then mirrors left-right (orient() in
// sprocket/imaging.py), so Mirror always flips the picture left-right as shown. These
// helpers convert points between the two.

export const clamp = (value, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, value));

const normal = (rotate) => ((rotate % 360) + 360) % 360;

export function toDisplay([x, y], rotate = 0, mirror = false) {
  let point;
  switch (normal(rotate)) {
    case 90: point = [1 - y, x]; break;
    case 180: point = [1 - x, 1 - y]; break;
    case 270: point = [y, 1 - x]; break;
    default: point = [x, y];
  }
  if (mirror) point[0] = 1 - point[0];
  return point;
}

export function toScanner([x, y], rotate = 0, mirror = false) {
  if (mirror) x = 1 - x;
  switch (normal(rotate)) {
    case 90: return [y, 1 - x];
    case 180: return [1 - x, 1 - y];
    case 270: return [1 - y, x];
    default: return [x, y];
  }
}

// Settings saved before Mirror was applied after rotating (orientation 1) mirrored
// first. At 90° and 270° that is the same picture as the new order with another 180°
// of rotation, so turning it by 180° keeps an existing setup looking as it did.
export function migrateOrientation({ rotate, mirror }) {
  return { rotate: mirror && normal(rotate) % 180 ? normal(rotate + 180) : normal(rotate), mirror };
}

export function mapRect(rect, fn) {
  const [ax, ay] = fn([rect.x0, rect.y0]);
  const [bx, by] = fn([rect.x1, rect.y1]);
  return { x0: Math.min(ax, bx), y0: Math.min(ay, by), x1: Math.max(ax, bx), y1: Math.max(ay, by) };
}
