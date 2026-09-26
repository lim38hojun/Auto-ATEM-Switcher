"""
Passthrough audio feature interface. Currently serves as a structured data bridge for scene metadata. Real-time audio DSP (BPM detection, instrument separation) is planned for future implementation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional


@dataclass
class AudioFeatures:
    timestamp: float
    bpm: float
    is_beat_peak: bool
    audio_rms: float
    high_freq_energy: float
    solo_cam_id: Optional[int]
    camera_audio_bonuses: Dict[int, float]

    def to_dict(self) -> Dict:
        return asdict(self)


class AudioAnalyzer:
    """Computes audio beat peaks and per-camera solo weight bonuses."""

    def __init__(self, bpm: float = 120.0, solo_boost_weight: float = 0.85) -> None:
        self.bpm = bpm
        self.solo_boost_weight = solo_boost_weight

    def analyze(self, timestamp: float, scene_meta: Optional[Dict] = None) -> AudioFeatures:
        meta = scene_meta or {}
        
        bpm = float(meta.get("bpm", 0.0))
        is_beat = bool(meta.get("is_beat_peak", False))
        rms = float(meta.get("audio_rms", 0.0))
        hi_freq = float(meta.get("audio_high_freq_energy", 0.0))
        solo_cam = meta.get("solo_instrument_cam")

        bonuses: Dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
        if solo_cam in (1, 2, 3, 4):
            bonuses[int(solo_cam)] = self.solo_boost_weight

        return AudioFeatures(
            timestamp=round(timestamp, 3),
            bpm=bpm,
            is_beat_peak=is_beat,
            audio_rms=round(rms, 3),
            high_freq_energy=round(hi_freq, 3),
            solo_cam_id=int(solo_cam) if solo_cam in (1, 2, 3, 4) else None,
            camera_audio_bonuses=bonuses,
        )
