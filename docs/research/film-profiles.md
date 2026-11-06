# Film-stock profiles: what they are and what would fit Sprocket

Research for [#4](https://github.com/C0wRat/sprocket/issues/4). Status: proposal; nothing here is
implemented yet. The follow-up work is listed at the end.

## Summary

"Film presets" in other tools are two different things:

1. **Look presets** (Lightroom `.xmp` presets, VSCO-style packs). Tone and colour edits applied
   to a picture that is already a positive. Sprocket already covers the simple part of this with
   the Adjust sliders and named presets (#3). The rest (tone curves, HSL, grain) would need new
   tools in the pipeline.
2. **Inversion profiles** (SilverFast NegaFix, VueScan's film vendor/brand/type). Per-stock data
   used *while turning the negative into a positive*: the colour of the orange mask and how
   each dye layer responds. This is the part that is really about the film stock.

The most useful thing for Sprocket is a small version of (2). A film profile would hold the
**relative contrast of the three dye layers**, so a roll can be converted with neutral colours
from its **film base alone**, without measuring each frame's colours and without needing a
typical frame to lock on. We can derive these numbers ourselves from a locked frame. No other
product's profiles need copying.

## What other tools do

| Tool | What a "film profile" is | Notes |
| --- | --- | --- |
| SilverFast NegaFix | Profiles chosen by vendor, film and speed (about 120 at launch). Each film's orange mask and characteristic curve are handled differently. | Users treat them as a starting point, then adjust the mask "tolerance" and curve and save their own preset. Opinions on accuracy vary. |
| VueScan | *Negative vendor / brand / type* on the Color tab. Older manuals say it holds sensitometric data for about 200 negative films. | Many users get the best results from *Generic*, and save their own settings per film. |
| Negative Lab Pro | No per-stock profiles that I could find. It analyses each image and offers "colour models" (Basic, Frontier, Noritsu…) and tone profiles. | Its "looks" are lab-scanner emulations, not film stocks. |
| darktable negadoctor | No stock profiles. Parameters: film base colour (D min, sampled from the unexposed rebate), D max, offset, paper grade (gamma), paper gloss. | These are close to Sprocket's own pipeline (see below). A stock profile there would just be preset values. |
| RawTherapee "film negative" | Per-channel exponents (red and blue relative to green) plus the film base colour. | The same idea as the proposal here. |
| Lightroom presets (`.xmp`) | Settings in the `crs:` namespace: `Exposure2012`, `Contrast2012`, `Saturation`, `Temperature`/`Tint` (or `IncrementalTemperature`/`IncrementalTint` for non-raw files), tone curves (`ToneCurvePV2012`, per channel), HSL, colour grading, grain. Creative profiles can embed a 3D LUT. | Made for positives. Most of the values only mean something inside Adobe's raw pipeline. |

## How Sprocket converts a negative today

From `sprocket/imaging.py`:

1. `measure_levels()` finds, per channel, the brightest raw value (the film base, which becomes
   black) and the darkest (the densest highlight, which becomes white), ignoring 0.1 % at each
   end, inside the crop.
2. `_negative_curve()` converts to density, `log(high / v)`, and stretches it to 0–1 **per
   channel**. That is what cancels the orange mask.
3. The result is raised to `NEGATIVE_GAMMA` (1.3), the Adjust sliders bend the midtones, and
   Contrast adds an S-curve.

Stretching each channel between its own end points also cancels the different contrast of the
three dye layers, but only if the frame has neutral highlights and shadows. That is why a
sunset gets neutralised. **Lock colours** fixes it by reusing a typical frame's end points for
the whole roll.

## Which parts of a profile would fit

| Part of a profile | Fits Sprocket? | How |
| --- | --- | --- |
| Orange mask / film base colour | Partly | Already measured per frame (`Levels.high`). The raw values depend on the scanner's lamp and calibration, so a stored base colour would not carry over between scanners. Better measured on each roll, e.g. from the rebate. |
| Relative contrast of the dye layers | **Yes** | The density range of red and blue relative to green, e.g. `(1.08, 1, 0.92)`. With the film base and the green channel's range, the red and blue end points follow, so colours stay right on a sunset frame without a lock. It doesn't depend on the scanner. |
| Overall contrast / gamma | Yes | Per-stock value for `NEGATIVE_GAMMA` (e.g. Ektar punchier than Portra). |
| Per-layer curve shape (toe, shoulder) | Not yet | Would need per-channel curves in `_curves()`. Small gain for normally exposed film. |
| The "look" (warmth, saturation) | Already | That's a preset (#3). A profile could name a default preset. |
| Grain, HSL, colour grading, LUTs | No | Outside what Sprocket does. Users can do it in darktable or Lightroom from the raw TIFF or the JPEG. |

## File formats

- There is **no common format for inversion profiles**. NegaFix and VueScan profiles are built
  into those products. Their contents aren't documented, and copying them would raise licensing
  questions.
- **Lightroom `.xmp`** is the closest thing to a standard for looks. Only `Exposure2012`,
  `Contrast2012`, `Saturation` and `IncrementalTemperature`/`IncrementalTint` map onto
  Sprocket's sliders, and only roughly: the scales and curves differ. An importer would promise
  more than it delivers, so I'd leave it out unless per-channel curves are added.
- **Our own format**: a JSON entry next to the presets, e.g.

  ```json
  { "name": "Portra 400 (measured)", "film": "color-negative",
    "layer_contrast": [1.08, 1.0, 0.92], "gamma": 1.3, "preset": "Portra warm" }
  ```

## Licensing

- Don't ship values taken from SilverFast, VueScan or Negative Lab Pro.
- Manufacturers' data sheets publish characteristic curves per dye layer. Reading slopes off
  them is using published facts, but they describe the film under standard processing, not our
  scanner.
- The cleanest source is **our own measurements**. The `low`/`high` levels of a locked frame
  already contain everything: `layer_contrast[c] = log(high[c] / low[c]) / log(high[g] / low[g])`.
  A user can make a profile from their own film, lab and scanner, and any bundled profiles
  would be measured by us on our own scans.

## Proposed follow-up issues

1. **Film profiles for colour negatives (layer contrast).** Add `layer_contrast` and `gamma`
   to the conversion. With a profile chosen, measure the film base (`high`) and the green
   range on the frame, and derive red and blue from the profile instead of measuring them.
   Profile picker under the film type, defaulting to "Measure each frame" (today's behaviour).
   Files: `imaging.py` (`measure_levels`, `_curves`), `service.py`, `app.js`, tests.
2. **Make a film profile from a locked frame.** "Save as film profile…" next to Lock colours,
   computing `layer_contrast` from the lock. Stored on the server like presets. Builds on 1
   and #3.
3. **Measure the film base from the rebate.** Optionally sample the unexposed strip outside
   the crop (as negadoctor does), so the base doesn't depend on the frame's shadows.
4. **Starter profiles measured by us** for a few common stocks (e.g. Portra 400, Gold 200,
   Ektar 100, Superia 400). Needs a test roll per stock with a grey card. Could be dropped if
   2 works well enough.
5. *(Later, optional)* Per-channel tone curves in Adjust, which would make a partial Lightroom
   `.xmp` importer worthwhile.

B&W negatives and slides don't need stock profiles in this sense. B&W has no mask, and its
contrast is the Contrast slider. Slides keep their own colours, and casts are a preset.

## Sources

- SilverFast NegaFix: [Photography Blog, NegaFix profile for Ektar 100](https://www.photographyblog.com/news/silverfast_negafix_profile_for_kodak_ektar_100_now_available);
  [ColorSync users list, 2009](https://lists.apple.com/archives/colorsync-users/2009/Sep/msg00139.html);
  [Large Format Photography forum, RPX25 with NegaFix](https://www.largeformatphotography.info/forum/archive/index.php/t-175165.html);
  [Luminous Landscape, scanning colour negatives](https://luminous-landscape.com/scanning-colour-negatives-raw-or-not)
- VueScan: [Hamrick, VueScan support for film types](https://www.hamrick.com/blog/vuescan-support-for-film-types.html);
  [VueScan user's guide (old)](https://stuff.mit.edu/afs/sipb/project/scanner/arch/i386_linux24/bin/html/vuesc.htm);
  [photoinfos.com, VueScan colour films](https://photoinfos.com/Fotosoftware/Vuescan/htm/vuescan-colorfilme-en.htm)
- Negative Lab Pro: [photoinfos.com guide](https://photoinfos.com/Fotolabor-digital/Negativ-zu-Positiv-umwandeln/Negative-Lab-Pro-Farbnegativ-zu-Positiv-umwandeln-en.htm);
  [Rangefinder Forum thread](https://rangefinderforum.com/threads/negative-lab-pro.169618/)
- darktable negadoctor: [darktable manual](https://docs.darktable.org/usermanual/4.6/en/module-reference/processing-modules/negadoctor/);
  [source (3.6.1)](https://grok.dragonflybsd.org/xref/dports/graphics/darktable/darktable-3.6.1/src/iop/negadoctor.c)
- RawTherapee film negative: [discuss.pixls.us thread](https://discuss.pixls.us/t/progress-with-rt-5-9-film-negative/34427?page=2)

These are mostly user guides and forum posts. The vendors don't document what their profiles
contain, so the table above describes behaviour, not file contents.
