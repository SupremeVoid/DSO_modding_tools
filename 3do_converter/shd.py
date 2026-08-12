"""
Parser/writer for Darkstar One (Ascaron) .shd files.  v1.0

These sit next to .3do files in ds_3dobj/3DView/objects and hold a
STENCIL SHADOW VOLUME mesh: a separate, simplified, flat-shaded copy of the
object's silhouette geometry used to cast shadows. They are NOT shaders and
NOT textures. Not every .3do has one (small props / self-illuminated pieces
often don't).

Same reversed-4-byte-tag convention as .3do:
    "HWSV" -> stored "VSWH"   (HardWare Shadow Volume -- inferred, see SPEC.md)
    "SLOD" -> stored "DOLS"   (Shadow LOD)

Layout (CONFIRMED by byte-identical round-trip on all 24 sample files,
36 SLOD chunks, 800 bytes - 8.0 MB, both index widths):

  0x00  tag "VSWH" (== "HWSV")
  0x04  version string "00.1"
  0x08  u32 lod_count   -- CONFIRMED: number of SLOD chunks that follow.
                           Matches the companion .3do's LOD count exactly in
                           all 24 pairs (values of 1, 2 and 3 all observed).
  0x0c  u32 reserved (0 in every sample)
  then lod_count x SLOD chunk, back to back:
      +0x00  tag "DOLS" (== "SLOD")
      +0x04  u32 vertex_count
      +0x08  u32 index_count
      +0x0c  u32 index_width_flag -- CONFIRMED: 0 => uint16 indices,
                                     1 => uint32 indices. Earlier batches were
                                     all 0, which is why this looked reserved.
                                     Required once vertex_count > 65535
                                     (propsshape_16 has 102,942 vertices), but
                                     also used below that threshold
                                     (mainshapelod_157: 64,998 verts, still 32-bit).
      +0x10  vertex_count x 24-byte shadow vertex:
                 position   : 3 floats  -- occupies the same space as the
                                            render mesh; the shadow bbox
                                            reproduces the .3do's stored bbox
                                            exactly in 13 of 24 pairs and
                                            closely in the rest (the shadow
                                            hull is not a strict copy of the
                                            render hull, so do NOT rely on
                                            them being identical)
                 normal     : 3 floats  -- 94% are unit length; ~6% have other
                                            magnitudes and a handful are
                                            exactly zero. Treat this as a
                                            per-face extrusion vector, not a
                                            guaranteed unit normal.
      +...   index_count x (uint16 or uint32 per the flag), flat triangle list
      (no alignment padding observed. index_count is even in all 36 chunks
       seen, so whether .shd pads odd counts the way .3do does is UNTESTED.)

Shadow-volume construction (observed, for reference if you ever need to
generate one from scratch):
  - Positions are a position-welded subset of the render mesh (for
    gate_door_04: all 28 unique render positions are present, plus 1 extra).
    The shadow mesh is much coarser than the render mesh in vertex terms but
    has MORE triangles, because it includes the extrusion quads.
  - In simple meshes vertices are emitted in blocks of 6 sharing one face
    normal (clean in gate_door_04). This does NOT generalise: across the full
    corpus only ~15% of 6-vertex blocks share a single normal, so the real
    emission order is more complex and is NOT yet reverse-engineered.
  - The index buffer reuses vertices across blocks to stitch quad "walls"
    along shared edges (the classic silhouette-extrusion pattern).
  - Every file ends with a run of trailing vertices that NO index references
    (18 of 174 in gate_door_04; 36-540 elsewhere).
    Purpose unconfirmed -- most likely scratch space the runtime writes
    extruded/far-cap vertices into at draw time, which is why they're
    allocated in the file but never indexed. Preserved verbatim.

IMPORTANT for modding: because the shadow mesh is a separate mesh with its
own topology, editing a .3do does NOT update its .shd. See SPEC.md
"Shadow volumes and game compatibility" for what that means in practice.
"""
import struct
from dataclasses import dataclass, field
from typing import List

MAGIC_HWSV = b'VSWH'
MAGIC_SLOD = b'DOLS'

SHADOW_VERTEX_STRIDE = 24
FILE_HEADER_LEN = 0x10
SLOD_HEADER_LEN = 0x10


@dataclass
class ShadowVertex:
    px: float; py: float; pz: float
    nx: float; ny: float; nz: float


@dataclass
class ShadowLOD:
    vertices: List[ShadowVertex]
    indices: List[int]
    header_template: bytearray   # this SLOD chunk's own 16-byte header
    wide_indices: bool = False   # True => 32-bit index buffer

    @property
    def face_count(self):
        return len(self.indices) // 3

    @property
    def unused_tail_count(self):
        """Trailing vertices no index references (see module docstring)."""
        if not self.indices:
            return len(self.vertices)
        return len(self.vertices) - (max(self.indices) + 1)


@dataclass
class ShadowModel:
    lods: List[ShadowLOD]
    file_header_template: bytearray

    @property
    def vertex_count(self):
        return sum(len(l.vertices) for l in self.lods)

    @property
    def face_count(self):
        return sum(l.face_count for l in self.lods)


class UnsupportedShadowFormat(Exception):
    pass


def parse(data: bytes) -> ShadowModel:
    if data[0:4] != MAGIC_HWSV:
        raise UnsupportedShadowFormat(f'not a .shd file (root tag {data[0:4]!r})')

    lod_count = struct.unpack_from('<I', data, 8)[0]
    file_header_template = bytearray(data[0:FILE_HEADER_LEN])

    lods = []
    off = FILE_HEADER_LEN
    for li in range(lod_count):
        if data[off:off + 4] != MAGIC_SLOD:
            raise UnsupportedShadowFormat(
                f'expected SLOD chunk #{li} at {off:#x}, got {data[off:off + 4]!r}')
        vtx_count = struct.unpack_from('<I', data, off + 4)[0]
        idx_count = struct.unpack_from('<I', data, off + 8)[0]
        wide = struct.unpack_from('<I', data, off + 12)[0]
        if wide not in (0, 1):
            raise UnsupportedShadowFormat(
                f'SLOD #{li}: unexpected index-width flag {wide} at {off + 12:#x}')
        header_template = bytearray(data[off:off + SLOD_HEADER_LEN])

        voff = off + SLOD_HEADER_LEN
        vertices = [ShadowVertex(*struct.unpack_from('<6f', data, voff + i * SHADOW_VERTEX_STRIDE))
                    for i in range(vtx_count)]

        ioff = voff + vtx_count * SHADOW_VERTEX_STRIDE
        fmt = 'I' if wide else 'H'
        indices = list(struct.unpack_from(f'<{idx_count}{fmt}', data, ioff))
        if indices and max(indices) >= vtx_count:
            raise UnsupportedShadowFormat(
                f'SLOD #{li}: index {max(indices)} out of range for {vtx_count} vertices')

        lods.append(ShadowLOD(vertices=vertices, indices=indices,
                               header_template=header_template, wide_indices=bool(wide)))
        off = ioff + idx_count * (4 if wide else 2)

    leftover = data[off:]
    if leftover:
        raise UnsupportedShadowFormat(
            f'{len(leftover)} unexpected trailing bytes after {lod_count} SLOD chunk(s)')

    return ShadowModel(lods=lods, file_header_template=file_header_template)


def build(model: ShadowModel) -> bytes:
    header = bytearray(model.file_header_template)
    struct.pack_into('<I', header, 8, len(model.lods))
    # Accumulate into a list and join once. Appending to an immutable `bytes`
    # in a per-vertex loop is O(n^2) -- it copied the whole buffer on every
    # vertex, costing ~10s on an 8 MB shadow mesh. This is O(n).
    parts = [bytes(header)]

    for lod in model.lods:
        lh = bytearray(lod.header_template)
        # Force 32-bit indices whenever the vertex count demands it, even if the
        # source file was 16-bit -- otherwise an edited/grown mesh would silently
        # wrap around and produce corrupt geometry.
        wide = lod.wide_indices or len(lod.vertices) > 0xFFFF
        struct.pack_into('<I', lh, 4, len(lod.vertices))
        struct.pack_into('<I', lh, 8, len(lod.indices))
        struct.pack_into('<I', lh, 12, 1 if wide else 0)
        parts.append(bytes(lh))

        # One pack call for the whole vertex block rather than one per vertex.
        flat = []
        for v in lod.vertices:
            flat.extend((v.px, v.py, v.pz, v.nx, v.ny, v.nz))
        parts.append(struct.pack(f'<{len(flat)}f', *flat))

        parts.append(struct.pack(f'<{len(lod.indices)}{"I" if wide else "H"}', *lod.indices))

    return b''.join(parts)


def export_obj(model: ShadowModel, path: str, lod_index: int = 0):
    """Debug/inspection export. The shadow mesh has no UVs; normals are
    per-face, so this is only useful for looking at the silhouette hull,
    not for re-import."""
    lod = model.lods[lod_index]
    with open(path, 'w') as f:
        f.write(f'# shadow volume mesh, SLOD {lod_index} of {len(model.lods)}\n')
        f.write(f'o shadow_lod{lod_index}\n')
        for v in lod.vertices:
            f.write(f'v {v.px:.6f} {v.py:.6f} {v.pz:.6f}\n')
        for v in lod.vertices:
            f.write(f'vn {v.nx:.6f} {v.ny:.6f} {v.nz:.6f}\n')
        for i in range(0, len(lod.indices), 3):
            a, b, c = (lod.indices[i] + 1, lod.indices[i + 1] + 1, lod.indices[i + 2] + 1)
            f.write(f'f {a}//{a} {b}//{b} {c}//{c}\n')
