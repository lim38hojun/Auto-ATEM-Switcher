"""
Unified Multi-Camera Ingestion & Per-Slot Source Router (`src/capture/ingestor.py`).

Supports:
  1. Per-Camera Slot Binding (`slot_bindings: Dict[int, CameraSlotBinding]`):
     Each camera slot (Cam 1 ~ Cam 4) can independently be bound to:
       - `"real_concert_mp4"`: 60-second real concert MP4 file (`data/real_concert/cam{N}_real_*.mp4`)
       - `"video_file"`      : Any user-selected custom `.mp4` video file
       - `"direct_device"`   : Specific Windows DirectShow USB/HDMI capture device (`device_index`)
       - `"multiview_roi"`   : Quadrant #N cropped from an ATEM Mini Pro 10-Split Multiview feed
       - `"synthetic"`       : Built-in concert simulator angle #N
       - `"disabled"`        : Disconnected / OFF (automatically excluded by VisionScorer)
  2. Global Quick-Modes:
       - `real_concert_mp4` (4 synchronized 1-minute real concert MP4 files)
       - `synthetic` (30-second synthetic concert scene)
       - `multiview_crop` (1080p ATEM 10-split Multiview auto-crop)
       - `live_webcam` (Live DirectShow capture card / OBS Virtual Camera)
       - `custom_per_slot` (Independent source per camera slot 1..4)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import cv2
import numpy as np

from scripts.generate_synthetic_scene import (
    CAM_H,
    CAM_W,
    FPS,
    TOTAL_FRAMES,
    compose_atem_10split_multiview,
    render_four_cameras_at_time,
)
from src.capture.device_manager import CameraSlotBinding, WindowsDeviceManager


@dataclass(frozen=True)
class NormalizedROI:
    x1: float
    y1: float
    x2: float
    y2: float

    def to_pixel_slice(self, width: int, height: int) -> Tuple[int, int, int, int]:
        px1 = max(0, min(width - 1, int(round(self.x1 * width))))
        py1 = max(0, min(height - 1, int(round(self.y1 * height))))
        px2 = max(px1 + 1, min(width, int(round(self.x2 * width))))
        py2 = max(py1 + 1, min(height, int(round(self.y2 * height))))
        return px1, py1, px2, py2


ATEM_10SPLIT_SAFE_ROIS: Dict[int, NormalizedROI] = {
    1: NormalizedROI(8 / 1920.0, 546 / 1080.0, 472 / 1920.0, 782 / 1080.0),
    2: NormalizedROI(488 / 1920.0, 546 / 1080.0, 952 / 1920.0, 782 / 1080.0),
    3: NormalizedROI(968 / 1920.0, 546 / 1080.0, 1432 / 1920.0, 782 / 1080.0),
    4: NormalizedROI(1448 / 1920.0, 546 / 1080.0, 1912 / 1920.0, 782 / 1080.0),
}


REAL_CONCERT_DEFAULT_FILES: Dict[int, str] = {
    1: "data/real_concert/cam1_real_wide.mp4",
    2: "data/real_concert/cam2_real_vocal.mp4",
    3: "data/real_concert/cam3_real_instrument.mp4",
    4: "data/real_concert/cam4_real_roaming.mp4",
}


def make_no_signal_frame(width: int = CAM_W, height: int = CAM_H) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def crop_atem_multiview_frame(
    multiview_frame: np.ndarray,
    target_size: Tuple[int, int] = (640, 360),
    custom_rois: Optional[Dict[int, NormalizedROI]] = None,
) -> Dict[int, np.ndarray]:
    h, w = multiview_frame.shape[:2]
    rois = custom_rois or ATEM_10SPLIT_SAFE_ROIS
    channels: Dict[int, np.ndarray] = {}

    for cam_id in range(1, 5):
        roi = rois[cam_id]
        x1, y1, x2, y2 = roi.to_pixel_slice(w, h)
        cropped = multiview_frame[y1:y2, x1:x2]
        if target_size is not None and (cropped.shape[1], cropped.shape[0]) != target_size:
            cropped = cv2.resize(cropped, target_size, interpolation=cv2.INTER_LINEAR)
        channels[cam_id] = cropped.copy()

    return channels


def scan_local_capture_devices(max_index: int = 5) -> List[Dict]:
    """Uses `WindowsDeviceManager` to return formal DirectShow/WMI device information."""
    hw_list = WindowsDeviceManager.discover_all_video_devices(max_probe=max_index)
    return [
        {
            "device_index": d.index,
            "name": f"{d.friendly_name} [{d.resolution}]",
            "friendly_name": d.friendly_name,
            "role": d.device_role,
            "role_kr": d.role_label_kr,
            "resolution": d.resolution,
            "is_available": d.is_available,
        }
        for d in hw_list
    ]


class MultiCamIngestor:
    """
    Unified multi-camera frame provider with full per-camera slot routing (`slot_bindings`)
    and 60-second Real Concert MP4 playback support.
    """

    def __init__(
        self,
        mode: str = "real_concert_mp4",
        data_dir: str | Path = "data/synthetic",
        quad_paths: Optional[Dict[int, str]] = None,
        multiview_path: Optional[str] = None,
        webcam_index: int = 0,
        active_channels: Optional[Iterable[int]] = None,
    ) -> None:
        self.mode = mode
        self.data_dir = Path(data_dir)
        self.quad_paths = quad_paths or {}
        self.multiview_path = multiview_path
        self.webcam_index = webcam_index
        self.active_channels: Set[int] = set(active_channels) if active_channels is not None else {1, 2, 3, 4}
        self.frame_idx = 0
        self.fps = float(FPS)

        self.slot_bindings: Dict[int, CameraSlotBinding] = {}
        self._slot_file_caps: Dict[int, cv2.VideoCapture] = {}
        self._device_caps: Dict[int, cv2.VideoCapture] = {}
        self._mv_cap: Optional[cv2.VideoCapture] = None

        self._apply_mode_to_slot_bindings(self.mode)

    def _apply_mode_to_slot_bindings(self, mode: str) -> None:
        self.close()
        self.mode = mode

        for cid in range(1, 5):
            if cid not in self.active_channels:
                self.slot_bindings[cid] = CameraSlotBinding(
                    cam_id=cid,
                    source_type="disabled",
                    display_label="사용 안 함 (꺼짐)",
                )
                continue

            if mode == "real_concert_mp4":
                fpath = self.quad_paths.get(cid, REAL_CONCERT_DEFAULT_FILES[cid])
                if not Path(fpath).exists():
                    fpath = str(self.data_dir / f"cam{cid}_{'wide' if cid==1 else 'vocal' if cid==2 else 'guitar_solo' if cid==3 else 'roaming'}.mp4")
                self.slot_bindings[cid] = CameraSlotBinding(
                    cam_id=cid,
                    source_type="video_file",
                    display_label=f"실제 공연 MP4 ({Path(fpath).name})",
                    file_path=fpath,
                )
            elif mode == "quad_files":
                default_synth = {
                    1: str(self.data_dir / "cam1_wide.mp4"),
                    2: str(self.data_dir / "cam2_vocal.mp4"),
                    3: str(self.data_dir / "cam3_guitar_solo.mp4"),
                    4: str(self.data_dir / "cam4_roaming.mp4"),
                }
                fpath = self.quad_paths.get(cid, default_synth[cid])
                self.slot_bindings[cid] = CameraSlotBinding(
                    cam_id=cid,
                    source_type="video_file",
                    display_label=f"MP4 파일 ({Path(fpath).name})",
                    file_path=fpath,
                )
            elif mode in ("multiview_crop", "live_webcam"):
                self.slot_bindings[cid] = CameraSlotBinding(
                    cam_id=cid,
                    source_type="multiview_roi",
                    display_label=f"ATEM 멀티뷰 #{cid}번 구역 크롭",
                    device_index=self.webcam_index,
                    multiview_slot=cid,
                )
            else:
                self.slot_bindings[cid] = CameraSlotBinding(
                    cam_id=cid,
                    source_type="synthetic",
                    display_label=f"가상 공연 카메라 #{cid}",
                )

        self._open_bound_resources()

    def _open_bound_resources(self) -> None:
        for cid, binding in self.slot_bindings.items():
            if binding.source_type == "video_file" and binding.file_path and Path(binding.file_path).exists():
                self._slot_file_caps[cid] = cv2.VideoCapture(binding.file_path)
            elif binding.source_type == "direct_device":
                dev_idx = binding.device_index
                if dev_idx not in self._device_caps:
                    cap = cv2.VideoCapture(dev_idx, cv2.CAP_DSHOW)
                    if not cap.isOpened():
                        cap = cv2.VideoCapture(dev_idx)
                    if cap.isOpened():
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        self._device_caps[dev_idx] = cap
            elif binding.source_type == "multiview_roi":
                if self.mode == "live_webcam":
                    dev_idx = binding.device_index
                    if dev_idx not in self._device_caps:
                        cap = cv2.VideoCapture(dev_idx, cv2.CAP_DSHOW)
                        if not cap.isOpened():
                            cap = cv2.VideoCapture(dev_idx)
                        if cap.isOpened():
                            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                            self._device_caps[dev_idx] = cap
                elif self._mv_cap is None:
                    mv_p = self.multiview_path or str(self.data_dir / "multiview_10split.mp4")
                    if Path(mv_p).exists():
                        self._mv_cap = cv2.VideoCapture(mv_p)

    def bind_camera_slot(
        self,
        cam_id: int,
        source_type: str,
        device_index: int = 0,
        device_name: str = "",
        file_path: str = "",
        multiview_slot: Optional[int] = None,
    ) -> CameraSlotBinding:
        """
        Directly assigns a specific video capture device, MP4 file, or Multiview ROI slot
        to an individual camera card (`cam_id` in 1..4).
        """
        if cam_id in self._slot_file_caps:
            self._slot_file_caps[cam_id].release()
            del self._slot_file_caps[cam_id]

        mv_slot = multiview_slot or cam_id
        if source_type == "disabled":
            self.active_channels.discard(cam_id)
            label = "꺼짐 (사용 안 함)"
        else:
            self.active_channels.add(cam_id)
            if source_type == "direct_device":
                label = f"장치 연결: {device_name or f'DirectShow #{device_index}'}"
            elif source_type == "video_file":
                label = f"파일: {Path(file_path).name}"
            elif source_type == "multiview_roi":
                label = f"ATEM 멀티뷰 #{mv_slot}번 구역"
            else:
                label = f"가상 카메라 #{cam_id}"

        binding = CameraSlotBinding(
            cam_id=cam_id,
            source_type=source_type,
            display_label=label,
            device_index=device_index,
            device_name=device_name,
            file_path=file_path,
            multiview_slot=mv_slot,
        )
        self.slot_bindings[cam_id] = binding
        self._open_bound_resources()
        return binding

    def set_active_channels(self, active_channels: Iterable[int]) -> None:
        valid = {int(c) for c in active_channels if int(c) in (1, 2, 3, 4)}
        if valid:
            self.active_channels = valid
            for cid in range(1, 5):
                if cid not in self.active_channels:
                    self.slot_bindings[cid] = CameraSlotBinding(
                        cam_id=cid,
                        source_type="disabled",
                        display_label="사용 안 함 (꺼짐)",
                    )
                elif self.slot_bindings.get(cid) and self.slot_bindings[cid].source_type == "disabled":
                    # Restore to current mode default
                    self._apply_mode_to_slot_bindings(self.mode)
                    break

    def set_mode(
        self,
        mode: str,
        quad_paths: Optional[Dict[int, str]] = None,
        multiview_path: Optional[str] = None,
        webcam_index: Optional[int] = None,
    ) -> None:
        if quad_paths:
            self.quad_paths = quad_paths
        if multiview_path:
            self.multiview_path = multiview_path
        if webcam_index is not None:
            self.webcam_index = int(webcam_index)
        self.frame_idx = 0
        self._apply_mode_to_slot_bindings(mode)

    def seek_frame(self, frame_idx: int) -> None:
        self.frame_idx = max(0, frame_idx)
        for cap in self._slot_file_caps.values():
            cap.set(cv2.CAP_PROP_POS_FRAMES, self.frame_idx)
        if self._mv_cap is not None:
            self._mv_cap.set(cv2.CAP_PROP_POS_FRAMES, self.frame_idx % TOTAL_FRAMES)

    def read_next(self) -> Tuple[Dict[int, np.ndarray], Dict, Optional[np.ndarray]]:
        t = (self.frame_idx % 1200) / self.fps
        
        needs_synthetic = any(b.source_type == "synthetic" for b in self.slot_bindings.values())
        raw_mv: Optional[np.ndarray] = None
        mv_cropped: Optional[Dict[int, np.ndarray]] = None

        # Pre-read multiview frame if any slot uses `multiview_roi`
        if any(b.source_type == "multiview_roi" for b in self.slot_bindings.values()):
            if self.mode == "live_webcam" and self.webcam_index in self._device_caps:
                ok, live_f = self._device_caps[self.webcam_index].read()
                if ok and live_f is not None:
                    raw_mv = cv2.resize(live_f, (1920, 1080))
                    mv_cropped = crop_atem_multiview_frame(raw_mv)
            if mv_cropped is None and self._mv_cap is not None and self._mv_cap.isOpened():
                ok, mv_f = self._mv_cap.read()
                if not ok or mv_f is None:
                    self._mv_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, mv_f = self._mv_cap.read()
                if ok and mv_f is not None:
                    raw_mv = mv_f
                    mv_cropped = crop_atem_multiview_frame(raw_mv)
            if mv_cropped is None:
                needs_synthetic = True

        if needs_synthetic:
            synth_frames, meta = render_four_cameras_at_time(t % 30.0)
        else:
            synth_frames = {}
            meta = {
                "gt_recommended_cams": [1, 2],
                "bpm": 0.0,
                "is_beat_peak": False,
                "audio_rms": 0.0,
                "audio_high_freq_energy": 0.0,
                "solo_instrument_cam": None,
                # Keys required by compose_atem_10split_multiview for overlay text
                "phase_name": "Live",
                "beat_idx": 0,
                "downbeat": False,
            }

        if any(b.source_type == "multiview_roi" for b in self.slot_bindings.values()):
            if mv_cropped is None:
                pgm = meta["gt_recommended_cams"][0]
                pvw = meta["gt_recommended_cams"][1] if len(meta["gt_recommended_cams"]) > 1 else pgm
                raw_mv = compose_atem_10split_multiview(synth_frames, pgm, pvw, meta)
                mv_cropped = crop_atem_multiview_frame(raw_mv)

        channels: Dict[int, np.ndarray] = {}
        for cid in range(1, 5):
            binding = self.slot_bindings.get(cid)
            if cid not in self.active_channels or (binding and binding.source_type == "disabled"):
                channels[cid] = make_no_signal_frame()
                continue

            stype = binding.source_type if binding else "synthetic"
            if stype == "video_file" and cid in self._slot_file_caps:
                cap = self._slot_file_caps[cid]
                ok, frame = cap.read()
                if not ok or frame is None:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = cap.read()
                channels[cid] = cv2.resize(frame, (CAM_W, CAM_H)) if (ok and frame is not None) else make_no_signal_frame()
            elif stype == "direct_device" and binding and binding.device_index in self._device_caps:
                cap = self._device_caps[binding.device_index]
                ok, frame = cap.read()
                channels[cid] = cv2.resize(frame, (CAM_W, CAM_H)) if (ok and frame is not None) else make_no_signal_frame()
            elif stype == "multiview_roi" and mv_cropped is not None:
                slot_idx = binding.multiview_slot if binding else cid
                channels[cid] = mv_cropped.get(slot_idx, make_no_signal_frame())
            else:
                channels[cid] = synth_frames.get(cid, make_no_signal_frame())

        gt_filtered = [c for c in meta.get("gt_recommended_cams", [1, 2]) if c in self.active_channels]
        if not gt_filtered:
            gt_filtered = sorted(list(self.active_channels))
        meta_copy = dict(meta)
        meta_copy["gt_recommended_cams"] = gt_filtered
        meta_copy["active_channels"] = sorted(list(self.active_channels))

        self.frame_idx = (self.frame_idx + 1) % 1200
        return channels, meta_copy, raw_mv

    def close(self) -> None:
        for cap in self._slot_file_caps.values():
            cap.release()
        self._slot_file_caps.clear()
        for cap in self._device_caps.values():
            cap.release()
        self._device_caps.clear()
        if self._mv_cap is not None:
            self._mv_cap.release()
            self._mv_cap = None
