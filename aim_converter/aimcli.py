"""
Shared command-line plumbing for the Darkstar One image tools (v2.0).

Mirrors `3do_converter/dscli.py` so both toolsets behave the same way:

    tool.py INPUT [-o OUTDIR] [options]

INPUT is a single file OR a folder. Folders are scanned NON-recursively, by
design: asset folders in this game nest deeply and a recursive default would
make it far too easy to rewrite thousands of files by accident.

Output defaults to the input's own location. Nothing is ever overwritten
without --force.

Unlike the mesh tools, these need Pillow (and numpy for aimfind.py); there is
no reasonable way to decode PNG and JPEG without it.
"""
import argparse
import os
import sys

VERSION = '2.0'


class Reporter:
    """Collects per-file results and prints a consistent summary.

    Distinguishes ERRORS (the file could not be processed) from ANOMALIES
    (processed fine, but worth a human look) — an anomaly on a modding asset
    is often the interesting part, not a failure.
    """

    def __init__(self, verb='Converted', quiet=False):
        self.verb = verb
        self.quiet = quiet
        self.ok = 0
        self.errors = []
        self.anomalies = []
        self.skipped = []
        self.outdir = None

    def _say(self, msg, err=False):
        if not self.quiet:
            print(msg, file=sys.stderr if err else sys.stdout)

    def log(self, src, dst, note=''):
        self.ok += 1
        self._say('  %s --> %s%s' % (os.path.basename(src), os.path.basename(dst),
                                     ('   [' + note + ']') if note else ''))

    def note(self, src, message):
        self._say('  %s: %s' % (os.path.basename(src), message))

    def anomaly(self, src, message):
        self.anomalies.append((os.path.basename(src), message))
        self._say('  ! %s: %s' % (os.path.basename(src), message))

    def error(self, src, message):
        self.errors.append((os.path.basename(src), message))
        self._say('  X %s: %s' % (os.path.basename(src), message), err=True)

    def skip(self, src, reason):
        self.skipped.append((os.path.basename(src), reason))
        self._say('  - %s: skipped (%s)' % (os.path.basename(src), reason))

    def summary(self):
        print()
        if self.outdir:
            print('Output in: %s' % self.outdir)
        parts = ['%d %s' % (self.ok, self.verb.lower())]
        if self.skipped:
            parts.append('%d skipped' % len(self.skipped))
        if self.anomalies:
            parts.append('%d with anomalies' % len(self.anomalies))
        if self.errors:
            parts.append('%d failed' % len(self.errors))
        print(', '.join(parts))
        if self.anomalies:
            print('\nAnomalies (processed, but worth checking):')
            for name, msg in self.anomalies:
                print('  %s: %s' % (name, msg))
        if self.errors:
            print('\nErrors:')
            for name, msg in self.errors:
                print('  %s: %s' % (name, msg))
        return 1 if self.errors else 0


def build_parser(description, in_ext, extra=None):
    p = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='A folder INPUT processes every %s in it (not recursive).' % in_ext)
    p.add_argument('input', help='a %s file, or a folder containing them' % in_ext)
    p.add_argument('-o', '--output', metavar='DIR',
                   help='output folder (default: alongside the input)')
    p.add_argument('-f', '--force', action='store_true',
                   help='overwrite existing output files')
    p.add_argument('-q', '--quiet', action='store_true', help='only print the summary')
    p.add_argument('--version', action='version', version='%(prog)s ' + VERSION)
    if extra:
        extra(p)
    return p


def collect_inputs(path, extensions):
    """Returns (files, default_outdir). `extensions` is a tuple like ('.aim',)."""
    exts = tuple(e.lower() for e in extensions)
    if os.path.isfile(path):
        if not path.lower().endswith(exts):
            raise SystemExit('error: %s is not a %s file' % (path, '/'.join(exts)))
        return [path], os.path.dirname(os.path.abspath(path))
    if os.path.isdir(path):
        files = sorted(os.path.join(path, f) for f in os.listdir(path)
                       if f.lower().endswith(exts)
                       and os.path.isfile(os.path.join(path, f)))
        if not files:
            raise SystemExit('error: no %s files found in %s' % ('/'.join(exts), path))
        return files, os.path.abspath(path)
    raise SystemExit('error: no such file or folder: %s' % path)


def resolve_output(args, default_dir):
    outdir = os.path.abspath(args.output) if args.output else default_dir
    os.makedirs(outdir, exist_ok=True)
    return outdir


def guard_overwrite(dst, force, reporter, src):
    if os.path.exists(dst) and not force:
        reporter.skip(src, '%s exists; use --force' % os.path.basename(dst))
        return False
    return True


def require_pillow():
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise SystemExit('error: this tool needs Pillow  ->  pip install pillow')
