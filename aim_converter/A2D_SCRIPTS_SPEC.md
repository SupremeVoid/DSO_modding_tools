# Ascaron A2dLib interface resources — `scripts/` formats

Companion to `AIM_SPEC.md`. Covers the resource files that tell the engine
*which* image to draw and *where it lives*, without which `.aim` editing is
guesswork.

These live in a `scripts/` folder inside the interface archives —
`ds_interface/scripts/` and `ds_add/scripts/`, which ship identical copies.
A2dLib loads them through a resource directory (`AddScriptDir`) indexed by
`Partmap.007`, registering resources by hash.

Contents of `ds_interface/scripts/` in Darkstar One:

| files | extension | resource | tag |
|---|---|---|---|
| 1107 | `.anim` | drawable / animation | `SH_ANIM` |
| 83 | `.screen` | screen layout | `SH_SCRN` |
| 10 | `.tex` | texture page index | `SH_TEXPG` |
| 1 | `Partmap.007` | resource hash index | — |

Other resource tags exist in the library — `SH_DWB`, `SH_DWFAB`, `SH_FONT`,
`SH_CURSR`, `SH_SND` — but no files of those types ship here.

All integers are little-endian.

---

## 1. Why this matters

**The standalone `images\*.aim` files are the packer's sources, not what the
game draws.** At runtime a UI graphic comes out of an atlas page, at a
rectangle recorded in a `.tex` file. Replacing `images\Auftraege.aim` on disk
therefore changes nothing on screen; the change has to go into
`TexPage_8_2.aim` at (950, 521).

This also explains why so few standalone sources ship at all — the
`ds_interface/images/` folder holds 37 files while the `.tex` indexes name 729
sub-images across 10 pages.

## 2. `.tex` — texture page index (`SH_TEXPG`)

A fixed-record file: a 28-byte header, then `(filesize - 28) / 284` records of
284 bytes each. Record 0 describes the page; the rest describe its contents.

```
Header, 28 bytes
  char[8]  "A2DFILE\0"
  u32      28              header size
  u32      17              constant in every sample; meaning unknown
  u32      0, 0, 0

Record 0 — the page                       (284 bytes)
  char[8]  "SH_TEXPG"
  u32      284             record size
  u32      count           number of sub-image records that follow
  char[]   page filename, NUL-terminated, zero-padded

Records 1..count — one per packed sub-image   (284 bytes each)
  u32      284             record size
  u32      x, y, w, h      rectangle within the page
  char[]   source filename, NUL-terminated, zero-padded
```

`count` always equals the record count minus one, and every rectangle lies
inside its page's bounds.

The ten pages in Darkstar One:

| file | page | sub-images |
|---|---|---|
| `TexPage1.tex` | `TexPage_8_0.aim` | 102 |
| `TexPage2.tex` | `TexPage_8_1.aim` | 177 |
| `TexPage3.tex` | `TexPage_8_2.aim` | 331 |
| `TexPage4.tex` | `TexPage_1_3.aim` | 45 |
| `TexPage5.tex` | `TexPage_0_4.aim` | 58 |
| `TexPage6.tex` | `Samplegroup_8_5.aim` | 12 |
| `TexPage7.tex` | `Samplegroup_8_6.aim` | 12 |
| `TexPage8.tex` | `TexPage_1_7.aim` | 7 |
| `TexPage9.tex` | `TexPage_0_8.aim` | 25 |
| `TexPage10.tex` | `Samplegroup_8_9.aim` | 12 |

Note the page naming: the **trailing** number is the page index and matches the
`.tex` number minus one. The leading number tracks the `.aim` encoding —
`0_*` pages are `BMPRES`, `1_*` are `IMJPG24A`, `8_*` are `IMTC32`. Pages
outside this list exist on disk (`TexPage_0_1`, `TexPage_1_1`, `TexPage_1_2`,
`TexPage_1_4`, `TexPage_1_6`) and are referenced by no `.tex`; they appear to
be alternate-encoding builds of the same indices that ship unused.

Each named graphic occurs in exactly one page across the whole index. Visual
duplication between pages is either genuinely different artwork or the same art
under a different name (`Auftraege` and `ND_Auftraege` are separate 27×38
entries in the same page).

## 3. `.anim` — drawable (`SH_ANIM`)

Every one of the 1107 files is exactly **3220 bytes**, a single fixed record.

```
0x000  char[8]  "SH_ANIM\0"
0x008  u32      32            section size
0x00c  u32      1             frame count — 1 in every shipped file
0x010  u32      width
0x014  u32      height
0x020  u32      24
0x024  u32      1, 1, 1
0x038  u32      32
0x03c  u32      0xffffffff
0x04c  u32      0xffffffff
0x058  u32      12
0x068  char[]   source image, e.g. "images\Auftraege.aim"
0x1a0  u32      width         (repeated)
0x1a4  u32      height        (repeated)
0x1b0  u32      1
0x0c7c u32      12
0x0c88 u32      12
```

Comparing any two `.anim` files, the **only** bytes that differ are the width
at `0x010`, the height at `0x014`, the source filename, and the repeated size
at `0x1a0`/`0x1a4`. Everything else is identical boilerplate across all 1107
files.

So a `.anim` names a source image and its size. The animation machinery is
present but unused — no shipped file has more than one frame. The size matches
the `.tex` rectangle for the same graphic exactly (`Auftraege.anim` says
27 × 38; `TexPage3.tex` places `Auftraege.aim` at 27 × 38).

The chain is therefore:

```
.screen  →  drawables  →  .anim  →  source images\X.aim
                                          ↓  (resolved through the .tex indexes)
                                    TexPage page + (x, y, w, h)
```

## 4. `.screen` — screen layout (`SH_SCRN`)

Not analysed. 83 files, variable size (4–40 KB), named per screen and per
resolution: `LOGBUCH_MISSIONEN_1024x768.screen`, `BORDCOMPUTER_1024x768.screen`,
`FRONTVIEW_1024x768.screen` and so on. The `_1024x768` suffix means layouts are
authored per resolution.

## 5. `Partmap.007`

Not analysed. 23232 bytes, one per `scripts/` folder. A2dLib logs
*"AddScriptDir: Failed to load Partmap in %s"*, *"Partmap already loaded"* and
*"Hash Collision while registering %s"*, so it is the hash index the resource
manager uses to resolve a resource name to a file in the directory.

## 6. Editing

To change a UI graphic:

1. Find it — `aimatlas.py find NAME` — which reports the page and rectangle.
2. Extract it — `aimatlas.py extract NAME` — which crops it out of the page.
3. Edit the PNG, keeping its exact pixel dimensions. The rectangle is fixed by
   the `.tex`; anything else would overlap a neighbour.
4. Patch it back — `aimatlas.py patch NAME edited.png` — which composites it
   into the page and writes a new `.aim`.
5. Install the page (not the source image) at the path the `.tex` names,
   relative to the game root: `images\TexPage_8_2.aim` → `<game>/images/`.

Changing a rectangle's size means editing the `.tex` record, and because
`x, y, w, h` is both the source region and the drawn size, that scales the
element on screen rather than sharpening it.
