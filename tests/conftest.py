import sys
from pathlib import Path

import pytest

FAKE_SCANIMAGE = Path(__file__).with_name("fake_scanimage.py")


@pytest.fixture
def fake_scanimage(tmp_path, monkeypatch):
    """An executable that behaves like scanimage (see fake_scanimage.py)."""
    wrapper = tmp_path / "scanimage"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SCANIMAGE}" "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("FAKE_SCANIMAGE_STATE", str(tmp_path / "fake-state"))
    return str(wrapper)


@pytest.fixture
def sysfs(tmp_path):
    """An empty stand-in for /sys/bus/usb/devices. Add scanners with plug_in."""
    path = tmp_path / "sysfs"
    path.mkdir()
    return path


@pytest.fixture
def plug_in(sysfs):
    """plug_in(port, product="130c", devnum=10): add a Plustek scanner to the fake sysfs."""

    def plug(port: str, product: str = "130c", devnum: int = 10) -> None:
        entry = sysfs / port
        entry.mkdir(exist_ok=True)
        bus = port.split("-")[0]
        for name, value in {"idVendor": "07b3", "idProduct": product, "busnum": bus, "devnum": str(devnum)}.items():
            (entry / name).write_text(value + "\n")

    return plug
