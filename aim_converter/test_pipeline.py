#!/usr/bin/env python3
"""
test_pipeline — regression suite for the Darkstar One image tools.  v2.0

    python3 test_pipeline.py path/to/samples [--scripts DIR] [--images DIR]

Point it at a folder of real `.aim` files. With --scripts/--images it also
exercises the atlas layer. Everything here is a property that must hold on
shipped data, not a synthetic fixture:

  parse          every .aim parses and its chunk chain consumes the file exactly
  roundtrip      IMTC32 decodes to pixels and re-encodes BYTE-IDENTICALLY, with
                 the tile grid re-derived from scratch rather than copied --
                 which independently proves the column-major order, the 256-step
                 split and the power-of-two remainder rule
  forced-tile    the same, with the tile size copied from the original
  sld            every compressed block decompresses to its declared raw size
  tex            .tex records are self-consistent and rectangles lie in-page
  atlas          a sprite extracted by name and patched straight back produces
                 a page identical to the original
"""
import os
import sys
import glob
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aim_io
import a2dtex


class Suite:
    def __init__(self):
        self.passed = 0
        self.failed = []

    def check(self, name, cond, detail=''):
        if cond:
            self.passed += 1
        else:
            self.failed.append('%s: %s' % (name, detail))
            print('  FAIL %s  %s' % (name, detail))

    def done(self):
        print('\n%d passed, %d failed' % (self.passed, len(self.failed)))
        return 1 if self.failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('samples', help='folder of .aim files')
    ap.add_argument('--scripts', help='folder of .tex files')
    ap.add_argument('--images', help='folder of atlas pages')
    a = ap.parse_args()
    s = Suite()

    files = sorted(glob.glob(os.path.join(a.samples, '*.aim')))
    if not files:
        sys.exit('no .aim files in %s' % a.samples)
    print('%d .aim files\n' % len(files))

    for path in files:
        name = os.path.basename(path)
        raw = open(path, 'rb').read()
        try:
            aim = aim_io.parse(raw)
        except Exception as e:
            s.check('parse ' + name, False, str(e))
            continue
        s.check('parse ' + name, True)

        for t in aim.tiles:
            for b in t.blocks:
                try:
                    out = aim_io.decompress(b)
                    s.check('sld ' + name, len(out) == b.raw_size,
                            'got %d want %d' % (len(out), b.raw_size))
                except Exception as e:
                    s.check('sld ' + name, False, str(e))

        if aim.encoding == 'IMTC32':
            img = aim_io.to_image(aim)
            iw, ih = aim.image_size
            sw, sh = aim.stored_size
            # a page may declare an addressing space that is not the grid's
            # shape; preserve it or the footer cannot match
            logical = aim.image_size if (iw > sw or ih > sh) else None
            again = aim_io.from_image(img, None, aim.flags, aim.footer_extra,
                                      logical=logical)
            s.check('roundtrip ' + name, again == raw,
                    'derived grid differs (%d vs %d bytes)' % (len(again), len(raw)))
            forced = aim_io.from_image(
                img, (aim.tiles[0].width, aim.tiles[0].height) if len(aim.tiles) == 1
                else None, aim.flags, aim.footer_extra, logical=logical)
            s.check('forced-tile ' + name, forced == raw, 'copied tile size differs')

    if a.scripts:
        texs = sorted(glob.glob(os.path.join(a.scripts, '*.tex')))
        print('\n%d .tex files' % len(texs))
        for path in texs:
            name = os.path.basename(path)
            try:
                tp = a2dtex.parse(open(path, 'rb').read())
                s.check('tex ' + name, True)
            except Exception as e:
                s.check('tex ' + name, False, str(e))
                continue
            if not a.images:
                continue
            page = os.path.join(a.images, tp.page.replace('\\', '/').rsplit('/', 1)[-1])
            if not os.path.exists(page):
                continue
            pa = aim_io.parse(open(page, 'rb').read())
            pw, ph = pa.image_size if pa.image_size[0] else pa.stored_size
            bad = [x.stem for x in tp.subimages
                   if x.x + x.w > pw or x.y + x.h > ph]
            s.check('tex-bounds ' + name, not bad, '%d out of page: %s'
                    % (len(bad), ', '.join(bad[:3])))

            if pa.encoding == 'IMTC32' and tp.subimages:
                sub = tp.subimages[0]
                canvas = aim_io.to_image(pa)
                crop = canvas.crop(sub.box)
                canvas.paste(crop, (sub.x, sub.y))
                rebuilt = aim_io.from_image(canvas, None, pa.flags, pa.footer_extra)
                s.check('atlas ' + name, rebuilt == open(page, 'rb').read(),
                        'extract+patch of %s changed the page' % sub.stem)

    return s.done()


if __name__ == '__main__':
    sys.exit(main())
