import numpy as np
import pytest
import tifffile
from PIL import Image

from sprocket.imaging import Adjust, orient, render, save_jpeg, save_tiff

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


def test_levels_are_measured_inside_the_crop():
    whole = render(negative(), Adjust())
    left_half = render(negative(), Adjust(), stats_crop=(0.0, 0.0, 0.5, 1.0))
    # The middle of the ramp is a mid grey normally, but the brightest part of the left half.
    assert whole[10, 195].mean() < 200
    assert left_half[10, 195].mean() > 240


def test_rotation_is_applied():
    assert render(negative(), Adjust(rotate=90)).shape == (400, 20, 3)


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
