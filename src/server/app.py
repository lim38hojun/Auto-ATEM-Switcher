"""
FastAPI Backend & Scene Camera Selection Task Evaluator (`src/server/app.py`).

Provides:
  1. Full 30-second Same-Scene Multi-Camera Selection Benchmark (`run_full_scene_benchmark`)
  2. REST endpoints for configuring Vision weights, Broadcast State Machine timers, Input Modes, and ATEM control
  3. Real-time WebSocket stream (`/ws/stream`) delivering annotated Cam 1~4 frames, Program/Preview tallies,
     audio beat triggers, and live score telemetry to the ATEM Multiview Web Testbench.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scripts.generate_synthetic_scene import (
    DURATION_SEC,
    FPS,
    TOTAL_FRAMES,
    generate_synthetic_dataset,
    render_four_cameras_at_time,
)
from src.atem.controller import ATEMController
from src.capture.ingestor import MultiCamIngestor, crop_atem_multiview_frame
from src.engine.state_machine import BroadcastStateMachine, RuleEngineConfig
from src.pipeline.audio_analyzer import AudioAnalyzer
from src.pipeline.vision_scorer import ChannelMetrics, VisionConfig, VisionScorer


def annotate_camera_frame(
    frame_bgr: np.ndarray,
    metric: ChannelMetrics,
    is_program: bool,
    is_preview: bool,
    solo_active: bool,
) -> np.ndarray:
    """Draws Golden Rule guides, performer bounding box, and status badges on a camera preview frame."""
    vis = frame_bgr.copy()
    h, w = vis.shape[:2]

    if not getattr(metric, "enabled", True) or not getattr(metric, "has_signal", True):
        vis[:] = (14, 15, 20)
        cv2.rectangle(vis, (4, 4), (w - 5, h - 5), (55, 60, 75), 2)
        cv2.putText(
            vis,
            f"CAM {metric.cam_id} — NO HDMI SIGNAL (EXCLUDED)",
            (int(w * 0.14), int(h * 0.48)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.68,
            (130, 140, 160),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            vis,
            "Auto-Excluded from AI Switching Pool",
            (int(w * 0.23), int(h * 0.58)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (95, 105, 125),
            1,
            cv2.LINE_AA,
        )
        return vis

    # 1. Subtle Golden Rule Center Box (30%..70% x, 20%..68% y)
    gx1, gy1, gx2, gy2 = int(w * 0.32), int(h * 0.20), int(w * 0.68), int(h * 0.68)
    cv2.rectangle(vis, (gx1, gy1), (gx2, gy2), (75, 85, 100), 1, cv2.LINE_AA)

    # 2. Subject Bounding Box
    bx, by, bw, bh = metric.subject_bbox
    bbox_col = (55, 65, 245) if metric.is_blurry else ((50, 230, 110) if not metric.edge_clipped else (40, 180, 245))
    cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), bbox_col, 2)

    # 3. Blur Hazard Overlay or Solo Boost Badge
    if metric.is_blurry:
        overlay = vis.copy()
        cv2.rectangle(overlay, (0, 0), (w, h), (15, 15, 120), -1)
        cv2.addWeighted(overlay, 0.28, vis, 0.72, 0, vis)
        cv2.rectangle(vis, (12, 12), (265, 46), (25, 25, 210), -1)
        cv2.putText(
            vis,
            f"BLUR EXCLUDED (Lap:{metric.laplacian_var:.0f})",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    elif solo_active:
        cv2.rectangle(vis, (12, 12), (225, 44), (190, 95, 25), -1)
        cv2.putText(
            vis,
            "AUDIO SOLO BOOST +0.25",
            (20, 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    # 4. Tally Border (Red = Program, Green = Preview)
    if is_program:
        cv2.rectangle(vis, (2, 2), (w - 3, h - 3), (45, 45, 245), 6)
    elif is_preview:
        cv2.rectangle(vis, (2, 2), (w - 3, h - 3), (45, 220, 80), 4)

    return vis


def encode_jpeg_b64(frame_bgr: np.ndarray, quality: int = 72, size: tuple[int, int] = (384, 216)) -> str:
    resized = cv2.resize(frame_bgr, size, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("ascii")


def run_full_scene_benchmark(
    vision_cfg: Optional[VisionConfig | str] = None,
    rule_cfg: Optional[RuleEngineConfig] = None,
    mode: str = "synthetic",
    active_channels: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Executes the 30-second Same-Scene Multi-Camera Selection Task benchmark across all frames
    for 2, 3, or 4 active cameras (`active_channels`).
    """
    if isinstance(vision_cfg, str):
        mode = vision_cfg
        vision_cfg = None
    active_list = sorted(active_channels) if active_channels else [1, 2, 3, 4]
    init_pgm = active_list[0]
    init_pvw = active_list[1] if len(active_list) > 1 else active_list[0]

    scorer = VisionScorer(vision_cfg or VisionConfig())
    audio = AudioAnalyzer()
    sm = BroadcastStateMachine(rule_cfg or RuleEngineConfig(), initial_program=init_pgm, initial_preview=init_pvw)

    gt_matches = 0
    blur_free_frames = 0
    beat_aligned_switches = 0
    normal_switches = 0
    flicker_violations = 0
    timeline_records: List[Dict[str, Any]] = []
    last_switch_t = 0.0

    from src.capture.ingestor import make_no_signal_frame

    for idx in range(TOTAL_FRAMES):
        t = idx / float(FPS)
        cam_frames, meta = render_four_cameras_at_time(t)
        for cid in range(1, 5):
            if cid not in active_list:
                cam_frames[cid] = make_no_signal_frame()

        gt_cams = [c for c in meta["gt_recommended_cams"] if c in active_list]
        if not gt_cams:
            gt_cams = active_list

        if mode == "multiview_crop":
            from scripts.generate_synthetic_scene import compose_atem_10split_multiview

            mv_frame = compose_atem_10split_multiview(cam_frames, sm.program_cam, sm.preview_cam, meta)
            cam_frames = crop_atem_multiview_frame(mv_frame)

        audio_feat = audio.analyze(t, meta)
        metrics = scorer.score_all_channels(cam_frames, audio_feat.camera_audio_bonuses, active_channels=active_list)
        decision = sm.step(t, metrics, is_beat_peak=audio_feat.is_beat_peak, active_channels=active_list)

        pgm = decision.program_cam
        pgm_metric = metrics[pgm]

        # 1. Check Blur & No-Signal Avoidance
        if not pgm_metric.is_blurry and pgm_metric.has_signal and pgm in active_list:
            blur_free_frames += 1

        # 2. Check Ground Truth Alignment (with 0.8s transition grace tolerance)
        if pgm in gt_cams or (t - last_switch_t) < 0.85:
            gt_matches += 1

        # 3. Check Switch Stability & Beat Sync
        if decision.switched and idx > 0:
            shot_dur = t - last_switch_t
            last_switch_t = t
            if decision.state != "EMERGENCY_CUT":
                normal_switches += 1
                if shot_dur < (sm.config.min_hold_sec - 0.08):
                    flicker_violations += 1
                if audio_feat.is_beat_peak or "BEAT" in decision.state:
                    beat_aligned_switches += 1

        # Downsample timeline records every 5 frames (0.25s) for clean UI chart rendering
        if idx % 5 == 0:
            timeline_records.append(
                {
                    "timestamp": round(t, 2),
                    "program_cam": pgm,
                    "preview_cam": decision.preview_cam,
                    "gt_primary_cam": gt_cams[0],
                    "gt_allowed_cams": gt_cams,
                    "phase_name": meta["phase_name"],
                    "scores": {cid: round(m.composite_score, 3) for cid, m in metrics.items()},
                    "blur_cams": [cid for cid, m in metrics.items() if m.is_blurry],
                    "state": decision.state,
                }
            )

    total_switches = len(sm.switch_history)
    mean_shot_dur = round(DURATION_SEC / max(1, total_switches + 1), 2)
    gt_accuracy = round(100.0 * gt_matches / float(TOTAL_FRAMES), 1)
    blur_avoidance = round(100.0 * blur_free_frames / float(TOTAL_FRAMES), 1)
    beat_sync_rate = round(100.0 * beat_aligned_switches / max(1, normal_switches), 1)

    return {
        "task_name": "30s Multi-Camera Concert Scene Selection Benchmark",
        "mode": mode,
        "total_frames": TOTAL_FRAMES,
        "duration_sec": DURATION_SEC,
        "metrics": {
            "gt_alignment_accuracy_pct": gt_accuracy,
            "blur_avoidance_rate_pct": blur_avoidance,
            "mean_shot_duration_sec": mean_shot_dur,
            "total_switches": total_switches,
            "flicker_violations": flicker_violations,
            "beat_sync_rate_pct": beat_sync_rate,
        },
        "switch_events": sm.switch_history,
        "timeline": timeline_records,
    }


# --- Global Runtime State for Interactive Web Dashboard ---

class DirectorRuntime:
    def __init__(self) -> None:
        self.data_dir = Path("data/synthetic")
        if not (self.data_dir / "cam1_wide.mp4").exists():
            generate_synthetic_dataset(self.data_dir)

        self.ingestor = MultiCamIngestor(mode="synthetic", data_dir=self.data_dir)
        self.scorer = VisionScorer()
        self.audio = AudioAnalyzer()
        self.state_machine = BroadcastStateMachine()
        self.atem = ATEMController()
        self.paused = False
        self.last_benchmark: Optional[Dict[str, Any]] = None


runtime = DirectorRuntime()
app = FastAPI(title="ATEM Mini Pro AI Broadcast Director & Multi-Cam Selection Testbench")

static_dir = Path(__file__).resolve().parents[2] / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


class ConfigUpdatePayload(BaseModel):
    w_framing: Optional[float] = None
    w_motion: Optional[float] = None
    w_sharpness: Optional[float] = None
    w_audio: Optional[float] = None
    blur_threshold: Optional[float] = None
    min_hold_sec: Optional[float] = None
    max_hold_sec: Optional[float] = None
    switch_margin: Optional[float] = None
    beat_sync_enabled: Optional[bool] = None
    auto_director_enabled: Optional[bool] = None
    paused: Optional[bool] = None


class ModeSwitchPayload(BaseModel):
    mode: str  # "synthetic" | "quad_files" | "multiview_crop" | "live_webcam"
    seek_frame: Optional[int] = None


class ManualCutPayload(BaseModel):
    target_cam: int
    atem_ip: Optional[str] = None


@app.get("/")
async def root_index() -> FileResponse:
    return FileResponse(str(static_dir / "index.html"))


@app.get("/api/state")
async def get_system_state() -> JSONResponse:
    return JSONResponse(
        {
            "mode": runtime.ingestor.mode,
            "paused": runtime.paused,
            "vision_config": {
                "w_framing": runtime.scorer.config.w_framing,
                "w_motion": runtime.scorer.config.w_motion,
                "w_sharpness": runtime.scorer.config.w_sharpness,
                "w_audio": runtime.scorer.config.w_audio,
                "blur_threshold": runtime.scorer.config.blur_threshold,
            },
            "rule_config": {
                "min_hold_sec": runtime.state_machine.config.min_hold_sec,
                "max_hold_sec": runtime.state_machine.config.max_hold_sec,
                "switch_margin": runtime.state_machine.config.switch_margin,
                "beat_sync_enabled": runtime.state_machine.config.beat_sync_enabled,
                "auto_director_enabled": runtime.state_machine.auto_director_enabled,
            },
            "atem_status": runtime.atem.get_status().to_dict(),
        }
    )


@app.post("/api/config")
async def update_config(payload: ConfigUpdatePayload) -> JSONResponse:
    vc = runtime.scorer.config
    rc = runtime.state_machine.config

    if payload.w_framing is not None:
        vc.w_framing = float(payload.w_framing)
    if payload.w_motion is not None:
        vc.w_motion = float(payload.w_motion)
    if payload.w_sharpness is not None:
        vc.w_sharpness = float(payload.w_sharpness)
    if payload.w_audio is not None:
        vc.w_audio = float(payload.w_audio)
    if payload.blur_threshold is not None:
        vc.blur_threshold = float(payload.blur_threshold)

    if payload.min_hold_sec is not None:
        rc.min_hold_sec = float(payload.min_hold_sec)
    if payload.max_hold_sec is not None:
        rc.max_hold_sec = float(payload.max_hold_sec)
    if payload.switch_margin is not None:
        rc.switch_margin = float(payload.switch_margin)
    if payload.beat_sync_enabled is not None:
        rc.beat_sync_enabled = bool(payload.beat_sync_enabled)
    if payload.auto_director_enabled is not None:
        runtime.state_machine.auto_director_enabled = bool(payload.auto_director_enabled)
    if payload.paused is not None:
        runtime.paused = bool(payload.paused)

    return await get_system_state()


@app.post("/api/mode")
async def change_mode(payload: ModeSwitchPayload) -> JSONResponse:
    if payload.mode in ("synthetic", "quad_files", "multiview_crop", "live_webcam"):
        runtime.ingestor.set_mode(payload.mode)
        runtime.scorer.reset_state()
        runtime.state_machine.reset(initial_program=1)
    if payload.seek_frame is not None:
        runtime.ingestor.seek_frame(payload.seek_frame)
    return await get_system_state()


@app.post("/api/upload/quad")
async def upload_custom_videos(
    cam1: Optional[UploadFile] = None,
    cam2: Optional[UploadFile] = None,
    cam3: Optional[UploadFile] = None,
    cam4: Optional[UploadFile] = None,
    multiview: Optional[UploadFile] = None,
) -> JSONResponse:
    upload_dir = Path("data/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)

    if multiview is not None and multiview.filename:
        mv_target = upload_dir / "custom_multiview.mp4"
        mv_target.write_bytes(await multiview.read())
        runtime.ingestor.set_mode("multiview_crop", multiview_path=str(mv_target))
        return JSONResponse({"status": "ok", "mode": "multiview_crop", "file": str(mv_target)})

    quad_paths: Dict[int, str] = {}
    for cid, up in [(1, cam1), (2, cam2), (3, cam3), (4, cam4)]:
        if up is not None and up.filename:
            target = upload_dir / f"custom_cam{cid}.mp4"
            target.write_bytes(await up.read())
            quad_paths[cid] = str(target)

    if quad_paths:
        runtime.ingestor.set_mode("quad_files", quad_paths=quad_paths)
    return JSONResponse({"status": "ok", "mode": runtime.ingestor.mode, "uploaded": list(quad_paths.keys())})


@app.post("/api/atem/cut")
async def manual_atem_cut(payload: ManualCutPayload) -> JSONResponse:
    if payload.atem_ip:
        runtime.atem.connect_hardware(payload.atem_ip)
    t = (runtime.ingestor.frame_idx % TOTAL_FRAMES) / float(FPS)
    decision = runtime.state_machine.manual_cut(payload.target_cam, t)
    atem_status = runtime.atem.sync_switcher_bus(
        program_cam=decision.program_cam,
        preview_cam=decision.preview_cam,
        switched=True,
        reason=decision.switch_reason,
    )
    return JSONResponse({"decision": decision.to_dict(), "atem": atem_status.to_dict()})


@app.post("/api/benchmark/run")
async def execute_benchmark_endpoint(mode: str = "synthetic") -> JSONResponse:
    report = run_full_scene_benchmark(
        vision_cfg=runtime.scorer.config,
        rule_cfg=runtime.state_machine.config,
        mode=mode,
    )
    runtime.last_benchmark = report
    return JSONResponse(report)


@app.websocket("/ws/stream")
async def websocket_multiview_stream(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            if not runtime.paused:
                frame_idx = runtime.ingestor.frame_idx
                t = (frame_idx % TOTAL_FRAMES) / float(FPS)
                channels, meta, raw_mv = runtime.ingestor.read_next()
                audio_feat = runtime.audio.analyze(t, meta)
                metrics = runtime.scorer.score_all_channels(channels, audio_feat.camera_audio_bonuses)
                decision = runtime.state_machine.step(t, metrics, is_beat_peak=audio_feat.is_beat_peak)
                atem_status = runtime.atem.sync_switcher_bus(
                    program_cam=decision.program_cam,
                    preview_cam=decision.preview_cam,
                    switched=decision.switched,
                    reason=decision.switch_reason,
                )

                # Encode annotated camera frames and Program/Preview frames
                cam_images: Dict[str, str] = {}
                for cid in range(1, 5):
                    annotated = annotate_camera_frame(
                        channels[cid],
                        metrics[cid],
                        is_program=(cid == decision.program_cam),
                        is_preview=(cid == decision.preview_cam),
                        solo_active=(audio_feat.solo_cam_id == cid),
                    )
                    cam_images[str(cid)] = encode_jpeg_b64(annotated)

                payload = {
                    "frame_idx": frame_idx,
                    "total_frames": TOTAL_FRAMES,
                    "timestamp": round(t, 2),
                    "mode": runtime.ingestor.mode,
                    "scene": meta,
                    "audio": audio_feat.to_dict(),
                    "metrics": {str(cid): m.to_dict() for cid, m in metrics.items()},
                    "decision": decision.to_dict(),
                    "atem": atem_status.to_dict(),
                    "switch_history": runtime.state_machine.switch_history[-10:],
                    "frames": cam_images,
                }
                await ws.send_json(payload)

            await asyncio.sleep(1.0 / 15.0)
    except WebSocketDisconnect:
        return
    except Exception:
        return
