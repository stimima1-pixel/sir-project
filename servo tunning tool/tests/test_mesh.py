"""sdr_tuner.mesh - the .obj parser behind the 3D Simulation page. No Qt - tested standalone, exactly like
test_kinematics.py/test_gcode.py test their own modules. FK/IK round-trips are checked against the 5 REAL vendor
`.obj` files bundled as samples (sdr_tuner/data/kinematics_samples/), not synthetic fixtures, wherever a real file
already covers the case; synthetic tiny .obj text covers the error paths a well-formed vendor file never hits."""
from pathlib import Path

import numpy as np
import pytest

from sdr_tuner import mesh as m

SAMPLES = Path(__file__).resolve().parent.parent / "sdr_tuner" / "data" / "kinematics_samples"


def sample(name):
    return m.parse_obj_file(SAMPLES / name)


# ---- real vendor files ---------------------------------------------------------------------------
def test_radar_has_one_group_per_kinematics_body_with_matching_names():
    groups = sample("radar.obj")
    assert set(groups) == {"chassis", "A:yaw", "B:pitch", "C:roll"}      # exact .kinematics Name column match


def test_each_groups_own_vertices_are_compacted_not_a_view_into_the_whole_file():
    groups = sample("robot-dog.obj")
    assert len(groups) == 13                                             # chassis + 12 leg joints
    # every group's own face indices must be valid inside ITS OWN (compacted) vertex array
    for name, mesh in groups.items():
        assert mesh.faces.min() >= 0
        assert mesh.faces.max() < len(mesh.vertices)
        assert mesh.vertices.shape[1] == 3
        assert mesh.faces.shape[1] == 3


def test_a_bodys_own_vertex_count_is_far_smaller_than_the_whole_files_total():
    groups = sample("robot-dog.obj")
    total_vertices = sum(len(mesh.vertices) for mesh in groups.values())
    # the smallest leg segment shouldn't drag along anywhere near the whole file's ~71k vertices
    smallest = min(len(mesh.vertices) for mesh in groups.values())
    assert smallest < total_vertices / 4


def test_humanoid_has_35_groups_one_per_body_in_its_kinematics_file():
    from sdr_tuner import kinematics as k
    model = k.parse_kinematics_file(SAMPLES / "humanoid.obj.kinematics")
    groups = sample("humanoid.obj")
    assert set(groups) == {body.name for body in model.bodies.values()}


def test_ko_bot_and_humanoid_parse_without_error_all_triangular_faces():
    for name in ("ko-bot.obj", "humanoid.obj"):
        groups = sample(name)
        assert len(groups) > 0
        for mesh in groups.values():
            assert mesh.faces.shape[1] == 3


# ---- synthetic edge cases -------------------------------------------------------------------------
TINY_OBJ = """\
o body_a
v 0 0 0
v 1 0 0
v 0 1 0
f 1 2 3
o body_b
v 10 0 0
v 11 0 0
v 10 1 0
f 4 5 6
"""


def test_two_groups_each_get_their_own_compact_mesh():
    groups = m.parse_obj_text(TINY_OBJ)
    assert set(groups) == {"body_a", "body_b"}
    a, b = groups["body_a"], groups["body_b"]
    assert a.vertices.shape == (3, 3)
    assert b.vertices.shape == (3, 3)
    np.testing.assert_array_equal(a.faces, [[0, 1, 2]])
    np.testing.assert_array_equal(b.vertices[0], [10.0, 0.0, 0.0])


def test_v_slash_vt_slash_vn_face_tokens_use_only_the_vertex_index():
    text = "o g\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1/1/1 2/2/1 3/3/1\n"
    groups = m.parse_obj_text(text)
    np.testing.assert_array_equal(groups["g"].faces, [[0, 1, 2]])


def test_a_quad_face_is_rejected_not_silently_misread():
    text = "o g\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n"
    with pytest.raises(m.MeshFileError, match="triangular"):
        m.parse_obj_text(text, source="quad.obj")


def test_a_face_before_any_group_is_rejected():
    text = "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    with pytest.raises(m.MeshFileError, match="before any"):
        m.parse_obj_text(text, source="noname.obj")


def test_an_empty_file_is_rejected():
    with pytest.raises(m.MeshFileError, match="no vertices"):
        m.parse_obj_text("", source="empty.obj")


def test_an_empty_group_with_no_faces_is_simply_omitted():
    text = "o unused\no used\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    groups = m.parse_obj_text(text)
    assert set(groups) == {"used"}


def test_the_error_message_names_the_source_and_line_number():
    text = "o g\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n"
    with pytest.raises(m.MeshFileError, match=r"bad\.obj:6"):
        m.parse_obj_text(text, source="bad.obj")
