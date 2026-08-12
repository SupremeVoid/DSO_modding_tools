# Darkstar One (DSO) Modding Tools

The purpose of this project is to create new modding tools for the game "Darkstar One" 2006 by Ascaron.

Tools available:

- **3do_converter**: Convert 3D Objects/Meshes (`.3do` and `.shd`) files to and from `.glb` (glTF 2.0).
- **aim_converter**: Convert Graphics/Icons (`.aim`) files to and from `.png`, and edit the UI
  texture atlases — including reading the `.tex` indexes that say which atlas page holds a given
  interface graphic.

Both toolsets share the same command shape: every tool takes a file or a folder, folders are scanned
non-recursively, output defaults alongside the input, and nothing is overwritten without `--force`.
Each folder carries its own `README.md` and a format specification derived from the files themselves.

## File formats documented

| Spec | Covers |
|---|---|
| `3do_converter/SPEC.md` | `.3do` meshes and `.shd` shadow meshes |
| `aim_converter/AIM_SPEC.md` | `.aim` images, all encodings, and the SLD compression codec |
| `aim_converter/A2D_SCRIPTS_SPEC.md` | `scripts/` interface resources: `.tex` atlas indexes, `.anim` drawables |

## Requirements

`3do_converter` is pure Python 3.8+ with no third-party packages.
`aim_converter` needs Pillow, and numpy for `aimfind.py`.
