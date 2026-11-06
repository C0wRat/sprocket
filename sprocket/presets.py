"""Named presets of the Adjust sliders, kept on the server so every browser sees them.

They live in <output>/.presets.json. A preset is a name, the film type it's for and the
slider values; it never includes locked colours, which belong to a roll, not a look.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from .service import FilmType

log = logging.getLogger(__name__)

FILENAME = ".presets.json"
Slider = Annotated[int, Field(ge=-100, le=100)]
PresetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class PresetValues(BaseModel):
    """What a preset sets: the film type it's for and the Adjust sliders."""

    film: FilmType = "color-negative"
    brightness: Slider = 0
    contrast: Slider = 0
    saturation: Slider = 0
    warmth: Slider = 0
    tint: Slider = 0


class Preset(PresetValues):
    name: PresetName


class PresetNotFound(Exception):
    pass


class PresetExists(Exception):
    pass


def _key(name: str) -> str:
    """Names are compared ignoring case and surrounding spaces, so "Portra" and "portra " are one preset."""
    return name.strip().casefold()


class PresetStore:
    def __init__(self, folder: Path):
        self.path = folder / FILENAME
        self._lock = threading.Lock()

    def entries(self) -> list[Preset]:
        with self._lock:
            return self._load()

    def save(self, name: str, values: PresetValues) -> Preset:
        """Create a preset, or replace the one with this name."""
        preset = Preset(name=name, **values.model_dump())
        with self._lock:
            presets = [p for p in self._load() if _key(p.name) != _key(preset.name)]
            presets.append(preset)
            self._write(presets)
        return preset

    def rename(self, name: str, new_name: str) -> Preset:
        new_name = Preset(name=new_name).name  # validate and strip
        with self._lock:
            presets = self._load()
            preset = next((p for p in presets if _key(p.name) == _key(name)), None)
            if preset is None:
                raise PresetNotFound(f"There's no preset called “{name}”.")
            if _key(new_name) != _key(name) and any(_key(p.name) == _key(new_name) for p in presets):
                raise PresetExists(f"There's already a preset called “{new_name}”.")
            preset.name = new_name
            self._write(presets)
        return preset

    def delete(self, name: str) -> None:
        with self._lock:
            presets = self._load()
            kept = [p for p in presets if _key(p.name) != _key(name)]
            if len(kept) == len(presets):
                raise PresetNotFound(f"There's no preset called “{name}”.")
            self._write(kept)

    def _load(self) -> list[Preset]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.warning("Couldn't read the presets in %s (%s); starting without them", self.path, exc)
            return []
        presets = []
        for item in data.get("presets", []) if isinstance(data, dict) else []:
            try:
                presets.append(Preset.model_validate(item))
            except ValidationError:
                log.warning("Skipping a preset in %s that isn't valid: %r", self.path, item)
        return sorted(presets, key=lambda p: (p.film, p.name.casefold()))

    def _write(self, presets: list[Preset]) -> None:
        """Write the file in one go (a temporary file renamed over it), so a crash never leaves half a file."""
        data = {"version": 1, "presets": [p.model_dump() for p in presets]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".presets-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
