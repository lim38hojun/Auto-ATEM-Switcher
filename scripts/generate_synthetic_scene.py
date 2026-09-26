"""
Synthetic Multi-Camera Concert Scene & ATEM 10-Split Multiview Generator.

Generates a synchronized 30-second (20 fps = 600 frames) 4-camera concert dataset
capturing the SAME musical performance from 4 camera angles:
  - Cam 1 (Wide Master): Full band stage shot, balanced framing, steady moderate motion.
  - Cam 2 (Vocal Close-up): Centered vocalist during verses (4s-10s, 24s-30s) with high framing & eye contact; drifts off-center during guitar solo (11s-17s).
  - Cam 3 (Guitarist Solo): Moderate framing normally, explosive fret/hand motion + high-frequency audio solo boost during 10s-18s.
  - Cam 4 (Roaming Stage Cam): Dynamic close angle during 18s-22s, but suffers severe camera shake & focus blur during 14s-17s and 22s-24.5s (must be excluded by Laplacian blur detector).

Also renders `multiview_10split.mp4` (1920x1080) following the exact Blackmagic ATEM Mini Pro
10-split hardware layout so the ATEM Multiview Crop mode can be tested end-to-end.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np


FPS = 20
DURATION_SEC = 30.0
TOTAL_FRAMES = int(FPS * DURATION_SEC)
CAM_W, CAM_H = 640, 360
MV_W, MV_H = 1920, 1080
BPM = 120.0  # 2 beats per second (every 0.5s = every 10 frames)


def _draw_stage_background(frame: np.ndarray, t: float, cam_id: int) -> None:
    """Draws subtle textured concert stage background with lighting beams and truss grid."""
    h, w = frame.shape[:2]
    # Base dark gradient
    y_idx = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    bg_top = np.array([28 + cam_id * 3, 18, 22], dtype=np.float32)
    bg_bot = np.array([10, 10, 14], dtype=np.float32)
    grad = (bg_top * (1.0 - y_idx) + bg_bot * y_idx).astype(np.uint8)
    frame[:] = np.broadcast_to(grad[:, None, :], (h, w, 3))

    # Stage truss & acoustic grid texture (gives realistic high-frequency edges when in focus!)
    for gx in range(20, w, 40):
        cv2.line(frame, (gx, 15), (gx, 55), (52, 56, 68), 1, cv2.LINE_AA)
    cv2.line(frame, (10, 35), (w - 10, 35), (65, 70, 85), 2, cv2.LINE_AA)

    # Stage floor perspective grid
    floor_y = int(h * 0.78)
    cv2.line(frame, (0, floor_y), (w, floor_y), (70, 75, 90), 2)
    for fx in range(0, w + 1, 60):
        cv2.line(frame, (fx, floor_y), (int((fx - w / 2) * 1.35 + w / 2), h), (42, 45, 55), 1)

    # Dynamic stage spotlights synchronized with beat
    beat_pulse = 0.5 + 0.5 * math.cos(2 * math.pi * (BPM / 60.0) * t)
    spot_color = (
        int(45 + 35 * beat_pulse),
        int(55 + 40 * beat_pulse),
        int(90 + 50 * beat_pulse),
    )
    cv2.circle(frame, (int(w * 0.3), 35), int(14 + 4 * beat_pulse), spot_color, -1, cv2.LINE_AA)
    cv2.circle(frame, (int(w * 0.7), 35), int(14 + 4 * beat_pulse), spot_color, -1, cv2.LINE_AA)


def _draw_performer(
    frame: np.ndarray,
    cx: int,
    cy: int,
    scale: float,
    role: str,
    motion_amp: float,
    t: float,
    eye_contact: bool = True,
) -> Tuple[int, int, int, int]:
    """
    Draws a high-detail articulated performer with head, eyes, torso, instrument/mic,
    and fine texture lines (so Laplacian sharpness is naturally high when focused and low when blurred).
    Returns subject bounding box (x, y, w, h).
    """
    bounce_y = int(math.sin(t * 14.0) * (6.0 * motion_amp * scale))
    sway_x = int(math.cos(t * 9.0) * (5.0 * motion_amp * scale))
    px = cx + sway_x
    py = cy + bounce_y

    head_r = max(12, int(26 * scale))
    body_w = max(24, int(58 * scale))
    body_h = max(40, int(92 * scale))

    # Torso jacket with fine stripes/zipper details (critical for sharpness measurement)
    color_jacket = (65, 110, 215) if role == "vocal" else (190, 95, 55) if role == "guitar" else (90, 175, 110)
    top_y = py - int(10 * scale)
    cv2.rectangle(
        frame,
        (px - body_w // 2, top_y),
        (px + body_w // 2, top_y + body_h),
        color_jacket,
        -1,
    )
    cv2.rectangle(
        frame,
        (px - body_w // 2, top_y),
        (px + body_w // 2, top_y + body_h),
        (230, 235, 245),
        2,
    )
    # High-contrast lapel/texture lines on jacket
    for offset in range(-body_w // 3, body_w // 3 + 1, max(4, int(7 * scale))):
        cv2.line(
            frame,
            (px + offset, top_y + 6),
            (px + offset, top_y + body_h - 6),
            (210, 215, 225),
            1,
        )

    # Head & Face
    head_cy = top_y - head_r - int(4 * scale)
    cv2.circle(frame, (px, head_cy), head_r, (195, 210, 235), -1, cv2.LINE_AA)
    cv2.circle(frame, (px, head_cy), head_r, (245, 245, 255), 2, cv2.LINE_AA)

    # Eyes & gaze (if eye_contact is True, sharp dark pupils centered)
    eye_dx = max(4, int(9 * scale))
    eye_y = head_cy - int(2 * scale)
    gaze_shift = 0 if eye_contact else int(5 * scale)
    cv2.circle(frame, (px - eye_dx + gaze_shift, eye_y), max(2, int(3.5 * scale)), (20, 20, 30), -1)
    cv2.circle(frame, (px + eye_dx + gaze_shift, eye_y), max(2, int(3.5 * scale)), (20, 20, 30), -1)
    # Mouth (singing animation for vocal)
    mouth_open = max(2, int((4 + 5 * motion_amp * abs(math.sin(t * 12))) * scale))
    cv2.ellipse(
        frame,
        (px + gaze_shift, head_cy + int(8 * scale)),
        (max(3, int(6 * scale)), mouth_open),
        0,
        0,
        360,
        (30, 30, 60),
        -1,
    )

    # Instrument / Hand Animation (generates strong local Optical Flow when motion_amp is high)
    hand_phase = t * 24.0
    hand_dx = int(math.sin(hand_phase) * (22.0 * motion_amp * scale))
    hand_dy = int(math.cos(hand_phase * 1.3) * (16.0 * motion_amp * scale))

    if role == "guitar":
        # Guitar neck & strings
        neck_start = (px - int(35 * scale), top_y + int(35 * scale))
        neck_end = (px + int(65 * scale), top_y + int(12 * scale) + int(hand_dy * 0.4))
        cv2.line(frame, neck_start, neck_end, (180, 200, 230), max(3, int(6 * scale)), cv2.LINE_AA)
        # Strumming / Fret hand fast motion
        hand_pos = (px + int(15 * scale) + hand_dx, top_y + int(28 * scale) + hand_dy)
        cv2.circle(frame, hand_pos, max(5, int(10 * scale)), (90, 245, 255), -1, cv2.LINE_AA)
        cv2.circle(frame, hand_pos, max(6, int(12 * scale)), (255, 255, 255), 2, cv2.LINE_AA)
    else:
        # Microphone stand & expressive hand gesture
        mic_top = (px + int(6 * scale), head_cy + int(14 * scale))
        cv2.line(frame, mic_top, (px + int(8 * scale), top_y + body_h), (210, 210, 220), max(2, int(3 * scale)))
        hand_pos = (px - int(22 * scale) + hand_dx, top_y + int(26 * scale) + hand_dy)
        cv2.circle(frame, hand_pos, max(4, int(8 * scale)), (220, 235, 250), -1, cv2.LINE_AA)

    bx = max(0, px - int(body_w * 0.75))
    by = max(0, head_cy - head_r - 6)
    bw = min(frame.shape[1] - bx, int(body_w * 1.5))
    bh = min(frame.shape[0] - by, (top_y + body_h) - by + 6)
    return (bx, by, bw, bh)


def render_four_cameras_at_time(t: float) -> Tuple[Dict[int, np.ndarray], Dict]:
    """
    Renders the 4 camera frames for timestamp t (seconds) and returns ground-truth metadata.
    """
    frames: Dict[int, np.ndarray] = {}
    for cid in range(1, 5):
        f = np.zeros((CAM_H, CAM_W, 3), dtype=np.uint8)
        _draw_stage_background(f, t, cid)
        frames[cid] = f

    # Determine scene phase & audio features
    beat_dist = abs((t * (BPM / 60.0)) - round(t * (BPM / 60.0)))
    is_beat_peak = beat_dist < 0.08  # Every 0.5s beat trigger
    is_guitar_solo = 10.0 <= t < 18.0
    is_cam4_blur = (14.0 <= t < 17.2) or (22.0 <= t < 24.6)

    # --- CAM 1: Wide Master Shot (Always stable, moderate motion, 3 performers visible) ---
    _draw_performer(frames[1], int(CAM_W * 0.26), int(CAM_H * 0.54), 0.68, "guitar", 0.35, t)
    _draw_performer(frames[1], int(CAM_W * 0.50), int(CAM_H * 0.50), 0.75, "vocal", 0.35, t)
    _draw_performer(frames[1], int(CAM_W * 0.74), int(CAM_H * 0.55), 0.65, "bass", 0.30, t)

    # --- CAM 2: Vocal Close-Up ---
    # High quality centered shot during 0-10s and 24-30s; drifts near edge during guitar solo (10.5-17.5s)
    if 10.5 <= t < 17.5:
        vocal_cx = int(CAM_W * 0.86)  # Off-center / edge clipping during guitar solo
        vocal_motion = 0.20
        vocal_eye = False
    else:
        vocal_cx = int(CAM_W * 0.50)
        vocal_motion = 0.72 if (4.0 <= t < 10.0 or t >= 24.0) else 0.45
        vocal_eye = True
    _draw_performer(
        frames[2],
        vocal_cx,
        int(CAM_H * 0.44),
        1.28,
        "vocal",
        vocal_motion,
        t,
        eye_contact=vocal_eye,
    )

    # --- CAM 3: Guitarist Solo Close-Up ---
    if is_guitar_solo:
        guitar_cx = int(CAM_W * 0.50)
        guitar_motion = 1.35  # Intense guitar solo hand/body motion
        guitar_eye = True
    else:
        guitar_cx = int(CAM_W * 0.64)
        guitar_motion = 0.22
        guitar_eye = False
    _draw_performer(
        frames[3],
        guitar_cx,
        int(CAM_H * 0.45),
        1.22,
        "guitar",
        guitar_motion,
        t,
        eye_contact=guitar_eye,
    )

    # --- CAM 4: Dynamic Roaming Stage Cam (High score 18-22s, Blurry/Shaky 14-17.2s & 22-24.6s) ---
    roam_cx = int(CAM_W * 0.50) if (18.0 <= t < 22.0) else int(CAM_W * 0.48)
    roam_motion = 0.95 if (18.0 <= t < 22.0) else 0.55
    _draw_performer(
        frames[4],
        roam_cx,
        int(CAM_H * 0.44),
        1.25,
        "vocal",
        roam_motion,
        t,
        eye_contact=True,
    )

    if is_cam4_blur:
        # Apply realistic severe camera shake + defocus Gaussian blur so Laplacian variance drops sharply
        shake_dx = int(28 * math.sin(t * 45.0))
        shake_dy = int(22 * math.cos(t * 38.0))
        M = np.float32([[1, 0, shake_dx], [0, 1, shake_dy]])
        shaken = cv2.warpAffine(frames[4], M, (CAM_W, CAM_H), borderMode=cv2.BORDER_REFLECT)
        frames[4] = cv2.GaussianBlur(shaken, (25, 25), 9.0)

    # Ground truth ideal camera(s) for task evaluation
    if 0.0 <= t < 4.0:
        gt_cams = [1, 2]
        phase_name = "Opening Band Wide / Vocal Intro"
    elif 4.0 <= t < 10.0:
        gt_cams = [2, 1]
        phase_name = "Verse 1 — Vocal Main Close-up"
    elif 10.0 <= t < 18.0:
        gt_cams = [3, 1]  # Cam 3 Guitar Solo primary, Cam 1 allowed for max_hold rotation
        phase_name = "Guitar Solo Peak (Audio + Motion Trigger)"
    elif 18.0 <= t < 22.0:
        gt_cams = [4, 2, 1]  # Cam 4 sharp roaming highlight
        phase_name = "Bridge Highlight — Roaming Action"
    elif 22.0 <= t < 24.6:
        gt_cams = [2, 1, 3]  # Cam 4 is BLURRY here! Any non-blurry cam (especially 2 or 1) is valid
        phase_name = "Cam 4 Blur Hazard — Emergency Evasion to Cam 2/1"
    else:
        gt_cams = [2, 1]
        phase_name = "Finale Chorus — Vocal & Master Wide"

    meta = {
        "timestamp": round(t, 3),
        "is_beat_peak": bool(is_beat_peak),
        "bpm": BPM,
        "solo_instrument_cam": 3 if is_guitar_solo else (2 if (4.0 <= t < 10.0 or t >= 24.0) else None),
        "audio_rms": round(0.88 if is_guitar_solo else (0.75 if is_beat_peak else 0.52), 3),
        "audio_high_freq_energy": round(0.92 if is_guitar_solo else 0.30, 3),
        "cam4_blur_hazard": bool(is_cam4_blur),
        "gt_recommended_cams": gt_cams,
        "phase_name": phase_name,
    }
    return frames, meta


def compose_atem_10split_multiview(
    cam_frames: Dict[int, np.ndarray],
    program_cam: int,
    preview_cam: int,
    meta: Dict,
) -> np.ndarray:
    """
    Composes a 1920x1080 frame matching the exact Blackmagic Design ATEM Mini Pro 10-Split Multiview.
      - Top Row: Preview (0..960, 0..540) | Program (960..1920, 0..540)
      - Middle Row: Cam 1 | Cam 2 | Cam 3 | Cam 4 (each 480x270 at y=540..810)
      - Bottom Row: Media Player | Streaming | Recording | Audio Meters (y=810..1080)
    """
    canvas = np.zeros((MV_H, MV_W, 3), dtype=np.uint8)
    canvas[:] = (18, 18, 20)

    # 1. Top Left: PREVIEW (Green border)
    pvw_img = cv2.resize(cam_frames[preview_cam], (960, 540))
    canvas[0:540, 0:960] = pvw_img
    cv2.rectangle(canvas, (2, 2), (957, 537), (40, 215, 70), 3)
    cv2.putText(canvas, f"Preview: Cam {preview_cam}", (20, 515), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 235, 90), 2)

    # 2. Top Right: PROGRAM (Red border)
    pgm_img = cv2.resize(cam_frames[program_cam], (960, 540))
    canvas[0:540, 960:1920] = pgm_img
    cv2.rectangle(canvas, (962, 2), (1917, 537), (45, 45, 235), 3)
    cv2.putText(canvas, f"Program: Cam {program_cam}", (980, 515), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (70, 70, 255), 2)

    # 3. Middle Row: CAM 1 ~ CAM 4 (each 480x270, y=540..810)
    for idx in range(1, 5):
        x0 = (idx - 1) * 480
        y0 = 540
        sub = cv2.resize(cam_frames[idx], (480, 270))
        canvas[y0 : y0 + 270, x0 : x0 + 480] = sub

        # Tally Border on Multiview Cell
        if idx == program_cam:
            border_col = (40, 40, 240)
            thick = 3
        elif idx == preview_cam:
            border_col = (40, 215, 70)
            thick = 3
        else:
            border_col = (70, 75, 85)
            thick = 1
        cv2.rectangle(canvas, (x0 + 1, y0 + 1), (x0 + 478, y0 + 268), border_col, thick)

        # Bottom label overlay (inside the bottom 26px so Safe Inner ROI [546:782] avoids it!)
        cv2.rectangle(canvas, (x0 + 4, y0 + 242), (x0 + 130, y0 + 266), (12, 12, 15), -1)
        cv2.putText(canvas, f"Cam {idx}", (x0 + 14, y0 + 260), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 240), 1)

    # 4. Bottom Row: Status cells (y=810..1080)
    labels = ["MEDIA PLAYER 1", "ON AIR / STREAMING", "HYPERDECK / REC", "FAIRLIGHT AUDIO"]
    for idx, text in enumerate(labels):
        x0 = idx * 480
        y0 = 810
        cv2.rectangle(canvas, (x0 + 1, y0 + 1), (x0 + 478, y0 + 268), (60, 65, 75), 1)
        cv2.putText(canvas, text, (x0 + 24, y0 + 45), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (165, 175, 190), 2)
        cv2.putText(
            canvas,
            f"Scene: {meta['phase_name'][:28]}",
            (x0 + 24, y0 + 95),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (130, 180, 230),
            1,
        )

    return canvas


def generate_synthetic_dataset(output_dir: str | Path = "data/synthetic") -> Dict[str, str]:
    """Generates cam1..4.mp4, multiview_10split.mp4, and scene_metadata.json."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    cam_files = {
        1: out_path / "cam1_wide.mp4",
        2: out_path / "cam2_vocal.mp4",
        3: out_path / "cam3_guitar_solo.mp4",
        4: out_path / "cam4_roaming.mp4",
    }
    mv_file = out_path / "multiview_10split.mp4"
    meta_file = out_path / "scene_metadata.json"

    writers = {cid: cv2.VideoWriter(str(path), fourcc, FPS, (CAM_W, CAM_H)) for cid, path in cam_files.items()}
    mv_writer = cv2.VideoWriter(str(mv_file), fourcc, FPS, (MV_W, MV_H))

    timeline_meta: List[Dict] = []

    for frame_idx in range(TOTAL_FRAMES):
        t = frame_idx / float(FPS)
        cam_frames, meta = render_four_cameras_at_time(t)
        meta["frame_idx"] = frame_idx
        timeline_meta.append(meta)

        for cid, frame in cam_frames.items():
            writers[cid].write(frame)

        pgm_cam = meta["gt_recommended_cams"][0]
        pvw_cam = meta["gt_recommended_cams"][1] if len(meta["gt_recommended_cams"]) > 1 else 1
        mv_frame = compose_atem_10split_multiview(cam_frames, pgm_cam, pvw_cam, meta)
        mv_writer.write(mv_frame)

    for w in writers.values():
        w.release()
    mv_writer.release()

    payload = {
        "fps": FPS,
        "duration_sec": DURATION_SEC,
        "total_frames": TOTAL_FRAMES,
        "bpm": BPM,
        "cameras": {
            "1": {"name": "Cam 1 (Wide Master)", "role": "wide"},
            "2": {"name": "Cam 2 (Vocal Close-up)", "role": "vocal"},
            "3": {"name": "Cam 3 (Guitar Solo)", "role": "guitar"},
            "4": {"name": "Cam 4 (Roaming Stage)", "role": "roaming"},
        },
        "timeline": timeline_meta,
    }
    meta_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "cam1": str(cam_files[1]),
        "cam2": str(cam_files[2]),
        "cam3": str(cam_files[3]),
        "cam4": str(cam_files[4]),
        "multiview": str(mv_file),
        "metadata": str(meta_file),
    }


if __name__ == "__main__":
    paths = generate_synthetic_dataset()
    print("Generated synthetic 4-camera concert dataset & ATEM 10-split Multiview:")
    for k, v in paths.items():
        print(f"  - {k}: {v}")
