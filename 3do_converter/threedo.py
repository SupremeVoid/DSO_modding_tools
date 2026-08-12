"""
Parser/writer for Darkstar One (Ascaron) .3do mesh files.
Container: 3DO -> MESH -> N x LOD -> M x submesh -> triangle-list geometry.

Reverse-engineered format summary (v1.0). Validated by byte-identical
round-trip (parse -> build reproduces the input exactly) on 35 sample files:
3 vertex formats, 1-3 LODs, 1-19 submeshes, 1 KB - 4.5 MB.
See SPEC.md for the derivation, the evidence behind each field, and the
list of earlier conclusions that later samples corrected.

Chunk-based container. Each chunk tag is stored as its name spelled
BACKWARDS, padded to 4 bytes:
    "3DO " -> "OD3 "   "MESH" -> "HSEM"   "LOD" -> "DOL "   "ATTR" -> "RTTA"
(The sibling .shd shadow format uses the same convention -- see shd.py.)

ROOT HEADER
  0x00  tag "OD3 " (== "3DO ")
  0x04  version string "00.2"
  0x08  reserved (0 in every sample)
  0x0c  count (1 in every sample; role unconfirmed, likely a constant)
  0x10  bbox center      (3 floats)  -- covers LOD0 ONLY, not all LODs
  0x1c  float, exactly 1.0 in every sample -- purpose unexplained; preserved
  0x20  bbox half-extent (3 floats)  -- covers LOD0 ONLY
  0x2c  reserved (0 in every sample)
  0x30  submesh_total (u32) -- number of entries in the table at 0x48; equals
                               the sum of every LOD's submesh count
  0x34  char[20] object name, NUL-padded (usually empty; the name lives in the
                 filename for most assets)
  0x48  submesh_total x (u16 submesh_index, u16 lod_index), LOD-major order
  ...   zero padding so the MESH chunk starts on a 16-byte boundary

MESH CHUNK
  +0    tag "HSEM" (== "MESH")
  +4    version string "00.1"
  +8    reserved (0)
  +12   lod_count (u32) -- number of LOD chunks that follow, back-to-back

LOD CHUNK (repeated lod_count times)
  +0    tag "DOL " (== "LOD")
  +4    reserved (0 in all 45 LOD chunks seen -- note .shd puts its index-width
        flag in the analogous slot, so watch this one if a wide .3do turns up)
  +8    submesh_count (u32) -- trailer records at the end of this chunk
  +12   vertex-format selector (u32), DUAL MEANING:
            bit 0x80000000 SET   -> a D3DVERTEXELEMENT9 declaration follows at
                                    +28; the low bits are the element count
                                    INCLUDING the D3DDECL_END terminator
            bit 0x80000000 CLEAR -> this field IS a legacy D3DFVF code and
                                    there is NO declaration block; the index
                                    buffer starts immediately at +28
  +16   index_count  (u32)
  +20   vertex_count (u32)  -- must be <= 65535; see "index width" below
  +24   vertex_stride (u32) -- always equals the stride implied by the
                               declaration/FVF; cross-checked on parse
  +28   vertex declaration (8 bytes per element + 8-byte D3DDECL_END),
        or nothing at all in the legacy FVF case
  ...   index buffer: index_count x uint16, flat triangle list, SHARED by every
        submesh in this LOD
  ...   [2-byte pad iff index_count is odd, keeping the vertex buffer 4-byte
         aligned -- confirmed via hideoutlod.3do]
  ...   vertex buffer: vertex_count x vertex_stride bytes, layout per the
        declaration
  ...   submesh_count x 24-byte trailer record:
          +0  tag "RTTA" (== "ATTR")
          +4  submesh_index (u32, 0-based)
          +8  face_start (u32, in TRIANGLES, not raw indices)
          +12 face_count (u32, triangles)
          +16 vert_start (u32)
          +20 vert_count (u32)
        Consecutive submeshes partition the shared buffers with no gap or
        overlap.

VERTEX FORMATS SEEN (all derived from the file, never assumed)
  48 bytes  POSITION:F3@0  NORMAL:F3@12  TEXCOORD0:F2@24  TANGENT:F4@32
            (tangent.w is a handedness sign, +1 or -1)
  56 bytes  as above plus TEXCOORD1:F2@32, tangent moved to @40
            (second UV set: glow/lightmap pass -- glow_alienshape, base,
             segshape27, mainshapelod_157)
  32 bytes  legacy D3DFVF 0x112 = XYZ | NORMAL | TEX1, no tangent
            (coll_stargate -- a collision hull)

INDEX WIDTH
  Every .3do sample uses uint16 indices and there is no width field. Content
  stays under the ceiling by construction: the largest single LOD is 57,986
  vertices, and files that would overflow are split across LODs (each LOD owns
  its own vertex buffer, so the 65,535 budget is PER-LOD, not per-file).
  Submeshes inside one LOD share that budget. The sibling .shd format does
  support 32-bit indices, so a wide .3do variant may exist in unsampled
  assets; parse() raises rather than truncating if it ever meets one.

REMAINING UNKNOWNS (preserved verbatim, never guessed)
  - the 1.0 float at 0x1c, and the meaning of the root count at 0x0c
  - why the bbox occasionally differs by 1 ULP from any recomputation
    (float32 reduction order in the original exporter; a derived cache field,
     so build() reuses the original bytes when geometry is unchanged)
"""
import struct
from dataclasses import dataclass, field
from typing import List

MAGIC_3DO  = b'OD3 '
MAGIC_MESH = b'HSEM'
MAGIC_LOD  = b'DOL '
MAGIC_ATTR = b'RTTA'

NAME_OFFSET  = 0x34
NAME_LEN     = 20   # CONFIRMED: 20, not 28 -- the 8 bytes after it are the LOD/submesh table
BBOX_CENTER_OFFSET  = 0x10
BBOX_F3_OFFSET      = 0x1c   # always exactly 1.0; purpose unexplained, preserved verbatim
BBOX_HALFEXT_OFFSET = 0x20
COUNT_B_OFFSET       = 0x30

LOD_DECL_OFFSET = 28    # vertex declaration starts here, relative to the LOD tag
VERTEX_STRIDE  = 48     # most common; NOT assumed -- real stride comes from the declaration
TRAILER_LEN    = 24

# --- Direct3D 9 vertex declaration (D3DVERTEXELEMENT9) ---------------------
# The block at LOD+28 that earlier passes mistook for an opaque "36-byte
# submesh/material table" is really a standard D3DVERTEXELEMENT9 array,
# 8 bytes per element, terminated by D3DDECL_END. It only looked like a
# constant because every sample until glow_alienshape.3do used the same
# vertex format. Decoding it is what makes this parser format-agnostic.
D3DDECLTYPE_SIZE = {0: 4, 1: 8, 2: 12, 3: 16, 4: 4, 5: 4, 6: 4, 7: 8, 8: 4,
                    9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 14: 4, 15: 4, 16: 8, 17: 0}
D3DDECLTYPE_NAME = {0: 'FLOAT1', 1: 'FLOAT2', 2: 'FLOAT3', 3: 'FLOAT4', 4: 'D3DCOLOR', 17: 'UNUSED'}
D3DDECLUSAGE_NAME = {0: 'POSITION', 1: 'BLENDWEIGHT', 2: 'BLENDINDICES', 3: 'NORMAL',
                     4: 'PSIZE', 5: 'TEXCOORD', 6: 'TANGENT', 7: 'BINORMAL',
                     8: 'TESSFACTOR', 9: 'POSITIONT', 10: 'COLOR', 11: 'FOG',
                     12: 'DEPTH', 13: 'SAMPLE'}
DECL_END_STREAM = 0xFF


@dataclass(frozen=True)
class VertexElement:
    stream: int
    offset: int
    dtype: int
    method: int
    usage: int
    usage_index: int

    @property
    def size(self):
        return D3DDECLTYPE_SIZE.get(self.dtype, 0)

    @property
    def float_count(self):
        return {0: 1, 1: 2, 2: 3, 3: 4}.get(self.dtype, 0)

    @property
    def key(self):
        return (self.usage, self.usage_index)

    def __str__(self):
        return (f'{D3DDECLUSAGE_NAME.get(self.usage, self.usage)}'
                f'{self.usage_index if self.usage_index else ""}'
                f':{D3DDECLTYPE_NAME.get(self.dtype, self.dtype)}@{self.offset}')


def parse_declaration(data: bytes, off: int):
    """Read a D3DVERTEXELEMENT9 array. Returns (elements, byte_length_including_END)."""
    elements = []
    cur = off
    while True:
        stream, offset, dtype, method, usage, usage_index = struct.unpack_from('<HHBBBB', data, cur)
        cur += 8
        if stream == DECL_END_STREAM:
            break
        if dtype not in D3DDECLTYPE_SIZE:
            raise UnsupportedFormat(f'unsupported D3DDECLTYPE {dtype} in vertex declaration at {cur - 8:#x}')
        elements.append(VertexElement(stream, offset, dtype, method, usage, usage_index))
        if len(elements) > 32:
            raise UnsupportedFormat('vertex declaration has no D3DDECL_END within 32 elements')
    return elements, cur - off


# --- Legacy D3DFVF path -----------------------------------------------------
# When the flags field's 0x80000000 bit is CLEAR, there is no declaration
# block at all: the field is a legacy fixed-function D3DFVF code and the
# index buffer starts immediately at LOD+28. Confirmed on coll_stargate.3do
# (flags 0x112 = XYZ|NORMAL|TEX1 -> 12+12+8 = 32 = its stored stride).
D3DFVF_POSITION_MASK = 0x00E
D3DFVF_XYZ           = 0x002
D3DFVF_XYZRHW        = 0x004
D3DFVF_NORMAL        = 0x010
D3DFVF_PSIZE         = 0x020
D3DFVF_DIFFUSE       = 0x040
D3DFVF_SPECULAR      = 0x080
D3DFVF_TEXCOUNT_MASK = 0xF00
D3DFVF_TEXCOUNT_SHIFT = 8
DECL_PRESENT_BIT     = 0x80000000


def elements_from_fvf(fvf: int):
    """Translate a D3DFVF bitfield into the same VertexElement list the
    declaration path produces, so everything downstream is format-agnostic."""
    elements = []
    off = 0

    def add(dtype, usage, usage_index=0):
        nonlocal off
        elements.append(VertexElement(0, off, dtype, 0, usage, usage_index))
        off += D3DDECLTYPE_SIZE[dtype]

    pos = fvf & D3DFVF_POSITION_MASK
    if pos == D3DFVF_XYZ:
        add(2, 0)                       # POSITION FLOAT3
    elif pos == D3DFVF_XYZRHW:
        add(3, 9)                       # POSITIONT FLOAT4
    elif pos:
        raise UnsupportedFormat(f'unsupported D3DFVF position bits {pos:#x} in {fvf:#x}')

    if fvf & D3DFVF_NORMAL:
        add(2, 3)                       # NORMAL FLOAT3
    if fvf & D3DFVF_PSIZE:
        add(0, 4)                       # PSIZE FLOAT1
    if fvf & D3DFVF_DIFFUSE:
        add(4, 10, 0)                   # COLOR0 D3DCOLOR
    if fvf & D3DFVF_SPECULAR:
        add(4, 10, 1)                   # COLOR1 D3DCOLOR

    tex_count = (fvf & D3DFVF_TEXCOUNT_MASK) >> D3DFVF_TEXCOUNT_SHIFT
    for i in range(tex_count):
        # Per-set sizes live in the FVF high word; every sample uses the
        # default (2 floats). Anything else would need that decoded too.
        add(1, 5, i)                    # TEXCOORDi FLOAT2

    return elements


def build_declaration(elements) -> bytes:
    out = b''
    for e in elements:
        out += struct.pack('<HHBBBB', e.stream, e.offset, e.dtype, e.method, e.usage, e.usage_index)
    out += struct.pack('<HHBBBB', DECL_END_STREAM, 0, 17, 0, 0, 0)   # D3DDECL_END
    return out



@dataclass
class Vertex:
    """Declaration-driven vertex. `attrs` maps (usage, usage_index) -> tuple of
    floats, so any vertex format round-trips even if this code has never seen
    that particular combination of elements before. The px/py/pz/... properties
    are conveniences for the common POSITION/NORMAL/TEXCOORD0/TANGENT case."""
    attrs: dict

    def _get(self, usage, idx=0, n=0):
        return self.attrs.get((usage, idx), (0.0,) * n)

    @property
    def position(self): return self._get(0, 0, 3)
    @property
    def normal(self):   return self._get(3, 0, 3)
    @property
    def uv(self):       return self._get(5, 0, 2)
    @property
    def tangent(self):  return self._get(6, 0, 4)

    @property
    def px(self): return self.position[0]
    @property
    def py(self): return self.position[1]
    @property
    def pz(self): return self.position[2]
    @property
    def nx(self): return self.normal[0]
    @property
    def ny(self): return self.normal[1]
    @property
    def nz(self): return self.normal[2]
    @property
    def u(self):  return self.uv[0]
    @property
    def v(self):  return self.uv[1]

    @staticmethod
    def make(position, normal, uv, tangent, extra=None):
        a = {(0, 0): tuple(position), (3, 0): tuple(normal),
             (5, 0): tuple(uv), (6, 0): tuple(tangent)}
        if extra:
            a.update(extra)
        return Vertex(attrs=a)


@dataclass
class Submesh:
    submesh_index: int
    face_start: int
    face_count: int
    vert_start: int
    vert_count: int


@dataclass
class LOD:
    indices: List[int]          # flat triangle list for the WHOLE lod (all submeshes share it)
    vertices: List[Vertex]
    submeshes: List[Submesh]
    lod_header_template: bytearray  # raw bytes of THIS lod's header, up to the vertex declaration
    elements: list = field(default_factory=list)   # D3DVERTEXELEMENT9 vertex declaration
    fvf: int = None    # set only for legacy fixed-function files (no declaration block)
    # trailer tags are regenerated fresh (cheap, fully understood); no template needed

    @property
    def stride(self):
        return sum(e.size for e in self.elements)

    @property
    def format_summary(self):
        return ' '.join(str(e) for e in self.elements)


@dataclass
class ThreeDOModel:
    name: str                     # best-effort; '' for multi-LOD files (see parse())
    lods: List[LOD]
    root_prefix_template: bytearray   # raw bytes [0:mesh_offset), for byte-exact passthrough
    mesh_header_template: bytearray   # raw bytes [mesh_offset:mesh_offset+16)
    _orig_bbox_bytes: bytes = b''     # bbox+mystery-float bytes as originally parsed (0x10:0x2c)
    _orig_positions: tuple = ()       # position snapshot at parse time, to detect real edits

    @property
    def face_count(self):
        return sum(len(l.indices) for l in self.lods) // 3

    @property
    def vertex_count(self):
        return sum(len(l.vertices) for l in self.lods)


class UnsupportedFormat(Exception):
    pass


def _parse_lod(data: bytes, off: int) -> LOD:
    if data[off:off + 4] != MAGIC_LOD:
        raise UnsupportedFormat(f'expected LOD chunk at {off:#x}, got {data[off:off + 4]!r}')
    count_S   = struct.unpack_from('<I', data, off + 8)[0]
    idx_count = struct.unpack_from('<I', data, off + 16)[0]
    vtx_count = struct.unpack_from('<I', data, off + 20)[0]
    stride    = struct.unpack_from('<I', data, off + 24)[0]

    # The vertex layout comes from the file, never assumed. Two encodings exist:
    #   flags & 0x80000000 set  -> a D3DVERTEXELEMENT9 declaration block follows
    #   flags & 0x80000000 clear -> flags IS a legacy D3DFVF code, no block
    flags = struct.unpack_from('<I', data, off + 12)[0]
    if flags & DECL_PRESENT_BIT:
        elements, decl_len = parse_declaration(data, off + LOD_DECL_OFFSET)
        fvf = None
    else:
        elements, decl_len = elements_from_fvf(flags), 0
        fvf = flags
    computed_stride = sum(e.size for e in elements)
    if computed_stride != stride:
        raise UnsupportedFormat(
            f'LOD {off:#x}: declaration implies stride {computed_stride} but header says {stride}')

    lod_header_len = LOD_DECL_OFFSET + decl_len
    lod_header_template = bytearray(data[off:off + LOD_DECL_OFFSET])

    idx_off = off + lod_header_len
    # .3do indices are uint16 in all 35 samples (largest per-LOD vertex count
    # is 57,986 in propsshape_16 -- under the 65,535 ceiling). The companion .shd format DOES have
    # a 32-bit index mode, so a wide .3do variant may well exist; fail loudly
    # rather than silently truncating if one ever turns up.
    if vtx_count > 0xFFFF:
        raise UnsupportedFormat(
            f'LOD {off:#x}: {vtx_count} vertices exceeds the uint16 index ceiling. '
            f'This file must use a wider index format that this parser has not seen '
            f'(compare shd.py, where SLOD+0x0c selects uint16/uint32).')
    indices = list(struct.unpack_from(f'<{idx_count}H', data, idx_off))
    if any(i >= vtx_count for i in indices):
        raise UnsupportedFormat(f'LOD {off:#x}: index buffer references out-of-range vertex')

    idx_bytes_len = idx_count * 2
    pad = (-idx_bytes_len) % 4     # 4-byte alignment pad before the vertex buffer (CONFIRMED)
    vtx_off = idx_off + idx_bytes_len + pad

    vertices = []
    for i in range(vtx_count):
        base = vtx_off + i * stride
        attrs = {}
        for e in elements:
            n = e.float_count
            if n:
                attrs[e.key] = struct.unpack_from(f'<{n}f', data, base + e.offset)
            else:   # e.g. D3DCOLOR -- keep the raw 4 bytes so it still round-trips
                attrs[e.key] = struct.unpack_from('<I', data, base + e.offset)
        vertices.append(Vertex(attrs=attrs))

    trailer_off = vtx_off + vtx_count * stride
    submeshes = []
    for i in range(count_S):
        t = data[trailer_off + i * TRAILER_LEN: trailer_off + (i + 1) * TRAILER_LEN]
        tag, subidx, fstart, fcount, vstart, vcount = struct.unpack('<4sIIIII', t)
        if tag != MAGIC_ATTR:
            raise UnsupportedFormat(f'LOD {off:#x}: expected ATTR trailer #{i}, got {tag!r}')
        submeshes.append(Submesh(subidx, fstart, fcount, vstart, vcount))

    next_off = trailer_off + count_S * TRAILER_LEN
    lod = LOD(indices=indices, vertices=vertices, submeshes=submeshes,
              lod_header_template=lod_header_template, elements=elements, fvf=fvf)
    return lod, next_off


def parse(data: bytes) -> ThreeDOModel:
    if data[0:4] != MAGIC_3DO:
        raise UnsupportedFormat(f'not a 3DO file (root tag {data[0:4]!r})')

    mesh_off = data.find(MAGIC_MESH)
    if mesh_off == -1:
        raise UnsupportedFormat('no MESH chunk found')

    # Object name: a fixed 20-byte NUL-padded buffer, immediately followed by
    # the (submesh_index, lod_index) table -- see build_root_prefix().
    raw = data[NAME_OFFSET:NAME_OFFSET + NAME_LEN].split(b'\x00', 1)[0]
    try:
        name = raw.decode('ascii')
    except UnicodeDecodeError:
        name = ''

    root_prefix_template = bytearray(data[0:mesh_off])
    mesh_header_template = bytearray(data[mesh_off:mesh_off + 16])
    count_M = struct.unpack_from('<I', data, mesh_off + 12)[0]

    lods = []
    off = mesh_off + 16
    for _ in range(count_M):
        lod, off = _parse_lod(data, off)
        lods.append(lod)

    leftover = data[off:]
    if leftover:
        raise UnsupportedFormat(f'{len(leftover)} unexpected trailing bytes after {count_M} LOD '
                                 f'chunk(s) — file has more structure than this parser handles')

    orig_bbox_bytes = bytes(data[BBOX_CENTER_OFFSET:BBOX_HALFEXT_OFFSET + 12])
    orig_positions = tuple((v.px, v.py, v.pz) for lod in lods for v in lod.vertices)

    return ThreeDOModel(name=name, lods=lods,
                         root_prefix_template=root_prefix_template,
                         mesh_header_template=mesh_header_template,
                         _orig_bbox_bytes=orig_bbox_bytes,
                         _orig_positions=orig_positions)


def _bbox_f32(vertices: List[Vertex]):
    """Best-effort recompute, float32 arithmetic throughout (center = max - (max-min)*0.5).
    This exact formula was empirically found to reproduce the original stored bbox
    bit-for-bit on most samples; a couple of multi-submesh files show ~1e-7 residual
    differences on one axis (likely a SIMD reduction-order artifact in the original
    exporter) -- negligible for geometry, and irrelevant whenever build() can instead
    reuse the original bytes verbatim (see build(), which prefers that path when
    positions are unchanged from parse time)."""
    import struct as _s
    def axis(vals):
        mn = mx = vals[0]
        for x in vals[1:]:
            if x < mn: mn = x
            if x > mx: mx = x
        mn = _s.unpack('<f', _s.pack('<f', mn))[0]
        mx = _s.unpack('<f', _s.pack('<f', mx))[0]
        half = _s.unpack('<f', _s.pack('<f', (mx - mn) * 0.5))[0]
        center = _s.unpack('<f', _s.pack('<f', mx - half))[0]
        return center, half
    cx, ex = axis([v.px for v in vertices])
    cy, ey = axis([v.py for v in vertices])
    cz, ez = axis([v.pz for v in vertices])
    return (cx, cy, cz), (ex, ey, ez)


def _build_lod(lod: LOD) -> bytes:
    header = bytearray(lod.lod_header_template)
    if len(header) != LOD_DECL_OFFSET:
        raise ValueError(f'lod_header_template must be exactly {LOD_DECL_OFFSET} bytes')

    stride = lod.stride
    struct.pack_into('<I', header, 8, len(lod.submeshes))
    if lod.fvf is not None:
        struct.pack_into('<I', header, 12, lod.fvf)          # legacy FVF, no decl block
    else:
        struct.pack_into('<I', header, 12, DECL_PRESENT_BIT | (len(lod.elements) + 1))
    struct.pack_into('<I', header, 16, len(lod.indices))
    struct.pack_into('<I', header, 20, len(lod.vertices))
    struct.pack_into('<I', header, 24, stride)

    decl_bytes = b'' if lod.fvf is not None else build_declaration(lod.elements)

    idx_bytes = struct.pack(f'<{len(lod.indices)}H', *lod.indices)
    pad = (-len(idx_bytes)) % 4

    vtx_bytes = bytearray()
    for v in lod.vertices:
        buf = bytearray(stride)
        for e in lod.elements:
            vals = v.attrs.get(e.key)
            n = e.float_count
            if n:
                if vals is None:
                    vals = (0.0,) * n
                struct.pack_into(f'<{n}f', buf, e.offset, *vals)
            else:
                struct.pack_into('<I', buf, e.offset, *(vals if vals else (0,)))
        vtx_bytes += buf

    trailers = bytearray()
    for sm in lod.submeshes:
        trailers += struct.pack('<4sIIIII', MAGIC_ATTR, sm.submesh_index,
                                 sm.face_start, sm.face_count, sm.vert_start, sm.vert_count)

    return bytes(header) + decl_bytes + idx_bytes + b'\x00' * pad + bytes(vtx_bytes) + bytes(trailers)


def build(model: ThreeDOModel) -> bytes:
    root = bytearray(model.root_prefix_template)

    all_vertices = model.lods[0].vertices     # bbox is LOD0-only (see build_root_prefix)
    current_positions = tuple((v.px, v.py, v.pz) for lod in model.lods for v in lod.vertices)

    if model._orig_bbox_bytes and current_positions == model._orig_positions:
        # Geometry unchanged since parse() -- reuse the original bytes verbatim for a
        # guaranteed exact match instead of risking a sub-ULP mismatch on recompute.
        root[BBOX_CENTER_OFFSET:BBOX_HALFEXT_OFFSET + 12] = model._orig_bbox_bytes
    else:
        (cx, cy, cz), (ex, ey, ez) = _bbox_f32(all_vertices)
        struct.pack_into('<3f', root, BBOX_CENTER_OFFSET, cx, cy, cz)
        struct.pack_into('<3f', root, BBOX_HALFEXT_OFFSET, ex, ey, ez)
        # BBOX_F3_OFFSET left untouched (copied from template) -- constant in every sample so far

    count_B = sum(len(l.submeshes) for l in model.lods)
    struct.pack_into('<I', root, COUNT_B_OFFSET, count_B)

    if len(root) - NAME_OFFSET == NAME_LEN and model.name:
        name_bytes = model.name.encode('ascii')[:NAME_LEN - 1]
        root[NAME_OFFSET:NAME_OFFSET + NAME_LEN] = name_bytes + b'\x00' * (NAME_LEN - len(name_bytes))

    mesh_header = bytearray(model.mesh_header_template)
    struct.pack_into('<I', mesh_header, 12, len(model.lods))

    out = bytes(root) + bytes(mesh_header)
    for lod in model.lods:
        out += _build_lod(lod)
    return out


def build_root_prefix(name: str, lods) -> bytes:
    """Construct the root header + pre-MESH region FROM SCRATCH (rather than
    reusing a template), so entirely new models can be authored.

    Layout, confirmed exactly on all 27 sample files:
        0x00  "OD3 " + "00.2" + reserved(0) + count(1)
        0x10  bbox center (3f) + 1.0 + bbox half-extent (3f) + reserved(0)
        0x30  u32 submesh_total   -- number of entries in the table below
        0x34  char[20] object name, NUL-padded
        0x48  submesh_total x (u16 submesh_index, u16 lod_index), LOD-major
        ...   zero padding so the MESH chunk starts on a 16-byte boundary
    """
    # CONFIRMED: the stored bbox covers LOD0 (the highest-detail level) only,
    # not the union of all LODs -- LOD0 reproduces the stored half-extent
    # exactly on every multi-LOD sample, the union does not.
    (cx, cy, cz), (ex, ey, ez) = _bbox_f32(lods[0].vertices)

    out = bytearray()
    out += MAGIC_3DO + b'00.2' + struct.pack('<II', 0, 1)
    out += struct.pack('<3f', cx, cy, cz)
    out += struct.pack('<f', 1.0)             # constant in every sample
    out += struct.pack('<3f', ex, ey, ez)
    out += struct.pack('<I', 0)

    table = b''.join(struct.pack('<HH', s, li)
                     for li, lod in enumerate(lods)
                     for s in range(len(lod.submeshes)))
    out += struct.pack('<I', len(table) // 4)

    nb = name.encode('ascii')[:NAME_LEN - 1]
    out += nb + b'\x00' * (NAME_LEN - len(nb))
    out += table
    out += b'\x00' * ((-len(out)) % 16)       # MESH must land 16-byte aligned
    return bytes(out)


def build_mesh_header(lod_count: int) -> bytes:
    return MAGIC_MESH + b'00.1' + struct.pack('<II', 0, lod_count)
