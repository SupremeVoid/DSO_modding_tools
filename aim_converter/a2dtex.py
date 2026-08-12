"""
Reader for Ascaron `.tex` texture-page index files (A2dLib resource type
`SH_TEXPG`, "CTexturePage — A Texture page containing 2d images for Drawables").

These sit in the game's `scripts/` folder and are what maps a named UI graphic
to the atlas page that actually holds it. Without them, editing interface art
is guesswork: the standalone `images\\*.aim` files are the packer's *sources*
and are not what the game draws at runtime.

FORMAT
------
  Header, 28 bytes:
    char[8]  "A2DFILE\\0"
    u32      28              header size
    u32      17              unknown, constant in every sample
    u32      0, 0, 0

  Then (filesize - 28) / 284 fixed-size records of 284 bytes.

  Record 0 -- the page itself:
    char[8]  "SH_TEXPG"
    u32      284             record size
    u32      count           number of sub-image records following
    char[]   page filename, NUL-terminated, zero-padded to the record

  Records 1..count -- one per packed sub-image:
    u32      284             record size
    u32      x, y, w, h      the sub-image's rectangle within the page
    char[]   source filename, NUL-terminated, zero-padded to the record

Every rectangle in every sample lies inside its page's bounds.
"""
import struct

MAGIC = b'A2DFILE\0'
TAG = b'SH_TEXPG'
HEADER_SIZE = 28
RECORD_SIZE = 284


class UnsupportedTex(Exception):
    pass


class SubImage:
    __slots__ = ('name', 'x', 'y', 'w', 'h', 'page')

    def __init__(self, name, x, y, w, h, page):
        self.name, self.x, self.y, self.w, self.h, self.page = name, x, y, w, h, page

    @property
    def box(self):
        """(left, upper, right, lower), ready for PIL crop/paste."""
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    @property
    def stem(self):
        """Bare name without the images\\ prefix or .aim suffix."""
        s = self.name.replace('\\', '/').rsplit('/', 1)[-1]
        return s[:-4] if s.lower().endswith('.aim') else s

    def __repr__(self):
        return '<%s %dx%d at (%d,%d) in %s>' % (self.stem, self.w, self.h,
                                                self.x, self.y, self.page)


class TexturePage:
    def __init__(self, page, subimages):
        self.page = page                  # e.g. 'images\\TexPage_8_2.aim'
        self.subimages = subimages

    @property
    def page_stem(self):
        s = self.page.replace('\\', '/').rsplit('/', 1)[-1]
        return s[:-4] if s.lower().endswith('.aim') else s

    def __repr__(self):
        return '<TexturePage %s, %d sub-images>' % (self.page_stem, len(self.subimages))


def _cstr(data, off):
    end = data.find(b'\0', off)
    return data[off:end].decode('latin1')


def parse(data: bytes) -> TexturePage:
    if data[:8] != MAGIC:
        raise UnsupportedTex('not an A2DFILE (magic %r)' % data[:8])
    if (len(data) - HEADER_SIZE) % RECORD_SIZE:
        raise UnsupportedTex('body is not a whole number of %d-byte records'
                             % RECORD_SIZE)
    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    if n < 1:
        raise UnsupportedTex('no records')

    off = HEADER_SIZE
    if data[off:off + 8] != TAG:
        raise UnsupportedTex('expected %r, got %r' % (TAG, data[off:off + 8]))
    count = struct.unpack_from('<I', data, off + 12)[0]
    page = _cstr(data, off + 16)
    if count != n - 1:
        raise UnsupportedTex('count %d disagrees with %d records' % (count, n - 1))

    subs = []
    for i in range(1, n):
        off = HEADER_SIZE + i * RECORD_SIZE
        x, y, w, h = struct.unpack_from('<4I', data, off + 4)
        subs.append(SubImage(_cstr(data, off + 20), x, y, w, h, page))
    return TexturePage(page, subs)


def load_index(paths):
    """Parse several .tex files into {source stem (lowercased): [SubImage, ...]}.

    A graphic can legitimately appear in more than one page, so values are
    lists. Returns (index, pages).
    """
    index, pages = {}, []
    for p in paths:
        with open(p, 'rb') as f:
            tp = parse(f.read())
        pages.append(tp)
        for s in tp.subimages:
            index.setdefault(s.stem.lower(), []).append(s)
    return index, pages
