"""Scan jobs: one at a time, each in a background thread, observed by polling."""

import itertools
import logging
import math
import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from . import imaging, library
from .scanner import (
    Capabilities,
    Device,
    ScanArea,
    ScanCancelled,
    ScanError,
    ScanProcess,
    find_device,
    query_capabilities,
)

log = logging.getLogger(__name__)

FilmType = Literal["color-negative", "slide", "bw-negative"]


class Busy(Exception):
    pass


class NotConnected(Exception):
    pass


class Crop(BaseModel):
    """Crop rectangle as fractions (0-1) of the full scan area, in scanner orientation."""

    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _check_size(self) -> "Crop":
        if self.x1 - self.x0 < 0.01 or self.y1 - self.y0 < 0.01:
            raise ValueError("crop rectangle is too small (or inverted)")
        return self

    def to_area(self, caps: Capabilities) -> ScanArea:
        """Millimetres for scanimage, rounded down so the window never exceeds the scan area."""
        width = _floor2(max((self.x1 - self.x0) * caps.width_mm, 1.0))
        height = _floor2(max((self.y1 - self.y0) * caps.height_mm, 1.0))
        left = _floor2(min(self.x0 * caps.width_mm, caps.width_mm - width))
        top = _floor2(min(self.y0 * caps.height_mm, caps.height_mm - height))
        return ScanArea(left, top, width, height)


def _floor2(value: float) -> float:
    return math.floor(value * 100) / 100


RawValue = Annotated[float, Field(ge=0, le=65535)]


class LockedLevels(BaseModel):
    """Levels measured on one frame (GET /api/levels), reused to give other frames the
    same colours. See imaging.Levels."""

    low: tuple[RawValue, RawValue, RawValue]
    high: tuple[RawValue, RawValue, RawValue]

    @model_validator(mode="after")
    def _check_order(self) -> "LockedLevels":
        if any(lo > hi for lo, hi in zip(self.low, self.high, strict=True)):
            raise ValueError("each low level must not be above its high level")
        return self

    def to_imaging(self) -> imaging.Levels:
        return imaging.Levels(self.low, self.high)


def _display_path(path: Path) -> str:
    """The path with the home folder shown as ~."""
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return "~" if relative == Path(".") else f"~/{relative.as_posix()}"


class Adjustments(BaseModel):
    """How the raw scan becomes a picture. The sliders run from -100 to 100."""

    film: FilmType = "color-negative"
    brightness: float = Field(0, ge=-100, le=100)
    contrast: float = Field(0, ge=-100, le=100)
    saturation: float = Field(0, ge=-100, le=100)
    warmth: float = Field(0, ge=-100, le=100)
    tint: float = Field(0, ge=-100, le=100)
    rotate: int = 0
    mirror: bool = False

    @field_validator("rotate")
    @classmethod
    def _check_rotate(cls, value: int) -> int:
        if value not in (0, 90, 180, 270):
            raise ValueError("rotate must be 0, 90, 180 or 270")
        return value

    @property
    def adjust(self) -> imaging.Adjust:
        return imaging.Adjust(
            self.film,
            brightness=self.brightness / 100,
            contrast=self.contrast / 100,
            saturation=self.saturation / 100,
            warmth=self.warmth / 100,
            tint=self.tint / 100,
            rotate=self.rotate,
            mirror=self.mirror,
        )


class ScanRequest(Adjustments):
    resolution: int = 3600
    crop: Crop | None = None
    levels: LockedLevels | None = None  # default: measured on the scan itself
    roll: str = Field("roll", max_length=200)
    frame: int = Field(1, ge=0, le=9999)
    save_jpeg: bool = True
    save_tiff: bool = True
    sharpen: Literal["off", "low", "medium", "high"] = "off"  # the JPEG only


@dataclass
class Job:
    id: int
    kind: str  # "preview" | "scan"
    state: str = "running"  # running | done | error | cancelled
    phase: str = "starting"  # starting | scanning | processing | finishing
    progress: float = 0.0
    attempt: int = 1
    error: str | None = None
    result: dict | None = None
    started: float = field(default_factory=time.monotonic)
    scan_started: float | None = None
    finished: float | None = None
    cancel_requested: bool = False
    proc: ScanProcess | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        now = time.monotonic()
        eta = None
        if self.phase == "scanning" and self.scan_started is not None and self.progress > 0.03:
            eta = (now - self.scan_started) * (1 - self.progress) / self.progress
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "phase": self.phase,
            "progress": round(self.progress, 4),
            "attempt": self.attempt,
            "elapsed": round((self.finished or now) - self.started, 1),
            "eta": None if eta is None else round(eta),
            "cancelling": self.cancel_requested and self.state == "running",
            "error": self.error,
            "result": self.result,
        }


class ScannerService:
    def __init__(self, output: Path, scanimage: str = "scanimage", device: str | None = None):
        self.output = output
        self.scanimage = scanimage
        self.device_override = device
        self.capabilities = Capabilities()
        self._caps_device: str | None = None
        self._lock = threading.Lock()
        self._job: Job | None = None
        self._ids = itertools.count(1)
        self._device: Device | None = None
        self._device_checked = -math.inf
        self._preview: np.ndarray | None = None
        self._preview_version = 0
        self._instance = secrets.token_hex(4)  # lets the UI tell server restarts apart

    # --- queries -------------------------------------------------------------

    def device(self, refresh: bool = False) -> Device | None:
        if self.device_override:
            return Device(self.device_override, "Scanner")
        now = time.monotonic()
        if refresh or now - self._device_checked > 2.0:
            self._device = find_device()
            self._device_checked = now
        return self._device

    def status(self) -> dict:
        device = self.device()
        caps = self.capabilities
        job = self._job
        return {
            "scanner": {
                "connected": device is not None,
                "device": device.name if device else None,
                "model": device.model if device else None,
            },
            "capabilities": {
                "resolutions": list(caps.resolutions),
                "width_mm": caps.width_mm,
                "height_mm": caps.height_mm,
                "preview_resolution": caps.preview_resolution,
            },
            "job": job.to_dict() if job else None,
            "preview": {"id": self.preview_id},
            "output_dir": _display_path(self.output),
        }

    @property
    def preview_id(self) -> str | None:
        """Identifies the current preview: it changes with every new one, and on restart."""
        return f"{self._instance}-{self._preview_version}" if self._preview_version else None

    def render_preview(
        self,
        adjust: imaging.Adjust,
        crop: tuple[float, float, float, float] | None,
        levels: imaging.Levels | None = None,
    ) -> bytes | None:
        image = self._preview
        if image is None:
            return None
        return imaging.encode_jpeg(imaging.render(image, adjust, levels, stats_crop=crop))

    def preview_levels(self, film: str, crop: tuple[float, float, float, float] | None) -> imaging.Levels | None:
        """The levels of the frame in the preview, measured inside the crop."""
        image = self._preview
        if image is None:
            return None
        return imaging.measure_levels(image, film, crop)

    # --- jobs ------------------------------------------------------------------

    def start_preview(self) -> Job:
        return self._start("preview", self._run_preview)

    def start_scan(self, req: ScanRequest) -> Job:
        if req.resolution not in self.capabilities.resolutions:
            raise ValueError(f"The scanner doesn't support {req.resolution} dpi.")
        if not (req.save_jpeg or req.save_tiff):
            raise ValueError("Choose at least one file type to save.")
        return self._start("scan", lambda job: self._run_scan(job, req))

    def cancel(self) -> bool:
        job = self._job
        if job is None or job.state != "running" or job.phase not in ("starting", "scanning"):
            return False
        job.cancel_requested = True
        if job.proc is not None:
            job.proc.cancel()
        return True

    def _start(self, kind: str, work: Callable[[Job], None]) -> Job:
        with self._lock:
            if self._job is not None and self._job.state == "running":
                raise Busy("The scanner is busy. Wait for the current job to finish.")
            if self.device(refresh=True) is None:
                raise NotConnected("The scanner is not connected. Check the USB cable and that it is switched on.")
            job = Job(next(self._ids), kind)
            self._job = job
        threading.Thread(target=self._run, args=(job, work), name=f"{kind}-{job.id}", daemon=True).start()
        return job

    def _run(self, job: Job, work: Callable[[Job], None]) -> None:
        log.info("%s #%d started", job.kind.capitalize(), job.id)
        state = "done"
        try:
            work(job)
        except ScanCancelled:
            state = "cancelled"
        except ScanError as exc:
            state, job.error = "error", str(exc)
            detail = f"\nscanimage said:\n{exc.detail}" if exc.detail else ""
            log.error("%s #%d failed: %s%s", job.kind.capitalize(), job.id, exc, detail)
        except Exception as exc:
            state, job.error = "error", f"Unexpected error: {exc}"
            log.exception("%s #%d failed", job.kind.capitalize(), job.id)
        finally:
            job.proc = None
            job.finished = time.monotonic()
            job.state = state
        log.info("%s #%d %s after %.1f s", job.kind.capitalize(), job.id, state, job.finished - job.started)

    def _run_preview(self, job: Job) -> None:
        self._refresh_capabilities()
        proc, image = self._acquire(job, self.capabilities.preview_resolution, None)
        try:
            job.phase = "processing"
            self._preview = image
            self._preview_version += 1
            job.result = {"width": image.shape[1], "height": image.shape[0]}
        finally:
            job.phase = "finishing"
            proc.wait_finished()

    def _run_scan(self, job: Job, req: ScanRequest) -> None:
        self._refresh_capabilities()
        area = req.crop.to_area(self.capabilities) if req.crop else None
        proc, image = self._acquire(job, req.resolution, area)
        try:
            job.phase = "processing"
            job.result = self._save(image, req)
        finally:
            job.phase = "finishing"
            proc.wait_finished()

    def _acquire(self, job: Job, resolution: int, area: ScanArea | None) -> tuple[ScanProcess, np.ndarray]:
        """Run scanimage until the image is in memory.

        A scan that fails before sending any data is retried once. With
        sane-backends 1.2.1 the OpticFilm 8100 now and then fails with
        "sane_start: Invalid argument", and in testing an immediate second try
        has always worked.
        """
        for attempt in (1, 2):
            if job.cancel_requested:
                raise ScanCancelled()
            device = self.device(refresh=True)
            if device is None:
                raise ScanError("The scanner is not connected.", retryable=False)
            proc = ScanProcess(self.scanimage, device.name, resolution, area)
            job.proc, job.phase, job.progress, job.attempt = proc, "starting", 0.0, attempt

            def on_progress(fraction: float) -> None:
                if job.phase == "starting":
                    job.phase, job.scan_started = "scanning", time.monotonic()
                job.progress = fraction

            try:
                return proc, proc.run(on_progress)
            except ScanError as exc:
                if attempt == 1 and exc.retryable and not exc.got_data and not job.cancel_requested:
                    detail = f" (scanimage said: {exc.detail.strip()})" if exc.detail.strip() else ""
                    log.warning("Scan attempt failed: %s%s. Trying again.", exc, detail)
                    time.sleep(1.0)
                    continue
                raise
        raise AssertionError("unreachable")

    def _save(self, image: np.ndarray, req: ScanRequest) -> dict:
        roll = library.sanitize_roll(req.roll)
        folder = self.output / roll
        folder.mkdir(parents=True, exist_ok=True)
        base = library.unique_base(folder, f"{roll}-{req.frame:02d}")
        adjust = req.adjust
        levels = req.levels.to_imaging() if req.levels else None
        device = self.device()
        info = imaging.ScanInfo(
            scanned=datetime.now(),
            make=device.make if device else "",
            model=device.model if device and device.make else "",
            description=f"{roll}, frame {req.frame}, {imaging.FILM_NAMES[req.film]}",
        )
        positive = imaging.render(image, adjust, levels, margin=0.02 if req.crop else 0.04)
        if req.save_jpeg:
            imaging.sharpen(positive, req.sharpen, req.resolution)
            imaging.save_jpeg(folder / f"{base}.jpg", positive, req.resolution, info=info)
        if req.save_tiff:
            raw = imaging.orient(image, adjust.rotate, adjust.mirror)
            imaging.save_tiff(folder / f"{base}{library.RAW_SUFFIX}", raw, req.resolution, info=info)
        imaging.save_thumbnail(folder / library.THUMBS / f"{base}.jpg", positive)
        height, width = positive.shape[:2]
        log.info("Saved %s (%d x %d px at %d dpi)", folder / base, width, height, req.resolution)
        return {
            "name": base,
            "roll": roll,
            "width": width,
            "height": height,
            "resolution": req.resolution,
            "next_frame": library.next_frame(self.output, roll),
        }

    def _refresh_capabilities(self) -> None:
        """Ask the scanner what it supports, once per device (cheap: under a second)."""
        device = self.device(refresh=True)
        if device is None or device.name == self._caps_device:
            return
        self._caps_device = device.name
        try:
            caps = query_capabilities(self.scanimage, device.name)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            log.warning("Couldn't read the scanner's options (%s); assuming an OpticFilm 8100", exc)
            return
        if caps != self.capabilities:
            log.info("Scanner options: %s", caps)
        self.capabilities = caps
