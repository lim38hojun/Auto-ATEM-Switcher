"""Unit tests for `BroadcastStateMachine` (`src/engine/state_machine.py`) across 2, 3, and 4 cameras."""

from src.engine.state_machine import BroadcastStateMachine, RuleEngineConfig
from src.pipeline.vision_scorer import ChannelMetrics


def _mock_metric(
    cam_id: int,
    score: float,
    is_blurry: bool = False,
    enabled: bool = True,
    has_signal: bool = True,
) -> ChannelMetrics:
    return ChannelMetrics(
        cam_id=cam_id,
        enabled=enabled,
        has_signal=has_signal,
        framing_score=score,
        motion_score=score,
        sharpness_score=0.1 if is_blurry else 0.9,
        laplacian_var=15.0 if is_blurry else 180.0,
        audio_bonus=0.0,
        is_blurry=is_blurry,
        edge_clipped=False,
        face_detected=False,
        composite_score=0.02 if (is_blurry or not enabled or not has_signal) else score,
        subject_bbox=(100, 60, 200, 180),
    )


def test_min_hold_lock_prevents_rapid_flicker() -> None:
    sm = BroadcastStateMachine(
        RuleEngineConfig(min_hold_sec=2.5, max_hold_sec=7.5, switch_margin=0.08, beat_sync_enabled=False),
        initial_program=1,
    )
    m = {1: _mock_metric(1, 0.45), 2: _mock_metric(2, 0.90), 3: _mock_metric(3, 0.50), 4: _mock_metric(4, 0.40)}
    d1 = sm.step(0.5, m)
    assert d1.switched is False
    assert d1.program_cam == 1
    assert d1.state == "HOLD_LOCKED"

    d2 = sm.step(1.8, m)
    assert d2.switched is False
    assert d2.program_cam == 1

    d3 = sm.step(2.6, m)
    assert d3.switched is True
    assert d3.program_cam == 2


def test_emergency_blur_evasion_breaks_min_hold_lock() -> None:
    sm = BroadcastStateMachine(
        RuleEngineConfig(min_hold_sec=2.5, emergency_blur_frames=2),
        initial_program=4,
    )
    m_blur = {
        1: _mock_metric(1, 0.70),
        2: _mock_metric(2, 0.82),
        3: _mock_metric(3, 0.60),
        4: _mock_metric(4, 0.02, is_blurry=True),
    }
    sm.step(0.4, m_blur)
    d_emerg = sm.step(0.5, m_blur)
    assert d_emerg.switched is True
    assert d_emerg.state == "EMERGENCY_CUT"
    assert d_emerg.program_cam == 2


def test_max_hold_forces_rotation_to_next_highest_camera() -> None:
    sm = BroadcastStateMachine(
        RuleEngineConfig(min_hold_sec=2.5, max_hold_sec=7.5, beat_sync_enabled=False),
        initial_program=2,
    )
    m_stay = {
        1: _mock_metric(1, 0.76),
        2: _mock_metric(2, 0.88),
        3: _mock_metric(3, 0.55),
        4: _mock_metric(4, 0.40),
    }
    d_rot = sm.step(7.6, m_stay)
    assert d_rot.switched is True
    assert d_rot.state == "FORCED_ROTATION"
    assert d_rot.program_cam == 1


def test_2cam_and_signal_loss_auto_recovery() -> None:
    sm = BroadcastStateMachine(
        RuleEngineConfig(min_hold_sec=2.5, max_hold_sec=7.5, beat_sync_enabled=False),
        initial_program=3,
    )
    # Only Cam 1 and Cam 2 have signal; Cam 3 & 4 have no signal (unplugged HDMI)
    m_2cam = {
        1: _mock_metric(1, 0.72, has_signal=True),
        2: _mock_metric(2, 0.85, has_signal=True),
        3: _mock_metric(3, 0.0, has_signal=False),
        4: _mock_metric(4, 0.0, has_signal=False),
    }
    d_rec = sm.step(0.2, m_2cam, active_channels=[1, 2])
    assert d_rec.switched is True
    assert d_rec.state == "SIGNAL_LOSS_CUT"
    assert d_rec.program_cam == 2
    assert d_rec.active_cam_count == 2
