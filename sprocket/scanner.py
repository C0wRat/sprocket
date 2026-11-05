"""Drive a SANE film scanner through SANE's `scanimage` command-line tool.

scanimage's PNM output is streamed straight from its stdout, which gives us:

* exact progress (bytes received / bytes expected, known from the PNM header);
* the image as soon as the last byte arrives, while the scanner is still
  returning the film holder (about 8 s on the OpticFilm 8100). scanimage's stdout
  is made unbuffered with `stdbuf -o0`; otherwise the last few kilobytes would sit
  in its buffer until the holder is back;
* a watchdog that can stop a hung backend. With sane-backends 1.2.1 the
  genesys backend never delivers any data in Gray mode on the OpticFilm 8100,
  so we always scan in Color and make black & white images in software.
"""

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import numpy as np

log = logging.getLogger(__name__)

PLUSTEK_VENDOR_ID = "07b3"
# Plustek film scanners handled by SANE's genesys backend: USB product id -> model.
OPTICFILM_MODELS = {
    "0807": "OpticFilm 7200",
    "0c07": "OpticFilm 7200 V2",
    "0c04": "OpticFilm 7200i",
    "0c12": "OpticFilm 7300",
    "0c3a": "OpticFilm 7400",
    "0c13": "OpticFilm 7500i",
    "0c3b": "OpticFilm 7600i",
    "130c": "OpticFilm 8100",
    "130d": "OpticFilm 8200i",
}
SYSFS_USB = Path("/sys/bus/usb/devices")
CHUNK = 1 << 20


@dataclass(frozen=True)
class Device:
    name: str  # SANE device name, e.g. "genesys:libusb:001:010"
    model: str


@dataclass(frozen=True)
class Capabilities:
    """What the scanner offers. The defaults are the OpticFilm 8100's."""

    resolutions: tuple[int, ...] = (600, 1200, 2400, 3600, 7200)
    width_mm: float = 36.33
    height_mm: float = 25.0

    @property
    def preview_resolution(self) -> int:
        return min(self.resolutions)


@dataclass(frozen=True)
class ScanArea:
    """Scan window in millimetres, in scanner orientation."""

    left: float
    top: float
    width: float
    height: float


class ScanError(Exception):
    def __init__(self, message: str, *, detail: str = "", got_data: bool = False, retryable: bool = True):
        super().__init__(message)
        self.detail = detail  # scanimage's stderr, for the log
        self.got_data = got_data
        self.retryable = retryable


class ScanCancelled(Exception):
    pass


def find_device(sysfs: Path = SYSFS_USB) -> Device | None:
    """Find a connected OpticFilm scanner through sysfs.

    This is instant, unlike `scanimage -L`, which probes every SANE backend (about
    8 s here). The SANE device name contains the USB bus and device numbers, which
    change whenever the scanner is plugged in again, so callers re-run this rather
    than keeping the name.
    """
    try:
        entries = sorted(sysfs.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            if (entry / "idVendor").read_text().strip().lower() != PLUSTEK_VENDOR_ID:
                continue
            product = (entry / "idProduct").read_text().strip().lower()
            if product not in OPTICFILM_MODELS:
                continue
            bus = int((entry / "busnum").read_text())
            dev = int((entry / "devnum").read_text())
        except (OSError, ValueError):
            continue
        return Device(f"genesys:libusb:{bus:03d}:{dev:03d}", f"Plustek {OPTICFILM_MODELS[product]}")
    return None


_RESOLUTIONS = re.compile(r"--resolution\s+([\d|]+)dpi")
_EXTENT = re.compile(r"^\s*-([xy])\s+[\d.]+\.\.([\d.]+)mm", re.MULTILINE)


def query_capabilities(scanimage: str, device: str, timeout: float = 30.0) -> Capabilities:
    """Read the resolutions and scan-area size from `scanimage -A`."""
    proc = subprocess.run(
        [scanimage, "-d", device, "-A"], capture_output=True, text=True, timeout=timeout, check=False
    )
    text = proc.stdout + proc.stderr
    resolutions = _RESOLUTIONS.search(text)
    extent = dict(_EXTENT.findall(text))
    if not resolutions or "x" not in extent or "y" not in extent:
        raise ValueError(f"unexpected `scanimage -A` output: {text[-300:]!r}")
    values = tuple(sorted({int(r) for r in resolutions.group(1).split("|") if r}))
    return Capabilities(values, float(extent["x"]), float(extent["y"]))


def describe_failure(stderr: str) -> tuple[str, bool]:
    """Turn scanimage's stderr into (message for the user, worth retrying)."""
    text = stderr.lower()
    if "device busy" in text:
        return (
            "The scanner is in use by another program (VueScan, SilverFast, XSane…). "
            "Close it and try again.",
            False,
        )
    if "open of device" in text:
        return ("Couldn't open the scanner. Check that it is plugged in and switched on.", True)
    if "error during device i/o" in text:
        return (
            "Lost contact with the scanner. Check the USB cable, or unplug the scanner "
            "and plug it back in.",
            True,
        )
    last = next((line for line in reversed(stderr.splitlines()) if line.strip()), "")
    return (f"Scan failed: {last}" if last else "Scan failed: scanimage exited unexpectedly.", True)


class ScanProcess:
    """One run of scanimage, streamed into memory."""

    first_data_timeout = 240.0  # positioning the holder + calibration + lamp warm-up
    stall_timeout = 90.0  # no data in the middle of a scan
    finish_timeout = 120.0  # returning the film holder after the last byte
    term_grace = 15.0  # the first SIGTERM cancels the scan and parks the holder

    def __init__(self, scanimage: str, device: str, resolution: int, area: ScanArea | None = None):
        self.scanimage = scanimage
        cmd = [scanimage, "-d", device, "--mode", "Color", "--depth", "16"]
        cmd += ["--resolution", str(resolution), "--format=pnm"]
        if area is not None:
            cmd += ["-l", f"{area.left:.2f}", "-t", f"{area.top:.2f}"]
            cmd += ["-x", f"{area.width:.2f}", "-y", f"{area.height:.2f}"]
        stdbuf = shutil.which("stdbuf")
        self.cmd = [stdbuf, "-o0", *cmd] if stdbuf else cmd
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr: deque[str] = deque(maxlen=40)
        self._stderr_thread: threading.Thread | None = None
        self._cancelled = threading.Event()
        self._terminating = threading.Lock()
        self._stage = "starting"  # starting -> reading -> finishing
        self._last_activity = time.monotonic()
        self._timeout_reason: str | None = None

    def run(self, on_progress: Callable[[float], None] | None = None) -> np.ndarray:
        """Start the scan and block until the whole image has been received.

        Returns a (height, width, 3) uint16 array of linear sensor values. The
        scanner is still busy afterwards: call wait_finished() before the next scan.
        """
        not_found = ScanError(
            f"`{self.scanimage}` was not found. Install SANE's command-line tools "
            "(Ubuntu/Debian: sudo apt install sane-utils).",
            retryable=False,
        )
        if shutil.which(self.scanimage) is None:
            raise not_found
        log.info("Running: %s", " ".join(self.cmd))
        # Debug level 1 makes the genesys backend print only its error messages, so a
        # failed scan says why instead of just "sane_start: Invalid argument".
        env = {**os.environ, "SANE_DEBUG_GENESYS": os.environ.get("SANE_DEBUG_GENESYS", "1")}
        try:
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=env,
            )
        except FileNotFoundError:
            raise not_found from None
        self._last_activity = time.monotonic()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        if self._cancelled.is_set():  # cancel() came in before the process existed
            self._terminate_in_background()

        try:
            return self._receive(on_progress)
        except BaseException:
            if self._proc.poll() is None:
                self._terminate_in_background()
            raise

    def wait_finished(self) -> None:
        """Wait for scanimage to exit. The scanner returns the film holder after the last byte."""
        if self._proc is None:
            return
        rc = self._proc.wait()
        if rc != 0:
            log.warning("scanimage exited with status %s after sending the image: %s", rc, " | ".join(self._stderr))

    def cancel(self) -> None:
        self._cancelled.set()
        if self._proc is not None and self._proc.poll() is None:
            self._terminate_in_background()

    def _receive(self, on_progress: Callable[[float], None] | None) -> np.ndarray:
        out = self._proc.stdout
        width, height, channels, maxval = self._read_header(out)
        sample_bytes = 2 if maxval > 255 else 1
        total = width * height * channels * sample_bytes
        buf = np.empty(total, dtype=np.uint8)
        view = memoryview(buf)
        got = 0
        self._stage = "reading"
        self._last_activity = time.monotonic()
        if on_progress:
            on_progress(0.0)
        while got < total:
            n = out.readinto(view[got : got + CHUNK])
            if not n:
                self._fail(got_data=got > 0)
            got += n
            self._last_activity = time.monotonic()
            if on_progress:
                on_progress(got / total)
        self._stage = "finishing"
        self._last_activity = time.monotonic()
        return _to_image(buf, height, width, channels, sample_bytes)

    def _read_header(self, out) -> tuple[int, int, int, int]:
        """Parse "P6\\n# comment\\nWIDTH HEIGHT\\nMAXVAL\\n" one byte at a time."""
        tokens: list[bytes] = []
        token = b""
        while len(tokens) < 4:
            ch = out.read(1)
            if not ch:
                self._fail(got_data=False)
            if ch == b"#" and not token:
                while ch not in (b"\n", b""):
                    ch = out.read(1)
                continue
            if ch.isspace():
                if token:
                    tokens.append(token)
                    token = b""
                continue
            token += ch
        # The single whitespace byte after MAXVAL has been consumed; pixel data follows.
        magic = tokens[0]
        if magic not in (b"P5", b"P6"):
            raise ScanError(f"Unexpected output from scanimage (starts with {magic!r}).", retryable=False)
        return int(tokens[1]), int(tokens[2]), 3 if magic == b"P6" else 1, int(tokens[3])

    def _fail(self, *, got_data: bool) -> NoReturn:
        """The output ended early: work out why and raise."""
        try:
            self._proc.wait(timeout=self.term_grace + 15)
        except subprocess.TimeoutExpired:
            self._terminate()
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)
        if self._cancelled.is_set():
            raise ScanCancelled()
        detail = "\n".join(self._stderr)
        if self._timeout_reason:
            raise ScanError(
                f"The scanner stopped responding ({self._timeout_reason}). If this keeps happening, "
                "unplug the scanner for a few seconds and plug it back in.",
                detail=detail,
                got_data=got_data,
                retryable=False,
            )
        message, retryable = describe_failure(detail)
        raise ScanError(message, detail=detail, got_data=got_data, retryable=retryable)

    def _drain_stderr(self) -> None:
        for raw in self._proc.stderr:
            line = raw.decode(errors="replace").strip()
            if line and "Setting debug level" not in line:
                self._stderr.append(line)
                log.debug("scanimage: %s", line)

    def _watchdog(self) -> None:
        limits = {
            "starting": (self.first_data_timeout, "no image data after {:.0f} s"),
            "reading": (self.stall_timeout, "no data for {:.0f} s"),
            "finishing": (self.finish_timeout, "still busy {:.0f} s after sending the image"),
        }
        proc = self._proc
        while proc.poll() is None:
            time.sleep(0.5)
            if self._cancelled.is_set():
                continue
            limit, reason = limits[self._stage]
            if time.monotonic() - self._last_activity > limit:
                self._timeout_reason = reason.format(limit)
                log.warning("Watchdog: %s; stopping scanimage", self._timeout_reason)
                self._terminate()
                return

    def _terminate(self) -> None:
        """Stop scanimage. It treats the first SIGTERM as "cancel" (the film holder is
        parked) and the second as "abort"; SIGKILL if neither works."""
        if not self._terminating.acquire(blocking=False):
            return  # another thread is already doing it
        try:
            steps = ((signal.SIGTERM, self.term_grace), (signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0))
            for sig, grace in steps:
                if self._proc.poll() is not None:
                    return
                try:
                    self._proc.send_signal(sig)
                except ProcessLookupError:
                    return
                try:
                    self._proc.wait(timeout=grace)
                    return
                except subprocess.TimeoutExpired:
                    log.warning("scanimage still running %.0f s after %s", grace, sig.name)
        finally:
            self._terminating.release()

    def _terminate_in_background(self) -> None:
        threading.Thread(target=self._terminate, daemon=True).start()


def _to_image(buf: np.ndarray, height: int, width: int, channels: int, sample_bytes: int) -> np.ndarray:
    if sample_bytes == 1:
        image = buf.reshape(height, width, channels).astype(np.uint16) * 257
    else:
        # PNM stores 16-bit samples big-endian. Swap in place rather than copying:
        # a 7200 dpi scan is ~440 MB.
        image = buf.view(">u2").reshape(height, width, channels)
        if sys.byteorder == "little":
            image = image.byteswap(inplace=True).view("<u2")
    if channels == 1:
        image = np.repeat(image, 3, axis=2)
    return image
