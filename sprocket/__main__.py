"""Command-line entry point: python -m sprocket"""

import argparse
import logging
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app
from .service import ScannerService


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="sprocket", description="Web UI for scanning film with a Plustek OpticFilm scanner."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to listen on (default: %(default)s, this computer only; "
        "0.0.0.0 lets other devices on your network use it)",
    )
    parser.add_argument("--port", type=int, default=8800, help="port to listen on (default: %(default)s)")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.environ.get("SPROCKET_OUTPUT", "~/Pictures/Sprocket")),
        help="folder to save scans in (default: %(default)s)",
    )
    parser.add_argument(
        "--device", default=os.environ.get("SPROCKET_DEVICE"), help="SANE device name (default: auto-detect)"
    )
    parser.add_argument(
        "--scanimage",
        default=os.environ.get("SPROCKET_SCANIMAGE", "scanimage"),
        help="path to SANE's scanimage program (default: %(default)s)",
    )
    parser.add_argument("--open", action="store_true", help="open the web UI in a browser")
    parser.add_argument("--debug", action="store_true", help="log more detail")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("sprocket")

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    service = ScannerService(output, scanimage=args.scanimage, device=args.device)
    device = service.device(refresh=True)
    others = [d for d in service.devices() if d != device]
    if device:
        log.info("Found %s (%s)", device.model, device.name)
        for other in others:
            log.info("Also found %s (%s); choose between them in the web page", other.model, other.name)
    else:
        log.warning("No OpticFilm scanner found yet. Plug it in and switch it on; Sprocket will notice.")

    host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0", "::") else args.host
    url = f"http://{host}:{args.port}/"
    log.info("Sprocket is running at %s  (scans are saved to %s)", url, output)
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(service), host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
