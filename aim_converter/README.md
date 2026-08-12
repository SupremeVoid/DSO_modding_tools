# Darkstar One image tools — v2.0

Convert *Darkstar One* `.aim` graphics to and from PNG, and edit the UI texture
atlases that hold most of the game's interface art.

Python 3.8+ and **Pillow**. `aimfind.py` additionally needs **numpy**.

```
pip install pillow numpy
```

`AIM_SPEC.md` documents the `.aim` image format, including the SLD compression
codec. `A2D_SCRIPTS_SPEC.md` documents the `scripts/` resource files that map a
named UI graphic to the atlas page holding it.

---

## Quick start

```bash
# 1. Convert the game's images to PNG
python3 aim2png.py "extracted/ds_interface/staticImages" -o work/

# 2. Edit work/whatever.png -- keeping its exact pixel dimensions

# 3. Convert back, copying the original's metadata
python3 png2aim.py work/ --like-dir "extracted/ds_interface/staticImages" -o mod/

# 4. Sanity-check before launching the game
python3 aimvalidate.py mod/

# Most UI art is not a standalone file. Find where a graphic really lives:
python3 aimatlas.py find Auftraege --scripts "extracted/ds_interface/scripts"
#   Auftraege   27 x 38  at ( 950,  521)  in  images\TexPage_8_2.aim
```

Every tool takes a **file or a folder**. Folders are scanned
**non-recursively** on purpose — asset folders nest deeply, and a recursive
default makes it far too easy to rewrite thousands of files by accident.

Output defaults to the input's own location. Existing files are never
overwritten without `--force`.

---

## Tools

| Tool | Purpose |
|---|---|
| `aim2png.py` | `.aim` → `.png`. Decompresses and reassembles tile grids. |
| `png2aim.py` | `.png` → `.aim`. Writes uncompressed `IMTC32`. |
| `aimatlas.py` | Find / list / extract / patch sprites inside UI atlases. |
| `aimfind.py` | Locate a sprite across pages by pixel matching. |
| `aimvalidate.py` | Structural checks on `.aim` and `.tex`. |
| `test_pipeline.py` | Full regression suite (developers). |

Libraries: `aim_io.py` (container), `sld.py` (the compression codec),
`a2dtex.py` (atlas indexes), `aimcli.py` (shared CLI plumbing).

Common options: `-o DIR`, `-f/--force`, `-q/--quiet`, `--version`, `--help`.
`aim2png.py --info` describes files without converting and `--padded` exports
the full tile grid; `png2aim.py --like`/`--like-dir` copies metadata from the
original; `aimvalidate.py -v` prints every check rather than only problems.

### What each format costs you

`aim2png.py` handles every encoding seen in the game — `IMTC32` (uncompressed
BGRA), `IMSLD32` (SLD-compressed), `BMPRES` (an embedded BMP) and `IMJPG24A`
(a JPEG plus a 1-bit alpha mask). It skips `IMSLDXT*`, which decompress to
S3TC blocks; no DXT decoder is included.

`png2aim.py` only writes `IMTC32`. That is a deliberate scope decision rather
than a gap: the engine accepts uncompressed files wherever it shipped a
compressed one, confirmed in game, so an SLD compressor would buy nothing but a
smaller file.

---

## Editing interface graphics

**Most UI art is not a standalone `.aim`.** The `images\*.aim` files are the
packer's sources; the game draws from atlas pages, and a `.tex` file in the
game's `scripts/` folder records which page holds each graphic and at what
rectangle. Replacing a standalone source on disk changes nothing on screen.

`aimatlas.py` works through those indexes.

```
python3 aimatlas.py find    NAME          --scripts DIR
python3 aimatlas.py list    [PAGE]        --scripts DIR
python3 aimatlas.py extract NAME|--all    --scripts DIR --images DIR [-o OUTDIR]
python3 aimatlas.py patch   NAME NEW.png  --scripts DIR --images DIR [-o OUTDIR]
```

`--scripts` points at the folder holding `*.tex` (e.g.
`extracted/ds_interface/scripts`), `--images` at the folder holding the
`TexPage_*.aim` pages (e.g. `extracted/ds_interface/images`).

### Where does this graphic live?

```
$ python3 aimatlas.py find Auftraege --scripts .../scripts
Auftraege                            27 x 38   at ( 950,  521)  in  images\TexPage_8_2.aim
```

If the name is wrong it suggests near matches. `list` dumps a whole page's
contents sorted by position, which is the quickest way to see what a page
holds.

### The editing round trip

```
python3 aimatlas.py extract Auftraege --scripts .../scripts --images .../images -o work/
#   ... edit work/Auftraege.png, keeping it exactly 27x38 ...
python3 aimatlas.py patch Auftraege work/Auftraege.png \
       --scripts .../scripts --images .../images -o out/
```

`patch` composites your PNG into the page at the recorded rectangle and writes
`TexPage_8_2.new.aim`, leaving every other sprite untouched. Rename it and
install it at the path the `.tex` names, relative to the game root —
`images\TexPage_8_2.aim` becomes `<game>/images/TexPage_8_2.aim`.

**The replacement must match the slot exactly.** The rectangle is fixed by the
`.tex`, and a larger image would overlap its neighbours, so `patch` refuses a
size mismatch rather than corrupting the page.

`extract --all` dumps every sprite from every page — 729 of them in Darkstar
One — which is the fastest way to find artwork by eye.

### A note on encoding

Pages ship in several encodings (`IMTC32`, `BMPRES`, `IMJPG24A`). `patch`
always writes `IMTC32`, which the engine accepts in place of any of them; the
file gets larger and, for a page that was `IMJPG24A`, its 1-bit alpha mask
becomes full 8-bit alpha. That is an improvement in itself, but it means an
edited page will not match its unedited neighbours byte for byte.

### If a graphic really is standalone

Some `.aim` files genuinely are loaded directly by name — the ones listed in
`asset_paths.txt`, under `staticImages/` and `images/`. Those you edit with
`aim2png.py` / `png2aim.py` and install at the same relative path. When in
doubt, check `aimatlas.py find` first: if it reports a page, the standalone
file is inert.

### Matching by pixels instead of by name

When you can see a graphic but do not know its name, crop it out of a converted
page and run `aimfind.py` (above) to locate it. `aimatlas.py find` is the
better tool once you know the name.

## Upscaling an atlas

Doubling a `TexPage_*` and putting it back does not work, and the reason is not
the file format. A 2048×2048 page written by these tools is well formed — 64
tiles in an 8×8 grid, round-tripping byte for byte.

**The sprite rectangles live outside the `.aim`.** The interface `.res` files
address each graphic by pixel coordinates into its page
(`texturepage-filename`, `texturepage-index`). Double the page and every one of
those rectangles now points at the wrong place: a rect that used to cover a
sprite at (x, y) covers the region that was at (x/2, y/2) before, so you get the
wrong artwork, magnified. That is what the breakage looks like.

There are three ways forward, in order of how likely they are to work.

### Scale the rectangles to match

The correct fix, and it lives in the Texturepage resource for that page: one
`OffsetNSize%d` rectangle per sub-image. Double the page, double every one of
them.

Be aware of what that buys, though. `OffsetNSize` is offset *and* size in one
rectangle — there is no separate display size — so doubling it doubles the
sprite on screen. Bigger, not sharper. That is the same natural-size behaviour
described under *Dimensions are layout*, and it means upscaling an atlas is a
way to make UI elements larger, not a way to make them crisper.

### Leave the page size alone

If entries carry a single rect, upscaling cannot buy sharpness on principle.
Better artwork at 1024×1024 — cleaner edges, more contrast, less noise — is the
only improvement available, and it needs no coordinate changes at all.

### `--logical`: tested, and it does not work

The idea was that if the engine turned rectangles into texture coordinates by
dividing by the page's *declared* size rather than its actual pixels, a page
holding 2048×2048 pixels while declaring 1024×1024 would sample correctly and
resolve twice as finely.

**It crashes the game** — `EXCEPTION_ACCESS_VIOLATION` (`0xc0000005`). The
engine sizes its texture from the declared footer size and then copies pixel
data according to each tile's own `data_size`, so a declared size smaller than
the real content overruns the buffer.

That is a useful fact in itself: **the footer size must match the actual pixel
content.** The option is still there, because a smaller-than-real declaration
is exactly how you would test a reader, but it has no legitimate use for
modding. Do not point it at the game.

## The modding workflow

### 1. Survey what you have

```
python3 aim2png.py ds_interface/staticImages --info
```

`--info` describes every file without writing anything: logical size, padded
tile size, encoding, tile count. Use it to find the asset you want and to note
its dimensions — you will need them.

### 2. Keep a pristine copy

Back up the whole folder before touching anything. `--like` reads metadata from
the untouched original, so once you overwrite a file you have lost your
reference for it.

### 3. Export

```
python3 aim2png.py originals/SidequestSymbol.aim -o work/
```

### 4. Edit

Open `work/SidequestSymbol.png` in any editor. **Keep the pixel dimensions
exactly as exported** — see *Dimensions are layout* below.

### 5. Build it back

```
python3 png2aim.py work/SidequestSymbol.png \
        --like originals/SidequestSymbol.aim -o mod/
```

Check the line it prints. The logical size it reports is exactly your PNG's
size, and that is what the game will use:

```
  SidequestSymbol.png --> SidequestSymbol.aim   [11x12 (stored 16x16), IMTC32, 1 tile(s)]
```

For a batch, point it at a folder and give it `--like-dir` so each PNG finds
its own original:

```
python3 png2aim.py work/ --like-dir originals/ -o mod/
```

### 6. Install

The game reads its data from `.cpr` archives, but a loose file on disk takes
priority over the archived copy. To override an asset, **reproduce the path it
has inside the archive, relative to the game root**:

| asset lives in | put your copy in |
|---|---|
| `ds_interface.cpr\staticImages\Foo.aim` | `<game>/staticImages/Foo.aim` |
| `ds_interface.cpr\images\Foo.aim` | `<game>/images/Foo.aim` |
| `ds_interface.cpr\staticImages\IconGoods\Foo.aim` | `<game>/staticImages/IconGoods/Foo.aim` |
| `ds_3dgen.cpr\3DView\Images\Foo.aim` | `<game>/3DView/Images/Foo.aim` |

The archive name is **not** part of the path — only the folder structure inside
it. Getting this wrong is the most common reason a mod silently does nothing:
the game simply falls through to the archived original, with no error.

So check where the file actually sits in your extraction before copying. Two
different folders in `ds_interface.cpr` hold `.aim` files — `staticImages` and
`images` — and they are not interchangeable.

`asset_paths.txt` in this package lists every `.aim` path referenced directly by
the executable, grouped by folder. It is not exhaustive: assets requested from
UI layouts or Lua scripts rather than from code do not appear in it.

Never rename a file. Lookups are by exact path.

### 7. Check, then test

```
python3 aimvalidate.py mod/
```

`aimvalidate.py` re-parses everything you wrote, proves `IMTC32` files
re-serialise byte-identically, and — pointed at a `scripts/` folder with
`--images` — verifies every atlas rectangle still lies inside its page. It
exits non-zero on failure, so it can gate a build.

Then launch and check. If nothing changed, the path is wrong far more often than the
file is.

For reference, the executable mounts its data sources in this order, base data
last:

```
ds_local            (a directory, not an archive)
user_data.zip
ds_patch.cpr
ds_loca.cpr  ds_add.cpr  ds_3dadd.cpr  ds_3dtex.cpr
ds_3dobj.cpr  ds_3dgen.cpr  ds_interface.cpr
ds_main.cpr
```

`ds_patch.cpr` is the archive intended for overrides, if you would rather ship a
mod as one file than as loose assets.

---

## Rules for replacing artwork

### Dimensions are layout

**UI elements are drawn at the texture's natural size.** The logical size in
the footer — which is exactly the size of the PNG you feed `png2aim.py` — is
what the element occupies on screen. This is established empirically: an 11×12
symbol replaced with an 87×82 one rendered roughly seven times larger and
pushed the surrounding layout around.

So, for UI art:

- **Match the original dimensions exactly** unless you specifically want the
  element to change size.
- **You cannot upscale UI art for sharpness.** A 2× texture renders 2× larger,
  not 2× sharper. The only way to improve a UI icon is better artwork at the
  same pixel count.
- Resizing *is* a legitimate lever if enlarging is the goal — making a cluster
  marker or a hostile indicator more visible, for instance. Just expect the
  layout around it to shift, and change size in small steps.

The padded tile size (`stored 16x16`) is invisible to the game and is handled
for you; only the logical size matters.

Textures under `ds_3dgen.cpr\3DView\Images\` are a different case — those are
sampled by UV coordinates, which are resolution-independent, so upscaling them
plausibly gains detail without changing anything's size. That has not been
tested.

### Always use `--like`

Three footer values per file have no established meaning (`AIM_SPEC.md` §9).
`--like` copies them verbatim along with the tile size and flags, so the engine
sees byte-identical metadata and only your pixels differ. Without it the tool
writes sensible defaults, which may not be what that particular asset wants.

### Preserve alpha, and the colour underneath it

Shipped files carry real colour in fully transparent pixels — `ND_CrimeStar.aim`
begins `ff ff ff 00`, white at zero alpha. That is deliberate: the GPU filters
colour and alpha independently, so if your editor zeroes RGB wherever alpha is
0, you get dark fringes around the edges when the texture is filtered. Avoid
"discard hidden colour" style export options.

### Atlases: keep the cells where they are

Some files are sprite sheets — `Samplegroup_8_5.aim` is a grid of small markers,
`yahoo.aim` a sheet of logos. The UI picks sub-regions by coordinates that live
outside the `.aim`, in the interface configuration. Edit the artwork in place,
but do not move a cell: the game will still sample the old rectangle.

Note that tiles are **not** this. Tiling is an internal storage subdivision of a
single logical image (a 1024×768 picture stored as 4×3 tiles of 256×256), fully
handled by the tools. Different states of an icon are separate files —
`Rang0.aim` / `Rang1.aim`, `ND_CrimeStar.aim` / `ND_CrimeStarEmpty.aim`.

### Compression is not required

You do not need to reproduce the original SLD compression. The engine accepts
`IMTC32` wherever it shipped `IMSLD32` — both are first-class in the same
container and dispatch happens on the chunk tag. Confirmed working in-game. The
replacement file is larger, and nothing else differs.

Uncompressed cost grows with the *padded* area, so doubling both dimensions
roughly quadruples file size. A 141×152 icon is 88 KB; the same art at 2× is
1.0 MB because its tile grows from 256×256 to a 2×2 grid of 256×256.

---

## Troubleshooting

| symptom | cause |
|---|---|
| Element renders far too large or small | Your PNG's dimensions differ from the original's. Check the size `png2aim.py` reported. |
| Dark or white fringes around edges | RGB was discarded under transparent pixels on PNG export. |
| Nothing changed at all, and the file is a standalone `images\\*.aim` | It is a packer source, not what the game draws. Run `aimatlas.py find` — if it reports a page, edit that page instead. |
| Nothing changed at all | Wrong folder. The loose path must mirror the path inside the `.cpr`, relative to the game root — see *Install*. The game falls through to the original silently. |
| Wrong part of a sprite sheet shows | A cell moved within an atlas; the UI still samples the original rectangle. |
| `aim2png.py` skips a file | It decompresses to S3TC blocks (`IMSLDXT*`); no DXT decoder is included. |

## Using the library

```python
import aim_io

aim = aim_io.parse(open('rocky.aim', 'rb').read())
print(aim_io.describe(aim))
# 60x85 (stored 64x128), IMSLD32, 1 tile(s), 1 sub-block(s)

print(aim.encoding, aim.image_size, aim.pixel_format, aim.is_compressed)
# IMSLD32 (60, 85) BGRA True

img = aim_io.to_image(aim)          # PIL RGBA image, cropped to logical size
img.save('rocky.png')

data = aim_io.from_image(img, tile_size=64, flags=aim.flags,
                         footer_extra=aim.footer_extra)
open('rocky_new.aim', 'wb').write(data)
```

Useful entry points:

- `parse(bytes) -> AimImage` — headers, tiles, sub-blocks, footer.
- `to_image(AimImage)` — assemble and decompress to a PIL image.
- `from_image(img, tile_size, flags, footer_extra) -> bytes` — encode `IMTC32`.
- `tile_image(tile)` — one tile as a PIL image, decompressing or decoding an
  embedded BMP/JPEG/TGA as needed.
- `decompress(sub_block) -> bytes` — one SLD sub-block.
- `sld.decompress(data, pos) -> bytes` — the codec on its own; usable for any
  Ascaron SLD stream, not just images.

`aim_io.parse` raises `UnsupportedAim` on anything it does not recognise rather
than guessing.

---

## Technical background

A short orientation; `AIM_SPEC.md` has the full detail.

**Container.** `AIMRES2.00` magic, then a `TILEDIM` chunk giving the tile
count, then **one chunk per tile**, then a footer. The chunk tag says how the
pixels are stored: `IMTC32` is plain 32-bit, `BMPRES` embeds a whole BMP file,
and `IMSL****` is SLD-compressed with the four characters after `IMSL` naming
the format it decompresses *to*. `IMJPG24A` pairs a JPEG with a 1-bit alpha
mask kept in a separate SLD stream, since JPEG has no alpha of its own.

**Pixel order is BGRA**, not RGBA — the engine is Direct3D 9 and
`D3DFMT_A8R8G8B8` is BGRA in memory. Reading it as RGBA gives recognisable
shapes in wrong colours, which is an easy mistake to miss.

**Tiling.** Each axis is cut into 256-pixel pieces with the remainder padded up
to the next power of two, so an 852×651 image becomes columns of
256+256+256+128 and rows of 256+256+256. An image under 256 in an axis is not
split, just padded — which is why a 60×85 image lives in a 64×**128** tile.
Tiles are stored **column-major**: top to bottom, then the next column. The
real size is in the footer; the rest is padding.

**SLD** is Ascaron's general-purpose byte compressor — it is applied to raw
BGRA and to S3TC blocks alike, and the format has chunk tags for using it on
non-image data too. It is an LZ77 with a bucketed offset code and no entropy
coding: a 3-bit bucket index selects one of eight offset field widths, which
are themselves carried in the stream header as eight 4-bit deltas. Match
lengths use an escalating field — read 2 bits, then 3, then 4, continuing
while the value read is all ones. A compressed payload is a chain of
independent sub-blocks, each decompressing to at most 64 KiB.

The codec was recovered from `AIM20.dll`, section `ASSEG`, RVA `0x8491c`. The
engine loads that DLL at runtime through `AimInitialize`; the `.aim` code is
not in the main executable or in `Walhalla.dll`.

---

## Limitations

- **`png2aim.py` writes `IMTC32` only.** There is no SLD compressor. This is a
  deliberate scope decision, not an obstacle: the game accepts uncompressed
  replacements, so writing compressed files buys nothing but file size.
- **S3TC output is not decoded.** `IMSLDXT1/3/5` payloads decompress correctly
  through SLD, but `aim2png.py` does not turn the resulting S3TC blocks into
  pixels, so those files are skipped on conversion. `aim_io.decompress` still
  returns the raw blocks if you want to decode them yourself.
- **`.screen` and `Partmap.007` are not parsed.** Screen layouts and the
  resource hash index are documented only at the level of what they are; see
  `A2D_SCRIPTS_SPEC.md`.
- Several metadata fields have no established meaning (`AIM_SPEC.md` §9). They
  are preserved on round trip, which is why `--like` exists.
