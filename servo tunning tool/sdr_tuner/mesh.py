"""Pure-function `.obj` mesh parser for the 3D Simulation page (see `gui/sim3d.py`), one body's mesh per named
group - the vendor's own `.obj` files (`data/kinematics_samples/*.obj`, a sibling of the matching `.kinematics`
files) name each `o <group>` group EXACTLY the same as a `.kinematics` file's own `Name` column (confirmed
directly: `robot-dog.obj`'s `o leg-1-1`/`o leg-1-2`/... match `robot-dog.obj.kinematics`'s `Name` column
row-for-row) - so a body's own transform (see `kinematics.KinematicModel.body_transforms`) can be applied straight
to the mesh this module returns for that same name. No Qt, no numpy-heavy per-body copies beyond what each group
actually needs - mirrors `kinematics.py`/`gcode.py`'s own "pure functions, no GUI dependency, testable standalone"
style (see `tests/test_mesh.py`).

**The file format** (checked directly against all 5 bundled vendor `.obj` files, not assumed from the OBJ spec in
general): `v x y z` vertices (no `w` component ever present), `o <name>` starts a new named group, `f i//n j//n
k//n` triangular faces referencing 1-based, file-GLOBAL vertex indices (never reset per group, never negative/
relative) - every face in every bundled file is already a triangle, so a face with any other vertex count raises
`MeshFileError` rather than silently mis-rendering. Per-vertex normals (`vn`, the `//n` part of a face token) and
texture coordinates (`vt`) are read past but not used: none of the bundled files carry a `.mtl` (material) at all,
so there is no per-face colour/texture to apply anyway, and `pyqtgraph.opengl.GLMeshItem`'s own `smooth=True`
already computes reasonable vertex normals from the triangle geometry itself - good enough for a live preview,
not meant to reproduce the vendor's own shading exactly.

**Each group's own vertex array is compacted, not a view into the whole file's vertices** - `robot-dog.obj` alone
has ~71k vertices across 13 named bodies; handing every body a reference to the SAME 71k-vertex array (indexed by
its own faces, most of them irrelevant to that body) would upload ~13x the vertex data to the GPU it actually
needs. `np.unique`/`np.searchsorted` (vectorised, not a per-triangle Python loop - matters at these face counts:
robot-dog/humanoid/ko-bot are each 130k-140k triangles) extract exactly the vertices each group's own faces
reference and remap face indices to a compact, local 0-based range."""
from collections import namedtuple
from pathlib import Path

import numpy as np

Mesh = namedtuple("Mesh", "vertices faces")   # vertices: (N, 3) float64 mm; faces: (M, 3) int64, 0-based into vertices


class MeshFileError(ValueError):
    pass


def parse_obj_text(text, source="<string>"):
    """Parses `.obj` text (see the module docstring) into `{group name: Mesh}`, in the order each group's `o` line
    first appears. Raises `MeshFileError` - with `source` and a line number in the message - on a non-triangular
    face or a face token appearing before any `o` line, rather than silently misreading either."""
    vertices = []
    group_faces = {}
    order = []
    current = None
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        kind = parts[0]
        if kind == "o":
            current = line[len(kind):].strip()
            if current not in group_faces:
                group_faces[current] = []
                order.append(current)
        elif kind == "v":
            vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif kind == "f":
            if current is None:
                raise MeshFileError(f"{source}:{line_no}: a face appears before any 'o <name>' group")
            if len(parts) != 4:
                raise MeshFileError(
                    f"{source}:{line_no}: only triangular faces are supported, got {len(parts) - 1} vertices")
            try:
                triangle = tuple(int(token.split("/")[0]) - 1 for token in parts[1:4])
            except ValueError:
                raise MeshFileError(f"{source}:{line_no}: could not parse face indices '{line}'") from None
            group_faces[current].append(triangle)
    if not vertices:
        raise MeshFileError(f"{source}: no vertices found")
    all_vertices = np.array(vertices, dtype=np.float64)
    result = {}
    for name in order:
        faces = group_faces[name]
        if not faces:
            continue
        faces_arr = np.array(faces, dtype=np.int64)
        used = np.unique(faces_arr)
        if used[0] < 0 or used[-1] >= len(all_vertices):
            raise MeshFileError(f"{source}: group '{name}' references a vertex index outside the file's own {len(all_vertices)} vertices")
        local_vertices = all_vertices[used]
        local_faces = np.searchsorted(used, faces_arr)
        result[name] = Mesh(local_vertices, local_faces)
    return result


def parse_obj_file(path):
    path = Path(path)
    return parse_obj_text(path.read_text(), source=str(path))
