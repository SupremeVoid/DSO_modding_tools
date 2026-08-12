#!/usr/bin/env python3
"""
3do2obj — convert Darkstar One .3do meshes to Wavefront OBJ.  v1.0

    3do2obj.py ship.3do
    3do2obj.py path/to/objects -o inspect/ --all-lods

OBJ is a LOSSY path, provided for quick inspection only. Prefer 3do2gltf.py
for anything you intend to put back in the game:
  - OBJ has no tangent channel, so tangents are discarded and must be
    recomputed on import (measured error up to 2.0 -- whole handedness flips)
  - OBJ carries one UV set, so the 56-byte dual-UV format loses TEXCOORD_1
  - OBJ is decimal text, so positions come back with ~5e-7 error
Each LOD becomes its own .obj (OBJ has no LOD concept); submeshes become
`g submesh_N` groups.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dscli
from threedo import parse, UnsupportedFormat
import obj_io


def add_args(p):
    p.add_argument('--all-lods', action='store_true',
                   help='write every LOD (default: LOD0, the highest detail)')


def main(argv=None):
    args = dscli.build_parser(__doc__, '.3do', add_args).parse_args(argv)
    files, default_dir = dscli.collect_inputs(args.input, ('.3do',))
    outdir = dscli.resolve_output(args, default_dir)

    rep = dscli.Reporter('Converted')
    rep.outdir = outdir
    if not args.quiet:
        print(f'Converting {len(files)} file(s) to OBJ (lossy -- see --help):')

    for src in files:
        stem = os.path.splitext(os.path.basename(src))[0]
        try:
            with open(src, 'rb') as f:
                model = parse(f.read())

            targets = range(len(model.lods)) if args.all_lods else [0]
            if len(model.lods) > 1 and not args.all_lods:
                rep.anomaly(src, f'{len(model.lods)} LODs present; only LOD0 written '
                                 f'(use --all-lods for the rest)')
            for li in targets:
                suffix = f'_lod{li}' if len(model.lods) > 1 else ''
                dst = os.path.join(outdir, f'{stem}{suffix}.obj')
                if not dscli.guard_overwrite(dst, args.force, rep, src):
                    continue
                obj_io.export_obj(model, dst, lod_index=li)
                if not args.quiet:
                    rep.log(src, dst)
                else:
                    rep.ok += 1

            if any((5, 1) in (e.key for e in l.elements) for l in model.lods):
                rep.anomaly(src, 'has a second UV set (TEXCOORD_1) which OBJ cannot '
                                 'store; use 3do2gltf.py if you need it')
        except UnsupportedFormat as e:
            rep.error(src, f'unsupported .3do structure: {e}')
        except Exception as e:
            rep.error(src, f'{type(e).__name__}: {e}')

    return rep.summary()


if __name__ == '__main__':
    sys.exit(main())
