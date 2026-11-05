"""Turn raw scans (16-bit linear RGB straight from the scanner) into finished images."""

import io
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

FILM_TYPES = ("color-negative", "slide", "bw-negative")

# Midtone contrast for negatives: exponent applied after inverting in density space.
NEGATIVE_GAMMA = 1.3
# Percentage of pixels allowed to clip to pure black, and to pure white.
CLIP_PERCENT = 0.1
# Levels are measured on at most this many pixels.
STATS_SAMPLES = 1_000_000
# Big images are converted a strip of about this many pixels at a time, which keeps the
# memory needed on top of the raw scan and the finished picture small.
STRIP_PIXELS = 1 << 20


@dataclass(frozen=True)
class Adjust:
    film: str = "color-negative"
    brightness: float = 0.0  # -1 (darker) to +1 (brighter)
    rotate: int = 0  # clockwise degrees: 0, 90, 180 or 270
    mirror: bool = False


@dataclass(frozen=True)
class Levels:
    """The darkest and brightest raw values that count, per channel (R, G, B).

    They set the conversion's end points. For a negative the brightest raw value is the
    film base, which becomes black, and the darkest is the densest highlight, which
    becomes white; a slide maps them the other way round. Each frame is measured on its
    own unless levels are given.
    """

    low: tuple[float, float, float]
    high: tuple[float, float, float]


def orient(image: np.ndarray, rotate: int = 0, mirror: bool = False) -> np.ndarray:
    """Mirror left-right, then rotate clockwise. Returns a view.

    The web UI maps crop rectangles with the same convention (static/geometry.js).
    """
    if mirror:
        image = image[:, ::-1]
    k = (rotate // 90) % 4
    return np.rot90(image, k=-k) if k else image


def measure_levels(
    image: np.ndarray,
    film: str,
    crop: tuple[float, float, float, float] | None = None,
    margin: float | None = None,
) -> Levels:
    """Measure a frame's levels, ignoring the brightest and darkest CLIP_PERCENT %.

    Only the part inside `crop` (x0, y0, x1, y1 as fractions of the image; default: all
    of it) counts, shrunk by `margin` on every side so the film holder's edges and the
    clear gaps between frames don't skew the result.
    """
    if margin is None:
        margin = 0.02 if crop else 0.04
    sample = _sample(image, crop, margin)
    clip = (CLIP_PERCENT, 100 - CLIP_PERCENT)
    if film == "color-negative":
        # Per channel: this is what removes the orange mask.
        low, high = np.percentile(sample.reshape(-1, 3), clip, axis=0)
    elif film == "bw-negative":
        low, high = np.repeat(np.percentile(_mono(sample), clip), 3).reshape(2, 3)
    elif film == "slide":
        # Shared by all channels, which keeps the slide's own colour balance.
        low, high = np.repeat(np.percentile(sample, clip), 3).reshape(2, 3)
    else:
        raise ValueError(f"unknown film type {film!r}")
    return Levels(tuple(float(v) for v in low), tuple(float(v) for v in high))


def render(
    image: np.ndarray,
    adjust: Adjust,
    levels: Levels | None = None,
    *,
    stats_crop: tuple[float, float, float, float] | None = None,
    margin: float | None = None,
) -> np.ndarray:
    """Raw scan -> oriented 8-bit positive: (H, W, 3), or (H, W) for black & white film.

    Without `levels` they are measured on this image, inside `stats_crop` shrunk by
    `margin` (see measure_levels()).
    """
    if levels is None:
        levels = measure_levels(image, adjust.film, stats_crop, margin)
    luts = [np.rint(curve * 255).astype(np.uint8) for curve in _curves(adjust, levels)]
    height, width = image.shape[:2]
    mono = adjust.film == "bw-negative"
    out = np.empty((height, width) if mono else (height, width, 3), dtype=np.uint8)
    for rows in _strips(height, width):
        strip = image[rows]
        if mono:
            out[rows] = luts[0][_mono(strip)]
        else:
            for c in range(3):
                out[rows, :, c] = luts[c][strip[..., c]]
    return orient(out, adjust.rotate, adjust.mirror)


def _strips(height: int, width: int):
    """Row slices covering an image a strip of about STRIP_PIXELS pixels at a time."""
    rows = max(1, STRIP_PIXELS // max(width, 1))
    for top in range(0, height, rows):
        yield slice(top, min(top + rows, height))


def _sample(image: np.ndarray, crop, margin: float) -> np.ndarray:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = crop or (0.0, 0.0, 1.0, 1.0)
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    left, right = int((x0 + mx) * w), math.ceil((x1 - mx) * w)
    top, bottom = int((y0 + my) * h), math.ceil((y1 - my) * h)
    region = image[top : max(bottom, top + 1), left : max(right, left + 1)]
    step = max(1, int(math.sqrt(region.shape[0] * region.shape[1] / STATS_SAMPLES)))
    return region[::step, ::step]


def _mono(image: np.ndarray) -> np.ndarray:
    """Weighted average of the linear RGB channels, for black & white film."""
    r, g, b = (image[..., c].astype(np.uint32) for c in range(3))
    return ((r + 2 * g + b) >> 2).astype(np.uint16)


def _curves(adjust: Adjust, levels: Levels) -> list[np.ndarray]:
    """One curve per output channel, from raw value (0-65535) to output (0-1)."""
    exponent = 2.0 ** -adjust.brightness
    if adjust.film in ("color-negative", "bw-negative"):
        channels = 1 if adjust.film == "bw-negative" else 3
        return [
            _negative_curve(levels.low[c], levels.high[c]) ** (NEGATIVE_GAMMA * exponent) for c in range(channels)
        ]
    if adjust.film == "slide":
        return [_slide_curve(levels.low[c], levels.high[c]) ** exponent for c in range(3)]
    raise ValueError(f"unknown film type {adjust.film!r}")


def _negative_curve(low: float, high: float) -> np.ndarray:
    """Invert a negative in log (density) space: `high` (the film base) -> 0, `low` -> 1."""
    lo = max(low, 1.0)
    hi = max(high, lo * 1.05)
    v = np.maximum(np.arange(65536, dtype=np.float64), 1.0)
    return np.clip(np.log(hi / v) / np.log(hi / lo), 0.0, 1.0)


def _slide_curve(low: float, high: float) -> np.ndarray:
    """Positive film: stretch `low`..`high` to 0..1, then sRGB encoding."""
    hi = max(high, low + 1.0)
    linear = np.clip((np.arange(65536, dtype=np.float64) - low) / (hi - low), 0.0, 1.0)
    encoded = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)
    return np.clip(encoded, 0.0, 1.0)


def encode_jpeg(image: np.ndarray, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(image)).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def save_jpeg(path: Path, image: np.ndarray, dpi: int, quality: int = 95) -> None:
    tmp = path.with_name(path.name + ".part")
    Image.fromarray(np.ascontiguousarray(image)).save(
        tmp, format="JPEG", quality=quality, subsampling=0, dpi=(dpi, dpi)
    )
    os.replace(tmp, path)


def save_tiff(path: Path, image: np.ndarray, dpi: int) -> None:
    """Save a 16-bit image as an uncompressed TIFF, which every editor can open."""
    tmp = path.with_name(path.name + ".part")
    photometric = "rgb" if image.ndim == 3 else "minisblack"
    tifffile.imwrite(
        tmp,
        np.ascontiguousarray(image),
        photometric=photometric,
        resolution=(dpi, dpi),
        resolutionunit="INCH",
        software="Sprocket",
        metadata=None,
    )
    os.replace(tmp, path)


def save_thumbnail(path: Path, image: np.ndarray, size: int = 480) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = image.shape[:2]
    step = max(1, min(h, w) // (2 * size))
    thumb = Image.fromarray(np.ascontiguousarray(image[::step, ::step]))
    thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
    thumb.save(path, format="JPEG", quality=85)
