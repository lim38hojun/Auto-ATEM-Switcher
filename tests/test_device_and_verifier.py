"""Unit and integration tests for formal device discovery, per-slot binding, ATEM tally loopback verification, and 60s real concert MP4s."""

from pathlib import Path

from scripts.download_real_concert_cams import prepare_real_concert_4cam_mp4s
from src.atem.verifier import ATEMSignalVerifier
from src.capture.device_manager import WindowsDeviceManager
from src.capture.ingestor import MultiCamIngestor


def test_real_concert_60s_4cam_mp4_files_exist_and_valid() -> None:
    paths = prepare_real_concert_4cam_mp4s("data/real_concert")
    assert len(paths) == 4
    for key, fpath in paths.items():
        p = Path(fpath)
        assert p.exists(), f"Missing real concert camera file: {fpath}"
        assert p.stat().st_size > 200_000, f"Real concert MP4 too small: {fpath}"


def test_per_camera_slot_custom_binding_and_ingestor() -> None:
    ingestor = MultiCamIngestor(mode="real_concert_mp4", active_channels=[1, 2, 3])
    # Bind Cam 1 to real concert wide MP4, Cam 2 to Multiview ROI #2, Cam 4 to disabled
    b1 = ingestor.bind_camera_slot(1, "video_file", file_path="data/real_concert/cam1_real_wide.mp4")
    b2 = ingestor.bind_camera_slot(2, "multiview_roi", multiview_slot=2)
    b4 = ingestor.bind_camera_slot(4, "disabled")

    assert b1.source_type == "video_file"
    assert b2.source_type == "multiview_roi"
    assert b4.source_type == "disabled"
    assert 4 not in ingestor.active_channels

    channels, meta, _ = ingestor.read_next()
    assert channels[1].shape == (360, 640, 3)
    assert channels[2].shape == (360, 640, 3)
    # Cam 4 is disabled -> pure black no-signal frame
    assert int(channels[4].max()) == 0
    ingestor.close()


def test_atem_signal_and_active_tally_loopback_verifier() -> None:
    ingestor = MultiCamIngestor(mode="real_concert_mp4", active_channels=[1, 2, 3, 4])
    report = ATEMSignalVerifier.run_full_verification(ingestor, active_channels=[1, 2, 3, 4])

    assert report["overall_passed"] is True
    assert len(report["slots"]) == 4
    for slot in report["slots"]:
        assert slot["has_live_signal"] is True
        assert slot["green_tally_detected_on_pvw"] is True
        assert slot["red_tally_detected_on_pgm"] is True
    ingestor.close()


def test_windows_device_manager_enumeration_runs_cleanly() -> None:
    devices = WindowsDeviceManager.discover_all_video_devices(max_probe=3)
    assert isinstance(devices, list)
