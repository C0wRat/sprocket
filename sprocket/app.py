"""HTTP API and the static web UI."""

import math
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import imaging, library
from .service import Adjustments, Busy, FilmType, LockedLevels, NotConnected, ScannerService, ScanRequest

STATIC_DIR = Path(__file__).parent / "static"


class RevalidatedStaticFiles(StaticFiles):
    """Static files the browser re-checks on every load, so an update never leaves a stale page."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(service: ScannerService) -> FastAPI:
    app = FastAPI(title="Sprocket", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service

    @app.exception_handler(Busy)
    async def busy(_request, exc: Busy) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(NotConnected)
    async def not_connected(_request, exc: NotConnected) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(ValueError)
    async def bad_value(_request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/status")
    def status() -> dict:
        return service.status()

    @app.post("/api/preview", status_code=202)
    def preview() -> dict:
        return service.start_preview().to_dict()

    @app.post("/api/scan", status_code=202)
    def scan(req: ScanRequest) -> dict:
        return service.start_scan(req).to_dict()

    @app.post("/api/cancel")
    def cancel() -> dict:
        return {"cancelled": service.cancel()}

    @app.get("/api/preview.jpg")
    def preview_image(
        adjustments: Annotated[Adjustments, Query()], crop: str | None = None, levels: str | None = None
    ) -> Response:
        data = service.render_preview(adjustments.adjust, _parse_crop(crop), _parse_levels(levels))
        if data is None:
            raise HTTPException(404, "No preview yet.")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/levels")
    def preview_levels(film: FilmType = "color-negative", crop: str | None = None) -> dict:
        """The preview's levels, for locking a roll's colours to this frame's."""
        levels = service.preview_levels(film, _parse_crop(crop))
        if levels is None:
            raise HTTPException(404, "No preview yet. Press Preview first.")
        return {"film": film, "low": levels.low, "high": levels.high}

    @app.get("/api/scans")
    def scans(limit: int = Query(60, ge=1, le=500)) -> dict:
        return {"scans": library.list_scans(service.output, limit)}

    @app.get("/api/next-frame")
    def next_frame(roll: str) -> dict:
        roll = library.sanitize_roll(roll)
        return {"roll": roll, "frame": library.next_frame(service.output, roll)}

    @app.get("/files/{path:path}")
    def files(path: str) -> FileResponse:
        root = service.output.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    return app


def _parse_crop(text: str | None) -> tuple[float, float, float, float] | None:
    """"x0,y0,x1,y1" (fractions of the scan area) -> tuple, or None."""
    if not text:
        return None
    try:
        values = [float(v) for v in text.split(",")]
        if len(values) != 4 or not all(math.isfinite(v) for v in values):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "crop must be four numbers: x0,y0,x1,y1") from None
    x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in values)
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return x0, y0, x1, y1


def _parse_levels(text: str | None) -> imaging.Levels | None:
    """"lowR,lowG,lowB,highR,highG,highB" (raw values, as GET /api/levels gives them) -> Levels, or None."""
    if not text:
        return None
    try:
        values = [float(v) for v in text.split(",")]
        if len(values) != 6:
            raise ValueError
        return LockedLevels(low=values[:3], high=values[3:]).to_imaging()
    except ValueError:  # includes pydantic's ValidationError
        raise HTTPException(400, "levels must be six numbers from 0 to 65535: lowR,lowG,lowB,highR,highG,highB") from None
