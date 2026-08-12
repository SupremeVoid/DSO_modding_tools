#!/usr/bin/env python3
"""
aim2png — convert Darkstar One / Ascaron .aim images to PNG.  v2.0

    aim2png.py path/to/staticImages -o work/
    aim2png.py hud.aim --info

SLD-compressed payloads are decompressed and multi-tile grids reassembled
automatically. Output is RGBA, cropped to the image's real logical size.

Files whose payload decompresses to S3TC blocks (IMSLDXT1/3/5) are reported
and skipped; no DXT decoder is included.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aimcli
import aim_io


def extra(p):
    p.add_argument('--padded', action='store_true',
                   help='export the full power-of-two tile grid instead of '
                        'cropping to the logical size')
    p.add_argument('--info', action='store_true',
                   help='describe each file and convert nothing')


def main():
    args = aimcli.build_parser(__doc__, '.aim', extra).parse_args()
    aimcli.require_pillow()
    files, default_dir = aimcli.collect_inputs(args.input, ('.aim',))
    rep = aimcli.Reporter('Converted', args.quiet)

    if not args.info:
        rep.outdir = aimcli.resolve_output(args, default_dir)

    for src in files:
        try:
            aim = aim_io.parse(open(src, 'rb').read())
        except aim_io.UnsupportedAim as e:
            rep.error(src, str(e))
            continue

        if args.info:
            print('  %-30s %s' % (os.path.basename(src), aim_io.describe(aim)))
            rep.ok += 1
            continue

        if aim.pixel_format.startswith('DXT'):
            rep.skip(src, 'decompresses to %s blocks, no S3TC decoder'
                     % aim.pixel_format)
            continue

        dst = os.path.join(rep.outdir,
                           os.path.splitext(os.path.basename(src))[0] + '.png')
        if not aimcli.guard_overwrite(dst, args.force, rep, src):
            continue
        try:
            img = _render(aim, args.padded)
            img.save(dst)
        except Exception as e:
            rep.error(src, str(e))
            continue
        rep.log(src, dst, aim_io.describe(aim))
        if aim.image_size[0] > aim.stored_size[0] or aim.image_size[1] > aim.stored_size[1]:
            rep.anomaly(src, 'declared size %dx%d exceeds the tile grid %dx%d'
                        % (aim.image_size + aim.stored_size))

    return rep.summary()


def _render(aim, padded):
    from PIL import Image
    if not padded:
        return aim_io.to_image(aim)
    full = Image.new('RGBA', aim.stored_size)
    x = 0
    for col in aim.columns:
        y = 0
        for t in col:
            full.paste(aim_io.tile_image(t), (x, y))
            y += t.height
        x += col[0].width
    return full


if __name__ == '__main__':
    sys.exit(main())
