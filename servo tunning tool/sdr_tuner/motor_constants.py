"""Conversions between a BLDC/PMSM motor's Ke (back-EMF constant), Kv (velocity constant) and Kt (torque constant).
No Qt - see gui/motor_constants_dialog.py for the dialog that uses this.

Conventions (fixed - matches the vendor's own "Kv, Ke, Kt Constants Converter" tool, found in their original IDE
and used here since it is the one already familiar to this drive's users):
    Ke: V/(rad/s), peak, line-to-neutral, ELECTRICAL angular velocity (numerically equals the flux linkage in Wb)
    Kt: (N*m)/A
    Kv: RPM/V, peak, line-to-line, MECHANICAL speed
    poles: rotor pole count (an even integer - poles = 2 x pole pairs)

The formulas were not assumed - they were reverse-derived from the standard 3-phase PMSM torque equation
(T = 1.5 x pole_pairs x flux_linkage x Iq) and the line-to-neutral/line-to-line peak voltage ratio (sqrt(3) for a
balanced 3-phase winding), THEN VERIFIED directly against the vendor's own tool (2026-09-24): poles=10,
Ke=0.01366658 gave Kt=0.10249935 and Kv=80.683 there, which these functions reproduce exactly."""
import math

SQRT3 = math.sqrt(3.0)


def kt_from_ke(ke, poles):
    return (3.0 * poles / 4.0) * ke


def ke_from_kt(kt, poles):
    return (4.0 * kt) / (3.0 * poles)


def _kt_kv_conjugate(x):
    """The Kt<->Kv relation is self-inverse (same formula either direction) - poles cancels out, since both are
    already mechanical-domain quantities (torque-per-current, speed-per-voltage); poles is only needed to bridge
    to/from Ke, which is defined in electrical-domain terms."""
    return 45.0 / (math.pi * SQRT3 * x)


kv_from_kt = _kt_kv_conjugate
kt_from_kv = _kt_kv_conjugate


def ke_from_kv(kv, poles):
    return ke_from_kt(kt_from_kv(kv), poles)


def kv_from_ke(ke, poles):
    return kv_from_kt(kt_from_ke(ke, poles))


def other_two(kind, value, poles):
    """kind is one of 'ke'/'kv'/'kt', value is that constant's own value - returns (ke, kv, kt) with `kind`'s own
    slot exactly `value` and the other two computed from it."""
    if kind == "ke":
        kt = kt_from_ke(value, poles)
        return value, kv_from_kt(kt), kt
    if kind == "kt":
        return ke_from_kt(value, poles), kv_from_kt(value), value
    if kind == "kv":
        kt = kt_from_kv(value)
        return ke_from_kt(kt, poles), value, kt
    raise ValueError(f"unknown constant kind: {kind!r}")
