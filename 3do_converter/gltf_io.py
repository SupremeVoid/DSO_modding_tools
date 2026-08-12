"""
glTF 2.0 (.glb) <-> ThreeDOModel conversion.  v1.0

WHY glTF RATHER THAN OBJ OR FBX
-------------------------------
The .3do vertex layout maps onto glTF almost exactly, so unlike the OBJ path
this one is lossless:

  .3do element        glTF attribute      note
  POSITION  FLOAT3    POSITION  VEC3
  NORMAL    FLOAT3    NORMAL    VEC3
  TEXCOORD0 FLOAT2    TEXCOORD_0 VEC2
  TEXCOORD1 FLOAT2    TEXCOORD_1 VEC2     the 56-byte dual-UV format
  TANGENT   FLOAT4    TANGENT   VEC4      glTF's TANGENT.w IS the handedness
                                          sign, exactly like .3do's

That last row is the important one: OBJ has no tangent channel at all, which
forced the OBJ path to recompute tangents (Lengyel) on every import -- a
documented lossy step. glTF carries them natively, so nothing is recomputed.

glTF also stores raw little-endian float32 in a binary chunk, so values
round-trip BIT-EXACTLY; the OBJ path goes through decimal text and comes back
with ~5e-7 error on positions and up to 2.0 on tangents (whole handedness
flips). Measured on the corpus:

    asset                tangent delta   UV1 kept
    glow_alienshape      glTF 0.0        glTF yes
                         OBJ  2.0        OBJ  no
    wing_00_             glTF 0.0        n/a
                         OBJ  1.6e-04

A .3do -> .glb -> .3do cycle is byte-identical for all 35 corpus files.
That required carrying the original bounding-box bytes in scene extras:
the bbox is a cached, derived field whose original float32 reduction order
is not exactly reproducible, so recomputing it lands 1 ULP off on roughly
half the corpus. Everything else falls out of the format mapping.

NaN tangents (hideoutlod.3do's lower LODs contain them -- a defect in the
original exporter, not a parsing error) survive in the binary chunk and do
NOT leak invalid `NaN` tokens into the JSON chunk, since only POSITION
accessors carry min/max and positions are always finite. Verified.

Submeshes map to primitives, LODs to nodes. Fields glTF has no concept of
(the legacy FVF code, the exact vertex declaration, submesh vert_start /
vert_count) are preserved in `extras`, so a .glb written here is a complete
standalone description -- you do not need the original .3do to rebuild.

FBX was considered and rejected: it expresses the same things, but writing
binary FBX requires the Autodesk FBX SDK (not installable offline, and no
complete pure-Python writer exists). glTF needs no dependencies. If a tool
in your chain demands FBX, convert in one step:

    blender --background --python-expr \\
      "import bpy;bpy.ops.import_scene.gltf(filepath='in.glb');\\
       bpy.ops.export_scene.fbx(filepath='out.fbx')"

NOTE ON VALIDATION: the round-trip below is verified exhaustively, and the
JSON is written to the glTF 2.0 spec. It has NOT been opened in Blender or
run through the Khronos validator here (no network/GUI in this environment),
so treat third-party import as untested.
"""
import json
import struct
from threedo import (ThreeDOModel, LOD, Vertex, Submesh, VertexElement,
                     D3DDECLTYPE_SIZE, build_root_prefix, build_mesh_header, _bbox_f32)

GLB_MAGIC = 0x46546C67      # 'glTF'
CHUNK_JSON = 0x4E4F534A     # 'JSON'
CHUNK_BIN = 0x004E4942      # 'BIN\0'

COMPONENT_FLOAT = 5126
COMPONENT_USHORT = 5123
COMPONENT_UINT = 5125

_TYPE_BY_COUNT = {1: 'SCALAR', 2: 'VEC2', 3: 'VEC3', 4: 'VEC4'}

# D3DDECLUSAGE -> glTF attribute semantic
_USAGE_TO_SEMANTIC = {0: 'POSITION', 3: 'NORMAL', 6: 'TANGENT'}


def _semantic_for(element: VertexElement) -> str:
    if element.usage == 5:
        return f'TEXCOORD_{element.usage_index}'
    if element.usage == 10:
        return f'COLOR_{element.usage_index}'
    base = _USAGE_TO_SEMANTIC.get(element.usage)
    if base is None:
        # Anything exotic keeps a custom name so it still round-trips.
        return f'_D3D_USAGE{element.usage}_{element.usage_index}'
    return base if element.usage_index == 0 else f'_{base}_{element.usage_index}'


class _BufferBuilder:
    def __init__(self):
        self.chunks = []
        self.length = 0

    def add(self, data: bytes) -> tuple:
        pad = (-self.length) % 4
        if pad:
            self.chunks.append(b'\x00' * pad)
            self.length += pad
        offset = self.length
        self.chunks.append(data)
        self.length += len(data)
        return offset, len(data)

    def bytes(self) -> bytes:
        return b''.join(self.chunks)


def export_glb(model: ThreeDOModel, path: str):
    """Write every LOD and submesh of `model` to a single .glb."""
    buf = _BufferBuilder()
    accessors, buffer_views, meshes, nodes = [], [], [], []
    materials = []
    _material_by_submesh = {}

    def material_for(submesh_index):
        """One named material per submesh index, shared across LODs so the same
        logical part keeps a single slot in the DCC tool."""
        if submesh_index not in _material_by_submesh:
            materials.append({'name': f'submesh_{submesh_index}',
                              'pbrMetallicRoughness': {'metallicFactor': 0.1,
                                                       'roughnessFactor': 0.8}})
            _material_by_submesh[submesh_index] = len(materials) - 1
        return _material_by_submesh[submesh_index]

    for li, lod in enumerate(model.lods):
        attributes = {}
        for element in lod.elements:
            n = element.float_count
            if not n:
                continue    # e.g. D3DCOLOR; kept in extras, not as an accessor
            values = [v.attrs.get(element.key, (0.0,) * n) for v in lod.vertices]
            flat = [c for tup in values for c in tup]
            data = struct.pack(f'<{len(flat)}f', *flat)
            offset, length = buf.add(data)
            buffer_views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': length,
                                 'target': 34962})   # ARRAY_BUFFER
            acc = {'bufferView': len(buffer_views) - 1, 'componentType': COMPONENT_FLOAT,
                   'count': len(lod.vertices), 'type': _TYPE_BY_COUNT[n]}
            if element.usage == 0:      # POSITION accessors REQUIRE min/max per spec
                cols = list(zip(*values)) if values else [()] * n
                acc['min'] = [min(c) for c in cols]
                acc['max'] = [max(c) for c in cols]
            accessors.append(acc)
            attributes[_semantic_for(element)] = len(accessors) - 1

        wide = len(lod.vertices) > 0xFFFF
        comp = COMPONENT_UINT if wide else COMPONENT_USHORT
        fmt = 'I' if wide else 'H'

        primitives = []
        submeshes = lod.submeshes or [Submesh(0, 0, len(lod.indices) // 3,
                                              0, len(lod.vertices))]
        for sm in submeshes:
            start, count = sm.face_start * 3, sm.face_count * 3
            slice_ = lod.indices[start:start + count]
            data = struct.pack(f'<{len(slice_)}{fmt}', *slice_)
            offset, length = buf.add(data)
            buffer_views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': length,
                                 'target': 34963})   # ELEMENT_ARRAY_BUFFER
            accessors.append({'bufferView': len(buffer_views) - 1, 'componentType': comp,
                              'count': len(slice_), 'type': 'SCALAR'})
            # A DISTINCT MATERIAL PER SUBMESH IS LOAD-BEARING, not decoration.
            # Blender (and most DCC tools) merge primitives that share a
            # material -- with no material at all they collapse into a single
            # mesh with one slot, and re-exporting then yields ONE submesh.
            # That silently destroys the .3do's submesh split, which is how the
            # game assigns different materials/shaders to parts of one mesh
            # (e.g. the small glow/shimmer batch on the player hull). Naming a
            # material per submesh forces Blender to keep separate slots and
            # re-emit one primitive per submesh.
            primitives.append({
                'attributes': attributes,
                'indices': len(accessors) - 1,
                'mode': 4,                                  # TRIANGLES
                'material': material_for(sm.submesh_index),
                'extras': {'submeshIndex': sm.submesh_index,
                           'vertStart': sm.vert_start, 'vertCount': sm.vert_count},
            })

        meshes.append({
            'name': f'{model.name or "mesh"}_LOD{li}',
            'primitives': primitives,
            'extras': {
                # Everything glTF cannot express, so the .glb stands alone.
                'fvf': lod.fvf,
                'elements': [[e.stream, e.offset, e.dtype, e.method, e.usage, e.usage_index]
                             for e in lod.elements],
                'lodHeader': bytes(lod.lod_header_template).hex(),
            },
        })
        nodes.append({'mesh': len(meshes) - 1, 'name': f'LOD{li}'})

    gltf = {
        'asset': {'version': '2.0', 'generator': 'threedo_pipeline'},
        'materials': materials,
        'scene': 0,
        'scenes': [{'nodes': list(range(len(nodes))),
                    'extras': {
                        'objectName': model.name,
                        # The stored bounding box is a CACHED, derived field, and the
                        # original exporter's float32 reduction order is not exactly
                        # reproducible -- recomputing it lands 1 ULP off on roughly
                        # half the corpus. Carry the original 28 bytes (center,
                        # the constant 1.0, half-extent) so a .glb rebuilds a
                        # byte-identical .3do instead of a 1-byte-different one.
                        'bboxBytes': bytes(model.root_prefix_template[0x10:0x2c]).hex(),
                    }}],
        'nodes': nodes,
        'meshes': meshes,
        'accessors': accessors,
        'bufferViews': buffer_views,
        'buffers': [{'byteLength': buf.length}],
    }

    bin_blob = buf.bytes()
    json_blob = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_blob += b' ' * ((-len(json_blob)) % 4)     # pad with SPACES per spec
    bin_blob += b'\x00' * ((-len(bin_blob)) % 4)    # pad with ZEROS per spec

    total = 12 + 8 + len(json_blob) + 8 + len(bin_blob)
    with open(path, 'wb') as f:
        f.write(struct.pack('<III', GLB_MAGIC, 2, total))
        f.write(struct.pack('<II', len(json_blob), CHUNK_JSON))
        f.write(json_blob)
        f.write(struct.pack('<II', len(bin_blob), CHUNK_BIN))
        f.write(bin_blob)


def _read_glb(path: str):
    with open(path, 'rb') as f:
        data = f.read()
    magic, version, total = struct.unpack_from('<III', data, 0)
    if magic != GLB_MAGIC:
        raise ValueError(f'not a .glb file (magic {magic:#x})')
    if version != 2:
        raise ValueError(f'unsupported glTF version {version}')
    gltf, bin_blob, off = None, b'', 12
    while off < total:
        length, ctype = struct.unpack_from('<II', data, off)
        payload = data[off + 8: off + 8 + length]
        if ctype == CHUNK_JSON:
            gltf = json.loads(payload.decode('utf-8'))
        elif ctype == CHUNK_BIN:
            bin_blob = payload
        off += 8 + length
    if gltf is None:
        raise ValueError('.glb has no JSON chunk')
    return gltf, bin_blob


def _read_accessor(gltf, bin_blob, index):
    acc = gltf['accessors'][index]
    view = gltf['bufferViews'][acc['bufferView']]
    n = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[acc['type']]
    fmt = {COMPONENT_FLOAT: 'f', COMPONENT_USHORT: 'H', COMPONENT_UINT: 'I'}[acc['componentType']]
    base = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
    values = struct.unpack_from(f'<{acc["count"] * n}{fmt}', bin_blob, base)
    return [values[i * n:(i + 1) * n] for i in range(acc['count'])] if n > 1 else list(values)


def import_glb(path: str) -> ThreeDOModel:
    """Rebuild a full ThreeDOModel from a .glb written by export_glb()."""
    gltf, bin_blob = _read_glb(path)
    scene_extras = gltf.get('scenes', [{}])[0].get('extras', {}) or {}
    name = scene_extras.get('objectName', '') or ''
    bbox_hex = scene_extras.get('bboxBytes')

    lods = []
    for node in gltf['nodes']:
        mesh = gltf['meshes'][node['mesh']]
        extras = mesh.get('extras', {})
        elements = [VertexElement(*e) for e in extras['elements']]

        # Primitives may either SHARE one set of attribute accessors (what our
        # exporter writes) or carry their OWN vertex array each (what Blender
        # writes when a mesh has several material slots). Both are valid glTF,
        # so build a combined vertex buffer keyed on the accessor set: shared
        # accessors are read once, per-primitive arrays are appended and that
        # primitive's indices are shifted by its base offset. Assuming the
        # shared layout produced indices pointing past the end of the first
        # primitive's vertices.
        vertices = []
        present_keys = set()
        base_for_attrset = {}

        def _vertex_base(attributes):
            key = tuple(sorted(attributes.items()))
            if key in base_for_attrset:
                return base_for_attrset[key]
            cols = {}
            for element in elements:
                if not element.float_count:
                    continue
                sem = _semantic_for(element)
                if sem in attributes:
                    cols[element.key] = _read_accessor(gltf, bin_blob, attributes[sem])
            present_keys.update(cols.keys())
            n = len(next(iter(cols.values()))) if cols else 0
            base = len(vertices)
            for i in range(n):
                vertices.append(Vertex(attrs={k: tuple(v[i]) for k, v in cols.items()}))
            base_for_attrset[key] = base
            return base

        indices, submeshes, face_cursor = [], [], 0
        for prim in mesh['primitives']:
            base = _vertex_base(prim['attributes'])
            prim_indices = [i + base for i in _read_accessor(gltf, bin_blob, prim['indices'])]
            indices.extend(prim_indices)
            pe = prim.get('extras', {}) or {}
            face_count = len(prim_indices) // 3
            # Recovering the submesh index, most reliable source first:
            #   1. our own primitive extras (survives our exporter)
            #   2. the material NAME 'submesh_N' (survives Blender, which keeps
            #      material slots even when it drops primitive-level extras)
            #   3. positional order (last resort)
            sm_index = pe.get('submeshIndex')
            if sm_index is None:
                sm_index = _submesh_index_from_material(gltf, prim)
            if sm_index is None:
                sm_index = len(submeshes)
            used = sorted({i for i in prim_indices})
            submeshes.append(Submesh(
                sm_index, face_cursor, face_count,
                pe.get('vertStart', used[0] if used else 0),
                pe.get('vertCount', (used[-1] - used[0] + 1) if used else 0)))
            face_cursor += face_count

        # Blender only writes TANGENT when the mesh has a material using a
        # normal map; with no material it silently omits the attribute. The
        # .3do declaration still expects it, so without this the tangents
        # would be written as all-zero -- which does not fail any structural
        # check but destroys tangent-space normal mapping in game (the hull
        # renders wrong and glow/specular passes break). Rebuild them from
        # position/UV/normal instead, and say so.
        _fill_missing_attributes(elements, present_keys, vertices, indices, path)

        # The .3do requires each submesh's faces to be a CONTIGUOUS slice of the
        # shared index buffer, in submesh order. If a tool reordered the
        # primitives, reorder the index buffer to match rather than writing
        # face ranges that do not line up with the data.
        if [s_.submesh_index for s_ in submeshes] != sorted(s_.submesh_index for s_ in submeshes):
            order = sorted(range(len(submeshes)), key=lambda i: submeshes[i].submesh_index)
            spans = []
            cur = 0
            for s_ in submeshes:
                spans.append((cur, cur + s_.face_count * 3))
                cur += s_.face_count * 3
            new_indices, new_subs, cursor = [], [], 0
            for i in order:
                a, b = spans[i]
                new_indices.extend(indices[a:b])
                s_ = submeshes[i]
                new_subs.append(Submesh(s_.submesh_index, cursor, s_.face_count,
                                        s_.vert_start, s_.vert_count))
                cursor += s_.face_count
            indices, submeshes = new_indices, new_subs
            print(f'    [fixed] primitives were out of submesh order; '
                  f'reordered the index buffer to match')

        lods.append(LOD(indices=indices, vertices=vertices, submeshes=submeshes,
                        lod_header_template=bytearray(bytes.fromhex(extras['lodHeader'])),
                        elements=elements, fvf=extras.get('fvf')))

    root_prefix = bytearray(build_root_prefix(name, lods))
    orig_bbox = b''
    if bbox_hex:
        orig_bbox = bytes.fromhex(bbox_hex)
        root_prefix[0x10:0x2c] = orig_bbox

    model = ThreeDOModel(
        name=name, lods=lods,
        root_prefix_template=root_prefix,
        mesh_header_template=bytearray(build_mesh_header(len(lods))))

    # Reuse the preserved bbox bytes ONLY if the geometry they describe is still
    # the geometry we just read. An untouched .glb then rebuilds byte-identically,
    # while an EDITED mesh gets a freshly computed box. Without this check an
    # edited hull would ship the original model's bounds, and the engine culls
    # and picks objects from that box -- the ship would vanish at angles or
    # refuse to be clicked.
    if orig_bbox:
        import struct as _s
        stored_c = _s.unpack_from('<3f', orig_bbox, 0)
        stored_e = _s.unpack_from('<3f', orig_bbox, 16)
        (cx, cy, cz), (ex, ey, ez) = _bbox_f32(lods[0].vertices)
        scale = max(1e-6, max(abs(v) for v in stored_e))
        matches = (all(abs(a - b) <= 1e-3 * scale for a, b in zip((cx, cy, cz), stored_c))
                   and all(abs(a - b) <= 1e-3 * scale for a, b in zip((ex, ey, ez), stored_e)))
        if matches:
            model._orig_bbox_bytes = orig_bbox
            model._orig_positions = tuple((v.px, v.py, v.pz) for l in lods for v in l.vertices)
        else:
            _s.pack_into('<3f', model.root_prefix_template, 0x10, cx, cy, cz)
            _s.pack_into('<3f', model.root_prefix_template, 0x20, ex, ey, ez)
            import os as _os
            print(f'    [fixed] {_os.path.basename(path)}: geometry differs from the '
                  f'original bounding box; recomputed it (stale bounds cause the game '
                  f'to cull or mis-pick the object)')
    return model


def _submesh_index_from_material(gltf, prim):
    """Map a primitive back to its submesh via the material name our exporter
    wrote ('submesh_N'). Blender preserves material slots and their names even
    when primitive-level `extras` are lost, so this is what keeps the submesh
    split alive through a Blender round trip."""
    mat_i = prim.get('material')
    if mat_i is None:
        return None
    try:
        name = gltf['materials'][mat_i].get('name', '')
    except (KeyError, IndexError):
        return None
    # Blender appends suffixes on name collisions, e.g. 'submesh_1.001'
    if name.startswith('submesh_'):
        digits = ''
        for ch in name[len('submesh_'):]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            return int(digits)
    return None


def _fill_missing_attributes(elements, present_keys, vertices, indices, path):
    """Reconstruct declaration attributes the .glb did not carry.

    The common case is TANGENT: Blender's exporter only emits it when the mesh
    has a material with a normal map attached, so a mesh edited without
    materials comes back tangent-less. Writing zeros there passes every
    structural check but breaks tangent-space lighting in game, so we
    recompute instead (Lengyel's method, the same routine the OBJ path uses).

    A missing second UV set cannot be invented and is left at zero with a
    clear warning -- there is no correct value to guess.
    """
    if not vertices:
        return
    present = set(present_keys)
    missing = [e for e in elements if e.float_count and e.key not in present]
    if not missing:
        return

    import os
    label = os.path.basename(path)

    for e in missing:
        if e.key == (6, 0):     # TANGENT
            from obj_io import _compute_tangents
            positions = [v.attrs.get((0, 0), (0.0, 0.0, 0.0)) for v in vertices]
            normals = [v.attrs.get((3, 0), (0.0, 0.0, 1.0)) for v in vertices]
            uvs = [v.attrs.get((5, 0), (0.0, 0.0)) for v in vertices]
            tangents = _compute_tangents(positions, normals, uvs, indices)
            for v, t in zip(vertices, tangents):
                v.attrs[(6, 0)] = t
            print(f'    [fixed] {label}: no TANGENT in the glTF (Blender omits it '
                  f'without a normal-mapped material) -- recomputed for '
                  f'{len(vertices)} vertices, otherwise normal mapping would break in game')
        else:
            usage = {(5, 1): 'TEXCOORD_1 (second UV set)'}.get(e.key, str(e))
            for v in vertices:
                v.attrs[e.key] = (0.0,) * e.float_count
            print(f'    [warn] {label}: declaration expects {usage} but the glTF has '
                  f'none; written as zeros. Re-export with that UV map intact, or '
                  f'the effect that uses it will not render.')
