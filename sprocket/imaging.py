"""Turn raw scans (16-bit linear RGB straight from the scanner) into finished images."""

import io
import math
import os
import re
import unicodedata
from dataclasses import KW_ONLY, dataclass
from datetime import datetime
from functools import cache
from pathlib import Path

import numpy as np
import tifffile
from PIL import ExifTags, Image, ImageCms, ImageFilter

from . import __version__

FILM_TYPES = ("color-negative", "slide", "bw-negative")
FILM_NAMES = {"color-negative": "colour negative", "slide": "slide", "bw-negative": "black & white negative"}
SOFTWARE = f"Sprocket {__version__}"
# Unsharp mask strength (percent) for each Sharpen setting.
SHARPEN_PERCENT = {"low": 60, "medium": 110, "high": 180}
# Differences smaller than this (out of 255) are left alone, so that film grain and noise
# in smooth areas are not sharpened as well.
SHARPEN_THRESHOLD = 3
# Sharpening works on bigger strips than the conversion, as each strip needs some rows
# of overlap with its neighbours.
SHARPEN_STRIP_PIXELS = 1 << 22

# Midtone contrast for negatives: exponent applied after inverting in density space.
NEGATIVE_GAMMA = 1.3
# Percentage of pixels allowed to clip to pure black, and to pure white.
CLIP_PERCENT = 0.1
# Levels are measured on at most this many pixels.
STATS_SAMPLES = 1_000_000
# Big images are converted a strip of about this many pixels at a time, which keeps the
# memory needed on top of the raw scan and the finished picture small (and the
# saturation step fast, as each strip fits in the CPU's cache).
STRIP_PIXELS = 1 << 18
# How strongly Warmth and Tint shift a channel's midtones at the ends of their sliders,
# in the same units as Brightness.
COLOUR_RANGE = 1.0
# How much red, green and blue add to brightness: Rec. 601 luma weights, the ones
# Pillow's ImageEnhance.Color uses.
LUMA_WEIGHTS = (0.299, 0.587, 0.114)
LUMA = np.array(LUMA_WEIGHTS, dtype=np.float32)


@dataclass(frozen=True)
class Adjust:
    """How a raw scan becomes a picture.

    The sliders run from -1 to +1; at 0 they leave the automatic conversion alone.
    Brightness, Warmth and Tint bend the midtones and keep black and white where they
    are, so they never clip.
    """

    film: str = "color-negative"
    _: KW_ONLY
    brightness: float = 0.0  # darker .. brighter
    contrast: float = 0.0  # flatter .. punchier: an S-curve through mid grey
    saturation: float = 0.0  # grey .. twice as colourful
    warmth: float = 0.0  # bluer .. more orange
    tint: float = 0.0  # greener .. more magenta
    rotate: int = 0  # clockwise degrees: 0, 90, 180 or 270
    mirror: bool = False


@dataclass(frozen=True)
class ScanInfo:
    """What the saved files record about a scan in their metadata."""

    scanned: datetime
    make: str = ""  # of the scanner, e.g. "Plustek"; empty if not known
    model: str = ""  # e.g. "Plustek OpticFilm 8100"
    description: str = ""  # e.g. "Trip 1, frame 3, colour negative"


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
    """Rotate clockwise, then mirror left-right. Returns a view.

    Mirroring last means it always flips the picture left-right as it is shown, whatever
    the rotation. The web UI maps crop rectangles with the same convention
    (static/geometry.js).
    """
    k = (rotate // 90) % 4
    if k:
        image = np.rot90(image, k=-k)
    return image[:, ::-1] if mirror else image


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
    curves = _curves(adjust, levels)
    height, width = image.shape[:2]
    if adjust.film == "bw-negative":
        lut = np.rint(curves[0] * 255).astype(np.uint8)
        out = np.empty((height, width), dtype=np.uint8)
        for rows in _strips(height, width):
            out[rows] = lut[_mono(image[rows])]
    elif adjust.saturation == 0:
        luts = [np.rint(curve * 255).astype(np.uint8) for curve in curves]
        out = np.empty((height, width, 3), dtype=np.uint8)
        for rows in _strips(height, width):
            for c in range(3):
                out[rows, :, c] = luts[c][image[rows, :, c]]
    else:
        # Saturation mixes the channels, so it works on unrounded values.
        luts = [(curve * 255).astype(np.float32) for curve in curves]
        out = np.empty((height, width, 3), dtype=np.uint8)
        for rows in _strips(height, width):
            rgb = np.stack([luts[c][image[rows, :, c]] for c in range(3)], axis=-1)
            out[rows] = _saturate(rgb, 1.0 + adjust.saturation)
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
    if adjust.film in ("color-negative", "bw-negative"):
        base, gamma = _negative_curve, NEGATIVE_GAMMA
    elif adjust.film == "slide":
        base, gamma = _slide_curve, 1.0
    else:
        raise ValueError(f"unknown film type {adjust.film!r}")
    shifts = (0.0,) if adjust.film == "bw-negative" else _colour_shifts(adjust.warmth, adjust.tint)
    return [
        _contrast(base(levels.low[c], levels.high[c]) ** (gamma * 2.0 ** -(adjust.brightness + shift)), adjust.contrast)
        for c, shift in enumerate(shifts)
    ]


def _colour_shifts(warmth: float, tint: float) -> tuple[float, float, float]:
    """Brightness shifts for the red, green and blue midtones.

    Warmth moves red against blue, and Tint red and blue against green, by `COLOUR_RANGE`
    between them at the ends of the sliders. Each is split by how much the channels add
    to brightness (luma), so the colour changes and the brightness hardly does.
    """
    r, g, b = LUMA_WEIGHTS
    w = warmth * COLOUR_RANGE
    t = tint * COLOUR_RANGE
    return (w * b / (r + b) + t * g, -t * (r + b), -w * r / (r + b) + t * g)


def _contrast(curve: np.ndarray, amount: float) -> np.ndarray:
    """A curve through mid grey, steeper there for amount > 0 (up to twice) and flatter
    for amount < 0 (down to half). Black and white stay where they are.

    More contrast is an S-curve that flattens towards black and white. Less contrast is
    a cubic that is at most twice as steep there, so it lifts the deepest shadows only
    gently and doesn't bring up their noise.
    """
    if amount > 0:
        k = 2.0**amount
        rise = curve**k
        return rise / (rise + (1.0 - curve) ** k)
    if amount < 0:
        return curve + amount * curve * (1.0 - curve) * (2.0 * curve - 1.0)
    return curve


def _saturate(rgb: np.ndarray, factor: float) -> np.ndarray:
    """Scale the colourfulness of float RGB (0-255) in place; returns it as 8-bit."""
    luma = (rgb @ LUMA)[..., None]
    rgb -= luma
    rgb *= factor
    rgb += luma
    np.clip(rgb, 0, 255, out=rgb)
    return np.rint(rgb, out=rgb).astype(np.uint8)


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


def sharpen(image: np.ndarray, strength: str, dpi: int) -> None:
    """Unsharp mask, in place: strength "off", "low", "medium" or "high".

    The radius grows with the resolution (1 px at 3600 dpi), so a setting looks alike at
    every resolution. Works a strip at a time with a few rows of overlap, which gives
    the same result as filtering the whole picture at once with far less memory.
    """
    if strength == "off":
        return
    radius = min(max(dpi / 3600, 0.5), 2.0)
    unsharp = ImageFilter.UnsharpMask(radius=radius, percent=SHARPEN_PERCENT[strength], threshold=SHARPEN_THRESHOLD)
    pad = math.ceil(4 * radius) + 2  # rows of context each side: more than the blur reaches
    height, width = image.shape[:2]
    rows = max(SHARPEN_STRIP_PIXELS // max(width, 1), pad)
    above = image[:0].copy()  # the unsharpened rows just above the current strip
    for top in range(0, height, rows):
        bottom = min(top + rows, height)
        source = np.concatenate((above, image[top : min(bottom + pad, height)]))
        start = len(above)
        above = image[max(top, bottom - pad) : bottom].copy()  # before they are overwritten
        sharpened = np.asarray(Image.fromarray(source).filter(unsharp))
        image[top:bottom] = sharpened[start : start + bottom - top]


def encode_jpeg(image: np.ndarray, quality: int = 88) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(image)).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def save_jpeg(path: Path, image: np.ndarray, dpi: int, quality: int = 95, info: ScanInfo | None = None) -> None:
    """Save the finished picture. Colour JPEGs are tagged as sRGB, so colour-managed
    programs show them as intended, and `info` goes into the EXIF data."""
    tmp = path.with_name(path.name + ".part")
    picture = Image.fromarray(np.ascontiguousarray(image))
    extra = {}
    if picture.mode == "RGB":
        extra["icc_profile"] = srgb_profile()
    if info is not None:
        extra["exif"] = _exif(info, dpi)
    picture.save(tmp, format="JPEG", quality=quality, subsampling=0, dpi=(dpi, dpi), **extra)
    os.replace(tmp, path)


def save_tiff(path: Path, image: np.ndarray, dpi: int, info: ScanInfo | None = None) -> None:
    """Save a 16-bit image as an uncompressed TIFF, which every editor can open."""
    tmp = path.with_name(path.name + ".part")
    photometric = "rgb" if image.ndim == 3 else "minisblack"
    tags = []
    if info is not None and info.make:
        tags += [(271, "s", 0, _ascii(info.make), True), (272, "s", 0, _ascii(info.model), True)]
    tifffile.imwrite(
        tmp,
        np.ascontiguousarray(image),
        photometric=photometric,
        resolution=(dpi, dpi),
        resolutionunit="INCH",
        software=SOFTWARE,
        datetime=info.scanned if info is not None else None,
        description=_ascii(info.description) if info is not None and info.description else None,
        extratags=tags,
        metadata=None,
    )
    os.replace(tmp, path)


@cache
def srgb_profile() -> bytes:
    """An sRGB ICC profile, from the colour engine built into Pillow."""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


# Letters that Unicode doesn't split into an ASCII letter and accents.
_TRANSLITERATIONS = str.maketrans(
    {
        "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L",
        "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ı": "i", "ħ": "h", "Ħ": "H",
    }
)  # fmt: skip


def _ascii(text: str) -> str:
    """EXIF and TIFF text is 7-bit ASCII: "Été à København" -> "Ete a Kobenhavn".

    Other scripts can't be spelled in ASCII and are left out, along with the punctuation
    that is then left dangling: "Москва, frame 3" -> "frame 3".
    """
    folded = unicodedata.normalize("NFKD", text.translate(_TRANSLITERATIONS))
    folded = folded.encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded).strip(" ,.-")


def _exif(info: ScanInfo, dpi: int) -> Image.Exif:
    exif = Image.Exif()
    stamp = info.scanned.strftime("%Y:%m:%d %H:%M:%S")
    exif[ExifTags.Base.Software] = SOFTWARE
    exif[ExifTags.Base.DateTime] = stamp
    exif[ExifTags.Base.XResolution] = dpi
    exif[ExifTags.Base.YResolution] = dpi
    exif[ExifTags.Base.ResolutionUnit] = 2  # inches
    if info.make:
        exif[ExifTags.Base.Make] = _ascii(info.make)
        exif[ExifTags.Base.Model] = _ascii(info.model)
    if info.description:
        exif[ExifTags.Base.ImageDescription] = _ascii(info.description)
    # When the picture was digitised; when it was taken isn't known.
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeDigitized] = stamp
    return exif


def save_thumbnail(path: Path, image: np.ndarray, size: int = 480) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = image.shape[:2]
    step = max(1, min(h, w) // (2 * size))
    thumb = Image.fromarray(np.ascontiguousarray(image[::step, ::step]))
    thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
    thumb.save(path, format="JPEG", quality=85)
