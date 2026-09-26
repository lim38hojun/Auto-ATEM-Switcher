"""
Computer Vision Scoring Pipeline for Multi-Camera Scene Selection (2, 3, or 4+ Cameras).

Evaluates active camera streams in real time (< 4ms total in native memory):
  1. Automatic No-Signal / Disconnected HDMI Port Detection (`has_signal`)
  2. Framing & Subject Centering Score (Golden Rule / Headroom / Edge Clipping Penalty)
  3. Performer Motion & Gesture Energy (Foreground motion minus global camera shake)
  4. Sharpness & Blur Exclusion via Laplacian Variance (`cv2.Laplacian(gray, cv2.CV_64F).var()`)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, Optional, Set, Tuple

import cv2
import numpy as np


@dataclass
class VisionConfig:
    w_framing: float = 0.50
    w_motion: float = 0.30
    w_sharpness: float = 0.20
    w_audio: float = 0.0
    blur_threshold: float = 45.0
    reference_sharpness: float = 220.0
    no_signal_luma_threshold: float = 7.5
    no_signal_std_threshold: float = 3.5


@dataclass
class ChannelMetrics:
    cam_id: int
    enabled: bool
    has_signal: bool
    framing_score: float
    motion_score: float
    sharpness_score: float
    laplacian_var: float
    audio_bonus: float
    is_blurry: bool
    edge_clipped: bool
    face_detected: bool
    composite_score: float
    subject_bbox: Tuple[int, int, int, int]

    def to_dict(self) -> Dict:
        return asdict(self)


class VisionScorer:
    """Computes normalized vision & audio composite scores for any active set of cameras (2, 3, or 4).

    Face Detection:
        Uses OpenCV FaceDetectorYN (YUNET ONNX model) when available at
        ``data/models/face_detection_yunet_2023mar.onnx``. Falls back to HSV
        contour-based subject localization if the model file is absent.
    """

    # YUNET model path relative to project root
    YUNET_MODEL_PATH = "data/models/face_detection_yunet_2023mar.onnx"
    _FACE_DET_W, _FACE_DET_H = 320, 180  # Fixed inference resolution

    def __init__(self, config: Optional[VisionConfig] = None) -> None:
        import os
        self.config = config or VisionConfig()
        self._prev_grays: Dict[int, np.ndarray] = {}
        self._face_detector = None  # cv2.FaceDetectorYN (YUNET), or None to use fallback

        model_path = self.YUNET_MODEL_PATH
        if hasattr(cv2, "FaceDetectorYN") and os.path.exists(model_path):
            try:
                self._face_detector = cv2.FaceDetectorYN.create(
                    model_path,
                    "",
                    (self._FACE_DET_W, self._FACE_DET_H),
                    score_threshold=0.6,
                    nms_threshold=0.3,
                    top_k=5,
                )
            except Exception:
                self._face_detector = None

    def reset_state(self) -> None:
        self._prev_grays.clear()

    def _check_has_video_signal(self, gray: np.ndarray) -> bool:
        """
        Detects whether an ATEM HDMI input port actually has an active camera signal.
        Unplugged HDMI ports on ATEM Mini Pro output black frames (mean luma ~ 0..6, std ~ 0..2).
        """
        mean_val = float(np.mean(gray))
        std_val = float(np.std(gray))
        if mean_val < self.config.no_signal_luma_threshold and std_val < self.config.no_signal_std_threshold:
            return False
        return True

    def _detect_subject_and_framing(
        self, small_bgr: np.ndarray
    ) -> Tuple[float, Tuple[int, int, int, int], bool, bool]:
        """
        Locates the dominant performer/subject region and scores framing quality:
          - High score when subject center is inside Golden Rule zone (x in [0.35, 0.65], y in [0.28, 0.60])
          - Penalty if main subject touches left/right frame boundaries (edge clipping)
        """
        h, w = small_bgr.shape[:2]
        gray = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)

        # --- Primary: YUNET DNN face detector (OpenCV 5 FaceDetectorYN) ---
        faces = []
        if self._face_detector is not None:
            _, detections = self._face_detector.detect(small_bgr)
            if detections is not None and len(detections) > 0:
                # YUNET rows: [x, y, w, h, lm0x, lm0y, ..., score]  — take x,y,w,h
                faces = detections[:, :4].astype(int).tolist()

        if len(faces) > 0:
            # Pick largest face by area
            largest_face = max(faces, key=lambda f: f[2] * f[3])
            x, y, bw, bh = largest_face

            cx = x + bw * 0.5
            cy = y + bh * 0.5

            norm_dx = (cx - 0.50 * w) / (0.32 * w)
            norm_dy = (cy - 0.44 * h) / (0.35 * h)
            dist_sq = norm_dx * norm_dx + norm_dy * norm_dy
            center_score = math.exp(-1.15 * dist_sq)

            occupancy = (bw * bh) / float(w * h)
            size_bonus = min(1.0, max(0.45, occupancy * 15.0))

            edge_clipped = (cx > 0.76 * w) or (cx < 0.24 * w) or (x + bw > 0.96 * w) or (x < 0.04 * w)
            framing = 0.68 * center_score + 0.32 * size_bonus + 0.15  # +0.15 face-presence bonus
            if edge_clipped:
                framing = max(0.12, framing - 0.38)

            return float(np.clip(framing, 0.0, 1.0)), (int(x), int(y), int(bw), int(bh)), edge_clipped, True

        hsv = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2HSV)

        # Mask out dark stage background to isolate illuminated performer(s)
        fg_mask = cv2.inRange(hsv, (0, 35, 65), (180, 255, 255))
        fg_mask[: int(h * 0.14), :] = 0
        fg_mask[int(h * 0.86) :, :] = 0

        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return 0.30, (int(w * 0.3), int(h * 0.25), int(w * 0.4), int(h * 0.5)), False, False

        largest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(largest)
        if area < 40:
            return 0.32, (int(w * 0.3), int(h * 0.25), int(w * 0.4), int(h * 0.5)), False, False

        x, y, bw, bh = cv2.boundingRect(largest)
        cx = x + bw * 0.5
        cy = y + bh * 0.45

        norm_dx = (cx - 0.50 * w) / (0.32 * w)
        norm_dy = (cy - 0.44 * h) / (0.35 * h)
        dist_sq = norm_dx * norm_dx + norm_dy * norm_dy
        center_score = math.exp(-1.15 * dist_sq)

        occupancy = (bw * bh) / float(w * h)
        size_bonus = min(1.0, max(0.45, occupancy * 6.5))

        edge_clipped = (cx > 0.76 * w) or (cx < 0.24 * w) or (x + bw > 0.96 * w) or (x < 0.04 * w)
        framing = 0.68 * center_score + 0.32 * size_bonus
        if edge_clipped:
            framing = max(0.12, framing - 0.38)

        return float(np.clip(framing, 0.0, 1.0)), (x, y, bw, bh), edge_clipped, False

    def _compute_motion_energy(
        self, cam_id: int, gray: np.ndarray, bbox: Tuple[int, int, int, int]
    ) -> Tuple[float, float]:
        """Measures net performer motion energy while subtracting global background camera shake."""
        prev = self._prev_grays.get(cam_id)
        self._prev_grays[cam_id] = gray.copy()
        if prev is None or prev.shape != gray.shape:
            return 0.30, 0.0

        smoothed_gray = cv2.GaussianBlur(gray, (5, 5), 0)
        smoothed_prev = cv2.GaussianBlur(prev, (5, 5), 0)
        diff = cv2.absdiff(smoothed_gray, smoothed_prev).astype(np.float32)
        diff[diff < 8] = 0
        h, w = gray.shape[:2]
        x, y, bw, bh = bbox
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(w, x + bw), min(h, y + bh)

        fg_region = diff[y1:y2, x1:x2]
        fg_energy = float(np.mean(fg_region)) if fg_region.size > 0 else float(np.mean(diff))

        border_mask = np.ones_like(diff, dtype=bool)
        border_mask[int(h * 0.15) : int(h * 0.85), int(w * 0.15) : int(w * 0.85)] = False
        bg_shake = float(np.mean(diff[border_mask])) if np.any(border_mask) else 0.0

        net_energy = max(0.0, fg_energy - 0.85 * bg_shake)
        motion_score = float(np.clip(net_energy / 11.5, 0.08, 1.0))
        return motion_score, bg_shake

    def score_channel(
        self,
        cam_id: int,
        frame_bgr: np.ndarray,
        audio_bonus: float = 0.0,
        enabled: bool = True,
    ) -> ChannelMetrics:
        """Evaluates a single camera frame and returns full `ChannelMetrics`."""
        small = cv2.resize(frame_bgr, (320, 180), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        has_signal = self._check_has_video_signal(gray)
        if not enabled or not has_signal:
            return ChannelMetrics(
                cam_id=cam_id,
                enabled=enabled,
                has_signal=has_signal,
                framing_score=0.0,
                motion_score=0.0,
                sharpness_score=0.0,
                laplacian_var=0.0,
                audio_bonus=0.0,
                is_blurry=True,
                edge_clipped=False,
                face_detected=False,
                composite_score=0.0,
                subject_bbox=(0, 0, 0, 0),
            )

        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        framing_score, small_bbox, edge_clipped, face_detected = self._detect_subject_and_framing(small)
        motion_score, bg_shake = self._compute_motion_energy(cam_id, gray, small_bbox)

        is_blurry = (lap_var < self.config.blur_threshold) or (
            bg_shake > 18.0 and lap_var < self.config.blur_threshold * 1.6
        )
        sharpness_score = float(np.clip(lap_var / self.config.reference_sharpness, 0.0, 1.0))

        sx = frame_bgr.shape[1] / 320.0
        sy = frame_bgr.shape[0] / 180.0
        scaled_bbox = (
            int(small_bbox[0] * sx),
            int(small_bbox[1] * sy),
            int(small_bbox[2] * sx),
            int(small_bbox[3] * sy),
        )

        if is_blurry:
            composite = round(0.05 * sharpness_score, 4)
        else:
            raw_score = (
                self.config.w_framing * framing_score
                + self.config.w_motion * motion_score
                + self.config.w_sharpness * sharpness_score
                + self.config.w_audio * audio_bonus
            )
            composite = round(float(np.clip(raw_score, 0.0, 1.0)), 4)

        return ChannelMetrics(
            cam_id=cam_id,
            enabled=True,
            has_signal=True,
            framing_score=round(framing_score, 4),
            motion_score=round(motion_score, 4),
            sharpness_score=round(sharpness_score, 4),
            laplacian_var=round(lap_var, 2),
            audio_bonus=round(audio_bonus, 4),
            is_blurry=bool(is_blurry),
            edge_clipped=bool(edge_clipped),
            face_detected=bool(face_detected),
            composite_score=composite,
            subject_bbox=scaled_bbox,
        )

    def score_all_channels(
        self,
        channels: Dict[int, np.ndarray],
        audio_bonuses: Optional[Dict[int, float]] = None,
        active_channels: Optional[Iterable[int]] = None,
    ) -> Dict[int, ChannelMetrics]:
        bonuses = audio_bonuses or {}
        active_set: Optional[Set[int]] = set(active_channels) if active_channels is not None else None
        return {
            cid: self.score_channel(
                cid,
                frame,
                audio_bonus=bonuses.get(cid, 0.0),
                enabled=(True if active_set is None else (cid in active_set)),
            )
            for cid, frame in sorted(channels.items())
        }
