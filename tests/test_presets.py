import json

import pytest
from fastapi.testclient import TestClient

from sprocket.app import create_app
from sprocket.presets import FILENAME, PresetExists, PresetNotFound, PresetStore, PresetValues
from sprocket.service import ScannerService


def test_presets_are_saved_renamed_and_deleted(tmp_path):
    store = PresetStore(tmp_path)
    assert store.list() == []
    store.save("Portra 400", PresetValues(warmth=12, saturation=-5))
    store.save("HP5 punchy", PresetValues(film="bw-negative", contrast=30))
    # Saving under an existing name (ignoring case and spaces) replaces it.
    store.save(" portra 400 ", PresetValues(warmth=20))
    assert [(p.name, p.film, p.warmth) for p in PresetStore(tmp_path).list()] == [
        ("HP5 punchy", "bw-negative", 0),
        ("portra 400", "color-negative", 20),
    ]

    store.rename("Portra 400", "Portra warm")
    with pytest.raises(PresetExists):
        store.rename("Portra warm", "hp5 PUNCHY")
    store.rename("portra warm", "Portra Warm")  # only the case changes
    with pytest.raises(PresetNotFound):
        store.rename("Ektar", "x")
    store.delete("HP5 punchy")
    with pytest.raises(PresetNotFound):
        store.delete("HP5 punchy")
    assert [p.name for p in store.list()] == ["Portra Warm"]


def test_a_damaged_presets_file_is_survived(tmp_path):
    path = tmp_path / FILENAME
    path.write_text("{not json")
    assert PresetStore(tmp_path).list() == []
    path.write_text(json.dumps({"presets": [{"name": "ok", "warmth": 5}, {"name": "bad", "warmth": 500}, "junk"]}))
    assert [p.name for p in PresetStore(tmp_path).list()] == ["ok"]


@pytest.fixture
def client(tmp_path, fake_scanimage):
    service = ScannerService(tmp_path, scanimage=fake_scanimage, device="fake")
    with TestClient(create_app(service)) as test_client:
        yield test_client


def test_presets_api(client, tmp_path):
    assert client.get("/api/presets").json() == {"presets": []}
    body = {"film": "slide", "brightness": 10, "contrast": -20, "saturation": 0, "warmth": 5, "tint": -3}
    saved = client.put("/api/presets/Velvia vivid", json=body)
    assert saved.status_code == 200 and saved.json() == {"name": "Velvia vivid", **body}
    assert client.get("/api/presets").json()["presets"] == [{"name": "Velvia vivid", **body}]
    # Kept in the output folder, so they survive a restart and every browser sees them.
    assert json.loads((tmp_path / FILENAME).read_text())["presets"][0]["name"] == "Velvia vivid"

    assert client.patch("/api/presets/Velvia vivid", json={"name": "Velvia"}).json()["name"] == "Velvia"
    client.put("/api/presets/Other", json={})
    assert client.patch("/api/presets/Velvia", json={"name": "other"}).status_code == 409
    assert client.patch("/api/presets/Nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/presets/Velvia").json() == {"deleted": "Velvia"}
    assert client.delete("/api/presets/Velvia").status_code == 404
    assert [p["name"] for p in client.get("/api/presets").json()["presets"]] == ["Other"]


def test_bad_presets_are_rejected(client):
    assert client.put("/api/presets/x", json={"warmth": 101}).status_code == 422
    assert client.put("/api/presets/x", json={"film": "polaroid"}).status_code == 422
    assert client.put("/api/presets/%20%20", json={}).status_code == 400  # blank name
    assert client.put("/api/presets/" + "n" * 61, json={}).status_code == 400
    assert client.patch("/api/presets/x", json={"name": ""}).status_code == 400  # checked before looking it up
