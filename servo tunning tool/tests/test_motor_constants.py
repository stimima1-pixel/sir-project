import pytest

from sdr_tuner import motor_constants as mc

# The real bench drive's own Datasheet values (10 poles, Ke=0.01366658 V/(rad/s)), and the vendor's own
# "Kv, Ke, Kt Constants Converter" tool's own output for them - read directly off the tool running in a Windows VM
# (2026-09-24), not assumed. Every test below is anchored to this one real, independently-obtained data point.
POLES = 10
KE = 0.01366658
KT = 0.10249935
KV = 80.683


def test_kt_from_ke_matches_the_vendors_own_tool():
    assert mc.kt_from_ke(KE, POLES) == pytest.approx(KT, rel=1e-4)


def test_kv_from_ke_matches_the_vendors_own_tool():
    assert mc.kv_from_ke(KE, POLES) == pytest.approx(KV, rel=1e-3)


def test_kv_from_kt_matches_the_vendors_own_tool():
    assert mc.kv_from_kt(KT) == pytest.approx(KV, rel=1e-3)


def test_ke_from_kt_round_trips():
    assert mc.ke_from_kt(mc.kt_from_ke(KE, POLES), POLES) == pytest.approx(KE, rel=1e-9)


def test_ke_from_kv_round_trips():
    assert mc.ke_from_kv(mc.kv_from_ke(KE, POLES), POLES) == pytest.approx(KE, rel=1e-6)


def test_kt_from_kv_is_the_same_formula_both_ways():
    """The Kt<->Kv relation has no poles term and is its own inverse."""
    assert mc.kt_from_kv(mc.kv_from_kt(KT)) == pytest.approx(KT, rel=1e-9)
    assert mc.kv_from_kt(mc.kt_from_kv(KV)) == pytest.approx(KV, rel=1e-9)


@pytest.mark.parametrize("kind,value", [("ke", KE), ("kt", KT), ("kv", KV)])
def test_other_two_agrees_with_the_direct_functions(kind, value):
    ke, kv, kt = mc.other_two(kind, value, POLES)
    assert ke == pytest.approx(KE, rel=1e-3)
    assert kv == pytest.approx(KV, rel=1e-3)
    assert kt == pytest.approx(KT, rel=1e-3)


def test_other_two_keeps_the_given_value_exact():
    assert mc.other_two("ke", KE, POLES)[0] == KE
    assert mc.other_two("kv", KV, POLES)[1] == KV
    assert mc.other_two("kt", KT, POLES)[2] == KT


def test_other_two_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        mc.other_two("bogus", 1.0, POLES)


def test_poles_scales_ke_kt_linearly():
    """Doubling the pole count doubles Kt for the same Ke (Kt = 3/4 x poles x Ke) - a sanity check the formula
    is not accidentally using pole PAIRS somewhere and poles elsewhere."""
    assert mc.kt_from_ke(KE, 20) == pytest.approx(2 * mc.kt_from_ke(KE, 10))
