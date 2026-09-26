"""
Broadcast Direction Rule Engine (`src/engine/state_machine.py`).

Supports variable camera counts (2, 3, or 4 active cameras), automatic no-signal recovery,
configurable second-level hold timers (`min_hold_sec`, `max_hold_sec`), and adaptive ATEM
transition style selection (`CUT`, `MIX`, `DIP`, `WIPE`, `DVE`, or `AI_AUTO`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Set

from src.pipeline.vision_scorer import ChannelMetrics


@dataclass
class RuleEngineConfig:
    min_hold_sec: float = 2.5
    max_hold_sec: float = 7.5
    switch_margin: float = 0.09
    beat_sync_enabled: bool = True
    beat_wait_window_sec: float = 0.45
    emergency_blur_frames: int = 2
    transition_mode: str = "AI_AUTO"  # "AI_AUTO" | "CUT" | "MIX" | "DIP" | "WIPE" | "DVE"
    transition_duration_sec: float = 0.7


@dataclass
class SwitchDecision:
    timestamp: float
    state: str
    program_cam: int
    preview_cam: int
    active_cam_count: int
    switched: bool
    switch_reason: str
    current_shot_duration: float
    candidate_score_gap: float
    recommended_transition: str = "CUT"

    def to_dict(self) -> Dict:
        return asdict(self)


class BroadcastStateMachine:
    """Finite State Machine governing ATEM Program/Preview camera selection for 2, 3, or 4 cameras."""

    def __init__(
        self,
        config: Optional[RuleEngineConfig] = None,
        initial_program: int = 1,
        initial_preview: int = 2,
    ) -> None:
        self.config = config or RuleEngineConfig()
        self.program_cam: int = initial_program
        self.preview_cam: int = initial_preview
        self.last_switch_time: float = 0.0
        self.state: str = "HOLD_LOCKED"
        self.armed_since: Optional[float] = None
        self.consecutive_blur_count: int = 0
        self.switch_history: List[Dict] = []
        self.auto_director_enabled: bool = True

    def reset(self, initial_program: int = 1) -> None:
        self.program_cam = initial_program
        self.preview_cam = 2 if initial_program != 2 else 1
        self.last_switch_time = 0.0
        self.state = "HOLD_LOCKED"
        self.armed_since = None
        self.consecutive_blur_count = 0
        self.switch_history.clear()

    def _resolve_transition_style(self, state: str, score_gap: float = 0.0) -> str:
        """Determines whether to use CUT or a smooth transition (MIX/DIP/WIPE/DVE)."""
        mode = (self.config.transition_mode or "AI_AUTO").upper()
        if mode in ("CUT", "MIX", "DIP", "WIPE", "DVE"):
            return mode
        # AI_AUTO mode: urgent blur/signal-loss or strong beat peaks use crisp CUT;
        # gentle rotations and moderate score handovers use smooth MIX dissolve.
        if state in ("EMERGENCY_CUT", "SIGNAL_LOSS_CUT", "BEAT_SYNC_CUT") or score_gap >= 0.24:
            return "CUT"
        return "MIX"

    def manual_cut(self, target_cam: int, timestamp: float, style: Optional[str] = None) -> SwitchDecision:
        """Executes an immediate manual transition to `target_cam` and resets hold timers."""
        prev_pgm = self.program_cam
        self.program_cam = target_cam
        self.preview_cam = prev_pgm if prev_pgm != target_cam else (1 if target_cam != 1 else 2)
        self.last_switch_time = timestamp
        self.state = "HOLD_LOCKED"
        self.armed_since = None
        self.consecutive_blur_count = 0
        used_style = style or (
            "CUT" if self.config.transition_mode == "AI_AUTO" else self.config.transition_mode
        )

        event = {
            "timestamp": round(timestamp, 3),
            "from_cam": prev_pgm,
            "to_cam": target_cam,
            "state": "MANUAL_OVERRIDE",
            "reason": "Manual Operator Switch",
            "transition": used_style,
        }
        self.switch_history.append(event)
        return SwitchDecision(
            timestamp=round(timestamp, 3),
            state="MANUAL_OVERRIDE",
            program_cam=self.program_cam,
            preview_cam=self.preview_cam,
            active_cam_count=4,
            switched=True,
            switch_reason="Manual Operator Switch",
            current_shot_duration=0.0,
            candidate_score_gap=0.0,
            recommended_transition=used_style,
        )

    def step(
        self,
        timestamp: float,
        metrics: Dict[int, ChannelMetrics],
        is_beat_peak: bool = False,
        active_channels: Optional[Iterable[int]] = None,
    ) -> SwitchDecision:
        """
        Evaluates current camera scores and broadcast heuristics for `timestamp`
        across any active subset of cameras (e.g. 2 cameras, 3 cameras, or 4 cameras).
        """
        if timestamp < self.last_switch_time:
            self.last_switch_time = timestamp

        shot_duration = max(0.0, timestamp - self.last_switch_time)

        active_set: Optional[Set[int]] = set(active_channels) if active_channels is not None else None
        connected_cams = [
            m
            for m in metrics.values()
            if getattr(m, "enabled", True)
            and getattr(m, "has_signal", True)
            and (active_set is None or m.cam_id in active_set)
        ]
        if not connected_cams:
            connected_cams = list(metrics.values())

        active_cam_count = len(connected_cams)

        # Rank all non-blurry connected cameras by composite_score descending
        valid_cams = [m for m in connected_cams if not m.is_blurry]
        if not valid_cams:
            valid_cams = connected_cams
        ranked = sorted(valid_cams, key=lambda m: m.composite_score, reverse=True)

        best_overall = ranked[0].cam_id
        alternatives = [m for m in ranked if m.cam_id != self.program_cam]
        best_alt_metric = alternatives[0] if alternatives else ranked[0]
        self.preview_cam = best_alt_metric.cam_id

        # 0. Immediate recovery if current program_cam was disabled or lost HDMI signal
        connected_ids = {m.cam_id for m in connected_cams}
        if self.program_cam not in connected_ids and best_overall != self.program_cam:
            return self._execute_switch(
                timestamp=timestamp,
                target_cam=best_overall,
                state="SIGNAL_LOSS_CUT",
                reason=f"Cam {self.program_cam} Disconnected/Disabled -> Auto Recovery to Cam {best_overall}",
                score_gap=best_alt_metric.composite_score,
                active_cam_count=active_cam_count,
            )

        current_metric = metrics.get(self.program_cam, ranked[0])
        score_gap = round(best_alt_metric.composite_score - current_metric.composite_score, 4)

        if not self.auto_director_enabled or active_cam_count <= 1:
            return SwitchDecision(
                timestamp=round(timestamp, 3),
                state="MANUAL_HOLD" if not self.auto_director_enabled else "SINGLE_CAM_LOCK",
                program_cam=self.program_cam,
                preview_cam=self.preview_cam,
                active_cam_count=active_cam_count,
                switched=False,
                switch_reason="Auto Director Paused" if not self.auto_director_enabled else "Single Active Camera Hold",
                current_shot_duration=round(shot_duration, 2),
                candidate_score_gap=score_gap,
                recommended_transition=self._resolve_transition_style("HOLD_LOCKED", score_gap),
            )

        # 1. Check Emergency Blur Evasion on current Program camera
        if current_metric.is_blurry:
            self.consecutive_blur_count += 1
        else:
            self.consecutive_blur_count = 0

        if (
            self.consecutive_blur_count >= self.config.emergency_blur_frames
            and best_alt_metric.cam_id != self.program_cam
        ):
            return self._execute_switch(
                timestamp=timestamp,
                target_cam=best_alt_metric.cam_id,
                state="EMERGENCY_CUT",
                reason=f"Emergency Blur Evasion (Cam {self.program_cam} Lap={current_metric.laplacian_var:.1f})",
                score_gap=score_gap,
                active_cam_count=active_cam_count,
            )

        # 2. Enforce Minimum Shot Hold Time (`min_hold_sec`)
        if shot_duration < self.config.min_hold_sec:
            self.state = "HOLD_LOCKED"
            self.armed_since = None
            return SwitchDecision(
                timestamp=round(timestamp, 3),
                state=self.state,
                program_cam=self.program_cam,
                preview_cam=self.preview_cam,
                active_cam_count=active_cam_count,
                switched=False,
                switch_reason=f"Min Hold Lock ({shot_duration:.1f}s / {self.config.min_hold_sec:.1f}s) [{active_cam_count}Cam]",
                current_shot_duration=round(shot_duration, 2),
                candidate_score_gap=score_gap,
                recommended_transition=self._resolve_transition_style("HOLD_LOCKED", score_gap),
            )

        # 3. Enforce Maximum Shot Duration Limit (`max_hold_sec` -> FORCED_ROTATION among active cameras)
        if shot_duration >= self.config.max_hold_sec and best_alt_metric.cam_id != self.program_cam:
            return self._execute_switch(
                timestamp=timestamp,
                target_cam=best_alt_metric.cam_id,
                state="FORCED_ROTATION",
                reason=f"Max Hold Limit ({shot_duration:.1f}s >= {self.config.max_hold_sec:.1f}s) -> Next Best Active Cam {best_alt_metric.cam_id}",
                score_gap=score_gap,
                active_cam_count=active_cam_count,
            )

        # 4. Standard Eligible Window (`min_hold_sec <= shot_duration < max_hold_sec`)
        if best_overall != self.program_cam and score_gap >= self.config.switch_margin:
            if not self.config.beat_sync_enabled or is_beat_peak:
                return self._execute_switch(
                    timestamp=timestamp,
                    target_cam=best_overall,
                    state="BEAT_SYNC_CUT" if is_beat_peak else "SCORE_SWITCH",
                    reason=f"Best Shot Cam {best_overall} (+{score_gap:.2f} gap)"
                    + (" [Beat Peak Sync]" if is_beat_peak else ""),
                    score_gap=score_gap,
                    active_cam_count=active_cam_count,
                )
            else:
                if self.armed_since is None:
                    self.armed_since = timestamp
                self.state = "BEAT_ARMED"
                if (timestamp - self.armed_since) >= self.config.beat_wait_window_sec:
                    return self._execute_switch(
                        timestamp=timestamp,
                        target_cam=best_overall,
                        state="BEAT_WINDOW_CUT",
                        reason=f"Best Shot Cam {best_overall} (+{score_gap:.2f} gap, beat window elapsed)",
                        score_gap=score_gap,
                        active_cam_count=active_cam_count,
                    )
                return SwitchDecision(
                    timestamp=round(timestamp, 3),
                    state="BEAT_ARMED",
                    program_cam=self.program_cam,
                    preview_cam=best_overall,
                    active_cam_count=active_cam_count,
                    switched=False,
                    switch_reason=f"Armed for Beat Peak -> Cam {best_overall} (+{score_gap:.2f})",
                    current_shot_duration=round(shot_duration, 2),
                    candidate_score_gap=score_gap,
                    recommended_transition=self._resolve_transition_style("BEAT_ARMED", score_gap),
                )

        self.state = "ELIGIBLE"
        self.armed_since = None
        return SwitchDecision(
            timestamp=round(timestamp, 3),
            state=self.state,
            program_cam=self.program_cam,
            preview_cam=self.preview_cam,
            active_cam_count=active_cam_count,
            switched=False,
            switch_reason=f"Keeping Cam {self.program_cam} ({active_cam_count}-Cam Active)",
            current_shot_duration=round(shot_duration, 2),
            candidate_score_gap=score_gap,
            recommended_transition=self._resolve_transition_style("ELIGIBLE", score_gap),
        )

    def _execute_switch(
        self,
        timestamp: float,
        target_cam: int,
        state: str,
        reason: str,
        score_gap: float,
        active_cam_count: int = 4,
    ) -> SwitchDecision:
        prev_cam = self.program_cam
        self.program_cam = target_cam
        self.preview_cam = prev_cam
        self.last_switch_time = timestamp
        self.state = "HOLD_LOCKED"
        self.armed_since = None
        self.consecutive_blur_count = 0
        used_style = self._resolve_transition_style(state, score_gap)

        self.switch_history.append(
            {
                "timestamp": round(timestamp, 3),
                "from_cam": prev_cam,
                "to_cam": target_cam,
                "state": state,
                "reason": reason,
                "score_gap": score_gap,
                "active_cam_count": active_cam_count,
                "transition": used_style,
            }
        )
        return SwitchDecision(
            timestamp=round(timestamp, 3),
            state=state,
            program_cam=self.program_cam,
            preview_cam=self.preview_cam,
            active_cam_count=active_cam_count,
            switched=True,
            switch_reason=reason,
            current_shot_duration=0.0,
            candidate_score_gap=score_gap,
            recommended_transition=used_style,
        )
