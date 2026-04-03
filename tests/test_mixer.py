"""Tests for arcade and tank drive mixing algorithms."""

from drive.mixer import arcade_mix, tank_mix


def test_arcade_neutral():
    left, right = arcade_mix(0.0, 0.0)
    assert left == 0.0 and right == 0.0


def test_arcade_forward():
    left, right = arcade_mix(1.0, 0.0)
    assert left == 1.0 and right == 1.0


def test_arcade_reverse():
    left, right = arcade_mix(-1.0, 0.0)
    assert left == -1.0 and right == -1.0


def test_arcade_turn_right():
    left, right = arcade_mix(1.0, 1.0, steer_sensitivity=1.0)
    assert left > right  # Left faster = turn right


def test_arcade_turn_left():
    left, right = arcade_mix(1.0, -1.0, steer_sensitivity=1.0)
    assert right > left  # Right faster = turn left


def test_arcade_output_clamped():
    left, right = arcade_mix(1.0, 1.0)
    assert -1.0 <= left <= 1.0
    assert -1.0 <= right <= 1.0


def test_arcade_extreme_inputs():
    left, right = arcade_mix(1.0, 1.0, steer_sensitivity=1.0)
    assert -1.0 <= left <= 1.0
    assert -1.0 <= right <= 1.0


def test_arcade_sensitivity_zero():
    """Zero sensitivity means no steering."""
    left, right = arcade_mix(0.5, 1.0, steer_sensitivity=0.0)
    assert left == right


def test_tank_center_is_stop():
    left, right = tank_mix(0.5, 0.5)
    assert abs(left) < 0.01 and abs(right) < 0.01


def test_tank_full_forward():
    left, right = tank_mix(1.0, 1.0)
    assert left == 1.0 and right == 1.0


def test_tank_full_reverse():
    left, right = tank_mix(0.0, 0.0)
    assert left == -1.0 and right == -1.0


def test_tank_spin_right():
    left, right = tank_mix(1.0, 0.0)
    assert left == 1.0 and right == -1.0


def test_tank_output_clamped():
    left, right = tank_mix(1.5, -0.5)
    assert -1.0 <= left <= 1.0
    assert -1.0 <= right <= 1.0
