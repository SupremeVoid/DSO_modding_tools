#!/usr/bin/env python3
"""
png2aim — build uncompressed IMTC32 .aim files from PNGs.  v2.0

    png2aim.py edited.png --like originals/SidequestSymbol.aim -o mod/
    png2aim.py work/ --like-dir originals/ -o mod/

Pads to the tile grid the game uses, writes BGRA, and sets the footer so the
engine sees the correct logical size.

--like copies the tile size, flags and the trailing footer values (whose
meaning is not established) from the original file, so the engine sees exactly
the metadata it saw before and only the pixels change. Use it whenever you are
replacing a shipped asset.

There is no SLD compressor. None is needed: the engine accepts IMTC32 wherever
it shipped a compressed encoding.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aimcli
import aim_io


def extra(p):
    p.add_argument('--like', metavar='ORIGINAL.aim',
                   help='copy tile size, flags and footer values from this file')
    p.add_argument('--like-dir', metavar='DIR',
                   help='for folder input: find each PNG\'s original by name here')
    p.add_argument('--tile', type=int, metavar='N',
                   help='force a square tile size (power of two)')
    p.add_argument('--logical', metavar='WxH',
                   help='override the size written to the footer. Single-tile '
                        'files only, and the engine CRASHES if it disagrees with '
                        'the pixels -- see the README before using it.')


def main():
    args = aimcli.build_parser(__doc__, '.png', extra).parse_args()
    aimcli.require_pillow()
    from PIL import Image

    logical = None
    if args.logical:
        try:
            logical = tuple(int(v) for v in args.logical.lower().split('x'))
            if len(logical) != 2:
                raise ValueError
        except ValueError:
            raise SystemExit('error: --logical wants WxH, e.g. 1024x1024')

    files, default_dir = aimcli.collect_inputs(args.input, ('.png',))
    rep = aimcli.Reporter('Written', args.quiet)
    rep.outdir = aimcli.resolve_output(args, default_dir)

    for src in files:
        stem = os.path.splitext(os.path.basename(src))[0]
        ref_path = args.like
        if ref_path is None and args.like_dir:
            cand = os.path.join(args.like_dir, stem + '.aim')
            ref_path = cand if os.path.exists(cand) else None

        tile, flags, footer_extra = args.tile, 18, (0, 0, 0)
        if ref_path:
            try:
                ref = aim_io.parse(open(ref_path, 'rb').read())
            except Exception as e:
                rep.error(src, 'cannot read --like %s: %s' % (ref_path, e))
                continue
            flags, footer_extra = ref.flags, ref.footer_extra
            if tile is None and len(ref.tiles) == 1:
                tile = (ref.tiles[0].width, ref.tiles[0].height)
            rw, rh = ref.image_size
            sw, sh = ref.stored_size
            if logical is None and len(ref.tiles) == 1 and (rw > sw or rh > sh) \
                    and rw * rh == sw * sh:
                logical = (rw, rh)      # a reshaped page; keep its addressing space
        elif args.like_dir:
            rep.anomaly(src, 'no matching .aim in --like-dir; using defaults')

        dst = os.path.join(rep.outdir, stem + '.aim')
        if not aimcli.guard_overwrite(dst, args.force, rep, src):
            continue
        try:
            data = aim_io.from_image(Image.open(src), tile_size=tile, flags=flags,
                                     footer_extra=footer_extra, logical=logical)
            aim_io.parse(data)          # never write something we cannot read back
            open(dst, 'wb').write(data)
        except Exception as e:
            rep.error(src, str(e))
            continue
        rep.log(src, dst, aim_io.describe(aim_io.parse(data)))
        if ref_path:
            ref = aim_io.parse(open(ref_path, 'rb').read())
            if ref.image_size != aim_io.parse(data).image_size:
                rep.anomaly(src, 'size %dx%d differs from the original %dx%d; UI '
                            'elements are drawn at natural size'
                            % (aim_io.parse(data).image_size + ref.image_size))
    return rep.summary()


if __name__ == '__main__':
    sys.exit(main())
