# Sprocket

[![CI](https://github.com/C0wRat/sprocket/actions/workflows/ci.yml/badge.svg)](https://github.com/C0wRat/sprocket/actions/workflows/ci.yml)

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

The menu at the top right switches between a **Light** and **Dark** look, or follows
your computer's setting (**System**, the default). There are also **Dracula**, **High
contrast** (WCAG AAA text contrast and thick focus rings) and **Retro film scanner**.
This browser remembers the choice.
The area around the picture stays a neutral dark grey either way, because a bright
surround changes how you judge exposure and colour.

## Scanning a roll

1. Put the film in the holder and push it into the scanner until the first frame is in place.
2. Choose the film type: **Color neg**, **Slide** or **B&W neg**.
3. Press **Preview** (or the P key). It takes about 18 seconds.
4. Drag on the preview to crop out the holder's edges, and drag the box to move it.
   Use **Rotate** and **Mirror** (or the M key) if the picture is the wrong way round
   or backwards. The R key rotates 90° clockwise, and Shift+R anticlockwise.
   To look closely at focus, grain or dust, scroll on the preview (or pinch) to zoom in
   where the pointer is, and drag with the middle mouse button, or with Space held, to
   move around. The buttons at the top right, or the keys **+** and **−**, zoom in and
   out; **0** fits the whole frame and **1** shows one preview pixel per screen pixel.
   The preview is 600 dpi, so it looks soft close up; the scan itself is sharper.
   **Crop** (or the C key) zooms to just the cropped part, to check the framing without
   the holder's edges, and keeps following it after Rotate or Mirror. Press it again
   for the whole frame; dragging the crop or zooming by hand also turns it off.
5. Fine-tune the picture with the **Adjust** sliders: Brightness, Contrast, Saturation,
   Warmth (bluer to more orange) and Tint (greener to more magenta). The preview updates
   right away without scanning again. Double-click a slider to set it back to 0, or type
   an exact value in the box next to it (Esc goes back to the value before).
   The histogram above the sliders shows the picture inside the crop, with the share
   of pixels clipped to pure black or white; it turns orange above 1 %. Holder edges
   left inside the crop show up there as clipping.
6. Set the roll name and frame number, pick a resolution, and press **Scan** (or S).
7. Move the holder to the next frame. Press Preview and Scan again. The frame number counts up by itself.

The crop, rotation and other settings are remembered, so a whole roll usually needs
only Preview and Scan for each frame. To give every frame of a roll the same colours,
press **Lock colours** on a typical frame (see below).

## What gets saved

```
~/Pictures/Sprocket/<roll>/<roll>-<frame>.jpg        finished picture (inverted for negatives)
~/Pictures/Sprocket/<roll>/<roll>-<frame>_raw.tif    16-bit scan, exactly as the scanner saw it
```

- The **JPEG** is the picture to share and print. Negatives are inverted and the
  orange mask is removed. Levels are set automatically from what's inside the crop,
  or taken from the locked frame when the colours are locked.
  **Sharpen the JPEG** (Low, Medium or High) gives it an unsharp mask that scales with
  the resolution and leaves grain-sized differences alone. It isn't shown in the
  preview, and at 7200 dpi it adds about 3 s to saving.
- The **raw TIFF** is the 16-bit linear data from the scanner, only cropped and
  rotated. Keep it if you want to do your own conversion later, e.g. with darktable's
  negadoctor, RawTherapee's film negative tool or Negative Lab Pro. A raw TIFF looks
  dark and, for negatives, orange; that's expected.
- Both files record the date of the scan, the roll, frame and film type and, unless
  you chose the device with `--device`, the scanner in their metadata (EXIF in the
  JPEG), so photo libraries can sort and caption them. Metadata text is plain ASCII,
  so "København" is written as "Kobenhavn" there; file and folder names keep it as typed.
  Colour JPEGs carry an sRGB profile, so colour-managed programs show them as intended.
- Existing files are never overwritten; a repeat scan of a frame is saved as `…-03-2.jpg`.
- Click a picture under **Recent scans** to see it full size. ← and → go through the
  other scans, and Esc or a click outside the picture closes it. Ctrl+click or
  middle-click opens it in a new tab instead.

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
mask of colour film. A gentle contrast curve is then applied. Slides keep their own
colour balance: they get shared black and white points and the standard sRGB curve.
B&W negatives are scanned in colour and mixed down to grey.

The Adjust sliders work on top of that. Brightness bends the midtones, and Warmth and
Tint do the same for single colour channels, so black stays black, white stays white
and nothing clips. Contrast is an S-curve through mid grey. Saturation moves each
pixel towards or away from grey. B&W film uses only Brightness and Contrast.

Each frame is levelled on its own, so a frame that is mostly one colour (a sunset,
say) can come out with its colours neutralised, and the frames of a roll can differ.
**Lock colours** fixes both: preview a frame of the roll with ordinary colours and
lock it, and every later preview and scan uses that frame's black and white points
instead of measuring its own. The film base of a roll is the same on every frame, so
the whole roll matches and the sunset stays orange. Exposure differences between
frames show up as they are, so adjust Brightness per frame if needed. The lock is kept
until you unlock it, also across restarts, and only applies to the film type it was
made for. Unlock it when you put in a different film; Sprocket reminds you when the
roll name changes.

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
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python -m pytest              # uses a fake scanimage, no scanner needed
node --test tests/*.test.mjs            # crop, zoom and histogram maths in the browser code
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for code style, how to run the app without a
scanner, and how to name branches, write commits and open a pull request.

| File | Purpose |
| --- | --- |
| `sprocket/scanner.py` | runs `scanimage`, streams the image, watchdog, finds the scanner |
| `sprocket/imaging.py` | negative/slide conversion, rotation, saving JPEG/TIFF |
| `sprocket/service.py` | one scan job at a time, in a background thread |
| `sprocket/library.py` | file naming and the list of recent scans |
| `sprocket/app.py` | HTTP API (FastAPI) |
| `sprocket/static/` | the web page: plain HTML, CSS and JavaScript, no build step |
