#!/usr/bin/env python3
"""
aimvalidate — check Darkstar One .aim images and .tex atlas indexes.  v2.0

    aimvalidate.py path/to/images
    aimvalidate.py path/to/scripts --images path/to/images -v

Handles BOTH formats in one tool, because the interesting failures are about
the relationship between them: a `.tex` rectangle that falls outside its page,
or a page a `.tex` names that is not on disk, is invisible to a per-format
check yet breaks the game's interface.

Checks performed
  .aim  - parses cleanly; the chunk chain consumes the file exactly
        - the IHHW footer is present, its size field is 16
        - tile dimensions agree between chunk header and per-tile trailer
        - the declared logical size fits inside the tile grid
        - IMTC32 re-serialises byte-identically (proves nothing was misread)
        - SLD payloads decompress to exactly their declared raw size
  .tex  - A2DFILE header; body is a whole number of 284-byte records
        - the record count matches the declared sub-image count
        - every sub-image rectangle is non-empty
        - with --images: the named page exists, and every rectangle lies
          inside it; overlapping rectangles are reported as anomalies

Exit code is 0 when nothing failed, 1 otherwise, so it can gate a build.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aimcli
import aim_io
import a2dtex


def check_aim(path, rep, verbose):
    data = open(path, 'rb').read()
    aim = aim_io.parse(data)
    notes = []

    if not aim.has_ihhw:
        rep.anomaly(path, 'no IHHW footer (DS.aim-style variant); size unknown')
    for t in aim.tiles:
        if t.pixels is not None and len(t.pixels) != t.width * t.height * t.bytes_per_pixel:
            raise ValueError('tile %dx%d has %d bytes of pixels'
                             % (t.width, t.height, len(t.pixels)))
    for t in aim.tiles:
        for b in t.blocks:
            out = aim_io.decompress(b)
            if len(out) != b.raw_size:
                raise ValueError('SLD block gave %d bytes, header said %d'
                                 % (len(out), b.raw_size))
        if t.blocks:
            notes.append('%d SLD block(s) ok' % len(t.blocks))

    sw, sh = aim.stored_size
    iw, ih = aim.image_size
    reshaped = False
    if iw > sw or ih > sh:
        if iw * ih <= sw * sh:
            reshaped = True
            notes.append('declared %dx%d addresses the %dx%d grid as a '
                         'differently-shaped run' % (iw, ih, sw, sh))
        else:
            rep.anomaly(path, 'declared size %dx%d claims %d pixels but the tile '
                        'grid %dx%d only holds %d'
                        % (iw, ih, iw * ih, sw, sh, sw * sh))

    if aim.encoding == 'IMTC32':
        img = aim_io.to_image(aim)
        logical = aim.image_size if reshaped else None
        rebuilt = aim_io.from_image(img, None, aim.flags, aim.footer_extra,
                                    logical=logical)
        if rebuilt != data:
            rep.anomaly(path, 're-encode is not byte-identical (%d vs %d bytes)'
                        % (len(rebuilt), len(data)))
        else:
            notes.append('byte-identical re-encode')

    if verbose:
        rep.note(path, aim_io.describe(aim) + ('  [' + ', '.join(notes) + ']' if notes else ''))
    return aim


def check_tex(path, rep, images_dir, verbose):
    tp = a2dtex.parse(open(path, 'rb').read())
    page_img = None
    if images_dir:
        name = tp.page.replace('\\', '/').rsplit('/', 1)[-1]
        p = os.path.join(images_dir, name)
        if not os.path.exists(p):
            rep.anomaly(path, 'page %s not found in %s' % (name, images_dir))
        else:
            page_img = aim_io.parse(open(p, 'rb').read())

    boxes = []
    for s in tp.subimages:
        if s.w <= 0 or s.h <= 0:
            raise ValueError('%s has an empty rectangle %dx%d' % (s.stem, s.w, s.h))
        if page_img is not None:
            pw, ph = page_img.image_size if page_img.image_size[0] else page_img.stored_size
            if s.x + s.w > pw or s.y + s.h > ph:
                rep.anomaly(path, '%s at (%d,%d) %dx%d falls outside the %dx%d page'
                            % (s.stem, s.x, s.y, s.w, s.h, pw, ph))
        boxes.append((s.x, s.y, s.x + s.w, s.y + s.h, s.stem))

    overlaps = 0
    boxes.sort()
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if b[0] >= a[2]:
                break
            if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                overlaps += 1
    if overlaps:
        rep.anomaly(path, '%d overlapping rectangle pair(s)' % overlaps)

    if verbose:
        rep.note(path, '%s, %d sub-images' % (tp.page_stem, len(tp.subimages)))
    return tp


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('input', help='a .aim or .tex file, or a folder of them')
    p.add_argument('--images', metavar='DIR',
                   help='folder holding the pages, so .tex rectangles can be '
                        'checked against them')
    p.add_argument('-v', '--verbose', action='store_true',
                   help='print every check, not only problems')
    p.add_argument('-q', '--quiet', action='store_true')
    p.add_argument('--version', action='version', version='%(prog)s ' + aimcli.VERSION)
    args = p.parse_args()
    aimcli.require_pillow()

    files, _ = aimcli.collect_inputs(args.input, ('.aim', '.tex'))
    rep = aimcli.Reporter('Checked', args.quiet)
    for src in files:
        try:
            if src.lower().endswith('.tex'):
                check_tex(src, rep, args.images, args.verbose)
            else:
                check_aim(src, rep, args.verbose)
            rep.ok += 1
        except Exception as e:
            rep.error(src, str(e))
    return rep.summary()


if __name__ == '__main__':
    sys.exit(main())
