"""Unit tests for ATEM Mini Pro 10-Split Multiview 1080p cropping (`src/capture/ingestor.py`)."""

from scripts.generate_synthetic_scene import (
    compose_atem_10split_multiview,
    render_four_cameras_at_time,
)
from src.capture.ingestor import crop_atem_multiview_frame
from src.pipeline.vision_scorer import VisionScorer


def test_atem_10split_multiview_cropping_dimensions_and_blur_preservation() -> None:
    frames, meta = render_four_cameras_at_time(15.5)  # Cam 4 is blurry at 15.5s
    mv_1080p = compose_atem_10split_multiview(frames, program_cam=3, preview_cam=1, meta=meta)

    assert mv_1080p.shape == (1080, 1920, 3)

    cropped_channels = crop_atem_multiview_frame(mv_1080p, target_size=(640, 360))
    assert set(cropped_channels.keys()) == {1, 2, 3, 4}
    for cid, img in cropped_channels.items():
        assert img.shape == (360, 640, 3), f"Cam {cid} shape mismatch"

    scorer = VisionScorer()
    metrics = scorer.score_all_channels(cropped_channels)
    # Safe Inner ROI excludes sharp border/label text so Cam 4 is still accurately detected as blurry!
    assert metrics[4].is_blurry is True
    assert metrics[1].is_blurry is False
    assert metrics[3].is_blurry is False
