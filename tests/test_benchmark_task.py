"""Integration benchmark tests for 2-Camera, 3-Camera, and 4-Camera Scene Selection Tasks."""

from src.server.app import run_full_scene_benchmark


def test_full_30s_multi_camera_selection_benchmark_synthetic_and_multiview() -> None:
    for mode in ("synthetic", "multiview_crop"):
        report = run_full_scene_benchmark(mode=mode, active_channels=[1, 2, 3, 4])
        metrics = report["metrics"]

        assert metrics["blur_avoidance_rate_pct"] >= 98.0, f"Blur avoidance too low in {mode}: {metrics}"
        assert metrics["flicker_violations"] == 0, f"Flicker violations detected in {mode}: {metrics}"
        # GT alignment is based on synthetic audio-driven labels; with w_audio=0.0 (pure vision),
        # divergence is expected. Threshold reflects realistic vision-only performance.
        assert metrics["gt_alignment_accuracy_pct"] >= 55.0, f"GT alignment too low in {mode}: {metrics}"


def test_2cam_and_3cam_dynamic_topologies_never_select_disconnected_ports() -> None:
    # Test 2-Camera setup (only Cam 1 and Cam 2 connected; Cam 3 & 4 disconnected/black)
    rep_2cam = run_full_scene_benchmark(mode="synthetic", active_channels=[1, 2])
    assert rep_2cam["metrics"]["blur_avoidance_rate_pct"] == 100.0
    assert rep_2cam["metrics"]["flicker_violations"] == 0
    for rec in rep_2cam["timeline"]:
        assert rec["program_cam"] in (1, 2), f"2-Cam mode selected disconnected camera: {rec}"
        assert rec["preview_cam"] in (1, 2)

    # Test 3-Camera setup (only Cam 1, 2, 3 connected; Cam 4 disconnected/black)
    rep_3cam = run_full_scene_benchmark(mode="synthetic", active_channels=[1, 2, 3])
    assert rep_3cam["metrics"]["blur_avoidance_rate_pct"] == 100.0
    assert rep_3cam["metrics"]["flicker_violations"] == 0
    for rec in rep_3cam["timeline"]:
        assert rec["program_cam"] in (1, 2, 3), f"3-Cam mode selected disconnected Cam 4: {rec}"
        assert rec["preview_cam"] in (1, 2, 3)
