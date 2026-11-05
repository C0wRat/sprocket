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
