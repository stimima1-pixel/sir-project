"""Config-driven forward and inverse kinematics for a multi-axis robot, from the vendor's own `.kinematics` CSV
file format (see the samples under sdr_tuner/data/kinematics_samples/, and the README's Multi-axis section for how
this format was found - `Servo tunning tool-original/plc/assets/*.obj.kinematics`, a sibling folder the user
pointed at directly). No Qt: pure functions/dataclasses, exactly like gcode.py - testable standalone
(tests/test_kinematics.py) with no GUI dependency at all.

**The file format**, one row per body/joint in a kinematic TREE, columns (semicolon-delimited):

    #;Name;Parent;Axis;Axis Type;Axis Direction X;Y;Z;Axis Origin Point(mm) X;Y;Z;
    Axis Limits(mm or deg) Min;Max;Axis Tolerance(mm or deg);
    Tool Direction X;Y;Z;Tool Tip(mm) X;Y;Z;Body Center of Mass(mm) X;Y;Z;Mass(kg);IMU

`#` is this row's own id; `Parent` is another row's id (blank = the root body, e.g. the chassis); `Axis` is the
0-based axis index this joint is driven by (blank = a rigid/structural link, not independently actuated - e.g.
ko-bot's own trailing rows past its last real joint) - the SAME numbering G-code's own axis letters/brackets use
(`gcode.py`'s own docstring), NOT the CAN node ID directly: the user's own correction, 2026-09-23, "node id 1 means
axis 0, node is 2 means axis 1" - `gcode_runner.py`'s `_axis_to_node()` is the one place that turns this column's
own value into the real node a connected `DriveClient` actually is. `Axis Type` is "rotary" (degrees) or "linear"
(mm) - every real vendor example seen only uses "rotary", but both are implemented. `Tool Direction`/`Tool Tip` are
set only on a chain's own end-effector row - what a G-code `Txx` (see gcode.py/gcode_runner.py) actually solves
for. `IMU` flags a body that carries an inertial sensor - see `read_imu()`.

**Interpretation - the one real judgement call this format needs, since no written spec for it exists**: `Axis
Origin Point` and `Axis Direction` are each given ONCE, directly in the root body's own fixed frame, not
accumulated through a parent-relative chain - confirmed by the numbers themselves (e.g. `robot-dog.obj`'s own
leg-1 joints all cluster in that ONE leg's own region of space, not small offsets from each other) and by the fact
that this is exactly the input a **Product of Exponentials** (screw-axis) kinematics description needs: for each
joint, one point ON its axis plus its direction, both in a single fixed frame - see Lynch & Park, "Modern
Robotics", the "space form" - chosen because it is a standard, well-tested formulation that needs no per-joint
relative-frame convention invented by this project. `Tool Tip` is the end-effector's own HOME position - where it
is when every joint in its chain is at 0.

Inverse kinematics comes in two forms. `solve_ik`/`Chain.solve` are POSITION-only (a 3-vector target) - the G-code
dialect's own move commands (see gcode.py) only ever give an X/Y/Z target, never an orientation, so solving for one
would be solving for something no G-code line can express; this is the one Txx (gcode_runner.py's `_tool_move`)
itself uses. `solve_pose_ik`/`Chain.solve_pose` (2026-09-23) solve for a FULL pose - position AND orientation
together - for the tool's own GUI "aim" control (Item Configuration's Kinematics Model group), which has no G-code
line to parse a target from at all, so it can ask for whatever it likes. Both solvers are a damped least squares
(Levenberg-Marquardt-style) search using a NUMERIC Jacobian (central differences of the solver's own error function)
rather than an analytic one - slower per iteration, but needs no separate derivative code to keep in sync with
forward_kinematics: IK correctness reduces entirely to FK correctness. General on purpose: the exact same code
solves `radar.obj`'s 3-axis turret and `humanoid.obj`'s 34-axis chain.

**A real limit of solving for position alone, worth knowing rather than being surprised by**: `radar.obj`'s own
pan/tilt/roll turret has every joint's axis - and its own tool tip - at the exact same point, (0, 0, 0); rotating
any of its 3 joints re-orients the tip but never moves its POSITION. Position-only IK against such a chain finds
every pose "converged" at once (the target is already satisfied, whatever the current joint values are) - correct
behaviour for what is actually being asked, not a bug, but not useful for aiming a pure orientation device either -
exactly the case `solve_pose_ik` exists for, since it also has to match the target ORIENTATION.

**Orientation IK needs one more judgement call the file format itself does not answer**: `Tool Tip` gives an
end-effector's home POSITION, but nothing in the format gives its home ORIENTATION (`Tool Direction` is a single
vector, not a full frame - it cannot by itself pin down rotation about its own axis). `tip_pose`/`solve_pose_ik`
take the tip's home orientation to be the IDENTITY in the model's own fixed/root frame - i.e. at every joint's own
0, the tip's local axes are simply the fixed frame's own axes. This is the natural default for a Product-of-
Exponentials "space form" description (there is no other frame to prefer) and needs no extra code:
`forward_kinematics` already accumulates exactly the rotation every joint composes, in this same fixed frame - this
is only a naming of what "the tip's own rotation" means, not a new computation."""
import math
from collections import namedtuple
from pathlib import Path

import numpy as np

HEADER_PREFIX = ["#", "Name", "Parent", "Axis", "Axis Type"]     # enough of the header to recognise the format


class KinematicsFileError(ValueError):
    pass


Body = namedtuple("Body", "id name parent axis axis_type direction origin limits tool_direction tool_tip mass imu")
IKResult = namedtuple("IKResult", "joint_values converged iterations final_error out_of_limits", defaults=[()])
IKPoseResult = namedtuple("IKPoseResult", "joint_values converged iterations position_error orientation_error_deg "
                                          "out_of_limits", defaults=[()])
# out_of_limits: indexes (into joint_values) of joints the solution puts outside their own Axis Limits - a caller
# must refuse to dispatch such a result even when `converged` (see _out_of_limits)
ImuReading = namedtuple("ImuReading", "present acceleration angular_velocity quaternion")


# ---- parsing ---------------------------------------------------------------------------------
def _float_or_none(text):
    text = text.strip()
    return float(text) if text else None


def _vec3_or_none(a, b, c):
    if not a.strip() and not b.strip() and not c.strip():
        return None
    return (float(a or 0), float(b or 0), float(c or 0))


def parse_kinematics_text(text, source="<string>"):
    """Parses the vendor's own `.kinematics` CSV text (see the module docstring) into a KinematicModel. Raises
    KinematicsFileError - with `source` in the message - if the header does not match what is expected, rather
    than silently misreading columns as something else."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise KinematicsFileError(f"{source}: empty file")
    header = lines[0].split(";")
    if header[:len(HEADER_PREFIX)] != HEADER_PREFIX:
        raise KinematicsFileError(f"{source}: not a recognised .kinematics file (unexpected header)")
    bodies = {}
    for line_no, line in enumerate(lines[1:], start=2):
        fields = line.split(";")
        fields += [""] * (25 - len(fields))
        try:
            row_id = int(fields[0])
        except ValueError:
            raise KinematicsFileError(f"{source}:{line_no}: '{fields[0]}' is not a row number") from None
        axis_type = fields[4].strip() or None
        if axis_type not in (None, "rotary", "linear"):
            raise KinematicsFileError(f"{source}:{line_no}: Axis Type '{axis_type}' is neither 'rotary' nor 'linear'")
        bodies[row_id] = Body(
            id=row_id, name=fields[1],
            parent=int(fields[2]) if fields[2].strip() else None,
            axis=int(float(fields[3])) if fields[3].strip() else None,
            axis_type=axis_type,
            direction=_vec3_or_none(fields[5], fields[6], fields[7]),
            origin=_vec3_or_none(fields[8], fields[9], fields[10]) or (0.0, 0.0, 0.0),
            limits=(_float_or_none(fields[11]), _float_or_none(fields[12])),
            tool_direction=_vec3_or_none(fields[14], fields[15], fields[16]),
            tool_tip=_vec3_or_none(fields[17], fields[18], fields[19]),
            mass=_float_or_none(fields[23]),
            imu=fields[24].strip(),
        )
    # A file that parses but cannot be computed with used to load fine and fail LATER, in whatever first used it:
    # a driven joint without an Axis Direction gave NaN screws - a TypeError at the first forward kinematics,
    # re-raised by the 3D view every 100 ms and hanging a Txx line; a body listed as its own parent (or any
    # parent cycle) made _actuated_ancestors loop forever, freezing the GUI on load. Refused here instead.
    for body in bodies.values():
        if body.axis is not None and body.direction is None:
            raise KinematicsFileError(f"{source}: row {body.id} ('{body.name}') is driven by axis {body.axis} but "
                                      f"has no Axis Direction")
        seen, node = set(), body
        while node.parent is not None:
            if node.parent not in bodies:
                raise KinematicsFileError(f"{source}: row {node.id} ('{node.name}') has parent {node.parent}, "
                                          f"which is not a row of this file")
            if node.id in seen:
                raise KinematicsFileError(f"{source}: row {body.id} ('{body.name}') is its own ancestor - the "
                                          f"Parent column forms a loop")
            seen.add(node.id)
            node = bodies[node.parent]
    return KinematicModel(bodies, source)


def parse_kinematics_file(path):
    path = Path(path)
    return parse_kinematics_text(path.read_text(), source=str(path))


# ---- the model --------------------------------------------------------------------------------
class KinematicModel:
    def __init__(self, bodies, source="<string>"):
        self.bodies = bodies      # row id -> Body
        self.source = source
        self._chains = None

    def chains(self):
        """{tool number: Chain} for every distinct end-effector in the file. Tool numbering follows the pattern
        confirmed against the vendor's own `DOG.NC` demo program (T3/T6/T9/T12 for robot-dog.obj's 4 legs, each
        ending at axis 2/5/8/11): **tool number = the chain's last actuated joint's own axis + 1**. A tool tip on
        a rigid, axis-less row (ko-bot.obj's own trailing rows) still resolves to the nearest ACTUATED ancestor's
        tool number - the rigid row is a structural extension of that same tool, not a separate one; the first
        (closest) tool-tip row for a given tool number wins if more than one row claims the same one."""
        if self._chains is not None:
            return self._chains
        result = {}
        for body in sorted(self.bodies.values(), key=lambda b: b.id):
            if body.tool_tip is None:
                continue
            joints = self._actuated_ancestors(body)
            if not joints:
                continue      # a tool tip with no actuated joint anywhere upstream - nothing for G-code to drive
            tool_number = joints[-1].axis + 1
            if tool_number in result:
                continue
            result[tool_number] = Chain(body.name, tool_number, joints, body.tool_tip, body.tool_direction)
        self._chains = result
        return result

    def _actuated_ancestors(self, body):
        """[Body, ...] from the ROOT down to and including `body` itself, one entry per ancestor (or `body`
        itself) that has a real Axis - base-to-tip order, what Chain's forward()/solve() need. A structural,
        axis-less row (Parent-linked but not itself driven) is skipped, its own fixed offset folded implicitly
        into the chain simply by not being a variable - only its DIRECT geometry (if it were itself queried) would
        need it, which chains() does not do."""
        ancestors = []
        node = body
        while node is not None:
            if node.axis is not None:
                ancestors.append(node)
            node = self.bodies.get(node.parent) if node.parent is not None else None
        ancestors.reverse()
        return ancestors

    def imu_bodies(self):
        """Row ids whose own IMU column is set (non-empty and not '0') - see read_imu()."""
        return [b.id for b in self.bodies.values() if b.imu and b.imu != "0"]

    def body_transforms(self, joint_values):
        """{body id: 4x4 transform} for EVERY body in the tree (not just tool-tip chains - see Chain, which only
        covers a chain's own end-effector) - what the 3D Simulation page (gui/sim3d.py) applies to each body's own
        mesh (mesh.py, whose group names match this file's own Name column 1:1). `joint_values`: {axis: value} -
        a DICT only, keyed by the model's own global axis index (unlike Chain.forward's list form, there is no
        single well-defined "axis order" across a whole TREE the way there is along one chain - different bodies
        depend on different, only partially overlapping sets of ancestor axes); a value in degrees for a rotary
        joint / mm for a linear one (the same unit Chain.forward's own callers already use - converted to radians
        internally), missing axes default to 0 (their home position). Reuses exactly the pieces `chains()` already
        has: `_actuated_ancestors` (root-to-body, base-to-tip order - a body's own transform depends only on the
        joints ABOVE it in the tree, per the module docstring's own "space form" note: a joint's screw is fixed in
        the ROOT frame, not parent-relative, so this is a plain product of exponentials over just those ancestors,
        not a per-level recursive composition) and `forward_kinematics` (the same product-of-exponentials math
        `tip_position`/`tip_pose` already use, called directly here since a body's own transform IS that product -
        there is no separate "home offset" to add the way a tool tip has one, since a body's own mesh vertices
        already sit at their home position directly in the model's shared root frame - the same frame `Tool Tip`/
        `Body Center of Mass` are given in)."""
        result = {}
        for body in self.bodies.values():
            ancestors = self._actuated_ancestors(body)
            screws = [(a.axis_type or "rotary", _unit(a.direction), np.array(a.origin, dtype=float))
                      for a in ancestors]
            thetas = [math.radians(joint_values.get(a.axis, 0.0)) if a.axis_type != "linear"
                      else float(joint_values.get(a.axis, 0.0)) for a in ancestors]
            result[body.id] = forward_kinematics(screws, thetas)
        return result


class Chain:
    """One kinematic chain (base to tip): the actuated joints (each a Body with a real Axis - a 0-based axis
    index, NOT a CAN node ID directly - see the module docstring) that move together to place ONE tool tip (a
    G-code `Txx`)."""

    def __init__(self, name, tool_number, joints, home_tip, home_direction):
        self.name = name
        self.tool_number = tool_number
        self.joints = joints                        # [Body, ...] base to tip, each with a real Axis
        self.axes = [j.axis for j in joints]         # axis indices (gcode_runner.py's _axis_to_node() turns each
                                                      # into the real CAN node) - same order as every joint-value
                                                      # list/dict here
        self.home_tip = np.array(home_tip, dtype=float)
        self.home_direction = home_direction
        self._screws = [(j.axis_type or "rotary", _unit(j.direction), np.array(j.origin, dtype=float))
                        for j in joints]

    def forward(self, joint_values):
        """joint_values: {axis: value} or a list in `self.axes` order - degrees for a rotary joint, mm for a
        linear one. -> (x, y, z) mm, the tool tip's position."""
        thetas = self._native(joint_values)
        return tuple(float(v) for v in tip_position(self._screws, thetas, self.home_tip))

    def solve(self, target_mm, initial=None, **kwargs):
        """target_mm: (x, y, z) mm. -> IKResult; `joint_values` is a list in `self.axes` order, in each joint's
        own native unit (degrees for rotary, mm for linear) - ready to convert into a drive command directly."""
        initial_native = self._native(initial) if initial is not None else None
        raw = solve_ik(self._screws, self.home_tip, target_mm, initial=initial_native,
                       bounds=self._bounds(), periodic=self._periodic(), **kwargs)
        values = [math.degrees(v) if j.axis_type != "linear" else v for j, v in zip(self.joints, raw.joint_values)]
        return IKResult(values, raw.converged, raw.iterations, raw.final_error, raw.out_of_limits)

    def forward_pose(self, joint_values):
        """Like forward(), but also returns the tip's own ORIENTATION (a 3x3 rotation matrix, in the model's fixed
        root frame - see tip_pose's own docstring, and the module docstring's own note on what "the tip's own
        orientation" means for a format with no explicit home orientation). -> ((x, y, z) mm, rotation (3, 3))."""
        thetas = self._native(joint_values)
        position, rotation = tip_pose(self._screws, thetas, self.home_tip)
        return tuple(float(v) for v in position), rotation

    def solve_pose(self, target_position_mm, target_rotation, initial=None, **kwargs):
        """Like solve(), but for a FULL pose target: target_position_mm (x, y, z) mm AND target_rotation (a 3x3
        rotation matrix - see euler_to_matrix for a human-friendly way to build one from roll/pitch/yaw). ->
        IKPoseResult; `joint_values` in each joint's own native unit, same as solve()."""
        initial_native = self._native(initial) if initial is not None else None
        raw = solve_pose_ik(self._screws, self.home_tip, target_position_mm, target_rotation,
                            initial=initial_native, bounds=self._bounds(), periodic=self._periodic(), **kwargs)
        values = [math.degrees(v) if j.axis_type != "linear" else v for j, v in zip(self.joints, raw.joint_values)]
        return IKPoseResult(values, raw.converged, raw.iterations, raw.position_error, raw.orientation_error_deg,
                            raw.out_of_limits)

    def _bounds(self):
        """Each joint's own Axis Limits in the solver's native units (radians for rotary, mm for linear), (lo, hi)
        with None for a limit the file leaves blank - or None for a joint with no limits at all."""
        bounds = []
        for j in self.joints:
            lo, hi = j.limits
            if lo is None and hi is None:
                bounds.append(None)
                continue
            if j.axis_type != "linear":
                lo, hi = (math.radians(lo) if lo is not None else None), (math.radians(hi) if hi is not None else None)
            bounds.append((lo, hi))
        return bounds

    def _periodic(self):
        return [j.axis_type != "linear" for j in self.joints]

    def _native(self, values):
        """A per-joint list/dict of values in DEGREES(rotary)/mm(linear) -> radians(rotary)/mm(linear) - what
        forward_kinematics/solve_ik actually compute with."""
        if isinstance(values, dict):
            values = [values.get(axis, 0.0) for axis in self.axes]
        return [math.radians(v) if j.axis_type != "linear" else float(v) for j, v in zip(self.joints, values)]


# ---- the math: Product of Exponentials (space form) --------------------------------------------
def _unit(v):
    v = np.array(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise KinematicsFileError("an axis direction of (0, 0, 0) is not a valid rotation/translation axis")
    return v / n


def _skew(v):
    x, y, z = v
    return np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]], dtype=float)


def _rodrigues(omega_hat, theta):
    """3x3 rotation matrix: `theta` radians about the unit vector `omega_hat`."""
    k = _skew(omega_hat)
    return np.eye(3) + math.sin(theta) * k + (1 - math.cos(theta)) * (k @ k)


def _rotary_exp(omega_hat, q, theta):
    """4x4 homogeneous transform: rotate `theta` radians about the line through point `q` with direction
    `omega_hat` - the closed form of exp([S]theta) for a revolute screw axis S = (omega_hat, -omega_hat x q)."""
    r = _rodrigues(omega_hat, theta)
    t = np.eye(4)
    t[:3, :3] = r
    t[:3, 3] = q - r @ q            # q + R@(x - q) = R@x + (q - R@q)
    return t


def _linear_exp(direction_hat, distance):
    """4x4 homogeneous transform: translate `distance` (mm) along `direction_hat` - a prismatic screw."""
    t = np.eye(4)
    t[:3, 3] = direction_hat * distance
    return t


def forward_kinematics(screws, thetas):
    """screws: [(axis_type, unit_direction, origin_point), ...] base to tip, each joint's own fixed screw axis
    (see the module docstring - given once, in the model's fixed/root frame). thetas: radians (rotary) / mm
    (linear), same order. -> 4x4 transform: the ROTATION every joint composes to, and the TRANSLATION that would
    be applied to a point at the origin - callers add the chain's own home position separately (see tip_position)."""
    t = np.eye(4)
    for (axis_type, direction, origin), theta in zip(screws, thetas):
        t = t @ (_rotary_exp(direction, origin, theta) if axis_type != "linear" else _linear_exp(direction, theta))
    return t


def tip_position(screws, thetas, home_position):
    """The end-effector's position (3,) after applying `thetas` to `screws` (base to tip - see
    forward_kinematics), starting from its HOME position (3,) - its position when every joint is at 0."""
    t = forward_kinematics(screws, thetas)
    home = np.array([home_position[0], home_position[1], home_position[2], 1.0])
    return (t @ home)[:3]


def tip_pose(screws, thetas, home_position):
    """Like tip_position, but also returns the tip's own ORIENTATION (a 3x3 rotation matrix) - needed for full-pose
    (position + orientation) inverse kinematics (solve_pose_ik). The tip's home orientation is taken to be the
    IDENTITY in the fixed/root frame (see the module docstring for why) - `forward_kinematics` already accumulates
    exactly the rotation every joint composes, in that same frame, so no extra computation is needed here beyond
    also returning it. -> (position (3,), rotation (3, 3))."""
    t = forward_kinematics(screws, thetas)
    home = np.array([home_position[0], home_position[1], home_position[2], 1.0])
    return (t @ home)[:3], t[:3, :3]


def euler_to_matrix(roll_deg, pitch_deg, yaw_deg):
    """Roll/pitch/yaw (degrees) -> a 3x3 rotation matrix, intrinsic Z-Y-X (yaw, then pitch, then roll - the usual
    aerospace/robotics convention: R = Rz(yaw) @ Ry(pitch) @ Rx(roll)). A human-friendly way to build a target
    orientation for solve_pose_ik/Chain.solve_pose - the math itself only ever works with the matrix directly."""
    roll, pitch, yaw = math.radians(roll_deg), math.radians(pitch_deg), math.radians(yaw_deg)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rz = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
    ry = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]])
    return rz @ ry @ rx


def matrix_to_euler(r):
    """The inverse of euler_to_matrix - a 3x3 rotation matrix -> (roll, pitch, yaw) degrees, same Z-Y-X convention.
    Only used to SHOW an already-known orientation (e.g. Chain.forward_pose's own result) back to the user;
    solve_pose_ik itself always works with the matrix directly, never round-trips through Euler angles. Like any
    3-angle Euler convention, this has a real singularity (gimbal lock) at pitch = +-90 degrees, where roll and yaw
    become the same rotation and only their SUM is well defined - this picks roll = 0 there, a standard,
    documented convention (euler_to_matrix(*matrix_to_euler(r)) still reconstructs the same matrix even then)."""
    sp = min(1.0, max(-1.0, -r[2, 0]))
    pitch = math.asin(sp)
    if abs(sp) > 1 - 1e-9:                             # gimbal lock: roll and yaw are not independently recoverable
        roll = 0.0
        yaw = math.atan2(-r[0, 1], r[1, 1])
    else:
        roll = math.atan2(r[2, 1], r[2, 2])
        yaw = math.atan2(r[1, 0], r[0, 0])
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


def _vee(skew):
    """The inverse of _skew(): a 3x3 skew-symmetric matrix -> its own 3-vector."""
    return np.array([skew[2, 1], skew[0, 2], skew[1, 0]])


def _log_so3(r):
    """The "log map" of a rotation matrix: the (axis * angle) 3-vector (radians) such that r == _rodrigues(axis_hat,
    angle) - the inverse of _rodrigues(). Standard closed form (Lynch & Park, "Modern Robotics", MatrixLog3),
    handling the near-180-degree case separately since the ordinary formula divides by sin(angle), which is ~0
    there."""
    cos_theta = min(1.0, max(-1.0, (np.trace(r) - 1) / 2))
    theta = math.acos(cos_theta)
    if theta < 1e-9:
        return np.zeros(3)                              # no rotation at all
    if math.pi - theta < 1e-6:
        # near 180 degrees: r - r.T is ~0, so the ordinary formula below breaks. Any column of (r + I) gives
        # (twice) a vector along the rotation axis, up to sign - pick the largest diagonal entry's own column for
        # numerical stability (the standard trick; the sign of the recovered axis is inherently ambiguous at
        # exactly 180 degrees, since a rotation by pi about +axis and -axis are the same rotation).
        k = int(np.argmax(np.diag(r)))
        v = r[:, k] + np.eye(3)[:, k]
        axis = v / np.linalg.norm(v)
        return axis * theta
    axis = _vee(r - r.T) / (2 * math.sin(theta))
    return axis * theta


def _rotation_error_vector(current_r, target_r):
    """The (axis * angle) rotation, in the fixed frame, that would need to be applied to `current_r` to reach
    `target_r` - the rotational analogue of "target - current" for a position: zero exactly when the two already
    match. Used by solve_pose_ik exactly where position IK uses target - current."""
    return _log_so3(target_r @ current_r.T)


def _numeric_jacobian(screws, thetas, home_position, eps=1e-6):
    """3xN numeric Jacobian of tip_position with respect to each joint's own theta - central difference. Chosen
    over an analytic Jacobian so IK correctness reduces to FK correctness alone (see the module docstring)."""
    n = len(thetas)
    j = np.zeros((3, n))
    for i in range(n):
        plus, minus = list(thetas), list(thetas)
        plus[i] += eps
        minus[i] -= eps
        j[:, i] = (tip_position(screws, plus, home_position) - tip_position(screws, minus, home_position)) / (2 * eps)
    return j


def _fold(thetas, periodic, seed):
    """A rotary joint's angle folded to the equivalent nearest the pose the search started from - a full turn
    away is the same pose but never the same move: an unfolded search happily "converged" on robot-dog to
    [5419, 29819, -1024] degrees, dispatched as 15 and 83 motor turns, when [19, -61, 56] was right there.
    Forward kinematics is periodic in a rotary joint, so folding changes nothing about the search itself (unlike
    clamping to the limits mid-search, which pins a joint against its limit and stalls the solver - tried)."""
    out = np.array(thetas, dtype=float)
    if periodic is not None:
        for i in range(len(out)):
            if periodic[i]:
                out[i] = seed[i] + (out[i] - seed[i] + math.pi) % (2 * math.pi) - math.pi
    return out


def _out_of_limits(thetas, bounds):
    """Indexes of the joints a solution puts outside their own Axis Limits (`bounds`: per joint (lo, hi) in
    native units, None where the file gives none). The limits were read but never used by the solver, only by
    the 3D view's sliders - a caller refuses such a solution instead of sending it to a real joint."""
    if bounds is None:
        return ()
    bad = []
    for i, b in enumerate(bounds):
        if b is None:
            continue
        lo, hi = b
        if (lo is not None and thetas[i] < lo - 1e-9) or (hi is not None and thetas[i] > hi + 1e-9):
            bad.append(i)
    return tuple(bad)


def solve_ik(screws, home_position, target, initial=None, max_iterations=200, tolerance=1e-3, damping=1e-2,
             bounds=None, periodic=None):
    """Damped least squares (Levenberg-Marquardt-style) inverse kinematics: find joint values (radians/mm, native
    units) whose forward kinematics puts the chain's tip at `target` (3,) mm, starting the search from `initial`
    (or all-0 - the chain's own home pose - if not given). -> IKResult. `tolerance` is mm; `damping` trades speed
    for stability near a singularity (a larger value converges more slowly but never diverges wildly). `bounds`
    ([(lo, hi) or None] per joint, native units) and `periodic` ([bool] per joint, True for a rotary one) keep the
    search physical - see _constrain."""
    n = len(screws)
    thetas = np.array(initial, dtype=float) if initial is not None else np.zeros(n)
    seed = thetas.copy()
    thetas = _fold(thetas, periodic, seed)
    target = np.asarray(target, dtype=float)
    iterations = 0
    for iterations in range(max_iterations):
        current = tip_position(screws, thetas, home_position)
        error = target - current
        distance = float(np.linalg.norm(error))
        if distance < tolerance:
            return IKResult(list(thetas), True, iterations, distance, _out_of_limits(thetas, bounds))
        j = _numeric_jacobian(screws, thetas, home_position)
        jjt = j @ j.T
        dtheta = j.T @ np.linalg.solve(jjt + damping ** 2 * np.eye(3), error)
        thetas = _fold(thetas + dtheta, periodic, seed)
    final = tip_position(screws, thetas, home_position)
    return IKResult(list(thetas), False, iterations + 1, float(np.linalg.norm(target - final)),
                    _out_of_limits(thetas, bounds))


def _pose_error(screws, thetas, home_position, target_position, target_rotation):
    """The 6-vector [position error (mm); orientation error (radians, axis*angle)] between the tip's own current
    pose and the target - zero exactly at the target, the full-pose analogue of solve_ik's own `target - current`.
    See _rotation_error_vector for the orientation half."""
    position, rotation = tip_pose(screws, thetas, home_position)
    pos_err = np.asarray(target_position, dtype=float) - position
    rot_err = _rotation_error_vector(rotation, target_rotation)
    return np.concatenate([pos_err, rot_err])


def _numeric_jacobian_pose(screws, thetas, home_position, target_position, target_rotation, eps=1e-6):
    """6xN numeric Jacobian of the pose ERROR (_pose_error, not the pose itself) with respect to each joint's own
    theta - central difference, mirroring _numeric_jacobian's own reasoning (IK correctness reduces to FK
    correctness alone). Differentiating the ERROR directly (rather than the pose) is what lets solve_pose_ik pair
    it with the same damped-least-squares update _numeric_jacobian's own caller uses - see solve_pose_ik."""
    n = len(thetas)
    j = np.zeros((6, n))
    for i in range(n):
        plus, minus = list(thetas), list(thetas)
        plus[i] += eps
        minus[i] -= eps
        e_plus = _pose_error(screws, plus, home_position, target_position, target_rotation)
        e_minus = _pose_error(screws, minus, home_position, target_position, target_rotation)
        j[:, i] = (e_plus - e_minus) / (2 * eps)
    return j


def solve_pose_ik(screws, home_position, target_position, target_rotation, initial=None, max_iterations=200,
                  tolerance=1e-3, orientation_tolerance=1e-3, damping=1e-2, bounds=None, periodic=None):
    """Damped least squares inverse kinematics for a FULL pose target (position AND orientation together) - the
    same Levenberg-Marquardt-style search solve_ik already uses for position alone, generalised to a 6-vector error
    (3 mm of position + 3 radians of a rotation "log map", see _rotation_error_vector) instead of 3. `tolerance` is
    mm (position), `orientation_tolerance` is radians (orientation) - both must be met to converge. Since the
    Jacobian here is of the ERROR itself (see _numeric_jacobian_pose), not of the current pose the way solve_ik's
    own Jacobian is, the update carries a leading minus sign that solve_ik's does not - the two are the same
    damped-least-squares step underneath (confirmed algebraically: applied to position alone, this formula reduces
    to exactly solve_ik's own). -> IKPoseResult."""
    n = len(screws)
    thetas = np.array(initial, dtype=float) if initial is not None else np.zeros(n)
    seed = thetas.copy()
    thetas = _fold(thetas, periodic, seed)
    target_position = np.asarray(target_position, dtype=float)
    target_rotation = np.asarray(target_rotation, dtype=float)
    iterations = 0
    for iterations in range(max_iterations):
        error = _pose_error(screws, thetas, home_position, target_position, target_rotation)
        pos_distance = float(np.linalg.norm(error[:3]))
        rot_distance = float(np.linalg.norm(error[3:]))
        if pos_distance < tolerance and rot_distance < orientation_tolerance:
            return IKPoseResult(list(thetas), True, iterations, pos_distance, math.degrees(rot_distance),
                                _out_of_limits(thetas, bounds))
        j = _numeric_jacobian_pose(screws, thetas, home_position, target_position, target_rotation)
        jjt = j @ j.T
        dtheta = -j.T @ np.linalg.solve(jjt + damping ** 2 * np.eye(6), error)
        thetas = _fold(thetas + dtheta, periodic, seed)
    error = _pose_error(screws, thetas, home_position, target_position, target_rotation)
    return IKPoseResult(list(thetas), False, iterations + 1, float(np.linalg.norm(error[:3])),
                        math.degrees(float(np.linalg.norm(error[3:]))), _out_of_limits(thetas, bounds))


# ---- IMU (placeholder - see the README: no real IMU hardware exists on this drive) --------------
def read_imu(_model, _body_id):
    """Always an explicit "not present" reading today - the user's own choice when asked whether to build IMU
    support: no real IMU hardware exists on this drive, but the API shape is here, keyed off the file's own
    per-body IMU column (KinematicModel.imu_bodies()) rather than a separate registry, ready for a real
    integration to fill in later without inventing a new "which bodies have one" concept."""
    return ImuReading(False, None, None, None)
