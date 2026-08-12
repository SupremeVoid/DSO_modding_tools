"""
OBJ <-> ThreeDOModel conversion, LOD- and submesh-aware.  v1.0

OBJ has no tangent-space data, so on reimport we recompute per-vertex
tangent + handedness from position/normal/UV/face winding using the
standard Lengyel method. This is a deliberate, documented lossy step.

Multi-LOD files: OBJ has no native LOD concept, so export_obj() exports
ONE chosen LOD (default: LOD 0, the highest-detail level). export_all_lods()
writes each LOD to its own file for the rare case you need them all.

Multi-submesh LODs: each submesh's face range is written as its own
`g submesh_N` group. import_obj() reads those groups back so a re-imported
OBJ can rebuild the same multi-submesh split, as long as the group names
are left intact (e.g. after editing in Blender, which preserves them).
"""
import struct
from threedo import ThreeDOModel, LOD, Vertex, Submesh, VertexElement


def export_obj(model: ThreeDOModel, path: str, lod_index: int = 0, flip_v: bool = True):
    lod = model.lods[lod_index]
    label = model.name or 'mesh'
    with open(path, 'w') as f:
        f.write(f'# exported from {label}.3do (LOD {lod_index} of {len(model.lods)}) by threedo_pipeline\n')
        f.write(f'o {label}_lod{lod_index}\n')
        for v in lod.vertices:
            f.write(f'v {v.px:.6f} {v.py:.6f} {v.pz:.6f}\n')
        for v in lod.vertices:
            f.write(f'vn {v.nx:.6f} {v.ny:.6f} {v.nz:.6f}\n')
        for v in lod.vertices:
            vv = (1.0 - v.v) if flip_v else v.v
            f.write(f'vt {v.u:.6f} {vv:.6f}\n')

        submeshes = lod.submeshes or [Submesh(0, 0, len(lod.indices) // 3, 0, len(lod.vertices))]
        for sm in submeshes:
            f.write(f'g submesh_{sm.submesh_index}\n')
            f.write(f'usemtl submesh_{sm.submesh_index}\n')
            start = sm.face_start * 3
            end = start + sm.face_count * 3
            for i in range(start, end, 3):
                a, b, c = (lod.indices[i] + 1, lod.indices[i + 1] + 1, lod.indices[i + 2] + 1)
                f.write(f'f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n')


def export_all_lods(model: ThreeDOModel, base_path: str, flip_v: bool = True):
    """base_path e.g. '/tmp/thing.obj' -> writes thing_lod0.obj, thing_lod1.obj, ..."""
    assert base_path.endswith('.obj')
    stem = base_path[:-4]
    paths = []
    for i in range(len(model.lods)):
        p = f'{stem}_lod{i}.obj'
        export_obj(model, p, lod_index=i, flip_v=flip_v)
        paths.append(p)
    return paths


def _parse_obj(path: str, flip_v: bool):
    positions, normals, uvs, faces = [], [], [], []
    current_group = 0
    group_name_to_id = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('v '):
                positions.append(tuple(float(x) for x in line.split()[1:4]))
            elif line.startswith('vn '):
                normals.append(tuple(float(x) for x in line.split()[1:4]))
            elif line.startswith('vt '):
                parts = line.split()
                u, v = float(parts[1]), float(parts[2])
                uvs.append((u, (1.0 - v) if flip_v else v))
            elif line.startswith('g '):
                gname = line.split(None, 1)[1].strip()
                if gname not in group_name_to_id:
                    group_name_to_id[gname] = len(group_name_to_id)
                current_group = group_name_to_id[gname]
            elif line.startswith('f '):
                corners = []
                for tok in line.split()[1:4]:
                    parts = (tok.split('/') + ['', '', ''])[:3]
                    pi = int(parts[0])
                    ti = int(parts[1]) if parts[1] else None
                    ni = int(parts[2]) if parts[2] else None
                    corners.append((pi - 1, (ti - 1) if ti is not None else None,
                                     (ni - 1) if ni is not None else None))
                if len(corners) != 3:
                    raise NotImplementedError('non-triangular face found; triangulate before import')
                faces.append((current_group, corners))
    return positions, normals, uvs, faces


def import_obj(path: str, flip_v: bool = True) -> LOD:
    """Parses one OBJ file into a single LOD (with submeshes recovered from `g`
    groups, in the order first seen -- matching what export_obj() writes)."""
    positions, normals, uvs, faces = _parse_obj(path, flip_v)

    faces_sorted = sorted(range(len(faces)), key=lambda i: faces[i][0])
    ordered_faces = [faces[i] for i in faces_sorted]

    key_to_new = {}
    new_positions, new_normals, new_uvs = [], [], []
    out_indices = []
    submesh_bounds = []   # (group_id, face_start, face_count)
    cur_group = None
    cur_start = 0
    for face_i, (gid, corners) in enumerate(ordered_faces):
        if gid != cur_group:
            if cur_group is not None:
                submesh_bounds.append((cur_group, cur_start, face_i - cur_start))
            cur_group = gid
            cur_start = face_i
        for (pi, ti, ni) in corners:
            key = (pi, ti, ni)
            if key not in key_to_new:
                key_to_new[key] = len(new_positions)
                new_positions.append(positions[pi])
                new_normals.append(normals[ni] if ni is not None else (0.0, 0.0, 1.0))
                new_uvs.append(uvs[ti] if ti is not None else (0.0, 0.0))
            out_indices.append(key_to_new[key])
    if cur_group is not None:
        submesh_bounds.append((cur_group, cur_start, len(ordered_faces) - cur_start))

    tangents = _compute_tangents(new_positions, new_normals, new_uvs, out_indices)
    vertices = [Vertex.make(p, n, uv, t)
                for p, n, uv, t in zip(new_positions, new_normals, new_uvs, tangents)]

    # NOTE: submesh vertex ranges assume groups don't share vertices (true for
    # our own export). Faces always render correctly either way (indices are
    # globally valid) -- only the reported per-submesh vert_start/vert_count
    # could overlap instead of cleanly partition if that assumption is ever
    # violated by a hand-edited OBJ.
    submeshes = []
    for gid, fstart, fcount in submesh_bounds:
        face_idx = range(fstart * 3, (fstart + fcount) * 3)
        vs = sorted(set(out_indices[i] for i in face_idx))
        submeshes.append(Submesh(gid, fstart, fcount, vs[0], vs[-1] - vs[0] + 1))

    default_elements = [
        VertexElement(0,  0, 2, 0, 0, 0),   # POSITION FLOAT3
        VertexElement(0, 12, 2, 0, 3, 0),   # NORMAL   FLOAT3
        VertexElement(0, 24, 1, 0, 5, 0),   # TEXCOORD0 FLOAT2
        VertexElement(0, 32, 3, 0, 6, 0),   # TANGENT  FLOAT4
    ]
    return LOD(indices=out_indices, vertices=vertices, submeshes=submeshes,
               lod_header_template=bytearray(28),   # placeholder; filled in by replace_lod()
               elements=default_elements)


def _compute_tangents(positions, normals, uvs, indices):
    """Per-vertex tangent + handedness (Lengyel's method), accumulated per
    triangle then orthogonalized against the normal."""
    n = len(positions)
    tan1 = [[0.0, 0.0, 0.0] for _ in range(n)]
    tan2 = [[0.0, 0.0, 0.0] for _ in range(n)]

    for i in range(0, len(indices), 3):
        i1, i2, i3 = indices[i], indices[i + 1], indices[i + 2]
        p1, p2, p3 = positions[i1], positions[i2], positions[i3]
        w1, w2, w3 = uvs[i1], uvs[i2], uvs[i3]

        x1, y1, z1 = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
        x2, y2, z2 = p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]
        s1, t1 = w2[0] - w1[0], w2[1] - w1[1]
        s2, t2 = w3[0] - w1[0], w3[1] - w1[1]

        denom = s1 * t2 - s2 * t1
        r = 1.0 / denom if abs(denom) > 1e-12 else 0.0
        sdir = ((t2 * x1 - t1 * x2) * r, (t2 * y1 - t1 * y2) * r, (t2 * z1 - t1 * z2) * r)
        tdir = ((s1 * x2 - s2 * x1) * r, (s1 * y2 - s2 * y1) * r, (s1 * z2 - s2 * z1) * r)

        for idx in (i1, i2, i3):
            tan1[idx][0] += sdir[0]; tan1[idx][1] += sdir[1]; tan1[idx][2] += sdir[2]
            tan2[idx][0] += tdir[0]; tan2[idx][1] += tdir[1]; tan2[idx][2] += tdir[2]

    out = []
    for i in range(n):
        nx, ny, nz = normals[i]
        tx, ty, tz = tan1[i]
        d = nx * tx + ny * ty + nz * tz
        ox, oy, oz = tx - nx * d, ty - ny * d, tz - nz * d
        length = (ox * ox + oy * oy + oz * oz) ** 0.5
        if length < 1e-8:
            ox, oy, oz = 1.0, 0.0, 0.0
        else:
            ox, oy, oz = ox / length, oy / length, oz / length
        cx, cy, cz = ny * tz - nz * ty, nz * tx - nx * tz, nx * ty - ny * tx
        w = 1.0 if (cx * tan2[i][0] + cy * tan2[i][1] + cz * tan2[i][2]) >= 0 else -1.0
        out.append((ox, oy, oz, w))
    return out


def replace_lod(model: ThreeDOModel, lod_index: int, new_lod: LOD) -> ThreeDOModel:
    """Return a NEW ThreeDOModel with model.lods[lod_index]'s geometry swapped
    for new_lod, reusing that LOD's own original header template, vertex
    declaration and legacy-FVF mode (so non-standard vertex formats survive)
    and leaving every other LOD untouched. This is the recommended Phase-3 path for multi-LOD assets:
    re-export/edit/reimport just the LOD you're working on."""
    import copy
    new_model = copy.deepcopy(model)
    src_lod = model.lods[lod_index]
    new_lod.lod_header_template = bytearray(src_lod.lod_header_template)
    # Keep the ORIGINAL vertex declaration so non-standard formats (e.g. the
    # dual-UV, 56-byte layout in glow_alienshape.3do) survive the round trip.
    # Attributes the OBJ can't carry (extra UV sets) are refilled below.
    new_lod.elements = list(src_lod.elements)
    # Carry the legacy-FVF marker too, or a fixed-function file would silently
    # be rewritten as a declaration file (different bytes, different layout).
    new_lod.fvf = src_lod.fvf
    _refill_missing_attrs(src_lod, new_lod)
    new_model.lods[lod_index] = new_lod
    # NOTE: deliberately NOT force-clearing _orig_positions here -- build() already
    # detects a real change by comparing current vertex positions against the
    # snapshot taken at parse() time. Forcing it would make even a genuine no-op
    # replacement lose the byte-exact bbox reuse path for no reason.
    return new_model


def _refill_missing_attrs(src_lod: LOD, new_lod: LOD):
    """OBJ carries only position/normal/uv0. If the original LOD's declaration
    has extra elements (a second TEXCOORD set, vertex colours, ...), those
    values would otherwise be written as zeros and silently corrupt the asset.

    We restore them by nearest-position lookup against the original vertices.
    Exact for an unmodified re-import (positions match bit-for-bit); for edited
    geometry it copies the value from the closest original vertex, which is a
    reasonable approximation but IS an approximation -- warn loudly so nobody
    ships a lightmapped mesh assuming it survived untouched."""
    extra_keys = [e.key for e in src_lod.elements if e.key not in ((0, 0), (3, 0), (5, 0), (6, 0))]
    if not extra_keys:
        return

    lookup = {}
    for v in src_lod.vertices:
        lookup.setdefault(tuple(round(c, 5) for c in v.position), v)

    exact = 0
    for v in new_lod.vertices:
        key = tuple(round(c, 5) for c in v.position)
        match = lookup.get(key)
        if match is not None:
            exact += 1
            for k in extra_keys:
                v.attrs[k] = match.attrs[k]
        else:
            for k in extra_keys:
                v.attrs[k] = tuple(0.0 for _ in range(
                    next(e.float_count for e in src_lod.elements if e.key == k)))

    names = ', '.join(str(k) for k in extra_keys)
    print(f'    [warn] declaration has non-OBJ attributes {names}; '
          f'restored {exact}/{len(new_lod.vertices)} by position match')
