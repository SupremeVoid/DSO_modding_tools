# Darkstar One `.3do` / `.shd` Format Specification

**Status: v1.0 — released.** This document supersedes the earlier
v0.1–v0.5 draft-plus-addenda structure, in which later findings contradicted
earlier text that was still sitting in the document. Everything below
reflects current, tested knowledge. A "Corrections history" section at the
end records what earlier drafts got wrong and why, so nobody re-derives a
retracted conclusion from an old copy.

The format is considered fully characterised for practical modding: 45 `.3do`
and 34 `.shd` files spanning every structural variant found round-trip
byte-identically, and command-line tools ship alongside this document
(see "Tools" below and README.md).

Both formats belong to Ascaron's in-house engine for *Darkstar One* (2006).
They are unrelated to the Panasonic 3DO console format that shares the
extension. No public documentation or open-source parser existed at the time
of writing; everything here was derived from the files themselves.

## Validation status

| | Files | Result |
|---|---|---|
| `.3do` | 49 | 49/49 byte-identical `parse -> build` round-trip |
| `.shd` | 34 | 34/34 byte-identical `parse -> build` round-trip |
| OBJ pipeline | 7 categories | export -> reimport -> rebuild, geometry + vertex format preserved |
| **glTF pipeline** | 49 | **49/49 byte-identical** `.3do -> .glb -> .3do` |
| **CLI tools** | 49 | **49/49 byte-identical** through `3do2gltf` + `gltf23do` as subprocesses |
| **In-game** | 1 | stock -> `.glb` -> Blender edit -> `.3do` **loads and renders correctly in the running game** |

The corpus is mostly stock game assets plus a few files produced by this
pipeline itself (a Blender round-tripped player hull); the latter are included
because they exercise DCC-tool output, which stock files do not.

Corpus coverage: 3 vertex formats (48 B, 56 B dual-UV, 32 B legacy FVF),
1–3 LODs per file, up to 7 submeshes in a single LOD, 1 KB – 4.5 MB
(`.3do`) and 800 B – 8.0 MB (`.shd`), both `.shd` index widths.

Byte-identical round-trip is the strongest check available without engine
source: every byte is accounted for. It is necessary but **not sufficient** —
it cannot detect a field being *misinterpreted* if the bytes are simply
copied back (this is exactly how a vertex-format bug survived one round of
testing; see Corrections history). Where possible, claims below are backed by
a second, independent check: statistical structure, cross-validation against
a companion file, exact byte arithmetic, or visual rendering.

---

## Common conventions

- Little-endian throughout.
- Chunk tags are the tag name **spelled backwards**, space-padded to 4 bytes:

| On disk | Real name | Format |
|---|---|---|
| `OD3 ` | `3DO ` | `.3do` root |
| `HSEM` | `MESH` | `.3do` |
| `DOL ` | `LOD` | `.3do` |
| `RTTA` | `ATTR` | `.3do` submesh trailer |
| `VSWH` | `HWSV` | `.shd` root (read as HardWare Shadow Volume — inferred) |
| `DOLS` | `SLOD` | `.shd` (Shadow LOD) |

---

## `.3do` — render mesh

### Root header

```
0x00  tag "OD3 "
0x04  version string "00.2"
0x08  reserved (0 in every sample)
0x0c  count (1 in every sample; role unconfirmed)
0x10  bbox center      3 x float32   <- covers LOD0 ONLY
0x1c  float32, exactly 1.0 in every sample; purpose unexplained
0x20  bbox half-extent 3 x float32   <- covers LOD0 ONLY
0x2c  reserved (0 in every sample)
0x30  submesh_total (u32) = number of entries in the table at 0x48
0x34  char[20] object name, NUL-padded (empty in most assets)
0x48  submesh_total x (u16 submesh_index, u16 lod_index), LOD-major order
...   zero padding so the MESH chunk starts on a 16-byte boundary
```

The 0x48 table was verified by reconstructing the expected
`(submesh, LOD)` pair list from the actual chunk structure and comparing:
it matched **exactly in all 35 files**, with MESH 16-byte aligned in all 35.

The bounding box covers **LOD0 only**, not the union of all LODs — LOD0
reproduces the stored half-extent exactly on every multi-LOD sample while
the union does not.

### MESH chunk

```
+0   tag "HSEM"
+4   version string "00.1"
+8   reserved (0)
+12  lod_count (u32) — LOD chunks follow back-to-back
```

### LOD chunk (× lod_count)

```
+0   tag "DOL "
+4   reserved (0 in all 54 LOD chunks seen)
+8   submesh_count (u32)
+12  vertex-format selector (u32) — DUAL MEANING, see below
+16  index_count  (u32)
+20  vertex_count (u32)
+24  vertex_stride (u32) — cross-checked against the declaration on parse
+28  vertex declaration, OR nothing at all (legacy FVF case)
...  index buffer: index_count x uint16, shared by every submesh in this LOD
...  [2-byte pad iff index_count is odd, keeping the vertex buffer 4-byte aligned]
...  vertex buffer: vertex_count x vertex_stride
...  submesh_count x 24-byte trailer record
```

**The vertex-format selector at +12 has two modes:**

| Condition | Meaning |
|---|---|
| `flags & 0x80000000` **set** | A `D3DVERTEXELEMENT9` declaration follows at +28. Low bits = element count *including* the `D3DDECL_END` terminator. |
| `flags & 0x80000000` **clear** | The field **is** a legacy `D3DFVF` code. No declaration block; the index buffer starts immediately at +28. |

Because the declaration is variable-length, **the index buffer's offset is
not fixed** — it is `LOD + 28 + declaration_length`.

### Vertex declaration (`D3DVERTEXELEMENT9`)

```c
struct D3DVERTEXELEMENT9 {   // 8 bytes
    WORD Stream;             // 0; 0x00FF marks D3DDECL_END
    WORD Offset;             // byte offset within the vertex
    BYTE Type;               // D3DDECLTYPE: 0=FLOAT1 1=FLOAT2 2=FLOAT3 3=FLOAT4 ... 17=UNUSED
    BYTE Method;             // 0 (D3DDECLMETHOD_DEFAULT) in all samples
    BYTE Usage;              // D3DDECLUSAGE: 0=POSITION 3=NORMAL 5=TEXCOORD 6=TANGENT ...
    BYTE UsageIndex;         // e.g. 1 for a second TEXCOORD set
};
// D3DDECL_END() == FF 00 | 00 00 | 11 | 00 | 00 | 00
```

The stride computed from the declaration equals the stored stride field in
every sample — an independent consistency check the parser enforces.

### Vertex formats observed

| Stride | Layout | Seen in |
|---|---|---|
| 48 B | `POSITION:F3@0  NORMAL:F3@12  TEXCOORD0:F2@24  TANGENT:F4@32` | the large majority |
| 56 B | as above + `TEXCOORD1:F2@32`, tangent at `@40` | `glow_alienshape`, `base`, `segshape27`, `mainshapelod_157` |
| 32 B | legacy FVF `0x112` = `XYZ \| NORMAL \| TEX1`, **no tangent** | `coll_stargate` (a collision hull) |

`tangent.w` is a handedness sign (+1 / −1). The 48-byte layout was originally
derived statistically (unit-length test isolating the normal and tangent
triples with stdev 0.0 on magnitude) and *later re-derived independently*
from the declaration — two methods agreeing.

The 56-byte second UV set is a glow/lightmap channel. Note the `coll_`
filename prefix indicates *purpose* (collision hull), not format:
`coll_playership.3do` uses the modern 48-byte declaration.

### Submesh trailer records

```
+0   tag "RTTA"
+4   submesh_index (u32, 0-based)
+8   face_start (u32, in TRIANGLES, not raw indices)
+12  face_count (u32, triangles)
+16  vert_start (u32)
+20  vert_count (u32)
```

All submeshes of a LOD share one index buffer and one vertex buffer;
each trailer describes a contiguous slice. Consecutive submeshes partition
the buffers with no gap or overlap — verified exactly on the 2-submesh
(`wing_00`: 984+14=998 tris, 1598+22=1620 verts) and 3-submesh
(`hideoutlod`: 2827+3268+3432=9527 tris, 8481+9360+8448=26289 verts) cases.

### Index width — 16-bit, per LOD

Every `.3do` uses uint16 indices and there is **no width field**. Content
stays under the ceiling by construction: the largest single LOD is **57,986
vertices** (`propsshape_16`). Files that would overflow are split across
LODs — `mainshapelod_157.3do` holds 76,046 vertices total but splits them
35,352 / 31,274 / 9,420.

**The 65,535 budget is per-LOD, not per-file**, because a LOD owns a vertex
buffer. Submeshes inside one LOD share that budget (`mainshapelod_157` LOD2
packs 7 submeshes into one 9,420-vertex buffer).

A 32-bit `.3do` variant may exist in unsampled assets, since the sibling
`.shd` format clearly supports wide indices. The parser raises an explicit
error rather than truncating if it meets a LOD above the ceiling.

---

## `.shd` — stencil shadow volume

A separate, coarser silhouette mesh used for stencil shadows. **Not** a
shader and **not** a texture. Not every `.3do` has one.

```
0x00  tag "VSWH"
0x04  version string "00.1"
0x08  lod_count (u32) — matches the companion .3do's LOD count in all 24 pairs
0x0c  reserved (0 in every sample)
then lod_count x SLOD chunk:
  +0x00  tag "DOLS"
  +0x04  vertex_count (u32)
  +0x08  index_count  (u32)
  +0x0c  index_width flag (u32): 0 = uint16, 1 = uint32
  +0x10  vertex_count x 24 bytes: position 3 x f32, normal 3 x f32
  +...   index_count x (uint16 | uint32), flat triangle list
```

### Index width

Proven by exact byte arithmetic, not inference:

- `propsshape_16.shd`: `16 + 16 + 102942*24 + 406968*4 = 4,098,512` = file
  size exactly (uint16 would be off by 813 KB). Unambiguous on its own:
  102,942 vertices cannot be addressed by uint16; max index is 101,741.
- `mainshapelod_157.shd`: `16 + 3*(16 + 64998*24 + 275838*4) = 7,989,976`
  = file size exactly.

**The flag is not purely driven by necessity** — `mainshapelod_157.shd`
sets it with only 64,998 vertices and max index 62,651, which would fit in
uint16. Treat it as an explicit exporter choice to preserve, not something
to recompute. All other 32 SLOD chunks use 16-bit, including ones with large
index counts (`hideoutlod`: 121,176 indices, 16-bit) — so the flag tracks
index *width*, not index *count*.

### Shadow geometry characteristics

- Positions occupy the same space as the render mesh. The shadow bbox
  reproduces the `.3do`'s stored bbox exactly in 13 of 24 pairs and closely
  in the rest — **the shadow hull is not a strict copy of the render hull**,
  so do not rely on them matching.
- Far fewer unique positions than the render mesh, but **more** triangles,
  because extrusion quads are baked in (typically 2–5× the render triangle
  count).
- The `normal` field: 94% of referenced vertices are unit length, ~6% have
  other magnitudes, a handful are exactly zero. Treat it as a per-face
  extrusion vector, not a guaranteed unit normal.
- Every file ends with a run of trailing vertices that **no index
  references** (18 of 174 in `gate_door_04`; 36–540 elsewhere). Most likely
  runtime scratch space for extruded/far-cap vertices. Unconfirmed;
  preserved verbatim.
- `index_count` is even in all 36 chunks seen, so whether `.shd` pads odd
  counts the way `.3do` does is **untested**.

Visual confirmation: rendering `propsshape_16`'s render mesh beside its
32-bit shadow volume shows the same scattered prop clusters in the same
positions, so the wide indices decode to real geometry rather than merely a
self-consistent byte count.

---

## Interchange format: use glTF 2.0, not OBJ or FBX

**Recommendation: glTF 2.0 (`.glb`) is the correct interchange format for
this project.** Every vertex attribute the `.3do` format uses maps 1:1 onto
glTF 2.0 core:

| `.3do` element | glTF attribute | Fidelity |
|---|---|---|
| `POSITION FLOAT3` | `POSITION VEC3` | exact |
| `NORMAL FLOAT3` | `NORMAL VEC3` | exact |
| `TEXCOORD FLOAT2` | `TEXCOORD_0 VEC2` | exact |
| `TEXCOORD1 FLOAT2` | `TEXCOORD_1 VEC2` | exact |
| `TANGENT FLOAT4` | `TANGENT VEC4` | **exact, including the w handedness sign** |

The tangent row is decisive. OBJ has no tangent channel, so the OBJ path must
discard tangents and recompute them (Lengyel's method) on import — the one
lossy step this project carried from the beginning. glTF stores tangent as
`vec4` with `w` = handedness, which is *precisely* how `.3do` stores it, so
the glTF path recomputes nothing.

Measured on the corpus:

| Asset | Tangent max delta | Second UV set kept |
|---|---|---|
| `glow_alienshape` | glTF **0.0** / OBJ **2.0** (handedness flips) | glTF yes / OBJ **no** |
| `wing_00_` | glTF **0.0** / OBJ 1.6e-04 | n/a |

Positions: glTF 0.0, OBJ ~5e-7 (decimal-text rounding).

glTF also covers everything else the format needs: submeshes map to mesh
primitives sharing one vertex buffer (the same model `.3do` uses, so submesh
slicing survives exactly), both uint16 and uint32 indices are supported, and
`TEXCOORD_1` carries the 56-byte dual-UV format that OBJ cannot represent at
all.

**Why not FBX.** FBX expresses the same things, but requires either
Autodesk's proprietary SDK or a Blender dependency. glTF 2.0 is an open
Khronos spec that reads and writes in pure Python with zero dependencies, and
imports natively into Blender, Maya, 3ds Max, Unity, Unreal and Godot. There
is no fidelity argument for FBX here — only added dependency weight.

### Achieving byte-identical glTF round-trip

`.3do -> .glb -> .3do` is byte-identical for **all 35 corpus files**. Two
things were needed beyond the attribute mapping:

- Fields glTF has no concept of — the exact vertex declaration, the legacy
  FVF code, and submesh `vert_start`/`vert_count` — are stored in `extras`,
  so a `.glb` is a complete standalone description and the original `.3do`
  is not needed to rebuild.
- The **bounding box bytes are carried verbatim** in scene `extras`. The
  bbox is a cached, derived field whose original float32 reduction order is
  not exactly reproducible; recomputing it lands 1 ULP off on roughly half
  the corpus (a single differing byte at 0x18). Preserving the bytes and
  reusing them when geometry is unchanged closes that gap, while an edited
  mesh still falls through to a fresh recompute.

### Round-tripping through a DCC tool is a different problem

The byte-identical guarantee covers `.3do -> .glb -> .3do` with *our own*
reader and writer. Passing the `.glb` through Blender is a separate path with
its own failure modes, all four of which were found only by testing the model
in the running game — every broken file passed structural validation first.

| What Blender does | Consequence if unhandled | Handling |
|---|---|---|
| Merges primitives that share a material (and a mesh with no materials has exactly one) | **Submeshes collapse.** The engine assigns materials/shaders per submesh, so a merged glow batch stops rendering as a glow batch | Export assigns a distinct named material per submesh (`submesh_N`), forcing separate slots |
| Drops primitive-level `extras` when re-exporting | Submesh identity lost | Import recovers it from the material name, then falls back to primitive order |
| Gives each primitive its **own** vertex array instead of sharing one | Indices point past the end of the first array → `IndexError`, or silent corruption | Import detects both layouts and offsets each primitive's indices by its base |
| Omits `TANGENT` unless the material has a normal map | Zero tangents; tangent-space normal mapping dead in game | Import recomputes tangents (Lengyel) and reports it |

A fifth issue was ours alone: the bbox-preservation optimisation reused the
*original* bounding box even for edited geometry, which the engine uses for
culling and picking.

The lesson worth recording: **structural validity is not visual correctness.**
Every one of these produced a file that parsed cleanly, re-serialised cleanly,
and had in-range indices. `dsvalidate.py --compare` exists because of this —
comparing a rebuilt file against its stock original catches submesh, LOD and
vertex-format drift that self-consistency checks cannot see.

### Mod installation: where the game actually reads from

Also learned in practice, and not a property of the file format at all. A loose
`3DView\objects\` folder inside a mod directory is **never loaded** — the
`.cpr` archive outranks it. Load priority, highest first:

1. Loose files in the game install directory (`<game>\3DView\objects\`)
2. `user_data.zip` in the active mod's root (containing `3DView\objects\...`)
3. `ds_3dobj.cpr` in the game install directory
4. A loose `3DView\` folder in the mod directory — never loaded

This fails silently: the game simply shows the unmodified model.

### Caveats

- **LODs.** glTF core has no LOD concept. All LODs are written as separate
  nodes in one `.glb`, with the LOD structure recorded in `extras`. Most DCC
  tools will show them overlapping; hide the ones you are not editing.
- **Coordinate system.** glTF is defined as Y-up, right-handed. The `.3do`
  axis convention has not been established, so the converter passes
  coordinates through verbatim. Round trips are exact, but a model may appear
  rotated in a DCC tool. If you determine the real convention, apply the fix
  in `gltf_io.py`, not in the parser.
- **NaN tangents.** `hideoutlod.3do`'s lower LODs genuinely contain them.
  They survive in the binary chunk and do not leak invalid `NaN` tokens into
  the JSON (only `POSITION` accessors carry min/max, and positions are always
  finite). Verified.

The OBJ path is retained as a lightweight debug/inspection route, and remains
useful when you only care about silhouette or topology.

## Tools

Five command-line tools ship with this spec. All accept a single file **or a
folder** (scanned non-recursively, so a stray path cannot rewrite an entire
asset tree), default their output to the input's location, and refuse to
overwrite without `--force`.

| Tool | Does |
|---|---|
| `3do2gltf.py` | `.3do` → `.glb`. The recommended export path. |
| `gltf23do.py` | `.glb` → `.3do`. Re-parses what it wrote before saving. |
| `3do2obj.py` | `.3do` → `.obj`. Lossy; inspection only. |
| `dsvalidate.py` | Structural checks on `.3do` and `.shd`, plus pairing checks. |
| `test_pipeline.py` | Full regression suite over a corpus folder. |

```
$ 3do2gltf.py path/to/objects -o converted/
Converting 45 file(s) to glTF 2.0:
  glow_main_6.3do --> glow_main_6.glb
  ...
Output in: /abs/path/converted
45 converted, 8 with anomalies
```

Errors (file could not be processed, exit code 1) are reported separately
from anomalies (processed fine, but a human should look). That split matters:
NaN tangents and near-ceiling vertex counts are *findings*, not failures, and
burying them in an error count would hide them.

## Modding guidance

1. **`.shd` is optional.** Several sampled `.3do` files have none
   (`AH_Industrie_block_`, `dist_boost3lod`, `coll_*`). Those objects cast
   no stencil shadow. Shipping without one is the safest first attempt.
2. **`.shd` does not auto-update when you edit a `.3do`.** They are
   independent meshes with independent topology. Old `.shd` + new hull =
   a shadow silhouette that does not match the model.
3. **Keep each LOD under 65,535 vertices.** Split across LODs or objects if
   needed; do not assume the format will widen for you.
4. **Use glTF (`.glb`), not OBJ, for anything you intend to ship.** The OBJ
   path recomputes tangents and cannot carry a second UV set; on
   `glow_alienshape` it flips tangent handedness outright (delta 2.0) and
   drops `TEXCOORD_1`. `obj_io.py` mitigates this by restoring extra
   attributes via position lookup and warning with an exact hit count
   (3375/3936 for that asset), but glTF avoids the problem entirely.
5. **Preserve the vertex format.** A legacy-FVF asset rewritten as a
   declaration asset has the same geometry but a different on-disk layout.
   The tools do this automatically; hand-built files should too.
6. **Run `dsvalidate.py` before testing in-game.** It catches the failures
   that are cheap to fix on disk and expensive to diagnose in the game:
   out-of-range indices, a stride disagreeing with the vertex declaration,
   submesh ranges that do not partition the buffers, a bounding box that no
   longer matches the geometry (which makes the engine cull or pick the
   object wrongly), and a `.shd` whose LOD count no longer matches its
   `.3do`.

## Open questions

- Generating a `.shd` from scratch. The files can be parsed, rebuilt and
  edited byte-exactly, but the original tool's vertex emission and
  edge/adjacency ordering has **not** been reproduced. In simple meshes
  vertices appear in blocks of 6 sharing a face normal, but across the full
  corpus only ~15% of 6-vertex blocks do, so the real ordering is more
  complex. This is the main gap for authoring brand-new shadow-casting models.
- The purpose of the unindexed trailing vertex run in `.shd`.
- The 1.0 float at `.3do` 0x1c and the root count at 0x0c.
- Whether a 32-bit-index `.3do` variant exists.
- Why the bbox occasionally differs by 1 ULP from any recomputation
  (float32 reduction order in the original exporter; a derived cache field,
  so `build()` reuses the original bytes when geometry is unchanged).

---

## Corrections history

Recorded because each of these was stated confidently at some point and
later disproved by a wider sample. Preserved so an old copy of this document
is not mistaken for current.

| Earlier claim | Reality | Caught by |
|---|---|---|
| The 36-byte block after the LOD header is a format constant | It is a `D3DVERTEXELEMENT9` declaration, variable-length, starting 4 bytes earlier than recorded | `glow_alienshape.3do` (56-byte stride) |
| Vertex stride is always 48 | Three formats exist (48/56/32) | `glow_alienshape`, `coll_stargate` |
| The vertex-format flags field is an unresolved constant | Dual-mode declaration-count / legacy FVF selector | `coll_stargate.3do` (flags `0x112`) |
| Object name buffer is 28 bytes | 20 bytes; the next 8 are the LOD/submesh table | 2-LOD samples |
| The pre-MESH region layout is undecodable without more samples | Fully decoded: name + `(submesh, lod)` table + 16-byte alignment padding | `anim0shapelod`, `anim1shapelod_1` |
| The count at 0x30 is an aggregate, not a loop count | It is exactly the entry count of the 0x48 table | same |
| Bounding box covers all LODs | Covers LOD0 only | `anim1shapelod_1` |
| The two zero fields in the `.3do` trailer are unused | They are `vert_start` / `vert_count` | multi-submesh files |
| `.shd` field at SLOD+0x0c is reserved | Index-width flag (uint16 / uint32) | `propsshape_16.shd` |
| `.shd` normals are unit length; vertices come in blocks of 6 sharing one normal | 94% unit, ~6% not; only ~15% of blocks share a normal — a `gate_door_04`-specific pattern | corpus-wide re-check during doc audit |
| `.shd` bbox matches the `.3do` bbox exactly | Exact in 13 of 24 pairs only | same |
| The glTF converter's `.3do -> .glb -> .3do` cycle is byte-identical | Was 18/35 as written; the bbox was being recomputed rather than preserved. Now genuinely byte-identical corpus-wide | corpus test during the glTF review |
| Byte-identical round-trip meant the glTF path was safe for modding | It only covered our own reader/writer. Four separate defects appeared once a real Blender round trip was tested in game, none of which any structural check flagged | testing the converted model in the running game |
| Preserving the original bbox bytes is always correct | Correct only when geometry is unchanged; an edited mesh kept the stock bounds, which the engine uses for culling and picking | `dsvalidate.py` on a user-edited hull |
| Materials in the glTF export are cosmetic | Load-bearing: without a distinct material per submesh, Blender merges the submeshes and per-part shader effects break | user-reported broken shimmer effect |

One bug is worth recording alongside these: `replace_lod()` silently dropped
the legacy-FVF marker, rewriting a fixed-function asset as a declaration
asset. Byte-identical round-trip did not catch it (the file was never
round-tripped, only rebuilt) and the geometry-only comparison passed. The
test suite now asserts vertex declaration and FVF mode survive the round
trip. This is the concrete reason the validation table above distinguishes
"necessary" from "sufficient".
