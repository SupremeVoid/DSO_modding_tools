"""
Shared command-line plumbing for the Darkstar One modding tools (v1.0).

Every tool takes the same shape:

    tool.py INPUT [-o OUTDIR] [options]

INPUT is a single file OR a folder. Folders are scanned NON-recursively, by
design: asset folders in this game nest deeply and a recursive default would
make it far too easy to rewrite thousands of files by accident.

Output defaults to the input's own location (per the project brief). Nothing
is ever overwritten without --force, again to make accidents hard.
"""
import argparse
import os
import sys

VERSION = '1.0'


class Reporter:
    """Collects per-file results and prints a consistent summary.

    Distinguishes ERRORS (the file could not be processed) from ANOMALIES
    (processed fine, but something is worth a human look). Keeping these
    separate matters: an anomaly on a modding asset is often the interesting
    part, not a failure, and burying it in an error count would hide it.
    """

    def __init__(self, verb='Converted'):
        self.verb = verb
        self.ok = 0
        self.errors = []      # (filename, message)
        self.anomalies = []   # (filename, message)
        self.skipped = []     # (filename, reason)
        self.outdir = None

    def log(self, src, dst, note=''):
        self.ok += 1
        arrow = f'{os.path.basename(src)} --> {os.path.basename(dst)}'
        print(f'  {arrow}{("   [" + note + "]") if note else ""}')

    def anomaly(self, src, message):
        self.anomalies.append((os.path.basename(src), message))
        print(f'  ! {os.path.basename(src)}: {message}')

    def error(self, src, message):
        self.errors.append((os.path.basename(src), message))
        print(f'  X {os.path.basename(src)}: {message}', file=sys.stderr)

    def skip(self, src, reason):
        self.skipped.append((os.path.basename(src), reason))
        print(f'  - {os.path.basename(src)}: skipped ({reason})')

    def summary(self):
        print()
        if self.outdir:
            print(f'Output in: {self.outdir}')
        parts = [f'{self.ok} {self.verb.lower()}']
        if self.skipped:
            parts.append(f'{len(self.skipped)} skipped')
        if self.anomalies:
            parts.append(f'{len(self.anomalies)} with anomalies')
        if self.errors:
            parts.append(f'{len(self.errors)} failed')
        print(', '.join(parts))

        if self.anomalies:
            print('\nAnomalies (processed, but worth checking):')
            for name, msg in self.anomalies:
                print(f'  {name}: {msg}')
        if self.errors:
            print('\nErrors:')
            for name, msg in self.errors:
                print(f'  {name}: {msg}')
        return 1 if self.errors else 0


def build_parser(description, in_ext, extra=None):
    p = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f'A folder INPUT processes every {in_ext} in it (not recursive).')
    p.add_argument('input', help=f'a {in_ext} file, or a folder containing them')
    p.add_argument('-o', '--output', metavar='DIR',
                   help='output folder (default: alongside the input)')
    p.add_argument('-f', '--force', action='store_true',
                   help='overwrite existing output files')
    p.add_argument('-q', '--quiet', action='store_true', help='only print the summary')
    p.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    if extra:
        extra(p)
    return p


def collect_inputs(path, extensions):
    """Returns (files, outdir_default). `extensions` is a tuple like ('.3do',)."""
    exts = tuple(e.lower() for e in extensions)
    if os.path.isfile(path):
        if not path.lower().endswith(exts):
            raise SystemExit(f'error: {path} is not a {"/".join(exts)} file')
        return [path], os.path.dirname(os.path.abspath(path))
    if os.path.isdir(path):
        files = sorted(os.path.join(path, f) for f in os.listdir(path)
                       if f.lower().endswith(exts)
                       and os.path.isfile(os.path.join(path, f)))
        if not files:
            raise SystemExit(f'error: no {"/".join(exts)} files found in {path}')
        return files, os.path.abspath(path)
    raise SystemExit(f'error: no such file or folder: {path}')


def resolve_output(args, default_dir):
    outdir = os.path.abspath(args.output) if args.output else default_dir
    os.makedirs(outdir, exist_ok=True)
    return outdir


def guard_overwrite(dst, force, reporter, src):
    if os.path.exists(dst) and not force:
        reporter.skip(src, f'{os.path.basename(dst)} exists; use --force')
        return False
    return True
