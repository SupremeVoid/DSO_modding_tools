#!/usr/bin/env python3
"""aimatlas -- work with Ascaron UI texture atlases through their .tex indexes.

  aimatlas.py find   NAME          [--scripts DIR]
  aimatlas.py list   [PAGE]        [--scripts DIR]
  aimatlas.py extract NAME|--all   [--scripts DIR] [--images DIR] [-o OUTDIR]
  aimatlas.py patch  NAME NEW.png  [--scripts DIR] [--images DIR] [--out FILE]

The `.tex` files in the game's `scripts/` folder map every named UI graphic to
the atlas page that holds it and its rectangle within that page. The standalone
`images\\*.aim` files are the packer's sources -- the game draws from the atlas,
so replacing a standalone file changes nothing. Edit the rectangle instead.

  find      where does this graphic live?
  list      what does a page contain?
  extract   cut a graphic (or all of them) out of its page as PNG
  patch     paste an edited PNG back into its page, writing a new .aim
"""
import argparse, glob, os, sys
import a2dtex, aim_io


def load(scripts):
    paths = sorted(glob.glob(os.path.join(scripts, '*.tex')))
    if not paths:
        sys.exit('no .tex files in %s -- point --scripts at the game\'s scripts folder'
                 % scripts)
    return a2dtex.load_index(paths)


def page_path(images, page):
    name = page.replace('\\', '/').rsplit('/', 1)[-1]
    p = os.path.join(images, name)
    if not os.path.exists(p):
        sys.exit('page %s not found in %s' % (name, images))
    return p


def resolve(index, name):
    hits = index.get(name.lower())
    if not hits:
        near = [k for k in index if name.lower() in k]
        msg = 'no graphic named %r' % name
        if near:
            msg += '\ndid you mean: %s' % ', '.join(sorted(near)[:8])
        sys.exit(msg)
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=['find', 'list', 'extract', 'patch'])
    ap.add_argument('args', nargs='*')
    ap.add_argument('--scripts', default='.', help="the game's scripts folder (*.tex)")
    ap.add_argument('--images', default='.', help="folder holding the TexPage_*.aim pages")
    ap.add_argument('-o', '--outdir', default='.')
    ap.add_argument('--out', help='output .aim path for patch (default: overwrite nothing, write <page>.new.aim)')
    ap.add_argument('--all', action='store_true', help='extract every sub-image')
    a = ap.parse_args()
    index, pages = load(a.scripts)

    if a.command == 'find':
        if not a.args:
            sys.exit('usage: aimatlas.py find NAME')
        for s in resolve(index, a.args[0]):
            print('%-34s %4d x %-4d at (%4d, %4d)  in  %s'
                  % (s.stem, s.w, s.h, s.x, s.y, s.page))
        return 0

    if a.command == 'list':
        if a.args:
            want = a.args[0].lower().replace('.aim', '')
            pages = [p for p in pages if want in p.page_stem.lower()]
            if not pages:
                sys.exit('no page matching %r' % a.args[0])
        for p in pages:
            print('\n%s  (%d sub-images)' % (p.page_stem, len(p.subimages)))
            for s in sorted(p.subimages, key=lambda s: (s.y, s.x)):
                print('   %4d,%4d  %4dx%-4d  %s' % (s.x, s.y, s.w, s.h, s.stem))
        return 0

    if a.command == 'extract':
        os.makedirs(a.outdir, exist_ok=True)
        targets = []
        if a.all:
            for p in pages:
                targets += p.subimages
        else:
            if not a.args:
                sys.exit('usage: aimatlas.py extract NAME   (or --all)')
            targets = resolve(index, a.args[0])
        cache = {}
        for s in targets:
            if s.page not in cache:
                cache[s.page] = aim_io.to_image(
                    aim_io.parse(open(page_path(a.images, s.page), 'rb').read()))
            out = os.path.join(a.outdir, s.stem + '.png')
            cache[s.page].crop(s.box).save(out)
            print('%-34s -> %s' % (s.stem, out))
        print('\n%d image(s) extracted' % len(targets))
        return 0

    if a.command == 'patch':
        from PIL import Image
        if len(a.args) != 2:
            sys.exit('usage: aimatlas.py patch NAME NEW.png')
        name, src = a.args
        hits = resolve(index, name)
        if len(hits) > 1:
            sys.exit('%r appears in %d pages; patch them one at a time by page'
                     % (name, len(hits)))
        s = hits[0]
        new = Image.open(src).convert('RGBA')
        if new.size != (s.w, s.h):
            sys.exit('%s is %dx%d but the slot is %dx%d -- the rectangle is fixed '
                     'by the .tex, so the replacement must match exactly'
                     % (src, new.width, new.height, s.w, s.h))
        path = page_path(a.images, s.page)
        aim = aim_io.parse(open(path, 'rb').read())
        if aim.pixel_format not in ('BGRA', 'JPEG+A', 'JPEG', 'BMP'):
            sys.exit('page is %s; patching that encoding is not supported'
                     % aim.pixel_format)
        canvas = aim_io.to_image(aim)
        canvas.paste(new, (s.x, s.y))
        out = a.out or os.path.join(
            a.outdir, os.path.basename(path).replace('.aim', '.new.aim'))
        data = aim_io.from_image(canvas, flags=aim.flags, footer_extra=aim.footer_extra)
        open(out, 'wb').write(data)
        print('patched %s at (%d,%d) %dx%d' % (s.stem, s.x, s.y, s.w, s.h))
        print('wrote %s: %s' % (out, aim_io.describe(aim_io.parse(data))))
        if aim.encoding != 'IMTC32':
            print('note: the page was %s and is now IMTC32 (uncompressed). The '
                  'engine accepts this; the file is larger.' % aim.encoding)
        return 0


if __name__ == '__main__':
    sys.exit(main())
