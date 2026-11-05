import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags, Image

from sprocket.app import create_app
from sprocket.service import ScannerService


@pytest.fixture
def output(tmp_path):
    folder = tmp_path / "scans"
    folder.mkdir()
    return folder


@pytest.fixture
def client(output, fake_scanimage):
    service = ScannerService(output, scanimage=fake_scanimage, device="fake")
    with TestClient(create_app(service)) as test_client:
        yield test_client


def wait_for_job(client, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get("/api/status").json()["job"]
        if job and job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def test_web_ui_is_served(client):
    assert "Sprocket" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_preview_then_scan(client, output):
    assert client.get("/api/status").json()["scanner"]["connected"] is True
    assert client.get("/api/preview.jpg").status_code == 404

    assert client.post("/api/preview").status_code == 202
    job = wait_for_job(client)
    assert job["state"] == "done", job
    status = client.get("/api/status").json()
    assert status["capabilities"]["resolutions"] == [100, 200]  # read from `scanimage -A`
    assert status["preview"]["id"]
    image = client.get(
        "/api/preview.jpg", params={"film": "slide", "rotate": 90, "mirror": True, "crop": "0.1,0.1,0.9,0.9"}
    )
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    adjusted = client.get(
        "/api/preview.jpg",
        params={"brightness": 10, "contrast": 20, "saturation": -30, "warmth": 40, "tint": -50, "v": "cache-buster"},
    )
    assert adjusted.status_code == 200

    crop = {"x0": 0.1, "y0": 0.2, "x1": 0.9, "y1": 0.6}  # 29.06 x 10 mm: 229 x 79 px at 200 dpi
    body = {
        "resolution": 200, "roll": "Trip 1", "frame": 3, "crop": crop, "rotate": 90,
        "contrast": 25, "warmth": -10, "sharpen": "medium",
    }
    assert client.post("/api/scan", json=body).status_code == 202
    job = wait_for_job(client)
    assert job["state"] == "done", job
    result = job["result"]
    assert (result["name"], result["width"], result["height"]) == ("Trip 1-03", 79, 229)
    assert result["next_frame"] == 4

    folder = output / "Trip 1"
    assert (folder / "Trip 1-03.jpg").is_file()
    assert (folder / "Trip 1-03_raw.tif").is_file()
    assert (folder / ".thumbs" / "Trip 1-03.jpg").is_file()
    with Image.open(folder / "Trip 1-03.jpg") as im:
        assert im.getexif()[ExifTags.Base.ImageDescription] == "Trip 1, frame 3, colour negative"
        assert "icc_profile" in im.info

    [scan] = client.get("/api/scans").json()["scans"]
    assert scan["name"] == "Trip 1-03"
    assert client.get(scan["files"]["jpeg"]["url"]).status_code == 200
    assert client.get("/api/next-frame", params={"roll": "Trip 1"}).json() == {"roll": "Trip 1", "frame": 4}


def test_colours_can_be_locked_to_a_frame(client, output):
    assert client.get("/api/levels").status_code == 404  # nothing to measure yet
    client.post("/api/preview")
    assert wait_for_job(client)["state"] == "done"

    lock = client.get("/api/levels", params={"film": "color-negative", "crop": "0.1,0.1,0.9,0.9"}).json()
    assert lock["film"] == "color-negative"
    assert all(lo < hi for lo, hi in zip(lock["low"], lock["high"]))
    levels = ",".join(str(v) for v in lock["low"] + lock["high"])
    # The preview with the frame's own levels looks the same as measuring them again.
    locked = client.get("/api/preview.jpg", params={"crop": "0.1,0.1,0.9,0.9", "levels": levels})
    unlocked = client.get("/api/preview.jpg", params={"crop": "0.1,0.1,0.9,0.9"})
    assert locked.status_code == 200 and locked.content == unlocked.content
    assert client.get("/api/preview.jpg", params={"levels": "1,2,3"}).status_code == 400
    assert client.get("/api/preview.jpg", params={"levels": "5,5,5,1,1,1"}).status_code == 400  # low above high
    assert client.get("/api/preview.jpg", params={"levels": "0,0,0,9e9,1,1"}).status_code == 400

    # Scans use the locked levels instead of measuring their own.
    def scan(**extra):
        assert client.post("/api/scan", json={"resolution": 100, "roll": "lock", **extra}).status_code == 202
        job = wait_for_job(client)
        assert job["state"] == "done", job
        return np.asarray(Image.open(output / "lock" / f"{job['result']['name']}.jpg"), dtype=float)

    own = scan()
    same = scan(levels={"low": lock["low"], "high": lock["high"]})
    # A film base three times as bright as this frame's: its blacks come out grey.
    washed_out = scan(levels={"low": lock["low"], "high": [v * 3 for v in lock["high"]]})
    assert abs(same.mean() - own.mean()) < 3
    assert washed_out.mean() > own.mean() + 20
    assert washed_out.min() > 30
    assert client.post("/api/scan", json={"resolution": 100, "levels": {"low": [0, 0, 0], "high": [1, 1, 70000]}}).status_code == 422


def test_bad_requests_are_rejected(client):
    assert client.post("/api/scan", json={"resolution": 123}).status_code == 400
    assert client.post("/api/scan", json={"resolution": 600, "save_jpeg": False, "save_tiff": False}).status_code == 400
    assert client.post("/api/scan", json={"film": "polaroid"}).status_code == 422
    assert client.post("/api/scan", json={"crop": {"x0": 0.5, "y0": 0, "x1": 0.4, "y1": 1}}).status_code == 422
    assert client.post("/api/scan", json={"rotate": 45}).status_code == 422
    assert client.post("/api/scan", json={"saturation": 101}).status_code == 422
    assert client.post("/api/scan", json={"sharpen": "extreme"}).status_code == 422
    assert client.get("/api/preview.jpg", params={"rotate": 45}).status_code == 422
    assert client.get("/api/preview.jpg", params={"warmth": -101}).status_code == 422
    for crop in ("nan,0,1,1", "0,0,inf,1", "0,0,1", "a,b,c,d"):
        response = client.get("/api/levels", params={"crop": crop})
        assert response.status_code == 400 and "four numbers" in response.json()["detail"], crop
    assert client.get("/files/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_one_job_at_a_time_and_cancel(client, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_DELAY", "0.05")
    assert client.post("/api/preview").status_code == 202
    assert client.post("/api/preview").status_code == 409
    deadline = time.monotonic() + 10
    while client.get("/api/status").json()["job"]["phase"] != "scanning":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert client.post("/api/cancel").json() == {"cancelled": True}
    assert wait_for_job(client)["state"] == "cancelled"


def test_a_failed_start_is_retried(client, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_MODE", "fail-once")
    client.post("/api/preview")
    assert wait_for_job(client)["state"] == "done"


def test_errors_are_reported(client, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_MODE", "busy")
    client.post("/api/preview")
    job = wait_for_job(client)
    assert job["state"] == "error"
    assert "another program" in job["error"]
