import numpy as np
import pytest
import tifffile
from PIL import Image, ImageFilter

from sprocket import imaging
from sprocket.imaging import Adjust, measure_levels, orient, render, save_jpeg, save_tiff

# Where a point at scanner coordinates (0.2, 0.1) lands on screen for each orientation.
# tests/geometry.test.mjs checks static/geometry.js against the same table.
ORIENTATIONS = [
    (0, False, (0.2, 0.1)),
    (90, False, (0.9, 0.2)),
    (180, False, (0.8, 0.9)),
    (270, False, (0.1, 0.8)),
    (0, True, (0.8, 0.1)),
    (90, True, (0.9, 0.8)),
    (180, True, (0.2, 0.9)),
    (270, True, (0.1, 0.2)),
]


@pytest.mark.parametrize("rotate, mirror, expected", ORIENTATIONS)
def test_orient_matches_the_ui(rotate, mirror, expected):
    image = np.zeros((100, 200), np.uint8)
    image[10, 40] = 255  # pixel centre at (0.2025, 0.105)
    out = orient(image, rotate, mirror)
    y, x = np.argwhere(out == 255)[0]
    assert (x + 0.5) / out.shape[1] == pytest.approx(expected[0], abs=0.01)
    assert (y + 0.5) / out.shape[0] == pytest.approx(expected[1], abs=0.01)


def negative(width=400, height=20):
    """A colour negative of a grey ramp that gets brighter to the right."""
    density = 0.15 + 1.2 * np.linspace(0, 1, width)
    row = np.stack([60000 * 10 ** -(density + mask) for mask in (0.25, 0.6, 0.85)], axis=-1)
    return np.repeat(row[None], height, axis=0).astype(np.uint16)


def test_color_negative_becomes_a_neutral_positive():
    out = render(negative(), Adjust("color-negative"))
    assert out.shape == (20, 400, 3) and out.dtype == np.uint8
    row = out[10].astype(int)
    lightness = row.mean(axis=1)
    assert np.all(np.diff(lightness) >= 0)  # brighter scene, brighter picture
    assert lightness[5] < 5 and lightness[-5] > 250  # full tonal range
    assert (row.max(axis=1) - row.min(axis=1)).max() <= 2  # orange mask gone: grey stays grey


def test_black_and_white_negative():
    out = render(negative(), Adjust("bw-negative"))
    assert out.shape == (20, 400)
    assert out[10, 5] < 5 and out[10, -5] > 250


def test_slide_keeps_its_tones_in_order():
    ramp = np.linspace(500, 40000, 300)
    image = np.repeat(np.stack([ramp] * 3, axis=-1)[None], 10, axis=0).astype(np.uint16)
    row = render(image, Adjust("slide"))[5, :, 0].astype(int)
    assert np.all(np.diff(row) >= 0)
    assert row[0] == 0 and row[-1] == 255


def test_brightness():
    means = [render(negative(), Adjust(brightness=b)).mean() for b in (-0.5, 0.0, 0.5)]
    assert means[0] < means[1] < means[2]


def test_contrast_is_an_s_curve():
    flat, normal, punchy = (render(negative(), Adjust(contrast=c))[10].mean(axis=1) for c in (-0.5, 0.0, 0.5))
    assert np.all(np.diff(punchy) >= 0)  # tones stay in order
    for curve in (flat, punchy):  # black and white stay put
        assert curve[0] == normal[0] and curve[-1] == normal[-1]
    dark, light = normal < 100, normal > 160
    assert np.all(punchy[dark] <= normal[dark]) and np.all(punchy[light] >= normal[light])
    assert np.all(flat[dark] >= normal[dark]) and np.all(flat[light] <= normal[light])
    assert punchy.std() > normal.std() > flat.std()


def test_saturation():
    levels = measure_levels(negative(), "color-negative")
    image = negative()
    image[..., 0] = image[..., 0] * 0.8  # a denser red layer, with the grey ramp's levels: a reddish picture

    def spread(out):
        return (out.max(axis=-1).astype(int) - out.min(axis=-1)).mean()

    normal = render(image, Adjust(), levels)
    assert spread(normal) > 10
    assert spread(render(image, Adjust(saturation=-1.0), levels)) <= 1  # grey
    less, more = (render(image, Adjust(saturation=s), levels) for s in (-0.5, 0.5))
    assert spread(less) < spread(normal) < spread(more)
    # The unrounded path used for saturation agrees with the fast one.
    nearly_off = render(image, Adjust(saturation=1e-9), levels)
    assert np.abs(nearly_off.astype(int) - normal).max() <= 1


def test_warmth_and_tint_colour_the_midtones_only():
    grey = render(negative(), Adjust())[10].astype(int)
    warm = render(negative(), Adjust(warmth=0.5))[10].astype(int)
    magenta = render(negative(), Adjust(tint=0.5))[10].astype(int)
    mid = slice(150, 250)
    assert np.all(warm[mid, 0] > grey[mid, 0]) and np.all(warm[mid, 2] < grey[mid, 2])
    assert np.all(magenta[mid, 1] < magenta[mid, 0]) and np.all(magenta[mid, 1] < magenta[mid, 2])
    for out in (warm, magenta):  # black stays black and white stays white
        assert out[:2].max() <= 2 and out[-2:].min() >= 253
    # The overall brightness hardly changes.
    assert abs(warm.mean() - grey.mean()) < 3 and abs(magenta.mean() - grey.mean()) < 3


def test_black_and_white_film_ignores_the_colour_controls():
    plain = render(negative(), Adjust("bw-negative"))
    coloured = render(negative(), Adjust("bw-negative", saturation=0.8, warmth=-0.6, tint=0.4))
    assert np.array_equal(plain, coloured)


@pytest.mark.parametrize("dpi", [600, 3600, 7200])
@pytest.mark.parametrize("shape", [(61, 37, 3), (61, 37)])
def test_sharpening_in_strips_matches_the_whole_picture(shape, dpi, monkeypatch):
    image = np.random.default_rng(1).integers(0, 256, shape, dtype=np.uint8)
    radius = min(max(dpi / 3600, 0.5), 2.0)
    unsharp = ImageFilter.UnsharpMask(radius=radius, percent=imaging.SHARPEN_PERCENT["high"], threshold=3)
    expected = np.asarray(Image.fromarray(np.ascontiguousarray(orient(image, 90, True))).filter(unsharp))
    monkeypatch.setattr(imaging, "SHARPEN_STRIP_PIXELS", 37 * 2)  # strips as short as the overlap allows
    picture = orient(image.copy(), 90, True)  # a view, as render() returns it
    imaging.sharpen(picture, "high", dpi)
    assert np.array_equal(picture, expected)
    still = image.copy()
    imaging.sharpen(still, "off", dpi)
    assert np.array_equal(still, image)


def test_sharpening_adds_edge_contrast():
    edge = np.zeros((40, 40, 3), np.uint8)
    edge[:, :20], edge[:, 20:] = 80, 170
    for strength in ("low", "medium", "high"):
        out = edge.copy()
        imaging.sharpen(out, strength, 3600)
        assert out[:, 19].mean() < 80 and out[:, 20].mean() > 170  # halo on either side
        assert out[:, :15].mean() == 80 and out[:, 25:].mean() == 170  # flat areas untouched
    low, high = edge.copy(), edge.copy()
    imaging.sharpen(low, "low", 3600)
    imaging.sharpen(high, "high", 3600)
    assert high[:, 20].mean() > low[:, 20].mean()


def test_levels_are_measured_inside_the_crop():
    whole = render(negative(), Adjust())
    left_half = render(negative(), Adjust(), stats_crop=(0.0, 0.0, 0.5, 1.0))
    # The middle of the ramp is a mid grey normally, but the brightest part of the left half.
    assert whole[10, 195].mean() < 200
    assert left_half[10, 195].mean() > 240


def test_rotation_is_applied():
    assert render(negative(), Adjust(rotate=90)).shape == (400, 20, 3)


@pytest.mark.parametrize("film", ["color-negative", "slide", "bw-negative"])
def test_measured_levels_can_be_reused(film):
    image = negative()
    crop = (0.0, 0.0, 0.5, 1.0)
    levels = measure_levels(image, film, crop)
    assert all(lo < hi for lo, hi in zip(levels.low, levels.high))
    expected = render(image, Adjust(film), stats_crop=crop)
    assert np.array_equal(render(image, Adjust(film), levels), expected)
    # Another frame converted with these levels keeps this frame's colours.
    darker = (image // 2).astype(np.uint16)
    assert not np.array_equal(render(darker, Adjust(film)), render(darker, Adjust(film), levels))


@pytest.mark.parametrize("film", ["color-negative", "bw-negative"])
def test_conversion_in_strips_matches_all_at_once(film, monkeypatch):
    image = np.random.default_rng(0).integers(0, 65535, (37, 23, 3), dtype=np.uint16)
    whole = render(image, Adjust(film))
    monkeypatch.setattr(imaging, "STRIP_PIXELS", 23 * 5)  # strips of 5 rows, the last one shorter
    assert np.array_equal(render(image, Adjust(film)), whole)


def test_save_tiff_and_jpeg(tmp_path):
    raw = negative()
    save_tiff(tmp_path / "scan_raw.tif", raw, 3600)
    with tifffile.TiffFile(tmp_path / "scan_raw.tif") as tif:
        assert np.array_equal(tif.asarray(), raw)
        assert tif.pages[0].tags["XResolution"].value == (3600, 1)
        assert tif.pages[0].tags["ResolutionUnit"].value == 2  # inch

    save_jpeg(tmp_path / "scan.jpg", render(raw, Adjust()), 3600)
    with Image.open(tmp_path / "scan.jpg") as im:
        assert im.size == (400, 20)
        assert im.info["dpi"] == (3600, 3600)
    assert not list(tmp_path.glob("*.part"))
