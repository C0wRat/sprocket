"""Where scans are saved, how they are named, and listing them.

Layout: <output>/<roll>/<roll>-<frame>.jpg          finished positive
                       <roll>-<frame>_raw.tif      16-bit scan, as the scanner saw it
                       .thumbs/<roll>-<frame>.jpg  thumbnail for the web UI
"""

import re
from pathlib import Path
from urllib.parse import quote

RAW_SUFFIX = "_raw.tif"
THUMBS = ".thumbs"


def sanitize_roll(name: str) -> str:
    """Make a roll name safe to use as a single folder name."""
    cleaned = re.sub(r"[^\w .-]+", "-", name).strip(" .-")
    return cleaned[:64] or "roll"


def unique_base(folder: Path, base: str) -> str:
    """`base`, or `base-2`, `base-3`… so that existing scans are never overwritten."""
    candidate, n = base, 1
    while (folder / f"{candidate}.jpg").exists() or (folder / f"{candidate}{RAW_SUFFIX}").exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def next_frame(output: Path, roll: str) -> int:
    """One more than the highest frame number already saved for this roll."""
    folder = output / roll
    pattern = re.compile(rf"{re.escape(roll)}-(\d+)(?:-\d+)?(?:\.jpg|{re.escape(RAW_SUFFIX)})")
    frames = []
    if folder.is_dir():
        for path in folder.iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                frames.append(int(match.group(1)))
    return max(frames, default=0) + 1


def file_url(output: Path, path: Path) -> str:
    return "/files/" + quote(path.relative_to(output).as_posix())


def list_scans(output: Path, limit: int = 60) -> list[dict]:
    """Saved scans, newest first, grouped by name (a JPEG and its raw TIFF are one scan)."""
    if not output.is_dir():
        return []
    scans: dict[tuple[str, str], dict] = {}
    for folder in output.iterdir():
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        for path in folder.iterdir():
            if path.name.endswith(RAW_SUFFIX):
                base, kind = path.name[: -len(RAW_SUFFIX)], "tiff"
            elif path.suffix.lower() == ".jpg":
                base, kind = path.stem, "jpeg"
            else:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entry = scans.setdefault(
                (folder.name, base),
                {"roll": folder.name, "name": base, "modified": 0.0, "files": {}, "thumb": None},
            )
            entry["files"][kind] = {"url": file_url(output, path), "size": stat.st_size}
            entry["modified"] = max(entry["modified"], stat.st_mtime)
    newest = sorted(scans.values(), key=lambda e: e["modified"], reverse=True)[:limit]
    for entry in newest:
        thumb = output / entry["roll"] / THUMBS / f"{entry['name']}.jpg"
        if thumb.is_file():
            entry["thumb"] = file_url(output, thumb)
    return newest
