"""
Unit tests for ATEM Mini Pro Smooth Transitions (`MIX`, `DIP`, `WIPE`, `DVE`),
Hardware Special Features (`PiP`, `FTB`, `AFV`), and Dynamic Hold Time Sliders.
"""

from __future__ import annotations

import numpy as np

from src.atem.controller import ATEMController
from src.desktop_app import render_atem_program_composite
from src.engine.state_machine import BroadcastStateMachine, RuleEngineConfig
from src.pipeline.vision_scorer import ChannelMetrics


def _make_metric(cam_id: int, score: float, blurry: bool = False) -> ChannelMetrics:
    return ChannelMetrics(
        cam_id=cam_id,
        enabled=True,
        has_signal=True,
        framing_score=score,
        motion_score=score,
        sharpness_score=0.1 if blurry else 0.9,
        laplacian_var=15.0 if blurry else 180.0,
        is_blurry=blurry,
        edge_clipped=False,
        face_detected=False,
        audio_bonus=0.0,
        composite_score=score,
        subject_bbox=(50, 40, 120, 100),
    )


def test_atem_controller_smooth_transitions_and_hardware_features() -> None:
    atem = ATEMController()

    # 1. Configure MIX transition with 0.8s duration (24 frames)
    status = atem.set_transition_style_and_rate("MIX", 0.8)
    assert status.transition_style == "MIX"
    assert status.transition_duration_sec == 0.8

    # Execute switch Cam 1 -> Cam 3
    status2 = atem.sync_switcher_bus(program_cam=3, preview_cam=2, switched=True, reason="Test MIX")
    assert status2.program_input == 3
    assert status2.last_executed_style == "MIX"
    assert "ATEM MIX (0.8s)" in status2.last_command

    # 2. Test PiP, FTB, and AFV hardware feature controls
    s_pip = atem.set_pip_enabled(True, fill_cam=4)
    assert s_pip.pip_enabled is True
    assert s_pip.pip_fill_cam == 4

    s_ftb = atem.toggle_fade_to_black(duration_sec=1.0)
    assert s_ftb.ftb_active is True
    s_ftb_off = atem.toggle_fade_to_black(duration_sec=1.0)
    assert s_ftb_off.ftb_active is False

    s_afv = atem.set_afv_enabled(True)
    assert s_afv.afv_enabled is True


def test_state_machine_dynamic_hold_sliders_and_ai_auto_transition() -> None:
    cfg = RuleEngineConfig(min_hold_sec=1.2, max_hold_sec=4.0, transition_mode="AI_AUTO")
    sm = BroadcastStateMachine(config=cfg, initial_program=1)

    metrics = {
        1: _make_metric(1, 0.55),
        2: _make_metric(2, 0.68),
        3: _make_metric(3, 0.50),
        4: _make_metric(4, 0.45),
    }

    # Before 1.2s min_hold_sec: locked
    dec_locked = sm.step(0.8, metrics, is_beat_peak=False, active_channels=[1, 2, 3, 4])
    assert dec_locked.switched is False
    assert dec_locked.state == "HOLD_LOCKED"

    # At 4.1s (>= max_hold_sec 4.0s): forced rotation uses smooth MIX transition in AI_AUTO mode
    metrics_close = {
        1: _make_metric(1, 0.65),
        2: _make_metric(2, 0.66),
        3: _make_metric(3, 0.50),
        4: _make_metric(4, 0.45),
    }
    dec_rot = sm.step(4.1, metrics_close, is_beat_peak=False, active_channels=[1, 2, 3, 4])
    assert dec_rot.switched is True
    assert dec_rot.state == "FORCED_ROTATION"
    assert dec_rot.recommended_transition == "MIX"


def test_render_atem_program_composite_styles() -> None:
    frame_a = np.full((180, 320, 3), 40, dtype=np.uint8)
    frame_b = np.full((180, 320, 3), 200, dtype=np.uint8)
    pip_frame = np.full((180, 320, 3), 120, dtype=np.uint8)

    for style in ("MIX", "DIP", "WIPE", "DVE", "CUT"):
        comp = render_atem_program_composite(
            from_bgr=frame_a,
            to_bgr=frame_b,
            style=style,
            progress=0.5,
            pip_enabled=True,
            pip_bgr=pip_frame,
            ftb_alpha=0.2,
        )
        assert comp.shape == (180, 320, 3)
        assert comp.dtype == np.uint8
