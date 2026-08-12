import glob
import os
import sys

from threedo import parse, build, UnsupportedFormat
from obj_io import export_obj, export_all_lods, import_obj, replace_lod
import gltf_io
import subprocess
import shutil

UPLOAD_DIR = '/mnt/user-data/uploads'

CASES = [
        ('AH_Industrie_block_.3do', 'single LOD, single submesh (baseline)'),
        ('wing_00_.3do', 'single LOD, MULTIPLE submeshes'),
        ('dist_boost3lod.3do', 'MULTIPLE LODs, single submesh each'),
        ('hideoutlod.3do', 'MULTIPLE LODs x MULTIPLE submeshes (stress case)'),
        ('glow_alienshape.3do', 'NON-STANDARD vertex format (56B, dual UV set)'),
        ('coll_stargate.3do', 'LEGACY D3DFVF format (32B, no declaration block)'),
        ('anim0shapelod.3do', 'TWO LODs (fills the earlier sample gap)'),
]

TMP = '/tmp'


def phase1_identity_all_files():
    """Strongest available check with no ground truth to compare against:
    parse(file) -> build() must reproduce the input byte-for-byte."""
    print('=' * 78)
    print('PHASE 1 — identity round-trip (parse -> rebuild) across every sample file')
    files = sorted(glob.glob(f'{UPLOAD_DIR}/*.3do'))
    rows = []
    for fp in files:
        name = os.path.basename(fp)
        with open(fp, 'rb') as f:
            original = f.read()
        try:
            model = parse(original)
            rebuilt = build(model)
            ok = rebuilt == original
            rows.append((name, ok, len(model.lods), sum(len(l.submeshes) for l in model.lods),
                         model.vertex_count, model.face_count, len(original), None))
        except Exception as e:
            rows.append((name, False, '-', '-', '-', '-', len(original), f'{type(e).__name__}: {e}'))

    print(f'{"file":26} {"result":6} {"LODs":>4} {"subm":>4} {"verts":>7} {"faces":>7} {"size":>9}')
    for name, ok, nl, ns, nv, nf, sz, err in rows:
        print(f'{name:26} {"PASS" if ok else "FAIL":6} {nl!s:>4} {ns!s:>4} {nv!s:>7} {nf!s:>7} {sz:>9}' +
              (f'   ERROR: {err}' if err else ''))
    all_ok = all(r[1] for r in rows)
    print(f'\n  {sum(r[1] for r in rows)}/{len(rows)} files byte-identical after round-trip.')
    return all_ok, {name: parse(open(f'{UPLOAD_DIR}/{name}', "rb").read()) for name, ok, *_ in rows if ok}


def phase23_obj_pipeline(models):
    """Full 3do -> OBJ -> 3do pipeline, one representative file per structural
    category found in the sample set (single/multi LOD, single/multi submesh,
    and each vertex format), comparing geometry (not raw bytes, since
    tangents are deliberately recomputed -- see obj_io.py)."""
    print('=' * 78)
    print('PHASE 2+3 — OBJ export, reimport, rebuild, geometry comparison')

    cases = CASES

    all_ok = True
    for fname, label in cases:
        if fname not in models:
            print(f'  [skip] {fname}: not available (failed phase 1)')
            all_ok = False
            continue
        model = models[fname]
        print(f'\n-- {fname}  [{label}] --')

        obj_path = f'{TMP}/{fname.replace(".3do","")}_lod0.obj'
        export_obj(model, obj_path, lod_index=0)
        n_groups = sum(1 for line in open(obj_path) if line.startswith('g '))
        print(f'  exported LOD0 -> {obj_path} ({n_groups} submesh group(s))')

        new_lod0 = import_obj(obj_path)
        new_model = replace_lod(model, 0, new_lod0)
        rebuilt_bytes = build(new_model)
        out_path = f'{TMP}/{fname.replace(".3do","")}_roundtrip.3do'
        with open(out_path, 'wb') as f:
            f.write(rebuilt_bytes)

        reparsed = parse(rebuilt_bytes)
        old_lod, new_lod = model.lods[0], reparsed.lods[0]

        ok = True
        if len(old_lod.vertices) != len(new_lod.vertices):
            print(f'  FAIL vertex count {len(old_lod.vertices)} vs {len(new_lod.vertices)}'); ok = False
        if [s.face_count for s in old_lod.submeshes] != [s.face_count for s in new_lod.submeshes]:
            print(f'  FAIL submesh face-count split: {[s.face_count for s in old_lod.submeshes]} '
                  f'vs {[s.face_count for s in new_lod.submeshes]}'); ok = False
        if ok:
            max_pos = max(abs(a.px - b.px) + abs(a.py - b.py) + abs(a.pz - b.pz)
                          for a, b in zip(old_lod.vertices, new_lod.vertices))
            max_uv = max(abs(a.u - b.u) + abs(a.v - b.v) for a, b in zip(old_lod.vertices, new_lod.vertices))
            print(f'  vertices={len(new_lod.vertices)} faces={len(new_lod.indices)//3} '
                  f'submeshes={[s.face_count for s in new_lod.submeshes]}')
            print(f'  max position delta={max_pos:.2e}  max uv delta={max_uv:.2e}  '
                  f'(float-text round-trip noise, tangents intentionally recomputed)')
            if max_pos > 1e-3 or max_uv > 1e-3:
                ok = False
        # vertex FORMAT must survive the round trip, not just the geometry
        if [str(e) for e in old_lod.elements] != [str(e) for e in new_lod.elements] or \
           old_lod.fvf != new_lod.fvf:
            print(f'  FAIL vertex format changed: {old_lod.format_summary} (fvf={old_lod.fvf}) '
                  f'-> {new_lod.format_summary} (fvf={new_lod.fvf})'); ok = False
        else:
            print(f'  vertex format preserved: stride {new_lod.stride}'
                  f'{" (legacy FVF %#x)" % new_lod.fvf if new_lod.fvf is not None else ""}')

        # other LODs (if any) must be byte-untouched. Compare raw position/normal/uv
        # tuples rather than dataclass `==`, since a couple of this format's own
        # vertices legitimately contain NaN tangents (see SPEC.md) and NaN != NaN
        # in IEEE 754 -- that would flag an untouched LOD as "changed" when it isn't.
        def _fingerprint(lod):
            return [(v.px, v.py, v.pz, v.nx, v.ny, v.nz, v.u, v.v) for v in lod.vertices], lod.indices
        for i in range(1, len(model.lods)):
            same_header = bytes(model.lods[i].lod_header_template) == bytes(reparsed.lods[i].lod_header_template)
            same_geom = _fingerprint(model.lods[i]) == _fingerprint(reparsed.lods[i])
            if not (same_header and same_geom):
                print(f'  FAIL LOD {i} was supposed to be untouched but differs'); ok = False
        if len(model.lods) > 1:
            print(f'  other {len(model.lods)-1} LOD(s) confirmed untouched')

        print(f'  RESULT: {"PASS" if ok else "FAIL"}')
        all_ok = all_ok and ok

    return all_ok


def phase2_multi_lod_export_demo():
    print('=' * 78)
    print('PHASE 2 — export_all_lods() demo on a multi-LOD file')
    with open(f'{UPLOAD_DIR}/dist_boost3lod.3do', 'rb') as f:
        model = parse(f.read())
    paths = export_all_lods(model, f'{TMP}/dist_boost3lod.obj')
    for p, lod in zip(paths, model.lods):
        print(f'  {p}: {len(lod.vertices)} verts, {len(lod.indices)//3} faces')


def phase1b_shadow_files():
    """.shd companion files: same identity round-trip standard."""
    print('=' * 78)
    print('PHASE 1b — identity round-trip across every .shd (shadow volume) file')
    import shd
    rows = []
    for fp in sorted(glob.glob(f'{UPLOAD_DIR}/*.shd')):
        name = os.path.basename(fp)
        with open(fp, 'rb') as f:
            original = f.read()
        try:
            m = shd.parse(original)
            ok = shd.build(m) == original
            rows.append((name, ok, len(m.lods), m.vertex_count, m.face_count, len(original)))
        except Exception as e:
            rows.append((name, False, '-', '-', '-', len(original)))
            print(f'  {name}: {type(e).__name__}: {e}')
    print(f'{"file":24} {"result":6} {"SLODs":>5} {"verts":>7} {"tris":>7} {"size":>9}')
    for name, ok, nl, nv, nf, sz in rows:
        print(f'{name:24} {"PASS" if ok else "FAIL":6} {nl!s:>5} {nv!s:>7} {nf!s:>7} {sz:>9}')
    print(f'\n  {sum(r[1] for r in rows)}/{len(rows)} .shd files byte-identical after round-trip.')
    return all(r[1] for r in rows)


def phase5_gltf_pipeline(models):
    """glTF 2.0 is the RECOMMENDED interchange path: it carries tangents
    (vec4 incl. handedness) and a second UV set natively, so unlike the OBJ
    path nothing is recomputed and nothing is dropped. This phase asserts the
    strongest possible property: .3do -> .glb -> .3do is BYTE-IDENTICAL."""
    print('=' * 78)
    print('PHASE 5 — glTF 2.0 (.glb) round-trip: .3do -> .glb -> .3do, byte-identical')
    ok_count = 0
    rows = []
    for name, model in sorted(models.items()):
        original = open(f'{UPLOAD_DIR}/{name}', 'rb').read()
        glb = f'{TMP}/{name[:-4]}.glb'
        try:
            gltf_io.export_glb(model, glb)
            rebuilt = build(gltf_io.import_glb(glb))
            ok = rebuilt == original
        except Exception as e:
            ok = False
            print(f'  {name}: {type(e).__name__}: {e}')
        ok_count += ok
        rows.append((name, ok, os.path.getsize(glb) if os.path.exists(glb) else 0))
    for name, ok, sz in rows:
        if not ok:
            print(f'  FAIL {name}')
    print(f'  {ok_count}/{len(rows)} files byte-identical through glTF '
          f'(all LODs and submeshes in a single .glb each)')
    return ok_count == len(rows)


def phase5b_gltf_vs_obj_fidelity(models):
    """Quantify what the OBJ path loses that the glTF path does not."""
    print('=' * 78)
    print('PHASE 5b — interchange fidelity: glTF vs OBJ on the same asset')
    for name in ('glow_alienshape.3do', 'wing_00_.3do'):
        if name not in models:
            continue
        model = models[name]
        lod = model.lods[0]
        gltf_io.export_glb(model, f'{TMP}/fid.glb')
        g = gltf_io.import_glb(f'{TMP}/fid.glb').lods[0]
        export_obj(model, f'{TMP}/fid.obj')
        o = import_obj(f'{TMP}/fid.obj')

        def delta(a, b, key):
            worst = 0.0
            for x, y in zip(a.vertices, b.vertices):
                u, v = x.attrs.get(key), y.attrs.get(key)
                if u is None or v is None:
                    continue
                for s, t in zip(u, v):
                    if s != s and t != t:
                        continue        # NaN in both: not a difference
                    worst = max(worst, abs(s - t))
            return worst

        has_uv1 = (5, 1) in lod.vertices[0].attrs
        print(f'  {name}:')
        print(f'    tangent max delta   glTF {delta(lod, g, (6, 0)):.3e}   '
              f'OBJ {delta(lod, o, (6, 0)):.3e}')
        print(f'    position max delta  glTF {delta(lod, g, (0, 0)):.3e}   '
              f'OBJ {delta(lod, o, (0, 0)):.3e}')
        if has_uv1:
            print(f'    second UV set       glTF preserved={(5,1) in g.vertices[0].attrs}   '
                  f'OBJ preserved={(5,1) in o.vertices[0].attrs}')


def phase6_cli_tools():
    """Exercise the shipped command-line tools as a user actually would --
    as subprocesses, on a whole folder. The library being correct does not
    prove the CLI wiring is, so this runs the real end-to-end path:
        folder of .3do  --3do2gltf-->  .glb  --gltf23do-->  .3do
    and requires the result to be byte-identical to the originals.
    """
    print('=' * 78)
    print('PHASE 6 — command-line tools (3do2gltf / gltf23do / dsvalidate)')
    here = os.path.dirname(os.path.abspath(__file__))
    work = f'{TMP}/cli_phase6'
    shutil.rmtree(work, ignore_errors=True)
    glb_dir, back_dir = f'{work}/glb', f'{work}/back'
    os.makedirs(glb_dir); os.makedirs(back_dir)

    def run(script, *cli_args):
        r = subprocess.run([sys.executable, os.path.join(here, script), *cli_args],
                           capture_output=True, text=True)
        return r

    r1 = run('3do2gltf.py', UPLOAD_DIR, '-o', glb_dir, '-q')
    r2 = run('gltf23do.py', glb_dir, '-o', back_dir, '-q')
    r3 = run('dsvalidate.py', UPLOAD_DIR)

    ok = True
    for label, r in (('3do2gltf', r1), ('gltf23do', r2), ('dsvalidate', r3)):
        status = 'ok' if r.returncode == 0 else f'EXIT {r.returncode}'
        print(f'  {label:11} {status}')
        if r.returncode != 0:
            ok = False
            print('    ' + (r.stderr or r.stdout).strip().splitlines()[-1])

    same = diff = 0
    for f in sorted(glob.glob(f'{back_dir}/*.3do')):
        original = os.path.join(UPLOAD_DIR, os.path.basename(f))
        if open(f, 'rb').read() == open(original, 'rb').read():
            same += 1
        else:
            diff += 1
            print(f'    FAIL {os.path.basename(f)} differs from source')
    print(f'  round trip through the CLI: {same}/{same + diff} byte-identical')
    ok = ok and diff == 0 and same > 0

    # a malformed file must fail cleanly, not crash or silently succeed
    junk = f'{work}/junk.3do'
    with open(junk, 'wb') as f:
        f.write(b'NOTA3DOFILE' * 8)
    rj = run('3do2gltf.py', junk, '-o', work)
    clean = rj.returncode == 1 and 'not a 3DO file' in (rj.stdout + rj.stderr)
    print(f'  malformed input rejected cleanly (exit 1, no traceback): {clean}')
    ok = ok and clean

    return ok


def phase4_pairing_check():
    """Does every .3do have a .shd, and do their LOD counts agree?"""
    print('=' * 78)
    print('PHASE 4 — .3do <-> .shd pairing and LOD-count agreement')
    import shd
    for fp in sorted(glob.glob(f'{UPLOAD_DIR}/*.3do')):
        stem = os.path.basename(fp)[:-4]
        shd_path = f'{UPLOAD_DIR}/{stem}.shd'
        try:
            m = parse(open(fp, 'rb').read())
        except Exception:
            continue
        if not os.path.exists(shd_path):
            print(f'  {stem:24} .3do LODs={len(m.lods)}   (no .shd -- casts no stencil shadow)')
            continue
        s = shd.parse(open(shd_path, 'rb').read())
        agree = len(s.lods) == len(m.lods)
        print(f'  {stem:24} .3do LODs={len(m.lods)} .shd SLODs={len(s.lods)} '
              f'{"match" if agree else "MISMATCH"}  '
              f'render {m.vertex_count}v/{m.face_count}f -> shadow {s.vertex_count}v/{s.face_count}f')


if __name__ == '__main__':
    ok1, models = phase1_identity_all_files()
    ok1b = phase1b_shadow_files()
    phase4_pairing_check()
    phase2_multi_lod_export_demo()
    ok23 = phase23_obj_pipeline(models)
    ok5 = phase5_gltf_pipeline(models)
    phase5b_gltf_vs_obj_fidelity(models)
    ok6 = phase6_cli_tools()

    print('=' * 78)
    print('SUMMARY')
    print(f'  Phase 1  (.3do identity round-trip, all files)   : {"PASS" if ok1 else "FAIL"}')
    print(f'  Phase 1b (.shd identity round-trip, all files)   : {"PASS" if ok1b else "FAIL"}')
    print(f'  Phase 2+3 (OBJ export/reimport/rebuild, {len(CASES)} structural categories) : '
          f'{"PASS" if ok23 else "FAIL"}')
    print(f'  Phase 5  (glTF .glb round-trip, byte-identical, all files) : {"PASS" if ok5 else "FAIL"}')
    print(f'  Phase 6  (CLI tools end-to-end, incl. error handling)      : {"PASS" if ok6 else "FAIL"}')
    sys.exit(0 if (ok1 and ok1b and ok23 and ok5 and ok6) else 1)
