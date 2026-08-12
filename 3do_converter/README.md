# Darkstar One model tools — v1.0

Convert *Darkstar One* `.3do` meshes to and from glTF 2.0 so you can edit them
in Blender (or Maya, Max, Unity, Unreal, Godot) and put them back in the game.

Pure Python 3.8+, **no third-party packages required.**

`SPEC.md` documents the file formats themselves, including how each field was
determined and which earlier conclusions turned out to be wrong.

---

## Quick start

```bash
# 1. Export the game's meshes to glTF
python3 3do2gltf.py "ds_3dobj/3DView/objects" -o work/

# 2. Edit work/whatever.glb in Blender, export as .glb
#    (keep "Custom Properties" on; do NOT merge material slots -- see below)

# 3. Convert back
python3 gltf23do.py work/whatever.glb -o "ds_3dobj/3DView/objects" --force

# 4. Sanity-check before launching the game
python3 dsvalidate.py "ds_3dobj/3DView/objects"

# 4b. Better: compare against the stock file to catch structural drift
python3 dsvalidate.py work/whatever.3do --compare "path/to/stock/whatever.3do"
```

Every tool takes a **file or a folder**. Folders are scanned
**non-recursively** on purpose — asset folders nest deeply, and a recursive
default makes it far too easy to rewrite thousands of files by accident.

Output defaults to the input's own location. Existing files are never
overwritten without `--force`.

---

## Tools

| Tool | Purpose |
|---|---|
| `3do2gltf.py` | `.3do` → `.glb`. **Recommended.** Lossless. |
| `gltf23do.py` | `.glb` → `.3do`. Verifies the result re-parses before saving. |
| `3do2obj.py` | `.3do` → `.obj`. **Lossy** — inspection only. |
| `dsvalidate.py` | Structural + pairing checks on `.3do` / `.shd`. |
| `test_pipeline.py` | Full regression suite (developers). |

Common options: `-o DIR`, `-f/--force`, `-q/--quiet`, `--version`, `--help`.
`3do2gltf.py --report` prints each mesh's structure; `3do2obj.py --all-lods`
writes every LOD; `dsvalidate.py -v` prints every check rather than only
problems.

### Why glTF and not OBJ or FBX

glTF's `TANGENT` is a `vec4` whose `w` is the handedness sign — exactly how
`.3do` stores tangents. Nothing is recomputed, so the round trip is
byte-identical. Measured on real assets:

| | tangent error | 2nd UV set |
|---|---|---|
| **glTF** | **0.0** | preserved |
| OBJ | up to **2.0** (handedness flips) | **lost** |

FBX could carry the same data but needs Autodesk's proprietary SDK or a
Blender dependency, for no fidelity gain. OBJ is kept only for quick looks.

---

## Things that will bite you

**Do not merge, delete, or reassign material slots in Blender.** This is the
one that will silently ruin a model. `3do2gltf.py` gives each submesh its own
named material (`submesh_0`, `submesh_1`, …) *specifically* so Blender keeps
them as separate mesh parts. Blender merges primitives that share a material,
so collapsing the slots collapses the submeshes — and the engine assigns
materials and shader effects **per submesh**. A hull whose glow/shimmer batch
got merged into the main body loads fine, passes every structural check, and
renders wrong in game. Keep every face in the slot it arrived in.

Verify with `dsvalidate.py yours.3do --compare stock.3do`, which reports
submesh-count changes explicitly.

**Blender must export custom properties.** `3do2gltf.py` stores the exact
vertex declaration, legacy-FVF mode, submesh ranges and original bounding box
in glTF `extras`. Keep **Include → Custom Properties** ticked. Without it the
file still converts — submesh identity is recovered from the material names as
a fallback — but the vertex layout falls back to the standard 48-byte form.
`gltf23do.py` warns rather than guessing silently.

**Tangents are rebuilt automatically, and that is expected.** Blender only
writes glTF `TANGENT` data when the mesh has a material with a normal map
attached. Without one it omits tangents entirely, and zero tangents destroy
tangent-space normal mapping in game. `gltf23do.py` detects this and
recomputes them, printing `[fixed] ... recomputed for N vertices`. To keep the
originals instead, plug the object's `*_nrm.dds` into a Normal Map node on the
material before exporting.

**Putting the file where the game will actually read it.** A loose
`3DView\objects\` folder inside a mod directory is **never loaded** — the
`.cpr` archive always outranks it. This costs people hours, because nothing
errors; the game just shows the unmodified model. Load priority, highest
first:

1. Loose files in the **game install** directory —
   `C:\Games\Darkstar One\3DView\objects\`
2. **`user_data.zip`** in the active mod's root folder (the zip must contain
   `3DView\objects\yourfile.3do`)
3. `ds_3dobj.cpr` in the game install directory
4. A loose `3DView\` folder inside the mod directory — **never loaded**

Use (1) while iterating: it needs no re-zipping and outranks everything.
Use (2) to distribute a mod. If the mod already ships a `user_data.zip`, add
your file *into* the existing archive rather than replacing it, or you will
disable that mod's other 3D changes.

**Keep each LOD under 65,535 vertices.** `.3do` indices are 16-bit, and the
budget is *per LOD*, not per file. `gltf23do.py` refuses rather than writing a
file that wraps around and renders as garbage.

**Editing a `.3do` does not update its `.shd`.** The `.shd` companion is a
separate stencil shadow-volume mesh with its own topology. Change the hull and
the shadow silhouette will still match the *old* shape. Generating a correct
`.shd` from scratch is **not solved** (see SPEC.md). Your options:

- ship without a `.shd` — several stock objects have none, so this is legal
  and the object simply casts no stencil shadow (safest first attempt), or
- keep the old `.shd` and accept an imprecise silhouette.

`gltf23do.py` flags this whenever it writes a `.3do` next to an existing
`.shd`.

**NaN tangents are normal.** Several stock files contain them (`hideoutlod`,
`segshape27`, `cat_1_`, …). They come from the original exporter, round-trip
correctly, and are reported as anomalies for information only.

**Coordinate system is passed through unchanged.** glTF is Y-up
right-handed; the `.3do` convention has not been established. Round trips are
exact, but a model may appear rotated in Blender. If you determine the real
convention, apply the fix in `gltf_io.py` — not in the parser.

---

## Library use

```python
from threedo import parse, build
import gltf_io, shd

model = parse(open('ship.3do', 'rb').read())
print(len(model.lods), model.vertex_count, model.lods[0].format_summary)

gltf_io.export_glb(model, 'ship.glb')
open('rebuilt.3do', 'wb').write(build(gltf_io.import_glb('ship.glb')))

shadow = shd.parse(open('ship.shd', 'rb').read())
```

| Module | Contents |
|---|---|
| `threedo.py` | `.3do` parser/writer, vertex declarations, FVF translation |
| `shd.py` | `.shd` shadow-volume parser/writer |
| `gltf_io.py` | glTF 2.0 (`.glb`) import/export |
| `obj_io.py` | OBJ import/export (lossy) |
| `dscli.py` | Shared CLI plumbing |

---

## Status

49 `.3do` and 34 `.shd` files — covering all three vertex formats, 1–3 LODs,
up to 7 submeshes per LOD, both `.shd` index widths, and sizes from 1 KB to
8 MB — round-trip **byte-identically**, including through the command-line
tools as subprocesses.

The full loop has also been **confirmed in the running game**: stock `.3do` →
`.glb` → edited in Blender → `.3do` → loaded and rendered correctly, with
textures and per-submesh shader effects intact.

Byte-identical round-trip is the strongest available check without engine
source, but it is *necessary, not sufficient*: it cannot catch a field being
misread if the bytes are simply copied back. Where possible, claims in
`SPEC.md` are backed by a second independent check. Run `test_pipeline.py`
against a folder of assets to reproduce all of it.
