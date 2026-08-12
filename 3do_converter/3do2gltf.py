#!/usr/bin/env python3
"""
3do2gltf — convert Darkstar One .3do meshes to glTF 2.0 (.glb).  v1.0

    3do2gltf.py ship.3do
    3do2gltf.py path/to/objects -o converted/

Every LOD and submesh goes into a single .glb. The exact vertex declaration,
legacy FVF mode, submesh ranges and original bounding-box bytes are stored in
glTF `extras`, so gltf23do.py can rebuild a byte-identical .3do from the .glb
alone -- you do not need to keep the original file.

Open the result in Blender, edit, export back to .glb, then run gltf23do.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dscli
from threedo import parse, UnsupportedFormat
import gltf_io


def add_args(p):
    p.add_argument('--report', action='store_true',
                   help='print mesh structure (LODs, submeshes, vertex format) per file')


def describe(model):
    bits = [f'{len(model.lods)} LOD(s)',
            f'{sum(len(l.submeshes) for l in model.lods)} submesh(es)',
            f'{model.vertex_count} verts', f'{model.face_count} tris']
    fmts = {l.stride for l in model.lods}
    bits.append('stride ' + '/'.join(str(s) for s in sorted(fmts)))
    if any(l.fvf is not None for l in model.lods):
        bits.append('legacy FVF')
    if any((5, 1) in (e.key for e in l.elements) for l in model.lods):
        bits.append('dual UV')
    return ', '.join(bits)


def check_anomalies(model, rep, src):
    for i, lod in enumerate(model.lods):
        nan = sum(1 for v in lod.vertices
                  for c in v.attrs.get((6, 0), ()) if c != c)
        if nan:
            rep.anomaly(src, f'LOD{i} contains {nan} NaN tangent components '
                             f'(present in the source file; preserved as-is)')
        if len(lod.vertices) > 60000:
            rep.anomaly(src, f'LOD{i} has {len(lod.vertices)} vertices, close to the '
                             f'65535 uint16 index ceiling -- keep edits from growing it')


def main(argv=None):
    args = dscli.build_parser(__doc__, '.3do', add_args).parse_args(argv)
    files, default_dir = dscli.collect_inputs(args.input, ('.3do',))
    outdir = dscli.resolve_output(args, default_dir)

    rep = dscli.Reporter('Converted')
    rep.outdir = outdir
    if not args.quiet:
        print(f'Converting {len(files)} file(s) to glTF 2.0:')

    for src in files:
        dst = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + '.glb')
        if not dscli.guard_overwrite(dst, args.force, rep, src):
            continue
        try:
            with open(src, 'rb') as f:
                model = parse(f.read())
            gltf_io.export_glb(model, dst)
            check_anomalies(model, rep, src)
            if not args.quiet:
                rep.log(src, dst, describe(model) if args.report else '')
            else:
                rep.ok += 1
        except UnsupportedFormat as e:
            rep.error(src, f'unsupported .3do structure: {e}')
        except Exception as e:
            rep.error(src, f'{type(e).__name__}: {e}')

    return rep.summary()


if __name__ == '__main__':
    sys.exit(main())
