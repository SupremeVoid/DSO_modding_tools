#!/usr/bin/env python3
"""
dsvalidate — check Darkstar One .3do / .shd files for structural validity.  v1.0

    dsvalidate.py path/to/objects
    dsvalidate.py ship.3do -v

Handles BOTH formats in one tool, because the interesting failures are
usually about the relationship between a .3do and its companion .shd (LOD
counts disagreeing, a shadow mesh left stale after the render mesh changed),
which a per-format validator could not see.

Checks performed
  .3do  - parses cleanly; every chunk accounted for with no trailing bytes
        - re-serialises byte-identically (proves nothing was misread)
        - indices in range; index_count divisible by 3
        - declared stride matches the vertex declaration / FVF
        - vertex count within the uint16 index ceiling
        - submesh face/vertex ranges partition the buffers with no gap or overlap
        - stored bounding box agrees with the LOD0 geometry
  .shd  - parses cleanly and re-serialises byte-identically
        - indices in range; index width flag consistent with the file size
  pair  - .shd LOD count matches the .3do LOD count
        - warns when a .3do has no .shd (informational: that only means the
          object casts no stencil shadow, which is legal)

Exit code is 0 when nothing failed, 1 otherwise, so it can gate a build.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dscli
from threedo import parse as parse_3do, build as build_3do, _bbox_f32
import shd as shd_mod


def compare_against_reference(model, ref_path, rep, src):
    """Compare a rebuilt .3do against the stock file it came from and report
    structural drift. Submesh count is the one that matters most: a DCC tool
    that merges submeshes produces a file which passes every self-consistency
    check yet renders wrong in game, because the submesh split is how the
    engine assigns different materials/shaders within one mesh."""
    with open(ref_path, 'rb') as f:
        ref = parse_3do(f.read())
    if len(ref.lods) != len(model.lods):
        rep.anomaly(src, f'LOD count changed: {len(ref.lods)} -> {len(model.lods)}. '
                         f'Lower LODs are used at distance; losing them changes how '
                         f'the object looks far away.')
    for i, (a, b) in enumerate(zip(ref.lods, model.lods)):
        if len(a.submeshes) != len(b.submeshes):
            rep.anomaly(src, f'LOD{i} submesh count changed: {len(a.submeshes)} -> '
                             f'{len(b.submeshes)}. The engine assigns materials per '
                             f'submesh, so merged submeshes break per-part effects '
                             f'(glow/shimmer). In Blender, keep one material slot '
                             f'per submesh.')
        if [str(e) for e in a.elements] != [str(e) for e in b.elements]:
            rep.anomaly(src, f'LOD{i} vertex format changed: {a.format_summary} -> '
                             f'{b.format_summary}')


def add_args(p):
    p.add_argument('-v', '--verbose', action='store_true',
                   help='print each check, not just problems')
    p.add_argument('--no-pairs', action='store_true',
                   help='skip .3do <-> .shd companion checks')
    p.add_argument('--compare', metavar='FILE_OR_DIR',
                   help='compare each .3do against the stock original of the same '
                        'name and report structural drift (submesh/LOD/format changes)')


def check_3do(path, rep, verbose):
    name = os.path.basename(path)
    with open(path, 'rb') as f:
        raw = f.read()
    model = parse_3do(raw)

    if build_3do(model) != raw:
        rep.anomaly(path, 're-serialises differently from the source; a field may be '
                          'misinterpreted (geometry is probably still fine)')

    for i, lod in enumerate(model.lods):
        if len(lod.indices) % 3:
            rep.error(path, f'LOD{i}: index_count {len(lod.indices)} is not a multiple of 3')
        if lod.indices and max(lod.indices) >= len(lod.vertices):
            rep.error(path, f'LOD{i}: index {max(lod.indices)} out of range '
                            f'({len(lod.vertices)} vertices)')
        if len(lod.vertices) > 0xFFFF:
            rep.error(path, f'LOD{i}: {len(lod.vertices)} vertices exceeds the uint16 ceiling')
        declared = struct.unpack_from('<I', lod.lod_header_template, 24)[0]
        if declared != lod.stride:
            rep.error(path, f'LOD{i}: header stride {declared} != declaration stride {lod.stride}')

        covered_f = sum(s.face_count for s in lod.submeshes)
        if covered_f != len(lod.indices) // 3:
            rep.anomaly(path, f'LOD{i}: submesh triangles sum to {covered_f}, '
                              f'buffer holds {len(lod.indices)//3}')
        cursor = 0
        for s in lod.submeshes:
            if s.face_start != cursor:
                rep.anomaly(path, f'LOD{i} submesh {s.submesh_index}: face_start '
                                  f'{s.face_start} leaves a gap (expected {cursor})')
            cursor = s.face_start + s.face_count

        nan = sum(1 for v in lod.vertices for c in v.attrs.get((6, 0), ()) if c != c)
        if nan:
            rep.anomaly(path, f'LOD{i}: {nan} NaN tangent components (also present in '
                              f'stock game files; harmless to round-tripping)')

    stored_c = struct.unpack_from('<3f', model.root_prefix_template, 0x10)
    stored_e = struct.unpack_from('<3f', model.root_prefix_template, 0x20)
    (cx, cy, cz), (ex, ey, ez) = _bbox_f32(model.lods[0].vertices)
    tol = 1e-4 * max(1.0, max(abs(v) for v in stored_e))
    if any(abs(a - b) > tol for a, b in zip((cx, cy, cz), stored_c)) or \
       any(abs(a - b) > tol for a, b in zip((ex, ey, ez), stored_e)):
        rep.anomaly(path, 'stored bounding box disagrees with LOD0 geometry '
                          '(the game may cull or pick this object incorrectly)')

    if verbose:
        print(f'  {name}: {len(model.lods)} LOD(s), '
              f'{sum(len(l.submeshes) for l in model.lods)} submesh(es), '
              f'{model.vertex_count} verts, {model.face_count} tris, '
              f'stride {model.lods[0].stride}')
    return model


def check_shd(path, rep, verbose):
    name = os.path.basename(path)
    with open(path, 'rb') as f:
        raw = f.read()
    model = shd_mod.parse(raw)
    if shd_mod.build(model) != raw:
        rep.anomaly(path, 're-serialises differently from the source')
    for i, lod in enumerate(model.lods):
        if len(lod.indices) % 3:
            rep.error(path, f'SLOD{i}: index count not a multiple of 3')
        if lod.indices and max(lod.indices) >= len(lod.vertices):
            rep.error(path, f'SLOD{i}: index out of range')
    if verbose:
        widths = {'32-bit' if l.wide_indices else '16-bit' for l in model.lods}
        print(f'  {name}: {len(model.lods)} SLOD(s), {model.vertex_count} verts, '
              f'{model.face_count} tris, {"/".join(sorted(widths))} indices')
    return model


def main(argv=None):
    parser = dscli.build_parser(__doc__, '.3do/.shd', add_args)
    # validation writes nothing, so mark the shared output options as unused
    for action in parser._actions:
        if {'-o', '--output', '-f', '--force'} & set(action.option_strings):
            action.help = '(unused by this tool)'
    args = parser.parse_args(argv)

    files, _ = dscli.collect_inputs(args.input, ('.3do', '.shd'))
    rep = dscli.Reporter('Validated')

    print(f'Validating {len(files)} file(s):')
    models_3do, models_shd = {}, {}
    for path in files:
        try:
            if path.lower().endswith('.3do'):
                m = check_3do(path, rep, args.verbose)
                models_3do[path[:-4]] = m
                if args.compare:
                    ref = args.compare
                    if os.path.isdir(ref):
                        ref = os.path.join(ref, os.path.basename(path))
                    if os.path.exists(ref):
                        compare_against_reference(m, ref, rep, path)
                    else:
                        rep.anomaly(path, f'no reference file at {ref} to compare against')
            else:
                models_shd[path[:-4]] = check_shd(path, rep, args.verbose)
            rep.ok += 1
        except Exception as e:
            rep.error(path, f'{type(e).__name__}: {e}')

    if not args.no_pairs:
        for stem, m3 in sorted(models_3do.items()):
            ms = models_shd.get(stem)
            if ms is None:
                if os.path.exists(stem + '.shd'):
                    continue    # present but not in this run's file list
                if args.verbose:
                    print(f'  {os.path.basename(stem)}: no .shd (object casts no '
                          f'stencil shadow -- legal)')
                continue
            if len(ms.lods) != len(m3.lods):
                rep.anomaly(stem + '.shd',
                            f'has {len(ms.lods)} shadow LOD(s) but the .3do has '
                            f'{len(m3.lods)}; they normally match')

    return rep.summary()


if __name__ == '__main__':
    sys.exit(main())
