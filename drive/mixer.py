"""Joystick-to-motor mixing algorithms (arcade and tank modes).

Both functions are pure (no side effects) and clamp output to [-1.0, +1.0].
"""


def arcade_mix(
    speed: float, steer: float, steer_sensitivity: float = 0.7
) -> tuple[float, float]:
    """Arcade drive: one axis for speed, one for steering.

    Args:
        speed: -1.0 (full reverse) to +1.0 (full forward).
        steer: -1.0 (full left) to +1.0 (full right).
        steer_sensitivity: Steering mix ratio (0.0-1.0).

    Returns:
        (left_speed, right_speed) each in [-1.0, +1.0].
    """
    steer = steer * steer_sensitivity
    left = speed + steer
    right = speed - steer

    # Normalize if either exceeds +/-1.0 (preserve ratio)
    max_val = max(abs(left), abs(right), 1.0)
    left /= max_val
    right /= max_val

    return (
        max(-1.0, min(1.0, left)),
        max(-1.0, min(1.0, right)),
    )


def tank_mix(left_throttle: float, right_throttle: float) -> tuple[float, float]:
    """Tank drive: independent left/right throttle control.

    Throttle values from Phase 1 are unipolar 0.0..+1.0.
    Convert to bipolar: 0.0 = full reverse, 0.5 = stop, 1.0 = full forward.

    Args:
        left_throttle: 0.0 to +1.0 from left throttle axis.
        right_throttle: 0.0 to +1.0 from right throttle axis.

    Returns:
        (left_speed, right_speed) each in [-1.0, +1.0].
    """
    left = (left_throttle * 2.0) - 1.0
    right = (right_throttle * 2.0) - 1.0
    return (
        max(-1.0, min(1.0, left)),
        max(-1.0, min(1.0, right)),
    )
