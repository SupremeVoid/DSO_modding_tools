#!/usr/bin/env python3
"""aimfind -- locate a sprite inside .aim texture pages.

  aimfind.py SPRITE.png FILE.aim [FILE.aim ...] [--tolerance N] [--alpha]

Searches every .aim for a region matching SPRITE.png and reports the file and
the pixel position of each hit. Use it when a UI graphic appears in several
`TexPage_*` atlases and you need to know which pages carry it.

Crop the sprite out of a converted page with any image editor and feed the crop
back in; the match is done on exact pixel values, with a tolerance for the
small differences JPEG-backed pages introduce.
"""
import argparse, os, sys
import numpy as np
from PIL import Image
import aim_io


def load_page(path):
    aim = aim_io.parse(open(path, 'rb').read())
    return aim_io.to_image(aim)


def find(page, tpl, tol, use_alpha):
    ch = 4 if use_alpha else 3
    P = np.asarray(page, dtype=np.int16)[:, :, :ch]
    T = np.asarray(tpl, dtype=np.int16)[:, :, :ch]
    ph, pw = P.shape[:2]
    th, tw = T.shape[:2]
    if th > ph or tw > pw:
        return []

    # cheap prefilter: a handful of sample points must match before we compare
    # the whole template, which keeps a full-page scan tractable in pure numpy
    pts = [(0, 0), (th - 1, 0), (0, tw - 1), (th - 1, tw - 1),
           (th // 2, tw // 2), (th // 3, tw // 3), (2 * th // 3, 2 * tw // 3)]
    ys, xs = ph - th + 1, pw - tw + 1
    ok = np.ones((ys, xs), dtype=bool)
    for dy, dx in pts:
        win = P[dy:dy + ys, dx:dx + xs]
        ok &= (np.abs(win - T[dy, dx]).max(axis=2) <= tol)
        if not ok.any():
            return []

    hits = []
    for y, x in zip(*np.nonzero(ok)):
        if np.abs(P[y:y + th, x:x + tw] - T).max() <= tol:
            hits.append((int(x), int(y)))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('sprite')
    ap.add_argument('files', nargs='+')
    ap.add_argument('--tolerance', type=int, default=8,
                    help='per-channel tolerance, 0 for exact (default 8)')
    ap.add_argument('--alpha', action='store_true',
                    help='match the alpha channel too')
    a = ap.parse_args()

    tpl = Image.open(a.sprite).convert('RGBA')
    print('sprite %dx%d, tolerance %d\n' % (tpl.width, tpl.height, a.tolerance))
    total = 0
    for path in a.files:
        try:
            page = load_page(path)
        except Exception as e:
            print('%-22s -- %s' % (os.path.basename(path), e))
            continue
        hits = find(page, tpl, a.tolerance, a.alpha)
        if hits:
            total += len(hits)
            print('%-22s %d hit(s): %s' % (os.path.basename(path), len(hits),
                                           ', '.join('(%d,%d)' % h for h in hits[:8])))
    print('\n%d match(es) across %d file(s)' % (total, len(a.files)))
    return 0 if total else 1


if __name__ == '__main__':
    sys.exit(main())
