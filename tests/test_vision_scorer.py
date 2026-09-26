"""Unit tests for `src/pipeline/vision_scorer.py` and `src/pipeline/audio_analyzer.py`."""

from scripts.generate_synthetic_scene import render_four_cameras_at_time
from src.pipeline.audio_analyzer import AudioAnalyzer
from src.pipeline.vision_scorer import VisionScorer


def test_blur_detection_disqualifies_cam4_at_hazard_window() -> None:
    scorer = VisionScorer()
    frames, meta = render_four_cameras_at_time(15.0)  # Cam 4 has severe camera shake & blur at t=15.0s
    metrics = scorer.score_all_channels(frames)

    assert metrics[4].is_blurry is True
    assert metrics[4].laplacian_var < scorer.config.blur_threshold
    assert metrics[4].composite_score < 0.05
    # Meanwhile Cam 1, 2, 3 must remain sharp (not blurry)
    assert metrics[1].is_blurry is False
    assert metrics[2].is_blurry is False
    assert metrics[3].is_blurry is False


def test_vocal_closeup_and_guitar_solo_scoring() -> None:
    scorer = VisionScorer()
    audio = AudioAnalyzer()

    # 1. Verse 1 (t=6.0s): Cam 2 Vocal Close-Up should outscore Cam 3
    frames_v, meta_v = render_four_cameras_at_time(6.0)
    af_v = audio.analyze(6.0, meta_v)
    m_v = scorer.score_all_channels(frames_v, af_v.camera_audio_bonuses)
    assert m_v[2].composite_score > m_v[3].composite_score

    # 2. Guitar Solo (t=12.0s & t=12.05s for motion delta): Cam 3 should rank #1
    frames_g1, meta_g1 = render_four_cameras_at_time(12.0)
    scorer.score_all_channels(frames_g1)
    frames_g2, meta_g2 = render_four_cameras_at_time(12.05)
    af_g2 = audio.analyze(12.05, meta_g2)
    m_g2 = scorer.score_all_channels(frames_g2, af_g2.camera_audio_bonuses)

    best_cam = max(m_g2.values(), key=lambda c: c.composite_score).cam_id
    assert best_cam == 3
