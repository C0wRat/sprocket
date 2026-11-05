import threading
import time

import numpy as np
import pytest

from sprocket.scanner import (
    Capabilities,
    ScanArea,
    ScanCancelled,
    ScanError,
    ScanProcess,
    find_device,
    query_capabilities,
)


def test_streams_the_whole_image(fake_scanimage):
    progress = []
    proc = ScanProcess(fake_scanimage, "fake", 100)
    image = proc.run(progress.append)
    proc.wait_finished()
    assert image.shape == (98, 143, 3)
    assert image.dtype == np.uint16
    # 16-bit samples survive the big-endian PNM round trip
    assert image[0, 0].tolist() == [int(60000 * 10 ** -(0.15 + m)) for m in (0.25, 0.6, 0.85)]
    assert progress[0] == 0.0 and progress[-1] == 1.0
    assert progress == sorted(progress)


def test_scan_area_is_passed_to_scanimage(fake_scanimage):
    image = ScanProcess(fake_scanimage, "fake", 200, ScanArea(1.0, 2.0, 12.7, 25.4)).run()
    assert image.shape[:2] == (200, 100)


def test_a_stalled_scanner_is_stopped(fake_scanimage, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_MODE", "stall")
    proc = ScanProcess(fake_scanimage, "fake", 100)
    proc.first_data_timeout = 1.0
    proc.term_grace = 0.5
    started = time.monotonic()
    with pytest.raises(ScanError, match="stopped responding") as info:
        proc.run()
    assert time.monotonic() - started < 10
    assert not info.value.retryable


def test_cancel_stops_the_scan(fake_scanimage, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_DELAY", "0.02")
    proc = ScanProcess(fake_scanimage, "fake", 100)
    scanning = threading.Event()

    def cancel_when_scanning():
        scanning.wait(5)
        proc.cancel()

    threading.Thread(target=cancel_when_scanning, daemon=True).start()
    with pytest.raises(ScanCancelled):
        proc.run(lambda fraction: fraction > 0.1 and scanning.set())


def test_busy_scanner_gets_a_helpful_message(fake_scanimage, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_MODE", "busy")
    with pytest.raises(ScanError, match="another program") as info:
        ScanProcess(fake_scanimage, "fake", 100).run()
    assert not info.value.retryable
    assert "Device busy" in info.value.detail


def test_missing_scanimage():
    with pytest.raises(ScanError, match="sane-utils"):
        ScanProcess("/nonexistent/scanimage", "fake", 100).run()


def test_query_capabilities(fake_scanimage):
    assert query_capabilities(fake_scanimage, "fake") == Capabilities((100, 200), 36.33, 25.0)


def test_find_device(tmp_path):
    scanner = tmp_path / "1-5"
    scanner.mkdir()
    for name, value in {"idVendor": "07b3", "idProduct": "130c", "busnum": "1", "devnum": "10"}.items():
        (scanner / name).write_text(value + "\n")
    keyboard = tmp_path / "1-4"
    keyboard.mkdir()
    (keyboard / "idVendor").write_text("3434\n")

    device = find_device(tmp_path)
    assert device.name == "genesys:libusb:001:010"
    assert device.model == "Plustek OpticFilm 8100"
    assert device.make == "Plustek"
    assert find_device(tmp_path / "missing") is None
