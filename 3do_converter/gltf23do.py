#!/usr/bin/env python3
"""
gltf23do — convert glTF 2.0 (.glb) back to Darkstar One .3do.  v1.0

    gltf23do.py ship.glb
    gltf23do.py edited/ -o game/objects/

Works best on .glb files written by 3do2gltf.py, which embed the original
vertex declaration, legacy-FVF mode and bounding box in glTF `extras`. A .glb
from elsewhere (or one a DCC tool stripped `extras` from) still converts, but
falls back to the standard 48-byte vertex layout -- the tool says so rather
than silently guessing.

IMPORTANT LIMITS (the tool checks these and refuses rather than shipping a
broken asset):
  - each LOD must stay under 65,535 vertices (.3do indices are uint16)
  - the mesh must be triangulated
Editing a .3do does NOT update its companion .shd shadow mesh; see
validate_shd.py and SPEC.md.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dscli
from threedo import build, parse, UnsupportedFormat
import gltf_io


def add_args(p):
    p.add_argument('--no-verify', action='store_true',
                   help='skip re-parsing the written .3do to confirm it reads back')


def main(argv=None):
    args = dscli.build_parser(__doc__, '.glb', add_args).parse_args(argv)
    files, default_dir = dscli.collect_inputs(args.input, ('.glb',))
    outdir = dscli.resolve_output(args, default_dir)

    rep = dscli.Reporter('Converted')
    rep.outdir = outdir
    if not args.quiet:
        print(f'Converting {len(files)} file(s) to .3do:')

    for src in files:
        dst = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + '.3do')
        if not dscli.guard_overwrite(dst, args.force, rep, src):
            continue
        try:
            model = gltf_io.import_glb(src)

            over = [(i, len(l.vertices)) for i, l in enumerate(model.lods)
                    if len(l.vertices) > 0xFFFF]
            if over:
                i, n = over[0]
                rep.error(src, f'LOD{i} has {n} vertices, over the 65,535 uint16 index '
                               f'ceiling. Split the mesh or decimate it; the .3do format '
                               f'cannot address this many vertices in one LOD.')
                continue

            data = build(model)

            if not args.no_verify:
                # Re-parse what we just produced. Writing a file the parser
                # cannot read back is the one failure mode that would waste the
                # user's time inside the game, so it is checked by default.
                try:
                    parse(data)
                except Exception as e:
                    rep.error(src, f'produced a .3do that fails to re-parse: {e}')
                    continue

            with open(dst, 'wb') as f:
                f.write(data)

            note = ''
            if not any(l.fvf is None for l in model.lods):
                note = 'legacy FVF preserved'
            if not args.quiet:
                rep.log(src, dst, note)
            else:
                rep.ok += 1

            shd = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + '.shd')
            if os.path.exists(shd):
                rep.anomaly(src, 'a matching .shd exists and was NOT regenerated; its '
                                 'shadow silhouette will still match the OLD geometry')
        except Exception as e:
            rep.error(src, f'{type(e).__name__}: {e}')

    return rep.summary()


if __name__ == '__main__':
    sys.exit(main())
