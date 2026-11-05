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


@dataclass(frozen=True)
class Adjust:
    film: str = "color-negative"
    brightness: float = 0.0  # -1 (darker) to +1 (brighter)
    rotate: int = 0  # clockwise degrees: 0, 90, 180 or 270
    mirror: bool = False


def orient(image: np.ndarray, rotate: int = 0, mirror: bool = False) -> np.ndarray:
    """Mirror left-right, then rotate clockwise. Returns a view.

    The web UI maps crop rectangles with the same convention (static/geometry.js).
    """
    if mirror:
        image = image[:, ::-1]
    k = (rotate // 90) % 4
    return np.rot90(image, k=-k) if k else image


def render(
    image: np.ndarray,
    adjust: Adjust,
    *,
    stats_crop: tuple[float, float, float, float] | None = None,
    margin: float | None = None,
) -> np.ndarray:
    """Raw scan -> oriented 8-bit positive: (H, W, 3), or (H, W) for black & white film.

    Levels are measured inside `stats_crop` (x0, y0, x1, y1 as fractions of the image;
    default: all of it), shrunk by `margin` on every side so the film holder's edges
    and the clear gaps between frames don't skew them.
    """
    if margin is None:
        margin = 0.02 if stats_crop else 0.04
    sample = _sample(image, stats_crop, margin)
    exponent = 2.0 ** -adjust.brightness
    if adjust.film == "color-negative":
        out = np.empty(image.shape, dtype=np.uint8)
        for c in range(3):
            out[..., c] = _negative_lut(sample[..., c], NEGATIVE_GAMMA * exponent)[image[..., c]]
    elif adjust.film == "bw-negative":
        out = _negative_lut(_mono(sample), NEGATIVE_GAMMA * exponent)[_mono(image)]
    elif adjust.film == "slide":
        out = _slide_lut(sample, exponent)[image]
    else:
        raise ValueError(f"unknown film type {adjust.film!r}")
    return orient(out, adjust.rotate, adjust.mirror)


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


def _negative_lut(values: np.ndarray, gamma: float) -> np.ndarray:
    """Lookup table that inverts a negative.

    The inversion happens in log (density) space, between the clearest part of the
    frame (film base -> black) and the densest (brightest highlight -> white). Done
    per channel, this also removes the orange mask of colour negative film.
    """
    lo, hi = np.percentile(values, (CLIP_PERCENT, 100 - CLIP_PERCENT))
    lo = max(float(lo), 1.0)
    hi = max(float(hi), lo * 1.05)
    v = np.arange(65536, dtype=np.float64)
    v[0] = 1.0
    positive = np.clip(np.log(hi / v) / np.log(hi / lo), 0.0, 1.0) ** gamma
    return np.rint(positive * 255).astype(np.uint8)


def _slide_lut(values: np.ndarray, gamma: float) -> np.ndarray:
    """Lookup table for slides (positive film): black and white points shared by all
    channels, which keeps the slide's own colour balance, then sRGB encoding."""
    lo, hi = np.percentile(values, (CLIP_PERCENT, 100 - CLIP_PERCENT))
    hi = max(float(hi), float(lo) + 1.0)
    linear = np.clip((np.arange(65536, dtype=np.float64) - lo) / (hi - lo), 0.0, 1.0)
    encoded = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - 0.055)
    return np.rint(np.clip(encoded, 0.0, 1.0) ** gamma * 255).astype(np.uint8)


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
