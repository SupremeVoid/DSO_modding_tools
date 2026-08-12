"""
Reader/writer for the Ascaron `.aim` image format.  v1.6

`.aim` = Ascaron IMage RESource (`AIMRES2.00`). 2D images: HUD elements,
icons, cursors, cockpit art, station-interface backgrounds and UI texture
atlases. Ascaron's house format, shared with Port Royale 2 and Patrician III.

See AIM_SPEC.md for the format. In brief:

  0x00  "AIMRES2.00" + 6 zero bytes
  0x10  u32 flags
  0x14  "TILEDIM " + u32 size(=8) + u32 tile_count + u32 (0)
  0x28  one chunk PER TILE, chained back to back
  ...   footer, one of two shapes:
          u32 image_width, u32 image_height, u32 16, "IHHW", 3 u32
          u32 image_width, u32 image_height          (no IHHW block)

Tiles are laid out COLUMN-MAJOR: consecutive tiles run top to bottom, then the
next column. Each axis is cut into 256-pixel pieces with the remainder padded
up to the next power of two, so a 852x651 image becomes columns of
256+256+256+128 and rows of 256+256+256.

Supported chunk types:

  IMTC32 / IMTC24   uncompressed, BGRA / BGR
      tag(8), u32 bytes_per_pixel, u32 pitch, u32 (0), u32 data_size,
      pixel data, then a u32 tile_width, u32 tile_height TRAILER
  BMPRES / JPGRES / TGARES   a complete BMP / JPEG / TGA file embedded
      tag(8), u32 width, u32 height, u32 data_size, file bytes
  IMJPG24A / IMJPG24 / IMJPG32   a JPEG plus a separate alpha channel
      tag(8), u32 (80), u32 width, u32 height, u32 payload_size, u32 jpeg_size,
      JPEG bytes, then an SLD chain holding the alpha
      IMJPG24A carries a 1-bit alpha mask, MSB first, w*h/8 bytes
  IMSLD32 and other IMSL* 32/16/8-bit   SLD-compressed
      tag(8), u32 a, u32 b, u32 width, u32 height, u32 payload_size, payload
  IMSLDXT1 / IMSLDXT3 / IMSLDXT5   SLD-compressed S3TC
      tag(8), u32 width, u32 height, u32 payload_size, payload
"""
import struct
from dataclasses import dataclass, field
from io import BytesIO
from typing import List, Optional

MAGIC = b'AIMRES2.00'
TAG_TILEDIM = b'TILEDIM '
TAG_FOOTER = b'IHHW'
HDR_SIZE = 0x28
MAX_BLOCK = 65536          # largest raw size of one SLD sub-block
TILE_STEP = 256            # tile grid step used by the shipped files

RAW_TAGS = {b'IMTC32  ': 4, b'IMTC24  ': 3}
FILE_TAGS = {b'BMPRES  ': 'BMP', b'JPGRES  ': 'JPEG', b'TGARES  ': 'TGA'}
JPEG_TAGS = {b'IMJPG24A': 1, b'IMJPG24 ': 0, b'IMJPG32 ': 8}   # alpha bits/pixel


class UnsupportedAim(Exception):
    pass


class CompressedAim(UnsupportedAim):
    """Raised when compressed data cannot be turned into pixels."""


@dataclass
class SubBlock:
    """One independently compressed SLD unit, <= 65536 bytes decompressed."""
    method: int
    raw_size: int
    flags: int
    raw: bytes             # the record the codec sees: raw_size, flags, widths, bits

    @property
    def widths(self):
        """The eight cumulative bucket bit-widths from the stream header."""
        w = struct.unpack_from('<I', self.raw, 8)[0]
        acc, out = 0, []
        for _ in range(8):
            acc += w & 0xf
            w >>= 4
            out.append(acc)
        return tuple(out)


@dataclass
class Tile:
    width: int
    height: int
    encoding: str
    pixels: Optional[bytes] = None                  # raw BGRA/BGR
    embedded: Optional[bytes] = None                # a whole BMP/JPEG/TGA file
    blocks: List[SubBlock] = field(default_factory=list)
    bytes_per_pixel: int = 4
    alpha_bits: int = 0        # IMJPG*: bits per pixel in the separate alpha
    unknown: tuple = ()

    @property
    def pixel_format(self):
        if self.encoding.startswith('IMSLDXT'):
            return self.encoding[4:]
        if self.encoding in ('IMTC24  ', 'IMTC24'):
            return 'BGR'
        if self.encoding.startswith('IMJPG'):
            return 'JPEG+A' if self.alpha_bits else 'JPEG'
        if self.encoding.strip() in FILE_TAGS_STR:
            return FILE_TAGS_STR[self.encoding.strip()]
        return 'BGRA'


FILE_TAGS_STR = {k.decode().strip(): v for k, v in FILE_TAGS.items()}


@dataclass
class AimImage:
    tile_count: int
    encoding: str
    tiles: List[Tile]
    image_size: tuple = (0, 0)
    flags: int = 18
    footer_extra: tuple = (0, 0, 0)
    has_ihhw: bool = True

    @property
    def is_compressed(self):
        return self.encoding.startswith('IMSL')

    @property
    def pixel_format(self):
        return self.tiles[0].pixel_format if self.tiles else 'BGRA'

    @property
    def columns(self):
        """Tiles grouped into columns, following the column-major layout."""
        if not self.tiles:
            return []
        img_h = self.image_size[1]
        cols, cur, h = [], [], 0
        for t in self.tiles:
            cur.append(t)
            h += t.height
            if h >= img_h or len(self.tiles) == 1:
                cols.append(cur)
                cur, h = [], 0
        if cur:
            cols.append(cur)
        return cols

    @property
    def stored_size(self):
        cols = self.columns
        if not cols:
            return (0, 0)
        return (sum(c[0].width for c in cols), sum(t.height for t in cols[0]))

    @property
    def grid(self):
        cols = self.columns
        return (len(cols), len(cols[0]) if cols else 0)


def _parse_blocks(data, off, end):
    blocks = []
    while off < end:
        if off + 13 > end:
            raise UnsupportedAim('truncated SLD sub-block header at 0x%x' % off)
        inner = struct.unpack_from('<I', data, off)[0]
        method = data[off + 4]
        raw_size, flags = struct.unpack_from('<2I', data, off + 5)
        if inner < 9 or off + 4 + inner > end:
            raise UnsupportedAim('bad sub-block length %d at 0x%x' % (inner, off))
        blocks.append(SubBlock(method, raw_size, flags, data[off + 5:off + 4 + inner]))
        off += 4 + inner
    if off != end:
        raise UnsupportedAim('SLD sub-block chain overran its payload')
    return blocks


def _parse_chunk(data, off):
    """Parse one tile chunk. Returns (Tile, next_offset)."""
    tag = data[off:off + 8]

    if tag in RAW_TAGS:
        bpp, pitch, _z, data_size = struct.unpack_from('<4I', data, off + 8)
        if bpp != RAW_TAGS[tag]:
            raise UnsupportedAim('%r with %d bytes/pixel' % (tag, bpp))
        start = off + 0x18
        pixels = data[start:start + data_size]
        tw, th = struct.unpack_from('<2I', data, start + data_size)
        if pitch and (pitch // bpp != tw or data_size // pitch != th):
            raise UnsupportedAim('tile trailer %dx%d disagrees with header'
                                 % (tw, th))
        return (Tile(tw, th, tag.decode().strip(), pixels=pixels,
                     bytes_per_pixel=bpp),
                start + data_size + 8)

    if tag in JPEG_TAGS:
        _unk, w, h, payload_size, jpeg_size = struct.unpack_from('<5I', data, off + 8)
        start = off + 0x1c
        end = off + 0x18 + payload_size
        a0 = start + jpeg_size
        t = Tile(w, h, tag.decode().strip(),
                 embedded=data[start:start + jpeg_size],
                 blocks=_parse_blocks(data, a0, end) if a0 < end else [])
        t.alpha_bits = JPEG_TAGS[tag]
        return t, end

    if tag in FILE_TAGS:
        w, h, data_size = struct.unpack_from('<3I', data, off + 8)
        start = off + 0x14
        return (Tile(w, h, tag.decode().strip(),
                     embedded=data[start:start + data_size]),
                start + data_size)

    if tag[:4] == b'IMSL':
        enc = tag.decode().strip()
        if enc.startswith('IMSLDXT'):
            w, h, csize = struct.unpack_from('<3I', data, off + 8)
            start = off + 0x14
        else:
            a, b, w, h, csize = struct.unpack_from('<5I', data, off + 8)
            start = off + 0x1c
        t = Tile(w, h, enc, blocks=_parse_blocks(data, start, start + csize))
        if not enc.startswith('IMSLDXT'):
            t.unknown = (a, b)
        return t, start + csize

    raise UnsupportedAim('unknown chunk tag %r at 0x%x' % (tag, off))


def parse(data: bytes) -> AimImage:
    if data[:10] != MAGIC:
        raise UnsupportedAim('not an .aim file (magic %r)' % data[:10])
    flags = struct.unpack_from('<I', data, 0x10)[0]
    if data[0x14:0x1c] != TAG_TILEDIM:
        raise UnsupportedAim('expected TILEDIM at 0x14, got %r' % data[0x14:0x1c])
    tile_count = struct.unpack_from('<I', data, 0x20)[0]

    tiles, off = [], HDR_SIZE
    for _ in range(tile_count):
        tile, off = _parse_chunk(data, off)
        tiles.append(tile)

    img_w = img_h = 0
    extra = (0, 0, 0)
    ok = False
    left = len(data) - off
    if left >= 16 and data[off + 12:off + 16] == TAG_FOOTER \
            and struct.unpack_from('<I', data, off + 8)[0] == 16:
        img_w, img_h = struct.unpack_from('<2I', data, off)
        ok = True
        if left >= 28:
            extra = struct.unpack_from('<3I', data, off + 16)
    elif left == 8:
        # Second footer variant: no IHHW block, just the logical size.
        # Seen on DS.aim, Ankunftshalle_01.aim and Bar_human_10.aim.
        w2, h2 = struct.unpack_from('<2I', data, off)
        if w2 and h2:
            img_w, img_h, ok = w2, h2, True

    enc = tiles[0].encoding if tiles else ''
    return AimImage(tile_count=tile_count, encoding=enc, tiles=tiles,
                    image_size=(img_w, img_h), flags=flags,
                    footer_extra=extra, has_ihhw=ok)


def decompress(block: SubBlock) -> bytes:
    """Decompress one SLD sub-block."""
    import sld
    if block.method != 1:
        raise UnsupportedAim('unknown SLD method %d' % block.method)
    out = sld.decompress(block.raw)
    if len(out) != block.raw_size:
        raise UnsupportedAim('SLD produced %d bytes, header said %d'
                             % (len(out), block.raw_size))
    return out


def tile_image(tile: Tile):
    """One tile as a PIL RGBA image."""
    from PIL import Image
    if tile.embedded is not None:
        img = Image.open(BytesIO(tile.embedded)).convert('RGBA')
        if tile.blocks and tile.alpha_bits:
            raw = b''.join(decompress(b) for b in tile.blocks)
            if tile.alpha_bits == 1:
                mask = Image.frombytes('1', (tile.width, tile.height), raw, 'raw', '1')
                img.putalpha(mask.convert('L'))
            else:
                img.putalpha(Image.frombytes('L', (tile.width, tile.height), raw))
        return img
    if tile.blocks:
        raw = b''.join(decompress(b) for b in tile.blocks)
        if tile.pixel_format not in ('BGRA', 'BGR'):
            raise CompressedAim('%s decompresses to %s blocks; no S3TC decoder'
                                % (tile.encoding, tile.pixel_format))
    else:
        raw = tile.pixels
    mode = 'BGRA' if tile.bytes_per_pixel == 4 else 'BGR'
    return Image.frombytes('RGBA' if mode == 'BGRA' else 'RGB',
                           (tile.width, tile.height), raw, 'raw', mode).convert('RGBA')


def to_image(aim: AimImage):
    """Assemble the tile grid into a PIL RGBA image, cropped to the logical size."""
    from PIL import Image
    cols = aim.columns
    if not cols:
        raise UnsupportedAim('no tiles')
    canvas = Image.new('RGBA', aim.stored_size)
    x = 0
    for col in cols:
        y = 0
        for t in col:
            canvas.paste(tile_image(t), (x, y))
            y += t.height
        x += col[0].width
    w, h = aim.image_size
    if not w or not h or w > canvas.width or h > canvas.height:
        return canvas
    return canvas.crop((0, 0, w, h))


def _pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def _split(total, step=TILE_STEP):
    """Cut an axis into `step`-sized pieces, last one padded to a power of two."""
    if total <= step:
        return [_pow2(total)]
    out = [step] * (total // step)
    rest = total % step
    if rest:
        out.append(_pow2(rest))
    return out


def from_image(img, tile_size=None, flags=18, footer_extra=(0, 0, 0),
               logical=None) -> bytes:
    """Encode a PIL image as an uncompressed IMTC32 .aim.

    `tile_size` may be None (derive the grid the way the shipped files do), an
    int (one square tile), or a (width, height) pair for a single tile.

    `logical` overrides the size written to the footer. Shipped single-tile
    pages do use this: `TexPage_8_3.aim` declares 1024x64 and `Warenhandel_BG`
    394x118, both while storing a 256x256 tile. The declared size is an
    addressing space over the stored run, and needs only to fit inside it.
    Declaring MORE pixels than the file holds is rejected here. It is also
    meaningless on a multi-tile file, where the footer size is the only thing
    defining the column count.
    """
    from PIL import Image
    img = img.convert('RGBA')
    w, h = img.size

    if tile_size is None:
        widths, heights = _split(w), _split(h)
    else:
        if isinstance(tile_size, (tuple, list)):
            tw, th = tile_size
        else:
            tw = th = tile_size
        if tw < w or th < h:
            raise ValueError('forced tile %dx%d is smaller than the image %dx%d'
                             % (tw, th, w, h))
        widths, heights = [tw], [th]

    canvas = Image.new('RGBA', (sum(widths), sum(heights)), (0, 0, 0, 0))
    canvas.paste(img, (0, 0))

    out = bytearray()
    out += MAGIC + b'\0' * 6
    out += struct.pack('<I', flags)
    out += TAG_TILEDIM + struct.pack('<3I', 8, len(widths) * len(heights), 0)

    x = 0
    for tw in widths:                               # column-major
        y = 0
        for th in heights:
            tile = canvas.crop((x, y, x + tw, y + th))
            out += b'IMTC32  ' + struct.pack('<4I', 4, tw * 4, 0, tw * th * 4)
            out += tile.tobytes('raw', 'BGRA')
            out += struct.pack('<2I', tw, th)       # per-tile trailer
            y += th
        x += tw

    lw, lh = logical if logical else (w, h)
    if logical and len(widths) * len(heights) > 1:
        raise ValueError('a logical-size override needs a single tile: the '
                         'footer size is what defines the column count')
    if logical and lw * lh > sum(widths) * sum(heights):
        raise ValueError('declared size %dx%d claims more pixels than the %dx%d '
                         'of image data; the engine sizes its texture from this '
                         'field and would overrun the buffer'
                         % (lw, lh, sum(widths), sum(heights)))
    out += struct.pack('<3I', lw, lh, 16) + TAG_FOOTER + struct.pack('<3I', *footer_extra)
    return bytes(out)


def describe(aim: AimImage) -> str:
    cols, rows = aim.grid
    sw, sh = aim.stored_size
    sizes = {(t.width, t.height) for t in aim.tiles}
    if len(aim.tiles) > 1:
        shape = ' (%s)' % ', '.join('%dx%d' % s for s in sorted(sizes)[:3])
        grid = ', %dx%d grid%s' % (cols, rows, shape)
    else:
        grid = ''
    blocks = sum(len(t.blocks) for t in aim.tiles)
    extra = ', %d SLD sub-block(s)' % blocks if blocks else ''
    return ('%dx%d (stored %dx%d), %s, %d tile(s)%s%s'
            % (aim.image_size[0], aim.image_size[1], sw, sh,
               aim.encoding, len(aim.tiles), grid, extra))
