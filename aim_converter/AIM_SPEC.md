# The Ascaron `.aim` Image Format — Specification v1.8

`.aim` = **A**scaron **IM**age **RES**ource, magic `AIMRES2.00`. It is the
house image format of Ascaron Entertainment, used by Darkstar One and shared
with their other titles (Port Royale 2 and Patrician III use the same
container, including an `IMSLDXT5` variant Darkstar does not).

In the extracted Darkstar One data it holds HUD elements, cursors, icons,
cockpit art, station-interface backgrounds and UI texture atlases. Known
locations:

- `ds_3dgen.cpr\3DView\Images\`
- `ds_interface\staticImages\`

The format is fully decoded: container, tiling, all metadata, and the SLD
compression codec. Everything below was derived from the files themselves and
from `AIM20.dll`, which is where the engine's implementation lives.

All integers are **little-endian**. Offsets in `+0x..` form are relative to the
start of the structure being described.

---

## 1. File layout

```
0x00  "AIMRES2.00" + 6 zero bytes
0x10  u32  flags
0x14  "TILEDIM " + u32 size(=8) + u32 tile_count + u32 (0)
0x28  image chunk(s)
...   footer
```

`flags` is 18 in nearly every file; a value of 2 occurs (`DS.aim`) and its
meaning is not established.

## 2. Tiling and padding

An image is stored as one or more **tiles**, and **each tile is its own
chunk**, chained back to back from `0x28`. There are exactly `tile_count`
chunks.

**Tiles are laid out column-major**: consecutive tiles run top to bottom, then
the next column begins. Verified on `Laptop_02.aim`, whose 12 tiles are nine
256×256 followed by three 128×256 — that only assembles into a coherent image
if the three narrow tiles form the final column.

Each axis is cut into `TILE_STEP` = 256 pixel pieces, with the **remainder
padded up to the next power of two**:

```
852 wide  ->  256 + 256 + 256 + 128     (remainder 84  -> 128)
651 high  ->  256 + 256 + 256           (remainder 139 -> 256)
```

An image no larger than 256 in an axis is not split; that axis is simply the
next power of two, **per axis independently** — 11×12 → 16×16, 21×19 → 32×32,
45×30 → 64×32, 60×85 → 64×**128**.

This rule reproduces the shipped files exactly, including `DS.aim` at 1329×1060
(6×5 tiles, final tile 64×64: 1329−1280 = 49 → 64, 1060−1024 = 36 → 64).

Only the top-left `image_width × image_height` region (from the footer) is real
content; the rest is padding, to be ignored on read and regenerated on write.

## 3. Chunk tags

The engine's full tag table, from `AIM20.dll` (12 bytes per entry: an 8-char
tag plus 4 zero bytes), in order:

```
AIMBMP    AIMIMG    AIMRF     AIMTIMG   BGRAPAL8  BMPRES    JPGRES    TGARES
IMTC24    IMTC32    IMHC1555  IMHC4444  IMHC565   IMPAL8    IMBF      IMDXT1
IMDXT3    IMDXT5    IMJPG24A  IMJPG24   IMJPG32   IMSLD32   IMSLD8    IMSLDXT1
IMSLDXT3  IMSLDXT5  IMSCHAN   MIPMCONT  TILEDIM   SLDCOMP   SLDBCOMP  IMSL565
IMSL4444  IMSL1555
```

The naming is systematic:

- `IMTC24` / `IMTC32` — uncompressed true colour, 24- and 32-bit.
- `IMHC565` / `IMHC1555` / `IMHC4444` — uncompressed 16-bit high colour.
- `IMPAL8` — 8-bit palettised, with `BGRAPAL8` carrying the palette.
- `IMDXT1` / `IMDXT3` / `IMDXT5` — uncompressed S3TC blocks.
- `IMJPG24` / `IMJPG24A` / `IMJPG32` — JPEG payloads. `AIM20.dll` links the
  Intel JPEG Library to decode these.
- `IMSL****` — **SLD-compressed**, one tag per decompressed pixel format:
  `IMSLD32`, `IMSLD8`, `IMSL565`, `IMSL4444`, `IMSL1555`, `IMSLDXT1/3/5`.
- `SLDCOMP ` / `SLDBCOMP` — SLD-compressed chunks in their own right. SLD is a
  general-purpose byte compressor, not an image codec, which is why the same
  stream compresses raw BGRA and S3TC blocks equally well.
- `MIPMCONT` — mipmap container. `IMSCHAN` — single channel.

Observed in Darkstar One data so far: `IMTC32`, `IMSLD32`, `IMSLDXT1`,
`BMPRES` and `IMJPG24A`.

## 4. Uncompressed and embedded chunks

### 4.1 `IMTC32` / `IMTC24` — raw pixels

```
+0x00  "IMTC32  "        or "IMTC24  "
+0x08  u32  bytes_per_pixel   (4 for IMTC32, 3 for IMTC24)
+0x0c  u32  pitch             (bytes per row; tile_width = pitch / bpp)
+0x10  u32  (0)
+0x14  u32  data_size         (tile_height = data_size / pitch)
+0x18  pixel data, data_size bytes
+...   u32  tile_width
+...   u32  tile_height       <- per-tile TRAILER, after the pixels
```

The two u32 **after** the pixel data repeat the tile's dimensions. They are
easy to mistake for a "stored size" pair at the head of the footer, because in
a single-tile file the last tile's trailer sits immediately before the footer
and its values coincide with the padded canvas size. In a multi-tile file that
reading breaks immediately.

**Pixel order is BGRA** (BGR for `IMTC24`) — the engine is Direct3D 9 and
`D3DFMT_A8R8G8B8` is BGRA in memory. Confirmed by decoded artwork being
legible only under that reading.

### 4.2 `BMPRES` / `JPGRES` / `TGARES` — an embedded image file

```
+0x00  "BMPRES  "        or "JPGRES  " / "TGARES  "
+0x08  u32  width
+0x0c  u32  height
+0x10  u32  data_size
+0x14  a complete BMP / JPEG / TGA file, data_size bytes
```

The payload is a normal file with its own header, decodable by any image
library. Observed as 24-bit bottom-up BMPs in the `TexPage_*` UI atlases.
These chunks chain per tile like any other: `TexPage_0_2.aim` is 512×512 as
four 256×256 `BMPRES` chunks.

### 4.3 `IMJPG24A` / `IMJPG24` / `IMJPG32` — JPEG plus alpha

```
+0x00  "IMJPG24A"        or "IMJPG24 " / "IMJPG32 "
+0x08  u32  (80)              constant in every sample; meaning unknown
+0x0c  u32  width
+0x10  u32  height
+0x14  u32  payload_size      counted from +0x18, i.e. including the next field
+0x18  u32  jpeg_size
+0x1c  JPEG data, jpeg_size bytes
+...   alpha channel as an SLD sub-block chain, to the end of the payload
```

The chunk ends at `chunk_start + 0x18 + payload_size`.

JPEG has no alpha channel, so the engine stores one separately and
SLD-compresses it. For **`IMJPG24A` the alpha is a 1-bit mask, MSB first**,
exactly `width * height / 8` bytes — which is what the internal class name
`CJpg24A1Image` describes. Verified on six files: the SLD `raw_size` equals
`width * height / 8` in every one, and the decoded mask lines up with the
artwork.

`IMJPG24` (no alpha) and `IMJPG32` (presumably a full 8-bit alpha plane) have
not been seen in a sample; the tools treat them as the same chunk layout with
0 and 8 alpha bits per pixel respectively, which is inference, not fact.

`AIM20.dll` links the Intel JPEG Library to decode the JPEG payload; the data
is a normal JFIF stream that any decoder reads.

## 5. `IMSL****` — compressed container

`IMSL` is the container; the four characters after it are the pixel format of
the data once decompressed.

```
IMSLD32 (and other IMSL* 32/16/8-bit)   IMSLDXT1 / IMSLDXT3 / IMSLDXT5
+0x00  "IMSLD32 "                       +0x00  "IMSLDXT1"
+0x08  u32  a   (2, or 0)               +0x08  u32  tile_width
+0x0c  u32  b   (1 or 2)                +0x0c  u32  tile_height
+0x10  u32  tile_width                  +0x10  u32  payload_size
+0x14  u32  tile_height                 +0x14  u32  payload_size (repeated)
+0x18  u32  payload_size                +0x18  payload
+0x1c  payload
```

Chunks chain per tile like every other type: the next tag begins at
`payload_start + payload_size`. This walk lands on exactly the `TILEDIM` tile
count in every multi-tile sample.

The meaning of the `a` and `b` fields is not established.

### 5.1 Sub-block chain

A payload is **not** a single compressed stream. It is a chain of
independently compressed sub-blocks, each decompressing to at most **65536**
bytes:

```
u32  inner_size    bytes following this field; sub-block total = 4 + inner_size
u8   method        1 = SLD
u32  raw_size      decompressed size of this sub-block (65536 except the last)
u32  flags         see 6.1
...  SLD stream
```

Walk it with `off += 4 + inner_size`. The chain ends exactly on the payload
end, and `sum(raw_size)` equals `tile_width * tile_height * bytes_per_pixel`.

The decompressor is entered at the `method` byte + 1 — that is, its input
pointer is the `raw_size` field, and `raw_size`, `flags` and the width word
that follows are the codec's own record header (section 6).

## 6. The SLD codec

Recovered from `AIM20.dll`, section `ASSEG`, function at RVA `0x8491c`.

SLD is a byte-oriented **LZ77 with a bucketed offset code**. There is no
entropy coding stage; the near-flat byte histogram of a compressed stream is
simply densely packed bit fields.

### 6.1 Record

```
u32  raw_size    decompressed size
u32  flags       bit31 = stored (not compressed); bit30 = additionally XOR 0x35
u32  widths      eight 4-bit deltas, LOW NIBBLE FIRST
...  bit stream  read as 32-bit little-endian words, LSB first
```

If bit 31 of `flags` is set the payload is `raw_size` literal bytes, XOR-ed
with `0x35` if bit 30 is also set. Otherwise the LZ stream follows. In
Darkstar One data `flags` is always 1, so the LZ path is always taken.

### 6.2 Bucket tables

`widths` holds eight 4-bit values, read low nibble first and accumulated:

```
bits[i] = bits[i-1] + nibble[i]          (bits[-1] = 0)
mask[i] = (1 << bits[i]) - 1
base[i] = base[i-1] + mask[i-1] + 1      (base[0] = 0)
```

The encoder chooses one of **eight presets** by block size — thresholds
800, 1600, 3500, 8000, 15000, 30000, 70000, 200000 bytes, table at `AIM20.dll`
RVA `0x801a0` — and writes it out as those deltas. This is why only a handful
of distinct `widths` words occur in practice:

| preset | `bits[0..7]` | encoded bytes | block size |
|---|---|---|---|
| 0 | 1, 2, 3, 4, 5, 6, 7, 8 | `11 11 11 11` | < 800 |
| 1 | 1, 3, 4, 5, 6, 7, 8, 9 | `21 11 11 11` | 800 .. 1599 |
| 2 | 1, 3, 4, 5, 6, 8, 9, 10 | `21 11 21 11` | 1600 .. 3499 |
| 3 | 1, 3, 5, 6, 8, 9, 10, 12 | `21 12 12 21` | 3500 .. 7999 |
| 4 | 1, 3, 5, 7, 8, 10, 12, 13 | `21 22 21 12` | 8000 .. 14999 |
| 5 | 1, 3, 5, 7, 9, 10, 12, 14 | `21 22 12 22` | 15000 .. 29999 |
| 6 | 1, 3, 5, 7, 9, 11, 13, 15 | `21 22 22 22` | 30000 .. 69999 |
| 7 | 1, 3, 5, 7, 9, 12, 15, 17 | `21 22 32 23` | >= 70000 |

A decoder should read `widths` from the stream rather than assume a preset;
the table above is documentation, not a lookup the decoder needs.

### 6.3 Token stream

Bits are consumed LSB-first from 32-bit little-endian words.

```
bit 0  ->  literal
             next 8 bits are the output byte

bit 1  ->  match
             3 bits              -> bucket b
             bits[b] bits        -> v
             distance = base[b] + v + 1

             length = 2 + escalating field:
                 for k = 2, 3, 4, ...
                     read k bits as x
                     length += x
                     stop unless x == (1 << k) - 1
```

The match copy is byte-by-byte, so `distance < length` is legal and encodes
run-length fills. Decoding stops once `raw_size` bytes have been produced.

## 7. Footer

Immediately after the image data:

```
u32 image_width, u32 image_height       the real, logical size
u32 16                                  byte size of the block below
"IHHW"
u32 x, u32 y, u32 z
```

The footer begins immediately after the last tile chunk — including, for
`IMTC32`, after that tile's width/height trailer (§4.1). There is no
"stored size" field: the padded canvas size is derived from the tile grid.
Locating the footer by counting backwards from `IHHW` appears to work on
single-tile `IMTC32` files and is wrong in general.

The engine reads the `IHHW` block as a tag plus four u32 and validates that the
size field is 16.

The logical size is what you edit. It was verified against the alpha bounding
box of decoded pixels across the sample set — exact match in every case.

It is also **authoritative for layout**: UI elements are drawn at the texture's
natural size, so changing the logical size changes how much screen space the
element occupies. Established empirically — an 11×12 UI symbol rebuilt at 87×82
rendered proportionally larger and displaced the surrounding layout.

The declared size is an **addressing space over the stored pixel run**, and
does not have to match the tile grid's shape. It needs only to fit inside it —
`declared_width × declared_height ≤ stored pixels`. Shipped single-tile pages
that do this:

| file | declared | pixels | tile grid | grid pixels |
|---|---|---|---|---|
| `TexPage_0_3.aim` | 512×128 | 65536 | 256×256 | 65536 |
| `TexPage_1_2.aim` | 512×128 | 65536 | 256×256 | 65536 |
| `TexPage_8_6.aim` | 512×128 | 65536 | 256×256 | 65536 |
| `TexPage_8_3.aim` | 1024×64 | 65536 | 256×256 | 65536 |
| `Warenhandel_BG.aim` | 394×118 | 46492 | 256×256 | 65536 |
| `yahoo.aim` | 257×96 | 24672 | 256×256 | 65536 |

The first four reshape the run exactly; the last two leave the remainder
unused. All six are shipped files that the game loads, so a declared size wider
or taller than the tile grid is normal, not corruption.

**On the one observed crash.** Writing a page with a single 2048×2048 tile
while declaring 1024×1024 crashed the game with `EXCEPTION_ACCESS_VIOLATION`.
An earlier revision of this document explained that as the engine sizing its
texture from the declared field and then overrunning it — that explanation is
**wrong**, because `Warenhandel_BG.aim` ships with a declared size well below
its stored pixels and loads fine. Two things were changed at once in that
experiment (a tile four times larger than any shipped tile, and a declared size
that disagreed with it), so which of them caused the fault is not established.
Treat single tiles larger than 256×256 as untested.

### 7.1 The second footer variant

Some files carry no `IHHW` block at all. Their footer is just **8 bytes**:

```
u32 image_width, u32 image_height
```

and nothing else. Confirmed on `DS.aim` (1329×1060), `Ankunftshalle_01.aim` and
`Bar_human_10.aim` (both 1024×768) — all large multi-tile `IMSLD32` images.

Distinguishing the two is unambiguous: after the last tile chunk, if 16 or more
bytes remain and bytes 12–15 are `IHHW` with the preceding size field equal to
16, it is the standard footer; if exactly 8 bytes remain, it is this variant.
Reading `DS.aim` this way gives 1329×1060 over a 6×5 grid whose final tile is
64×64 — the 49×36 remainder padded up — which is what the tile data itself
implies.

`yahoo.aim` is the only sample left whose declared size is not simply
explained: 257×96 inside a 256×256 tile fits the buffer, but no reshape maps
it cleanly.

## 8. Modding

Both encodings are first-class in the same container and the engine dispatches
on the chunk tag, so **a compressed original can be replaced with an
uncompressed `IMTC32` edit**. This is confirmed in-game on Darkstar One: an
`IMSLD32` file rebuilt as `IMTC32`, carrying the same tile size, flags and
footer values, loads and displays correctly. The file grows; nothing else
changes.

That makes the full round trip — read anything, edit as PNG, write back as
`IMTC32` — available without an SLD compressor.

Replacement artwork should keep the original logical dimensions. UI elements
are drawn at natural size (§7), so a larger texture is a larger element, not a
sharper one. Textures addressed by UV coordinates rather than by UI layout —
those under `3DView\Images\` — are not subject to this, though that has not
been tested.

Most interface art is not a standalone `.aim` at all: it is packed into
`TexPage_<group>_<page>.aim` atlases and addressed from A2dLib's resource
system — individual files in a `scripts/` folder (`*.screen`, `*.anim`)
indexed by `scripts/Partmap.007`. A texture page is an `SH_TEXPG` resource
(*"CTexturePage — A Texture page containing 2d images for Drawables"*)
carrying `TextureFile`, a `Count`, and one `OffsetNSize%d` rectangle —
`"x y w h"` — per sub-image. Drawables reference a page by
`texturepage-filename` plus a `texturepage-index` into that list. The same
graphic is frequently duplicated across the atlases of several groups.

Note that the choice of encoding in the shipped data is not driven by size or
content: `MissionSymbol.aim` (11×12) is uncompressed while `SidequestSymbol.aim`
(11×12, same UI set, loaded by the same code) is compressed, and a 141×152
commodity icon is uncompressed while a 21×19 marker is compressed. It appears
to be a per-asset build flag.

## 9. Not established

- The `flags` field at `0x10` (18 vs 2).
- The `a` and `b` fields in the `IMSLD32` chunk header.
- The three u32 following `IHHW`.
- The anomalous logical size in `yahoo.aim` (257×96 in a 256×256 tile).
- Real-world examples of `IMJPG*`, `IMPAL8`, `IMHC*`, `MIPMCONT`, `SLDCOMP`
  and `SLDBCOMP` — the engine supports them but none has appeared in a sample.

## 10. Verification

Reproducible with the accompanying tools on the 25 files to hand
(12 `IMTC32`, 5 `IMSLD32`, 2 `BMPRES`, 6 `IMJPG24A`):

- All 25 parse. Chunk chains consume the file exactly: the footer begins on the
  byte after the last tile chunk, and the `IHHW` tag and its size field of 16
  validate.
- All 12 `IMTC32` files decode to PNG and re-encode **byte-identically** from
  the decoded pixels, with the tile grid re-derived from scratch by the §2 rule
  rather than copied from the original. That includes `Laptop_02.aim`, 12 tiles
  of two different sizes — so the round trip independently confirms the
  column-major order, the 256-step split and the power-of-two remainder.
- All 5 `IMSLD32` files decompress to exactly their declared sizes
  (1024, 4096, 4096, 32768, and 4 × 65536) and render as coherent artwork.
  `SidequestSymbol.aim`, 76 bytes of stream, decodes to a letter **Q** whose
  letterforms match the independently decoded **M** in the uncompressed
  `MissionSymbol.aim` — same icon set, same 11×12 canvas.
- Both `BMPRES` files decode, including the 2×2 tiled `TexPage_0_2.aim`.
- All 6 `IMJPG24A` files decode. Every alpha chain decompresses to exactly
  `width * height / 8` bytes and the resulting 1-bit mask matches the artwork
  (checkbox widgets, scrollbars and panel frames come out with clean edges and
  correct transparency).
- An `IMTC32` file written by `png2aim.py` in place of a shipped `IMSLD32`
  loads and displays correctly in Darkstar One, both at the original size and
  at a changed size (the latter scaling the on-screen element accordingly).
