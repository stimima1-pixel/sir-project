"""sdr_tuner.kinematics - parsing the vendor's own .kinematics CSV format, forward kinematics, and the damped
least squares inverse kinematics solver. No Qt - tested standalone, exactly like test_gcode.py tests gcode.py.

FK/IK round-trips are checked against the 4 REAL vendor files bundled as samples (sdr_tuner/data/kinematics_samples/
- copies of the vendor's own demo assets, found in a sibling folder the user pointed at directly - see the README),
not synthetic fixtures, wherever a real file already covers the case."""
import math
import random
from pathlib import Path

import numpy as np
import pytest

from sdr_tuner import kinematics as k

SAMPLES = Path(__file__).resolve().parent.parent / "sdr_tuner" / "data" / "kinematics_samples"


def sample(name):
    return k.parse_kinematics_file(SAMPLES / name)


# ---- parsing -----------------------------------------------------------------------------------
def test_radar_is_the_simplest_real_example_a_3_axis_pan_tilt_roll_turret():
    model = sample("radar.obj.kinematics")
    assert len(model.bodies) == 4                                       # chassis + 3 joints
    yaw, pitch, roll = (model.bodies[i] for i in (1, 2, 3))
    assert (yaw.name, yaw.axis, yaw.axis_type) == ("A:yaw", 0, "rotary")
    assert (pitch.name, pitch.axis) == ("B:pitch", 1)
    assert (roll.name, roll.axis) == ("C:roll", 2)
    assert roll.tool_tip == (0.0, 0.0, 0.0) and roll.tool_direction == (1.0, 0.0, 0.0)


def test_ko_bot_is_a_5_actuated_joint_arm_with_two_trailing_rigid_rows():
    """joint6/joint7 have no Axis at all (blank) - rigid, not independently driven - matching the file's own
    trailing rows past the last real joint (joint5, axis 4)."""
    model = sample("ko-bot.obj.kinematics")
    assert len(model.bodies) == 8                                       # chassis + 7 rows, 2 of them rigid
    actuated = [b for b in model.bodies.values() if b.axis is not None]
    assert len(actuated) == 5
    rigid = [b for b in model.bodies.values() if b.parent is not None and b.axis is None]
    assert {b.name for b in rigid} == {"joint6", "joint7"}


def test_robot_dog_is_4_legs_of_3_axes_each_12_axes_total():
    model = sample("robot-dog.obj.kinematics")
    actuated = [b for b in model.bodies.values() if b.axis is not None]
    assert len(actuated) == 12
    assert sorted(b.axis for b in actuated) == list(range(12))


def test_humanoid_is_34_axes_the_largest_real_example():
    model = sample("humanoid.obj.kinematics")
    actuated = [b for b in model.bodies.values() if b.axis is not None]
    assert len(actuated) == 34


def test_an_unrecognised_header_is_refused_clearly():
    with pytest.raises(k.KinematicsFileError, match="not a recognised"):
        k.parse_kinematics_text("Name,Parent,Axis\nfoo,,0\n")


def test_an_empty_file_is_refused_clearly():
    with pytest.raises(k.KinematicsFileError, match="empty"):
        k.parse_kinematics_text("")


def test_a_zero_axis_direction_is_refused_when_a_chain_is_built():
    text = ("#;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;"
           "Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);"
           "Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU\n"
           "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n"
           "1;joint1;0;0;rotary;0;0;0;0;0;10;;;;;;;10;0;0;;;;0.1;\n")
    model = k.parse_kinematics_text(text)
    with pytest.raises(k.KinematicsFileError, match="not a valid rotation"):
        model.chains()


# ---- tool numbering (confirmed against the vendor's own DOG.NC demo - see the README) -----------
def test_robot_dog_tool_numbers_match_the_vendors_own_dog_nc_demo():
    """DOG.NC (the vendor's own demo program) addresses this exact model's 4 legs as T3/T6/T9/T12 - the tool
    number is the chain's last actuated joint's own axis + 1."""
    chains = sample("robot-dog.obj.kinematics").chains()
    assert sorted(chains) == [3, 6, 9, 12]
    assert chains[3].axes == [0, 1, 2] and chains[6].axes == [3, 4, 5]
    assert chains[9].axes == [6, 7, 8] and chains[12].axes == [9, 10, 11]


def test_radar_has_one_chain_tool_3():
    chains = sample("radar.obj.kinematics").chains()
    assert list(chains) == [3] and chains[3].axes == [0, 1, 2]


def test_a_tool_tip_on_a_rigid_trailing_row_still_resolves_to_the_last_actuated_axis():
    """ko-bot's joint6/joint7 (no Axis) repeat joint5's own Tool Tip - the tool number still comes from joint5
    (axis 4), not left unresolved just because later rows also mention a tip."""
    chains = sample("ko-bot.obj.kinematics").chains()
    assert list(chains) == [5] and chains[5].axes == [0, 1, 2, 3, 4]


def test_humanoids_two_arms_are_asymmetric_in_the_vendors_own_file_and_both_are_reported():
    """A real inconsistency found in the vendor's own demo file, not a parsing bug: the right arm has ONE tool-
    tip row (its very last joint); the left arm has tool tips on BOTH its last two joints (rows 33 and 34) - this
    parser reports exactly what the file says, one chain per tool-tip row, rather than guessing which one the
    vendor "meant"."""
    chains = sample("humanoid.obj.kinematics").chains()
    assert 27 in chains and len(chains[27].axes) == 9                   # right arm: one chain
    assert 33 in chains and 34 in chains                                # left arm: both rows report a chain
    assert len(chains[33].axes) == 8 and len(chains[34].axes) == 9      # 34's own chain has one more joint than 33's


# ---- forward kinematics --------------------------------------------------------------------
@pytest.mark.parametrize("name", ["radar.obj.kinematics", "ko-bot.obj.kinematics", "robot-dog.obj.kinematics",
                                  "humanoid.obj.kinematics"])
def test_forward_kinematics_at_the_home_pose_matches_the_files_own_tool_tip(name):
    """Every joint at 0 must put the tip exactly where the file says (Tool Tip) - this is what "home pose" means."""
    for chain in sample(name).chains().values():
        home = chain.forward([0.0] * len(chain.axes))
        assert math.dist(home, tuple(chain.home_tip)) < 1e-9


def test_forward_kinematics_of_a_single_rotary_joint_matches_a_hand_computed_rotation():
    """A 90 degree rotation about the Z axis through the origin sends (10, 0, 0) to (0, 10, 0) - checked by hand,
    not just against itself."""
    text = ("#;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;"
           "Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);"
           "Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU\n"
           "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n"
           "1;joint1;0;0;rotary;0;0;1;0;0;0;;;;;;;10;0;0;;;;0.1;\n")
    chain = k.parse_kinematics_text(text).chains()[1]
    tip = chain.forward([90.0])
    assert math.dist(tip, (0.0, 10.0, 0.0)) < 1e-9


def test_forward_kinematics_of_a_linear_joint_translates_along_its_axis():
    """No real vendor file uses a linear joint, but the format's own Axis Type column allows it - a 5 mm move
    along the X axis should add exactly 5 mm to X."""
    text = ("#;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;"
           "Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);"
           "Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU\n"
           "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n"
           "1;slide;0;0;linear;1;0;0;0;0;0;0;50;;;;;20;0;0;;;;0.1;\n")
    chain = k.parse_kinematics_text(text).chains()[1]
    assert math.dist(chain.forward([0.0]), (20.0, 0.0, 0.0)) < 1e-9
    assert math.dist(chain.forward([5.0]), (25.0, 0.0, 0.0)) < 1e-9


# ---- inverse kinematics ----------------------------------------------------------------------
@pytest.mark.parametrize("name", ["radar.obj.kinematics", "ko-bot.obj.kinematics", "robot-dog.obj.kinematics",
                                  "humanoid.obj.kinematics"])
def test_ik_round_trips_through_fk_for_every_chain_in_every_real_file(name):
    """The actual correctness check this whole module rests on: solving IK for a target FK already produced must
    converge back to (very nearly) that same target, for every chain in every bundled real vendor file - a
    3-axis turret and a 34-axis humanoid, same code, same tolerance."""
    random.seed(20260922)
    for chain in sample(name).chains().values():
        n = len(chain.axes)
        pose = [random.uniform(-15, 15) for _ in range(n)]
        target = chain.forward(pose)
        result = chain.solve(target)
        assert result.converged, f"{name} chain T{chain.tool_number} did not converge"
        recovered = chain.forward(result.joint_values)
        assert math.dist(recovered, target) < 1.0        # within 1 mm


def test_ik_returns_joint_values_in_each_joints_own_native_unit():
    """A rotary joint's solved value is degrees, ready to convert into a drive command directly (no caller-side
    unit juggling) - checked against a target reached by a known, hand-picked angle. Uses robot-dog's own leg, not
    radar's turret: radar's every joint AND its own tip sit at the exact same point (0, 0, 0) - a pure pan/tilt/
    roll head, where rotating any axis re-orients the tip but never moves its POSITION at all, so position-only IK
    has nothing to solve for there (see test_position_only_ik_is_vacuous_for_a_pure_orientation_chain below)."""
    chain = sample("robot-dog.obj.kinematics").chains()[3]
    target = chain.forward([15.0, -10.0, 5.0])
    result = chain.solve(target, initial=[0.0, 0.0, 0.0])
    assert result.converged
    assert math.dist(chain.forward(result.joint_values), target) < 0.5


def test_position_only_ik_is_vacuous_for_a_pure_orientation_chain():
    """radar.obj's own 3 joints - and its tool tip - are all at the exact same point (0, 0, 0): rotating yaw,
    pitch or roll re-orients the tip but never changes its POSITION. Position-only IK (this module's own scope -
    see kinematics.py's module docstring for why) correctly finds ANY pose "converged" immediately, since every
    pose already satisfies the only thing being solved for - a real, documented limit of solving for position
    alone, not a bug."""
    chain = sample("radar.obj.kinematics").chains()[3]
    result = chain.solve((0.0, 0.0, 0.0), initial=[12.0, -7.0, 40.0])
    assert result.converged and result.iterations == 0
    assert result.joint_values == pytest.approx([12.0, -7.0, 40.0])   # the initial guess, unchanged: already satisfied


def test_ik_reports_not_converged_for_an_unreachable_target_instead_of_guessing():
    chain = sample("robot-dog.obj.kinematics").chains()[3]
    result = chain.solve((1e6, 1e6, 1e6))             # nowhere near any real leg's reach
    assert not result.converged
    assert result.final_error > 1.0                   # a real, reported miss - not silently treated as success


def test_ik_starting_from_a_given_initial_pose_still_converges():
    chain = sample("ko-bot.obj.kinematics").chains()[5]
    target = chain.forward([10.0, -5.0, 15.0, 0.0, 5.0])
    result = chain.solve(target, initial=[5.0, 0.0, 10.0, 0.0, 0.0])
    assert result.converged
    assert math.dist(chain.forward(result.joint_values), target) < 1.0


# ---- orientation (full pose) inverse kinematics (2026-09-24) ---------------------------------------------------
def test_euler_to_matrix_matches_hand_computed_rotations():
    assert np.allclose(k.euler_to_matrix(0, 0, 0), np.eye(3))
    # a 90 degree yaw sends (10, 0, 0) -> (0, 10, 0), same hand-checked rotation as the FK test above
    assert np.allclose(k.euler_to_matrix(0, 0, 90) @ [10, 0, 0], [0, 10, 0], atol=1e-9)
    # a 90 degree pitch sends (10, 0, 0) -> (0, 0, -10)
    assert np.allclose(k.euler_to_matrix(0, 90, 0) @ [10, 0, 0], [0, 0, -10], atol=1e-9)
    # a 90 degree roll sends (0, 10, 0) -> (0, 0, 10)
    assert np.allclose(k.euler_to_matrix(90, 0, 0) @ [0, 10, 0], [0, 0, 10], atol=1e-9)


def test_euler_to_matrix_round_trips_through_matrix_to_euler_away_from_gimbal_lock():
    for roll, pitch, yaw in [(20, 30, 40), (-15, 60, 170), (0, 0, 0), (5, -45, -100)]:
        r = k.euler_to_matrix(roll, pitch, yaw)
        assert np.allclose((roll, pitch, yaw), k.matrix_to_euler(r), atol=1e-6)


def test_matrix_to_euler_at_gimbal_lock_still_reconstructs_the_same_matrix():
    """At pitch = +-90 degrees, roll and yaw are not individually recoverable (only their sum/difference is) - the
    documented convention (roll = 0) does not recover the ORIGINAL roll/yaw, but euler_to_matrix(matrix_to_euler(r))
    must still reconstruct the same matrix, since the composite rotation itself is never ambiguous."""
    for roll, pitch, yaw in [(25, 90, -15), (10, -90, 50)]:
        r = k.euler_to_matrix(roll, pitch, yaw)
        recovered_roll, recovered_pitch, recovered_yaw = k.matrix_to_euler(r)
        assert recovered_roll == pytest.approx(0.0, abs=1e-9)
        assert recovered_pitch == pytest.approx(pitch, abs=1e-6)
        assert np.allclose(r, k.euler_to_matrix(recovered_roll, recovered_pitch, recovered_yaw), atol=1e-9)


def test_forward_pose_at_home_has_identity_orientation():
    """Every joint at 0 -> the tip's own orientation is the fixed frame's own identity, by this module's own
    documented convention (see the module docstring) - checked for every chain in every real bundled file, not
    just one."""
    for name in ["radar.obj.kinematics", "ko-bot.obj.kinematics", "robot-dog.obj.kinematics", "humanoid.obj.kinematics"]:
        for chain in sample(name).chains().values():
            _, rotation = chain.forward_pose([0.0] * len(chain.axes))
            assert np.allclose(rotation, np.eye(3), atol=1e-9)


def test_pose_ik_round_trips_through_forward_pose_for_a_9_joint_arm():
    """The full-pose analogue of test_ik_round_trips_through_fk_for_every_chain_in_every_real_file: solving for a
    pose forward_pose already produced must converge back to (very nearly) that same pose - position AND
    orientation both. Uses humanoid's own 9-axis right arm (T27), which has enough joints to have a real chance of
    reaching an arbitrary nearby orientation, unlike a 3-axis leg."""
    random.seed(20260924)
    chain = sample("humanoid.obj.kinematics").chains()[27]
    n = len(chain.axes)
    pose = [random.uniform(-15, 15) for _ in range(n)]
    target_position, target_rotation = chain.forward_pose(pose)
    result = chain.solve_pose(target_position, target_rotation)
    assert result.converged, f"pos error {result.position_error} mm, rot error {result.orientation_error_deg} deg"
    recovered_position, recovered_rotation = chain.forward_pose(result.joint_values)
    assert math.dist(recovered_position, target_position) < 1.0
    assert np.linalg.norm(recovered_rotation - target_rotation) < 0.05


def test_pose_ik_solves_the_orientation_only_case_that_position_only_ik_could_not():
    """The exact chain test_position_only_ik_is_vacuous_for_a_pure_orientation_chain calls out: radar.obj's 3
    joints - and its own tool tip - are all at (0, 0, 0), so position alone can never distinguish one pose from
    another there. Full pose IK has no such blind spot: it must also match the target ORIENTATION, so it correctly
    converges to (very nearly) the actual joint values that produced it, not just any pose that happens to also
    put the tip at (0, 0, 0) - the real reason this solver exists."""
    chain = sample("radar.obj.kinematics").chains()[3]
    pose = [12.0, -7.0, 40.0]
    target_position, target_rotation = chain.forward_pose(pose)
    result = chain.solve_pose(target_position, target_rotation, initial=[0.0, 0.0, 0.0])
    assert result.converged
    assert result.joint_values == pytest.approx(pose, abs=1e-2)
    recovered_position, recovered_rotation = chain.forward_pose(result.joint_values)
    assert math.dist(recovered_position, target_position) < 1.0
    assert np.linalg.norm(recovered_rotation - target_rotation) < 0.05


def test_pose_ik_returns_joint_values_in_each_joints_own_native_unit():
    chain = sample("robot-dog.obj.kinematics").chains()[3]
    target_position, target_rotation = chain.forward_pose([15.0, -10.0, 5.0])
    result = chain.solve_pose(target_position, target_rotation, initial=[0.0, 0.0, 0.0])
    assert result.converged
    recovered_position, recovered_rotation = chain.forward_pose(result.joint_values)
    assert math.dist(recovered_position, target_position) < 0.5
    assert np.linalg.norm(recovered_rotation - target_rotation) < 0.05


def test_pose_ik_reports_not_converged_for_an_unreachable_target_instead_of_guessing():
    chain = sample("robot-dog.obj.kinematics").chains()[3]
    result = chain.solve_pose((1e6, 1e6, 1e6), np.eye(3))       # nowhere near any real leg's reach
    assert not result.converged
    assert result.position_error > 1.0                          # a real, reported miss - not silently "converged"


def test_pose_iks_own_default_tolerance_is_tighter_than_a_gui_field_can_even_type():
    """2026-09-24, found verifying orientation IK live against a real drive: solve_pose's own default position
    tolerance is 1e-3 mm (1 micron) - tighter than the Tool Pose GUI's own Target Position fields can even express
    (2 decimal places, 0.01 mm). A target that is genuinely, exactly reachable - one this module's own forward_pose
    just produced - can still fail to "converge" by the default tolerance once rounded to what a person could type
    or the GUI would show, for a single-joint chain where a tiny orientation residual maps directly to a position
    residual through the joint's own tip radius. A looser tolerance (kinematics.py's own default stays unchanged
    for math-level callers; gcode_runner.py's move_to_pose is the one that now asks for 0.05 mm instead - see its
    own comment) comfortably absorbs that same rounding. Uses a synthetic single-joint fixture, not a bundled
    sample - the effect needs a chain with only one degree of freedom to be reliably reproducible."""
    text = ("#;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;"
           "Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);"
           "Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU\n"
           "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n"
           "1;joint;0;0;rotary;0;0;1;0;0;0;;;;1;0;0;100;0;0;;;;0.1;0\n")
    chain = k.parse_kinematics_text(text).chains()[1]
    exact_position, _ = chain.forward_pose([30.0])
    typed_position = tuple(round(v, 2) for v in exact_position)         # what a 2-decimal GUI field would hold
    typed_rotation = k.euler_to_matrix(0.0, 0.0, 30.0)
    default = chain.solve_pose(typed_position, typed_rotation, initial=[0.0])
    assert not default.converged, "the tight default no longer fails on typed-precision rounding - re-check this test"
    loose = chain.solve_pose(typed_position, typed_rotation, initial=[0.0], tolerance=0.05)
    assert loose.converged
    assert loose.joint_values == pytest.approx([30.0], abs=1e-2)


# ---- IMU placeholder -----------------------------------------------------------------------
def test_read_imu_is_always_not_present_today():
    model = sample("radar.obj.kinematics")
    reading = k.read_imu(model, 0)
    assert reading.present is False
    assert reading.acceleration is None and reading.angular_velocity is None and reading.quaternion is None


def test_imu_bodies_reads_the_files_own_imu_column():
    text = ("#;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;"
           "Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);"
           "Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU\n"
           "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;1\n"
           "1;joint1;0;0;rotary;0;0;1;0;0;0;;;;;;;10;0;0;;;;0.1;0\n")
    model = k.parse_kinematics_text(text)
    assert model.imu_bodies() == [0]


# ---- body_transforms (for the 3D Simulation page, gui/sim3d.py) --------------------------------
def test_body_transforms_are_identity_for_every_body_at_the_home_pose():
    model = sample("robot-dog.obj.kinematics")
    transforms = model.body_transforms({})
    assert len(transforms) == len(model.bodies)
    for t in transforms.values():
        np.testing.assert_allclose(t, np.eye(4), atol=1e-9)


def test_body_transforms_only_moves_a_bodys_own_descendants():
    model = sample("robot-dog.obj.kinematics")
    transforms = model.body_transforms({0: 30.0})            # axis 0 = leg-1-1's own joint
    ids_by_name = {b.name: b.id for b in model.bodies.values()}
    # the root (no actuated ancestors at all) is untouched
    np.testing.assert_allclose(transforms[ids_by_name["chassis"]], np.eye(4), atol=1e-9)
    # leg-1-1 itself, and its own descendants leg-1-2/leg-1-3, all move
    for name in ("leg-1-1", "leg-1-2", "leg-1-3"):
        assert not np.allclose(transforms[ids_by_name[name]], np.eye(4))
    # a DIFFERENT leg, driven by different axes, is untouched by axis 0 alone
    for name in ("leg-2-1", "leg-2-2", "leg-2-3"):
        np.testing.assert_allclose(transforms[ids_by_name[name]], np.eye(4), atol=1e-9)


def test_body_transforms_matches_chains_own_already_tested_forward_kinematics_at_the_tip():
    model = sample("robot-dog.obj.kinematics")
    joint_values = {0: 15.0, 1: -20.0, 2: 10.0}
    chain = model.chains()[3]                                 # leg-1's own tool number (axis 2 + 1)
    expected_tip = np.array(chain.forward(joint_values))
    leg_1_3 = next(b for b in model.bodies.values() if b.name == "leg-1-3")
    transform = model.body_transforms(joint_values)[leg_1_3.id]
    home = np.array([*leg_1_3.tool_tip, 1.0])
    np.testing.assert_allclose((transform @ home)[:3], expected_tip, atol=1e-9)


def test_a_rigid_axisless_row_gets_the_same_transform_as_its_nearest_actuated_ancestor():
    model = sample("ko-bot.obj.kinematics")                   # joint6/joint7 have a blank Axis column
    joint_values = {0: 10.0, 1: 20.0, 2: -15.0, 3: 5.0, 4: 8.0}
    transforms = model.body_transforms(joint_values)
    by_name = {b.name: b.id for b in model.bodies.values()}
    np.testing.assert_allclose(transforms[by_name["joint6"]], transforms[by_name["joint5"]], atol=1e-9)
    np.testing.assert_allclose(transforms[by_name["joint7"]], transforms[by_name["joint5"]], atol=1e-9)


def test_body_transforms_defaults_a_missing_axis_to_its_home_value_of_zero():
    model = sample("robot-dog.obj.kinematics")
    with_all_axes = model.body_transforms({0: 0.0, 1: 0.0, 2: 0.0})
    with_none_given = model.body_transforms({})
    for body_id in with_all_axes:
        np.testing.assert_allclose(with_all_axes[body_id], with_none_given[body_id], atol=1e-9)


# ---- audit fixes (2026-09-25): files refused up front, solutions folded within a turn, limits honoured ---------
_HEADER = ("#;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;"
           "Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);"
           "Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU\n")


def test_a_driven_joint_without_an_axis_direction_is_refused_when_the_file_is_parsed():
    """It used to load, then TypeError at the first forward kinematics - re-raised by the 3D view every 100 ms."""
    text = _HEADER + "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n" + "1;joint1;0;0;rotary;;;;0;0;0;;;;;;;10;0;0;;;;0.1;\n"
    with pytest.raises(k.KinematicsFileError, match="has no Axis Direction"):
        k.parse_kinematics_text(text)


def test_a_parent_loop_or_a_missing_parent_is_refused_instead_of_hanging():
    with pytest.raises(k.KinematicsFileError, match="Parent column forms a loop"):
        k.parse_kinematics_text(_HEADER + "0;base;0;;;;;;0;0;0;;;;;;;;;;;;;1;0\n")     # its own parent: looped forever
    with pytest.raises(k.KinematicsFileError, match="not a row of this file"):
        k.parse_kinematics_text(_HEADER + "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n"
                                + "1;arm;7;0;rotary;0;0;1;0;0;0;;;;;;;10;0;0;;;;0.1;\n")


def test_a_rotary_solution_is_folded_to_within_a_turn_of_where_the_search_started():
    import numpy as np
    folded = k._fold([math.radians(370.0), math.radians(-350.0)], [True, True], np.array([0.0, 0.0]))
    assert folded[0] == pytest.approx(math.radians(10.0)) and folded[1] == pytest.approx(math.radians(10.0))
    assert k._fold([5.0], [False], np.array([0.0]))[0] == 5.0                  # a linear joint is never folded


def test_a_solution_outside_the_axis_limits_is_flagged_for_the_caller_to_refuse():
    text = _HEADER + "0;base;;;;;;;0;0;0;;;;;;;;;;;;;1;0\n" + "1;joint1;0;0;rotary;0;0;1;0;0;0;-45;45;;;;;10;0;0;;;;0.1;\n"
    chain = k.parse_kinematics_text(text).chains()[1]
    result = chain.solve((0.0, 10.0, 0.0))                       # a 90 degree turn - beyond the +-45 the file allows
    assert result.converged and result.out_of_limits == (0,)
    assert chain.solve((10.0, 0.0, 0.0)).out_of_limits == ()


def test_the_robot_dog_never_answers_a_target_with_whole_motor_turns_any_more():
    """`G00 T3 X-500 Y200 Z-100` used to "converge" to [5419, 29819, -1024] degrees - 15 and 83 motor turns."""
    from pathlib import Path
    path = Path(k.__file__).parent / "data" / "kinematics_samples" / "robot-dog.obj.kinematics"
    if not path.exists():
        pytest.skip("bundled robot-dog sample not present")
    chain = k.parse_kinematics_file(path).chains()[3]
    result = chain.solve((-500.0, 200.0, -100.0), initial=[0.0] * len(chain.axes))
    assert all(abs(v) <= 180.0 + 1e-6 for v in result.joint_values)
