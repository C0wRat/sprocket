# Sprocket

A small web app for scanning 35 mm film with a **Plustek OpticFilm 8100** on Linux.
Preview a frame, drag to crop, pick the film type, and scan. You get a finished
JPEG plus the untouched 16-bit scan as a TIFF.

It drives the scanner through SANE's `scanimage`, so there are no vendor drivers
to install. Other OpticFilm models handled by SANE's `genesys` backend (7200,
7300, 7400, 7500i, 7600i, 8200i) are detected too, but only the 8100 has been
tested.

## Requirements

- Linux with SANE's command-line tools: `sudo apt install sane-utils`.
  `scanimage -L` should list the scanner (it takes a few seconds).
- Python 3.10 or newer.

## Start it

```sh
./run.sh            # the first run sets up a Python environment in .venv
```

Then open <http://localhost:8800>. Useful options:

| Option | What it does |
| --- | --- |
| `--open` | open the page in your browser |
| `--output DIR` | where to save scans (default `~/Pictures/Sprocket`) |
| `--port N` | listen on another port (default 8800) |
| `--host 0.0.0.0` | let other devices on your network use it, e.g. a tablet next to the scanner. There is no login, so only do this on a network you trust. |
| `--device NAME` | use this SANE device instead of auto-detecting |
| `--debug` | log more detail, including everything `scanimage` prints |

## Scanning a roll

1. Put the film in the holder and push it into the scanner until the first frame is in place.
2. Choose the film type: **Color neg**, **Slide** or **B&W neg**.
3. Press **Preview** (or the P key). It takes about 18 seconds.
4. Drag on the preview to crop out the holder's edges, and drag the box to move it.
   Use **Rotate** and **Mirror** if the picture is the wrong way round or backwards,
   and **Brightness** to taste. The preview updates right away without scanning again.
5. Set the roll name and frame number, pick a resolution, and press **Scan** (or S).
6. Move the holder to the next frame. Press Preview and Scan again. The frame number counts up by itself.

The crop, rotation and other settings are remembered, so a whole roll usually needs
only Preview and Scan for each frame.

## What gets saved

```
~/Pictures/Sprocket/<roll>/<roll>-<frame>.jpg        finished picture (inverted for negatives)
~/Pictures/Sprocket/<roll>/<roll>-<frame>_raw.tif    16-bit scan, exactly as the scanner saw it
```

- The **JPEG** is the picture to share and print. Negatives are inverted and the
  orange mask is removed. Levels are set automatically from what's inside the crop.
- The **raw TIFF** is the 16-bit linear data from the scanner, only cropped and
  rotated. Keep it if you want to do your own conversion later, e.g. with darktable's
  negadoctor, RawTherapee's film negative tool or Negative Lab Pro. A raw TIFF looks
  dark and, for negatives, orange; that's expected.
- Existing files are never overwritten; a repeat scan of a frame is saved as `…-03-2.jpg`.

Measured with the OpticFilm 8100 and sane-backends 1.2.1:

| | Time | Raw TIFF |
| --- | --- | --- |
| Preview, 600 dpi | ~18 s; the picture appears after ~10 s | not saved |
| Full frame, 3600 dpi | ~40 s | ~100 MB |
| Full frame, 7200 dpi | ~70 s | ~440 MB |

The first scan at each resolution also calibrates, which adds a few seconds. After
the last line is read the scanner takes about 8 seconds to park the film holder.
Sprocket processes and saves the files during that time.

## How the negative conversion works

The scanner returns linear light values. For a negative, Sprocket converts each
channel to density (a log scale), then stretches it between the clearest part of the
frame, which becomes black, and the densest part, which becomes white. The top and
bottom 0.1 % of pixels are allowed to clip. Doing this per channel cancels the orange
mask of colour film. A gentle contrast curve is then applied, and the Brightness
slider adjusts it. Slides keep their own colour balance: they get shared black and
white points and the standard sRGB curve. B&W negatives are scanned in colour and
mixed down to grey.

Each frame is levelled on its own, so a frame that is mostly one colour (a sunset,
say) can come out with its colours neutralised. The raw TIFF is there for frames
like that.

## Troubleshooting

- **"Scanner not found"**: check that it is switched on and that `lsusb` shows a
  Plustek device (`07b3:130c` for the 8100).
- **"In use by another program"**: close VueScan, SilverFast or XSane. Only one
  program can use the scanner at a time.
- **"sane_start: Invalid argument"**: the 8100 with sane-backends 1.2.1 does this now
  and then. Sprocket retries once on its own, and in testing the retry has always
  worked. If it keeps failing, unplug the scanner for a few seconds.
- **The scanner stops responding**: Sprocket gives up after a while and tells you.
  Unplug the scanner and plug it back in.
- Running `scanimage` yourself? Avoid `--mode Gray` on the 8100. With sane-backends
  1.2.1 it waits forever for data. Sprocket always scans in colour.

## Development

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest              # uses a fake scanimage, no scanner needed
node --test tests/geometry.test.mjs     # crop maths in the browser code
```

| File | Purpose |
| --- | --- |
| `sprocket/scanner.py` | runs `scanimage`, streams the image, watchdog, finds the scanner |
| `sprocket/imaging.py` | negative/slide conversion, rotation, saving JPEG/TIFF |
| `sprocket/service.py` | one scan job at a time, in a background thread |
| `sprocket/library.py` | file naming and the list of recent scans |
| `sprocket/app.py` | HTTP API (FastAPI) |
| `sprocket/static/` | the web page: plain HTML, CSS and JavaScript, no build step |
