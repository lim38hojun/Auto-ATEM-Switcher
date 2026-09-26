"""
ATEM Mini Pro Switcher Controller (`src/atem/controller.py`).

Supports:
  1. `MockATEMSwitcher`: Full in-memory hardware state simulator (Program/Preview tally,
     Cut / Mix / Dip / Wipe / DVE Auto transition logs, PiP DVE Keyer, Fade-to-Black, AFV).
  2. `PyATEMMaxAdapter`: Real Blackmagic Design ATEM Mini Pro hardware socket control
     over UDP/IP when `PyATEMMax` is installed and an ATEM IP is reachable:
       - `execCutME(0)` / `execAutoME(0)`
       - `setTransitionStyle(0, "mix" | "dip" | "wipe" | "dVE")`
       - `setTransitionMixRate` / `setTransitionDipRate` / `setTransitionWipeRate` / `setTransitionDVERate`
       - `setKeyerType(0, 0, "dVE")`, `setKeyerFillSource(0, 0, cam)`, `setKeyerOnAirEnabled(0, 0, enabled)` (PiP)
       - `setFadeToBlackRate(0, frames)` + `execFadeToBlackME(0)` (FTB)
       - `setAudioMixerInputMixOption(cam, "afv" | "on")` (Audio-Follow-Video)
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


@dataclass
class ATEMStatus:
    mode: str  # "mock" | "pyatemmax"
    connected: bool
    atem_ip: str
    program_input: int
    preview_input: int
    transition_style: str  # "AI_AUTO" | "CUT" | "MIX" | "DIP" | "WIPE" | "DVE"
    last_executed_style: str  # "CUT" | "MIX" | "DIP" | "WIPE" | "DVE"
    transition_duration_sec: float
    pip_enabled: bool
    pip_fill_cam: int
    ftb_active: bool
    afv_enabled: bool
    total_switches: int
    last_command: str

    def to_dict(self) -> Dict:
        return asdict(self)


class ATEMController:
    """Unified ATEM Mini Pro controller with PyATEMMax hardware protocol & Mock fallback."""

    STYLE_TO_PYATEM = {
        "MIX": "mix",
        "DIP": "dip",
        "WIPE": "wipe",
        "DVE": "dVE",
    }

    def __init__(self, atem_ip: str = "192.168.10.240", prefer_hardware: bool = False) -> None:
        self.atem_ip = atem_ip
        self.mode = "mock"
        self.connected = True
        self.program_input = 1
        self.preview_input = 2
        self.transition_style = "AI_AUTO"
        self.last_executed_style = "CUT"
        self.transition_duration_sec: float = 0.7
        self.pip_enabled: bool = False
        self.pip_fill_cam: int = 2
        self.ftb_active: bool = False
        self.afv_enabled: bool = False
        self.total_switches = 0
        self.last_command = "INIT MockATEMSwitcher Ready"
        self.command_log: List[Dict] = []
        self._hw_switcher = None
        self._transition_end_time: float = 0.0

        if prefer_hardware:
            self.connect_hardware(atem_ip)

    def connect_hardware(self, atem_ip: str) -> bool:
        """Attempts to connect to a physical ATEM Mini Pro via PyATEMMax; falls back to Mock on failure."""
        self.atem_ip = atem_ip
        try:
            import PyATEMMax  # type: ignore

            switcher = PyATEMMax.ATEMMax()
            switcher.connect(atem_ip)
            if switcher.waitForConnection(infinite=False, timeout=1.5):
                self._hw_switcher = switcher
                self.mode = "pyatemmax"
                self.connected = True
                self.last_command = f"CONNECTED PyATEMMax @ {atem_ip}"
                self.set_transition_style_and_rate(self.transition_style, self.transition_duration_sec)
                return True
        except Exception as exc:
            self.last_command = f"Hardware unreachable ({exc.__class__.__name__}) -> Active MockATEMSwitcher"

        self.mode = "mock"
        self.connected = True
        self._hw_switcher = None
        return False

    def check_connection_health(self) -> bool:
        """Checks hardware connection status and attempts to reconnect once if disconnected."""
        if self._hw_switcher is not None:
            if not getattr(self._hw_switcher, "connected", False):
                self.last_command = "Connection lost. Attempting reconnect..."
                self.connect_hardware(self.atem_ip)
            else:
                self.connected = True
        return self.connected

    def set_transition_style_and_rate(self, style: str, duration_sec: float = 0.7) -> ATEMStatus:
        """Configures ATEM transition style (`AI_AUTO`, `CUT`, `MIX`, `DIP`, `WIPE`, `DVE`) and duration."""
        valid = {"AI_AUTO", "CUT", "MIX", "DIP", "WIPE", "DVE"}
        self.transition_style = style.upper() if style.upper() in valid else "AI_AUTO"
        self.transition_duration_sec = max(0.2, min(2.5, round(float(duration_sec), 2)))
        frames = max(6, min(75, int(round(self.transition_duration_sec * 30.0))))

        if self._hw_switcher is not None:
            try:
                resolved_style = "MIX" if self.transition_style in ("AI_AUTO", "CUT") else self.transition_style
                hw_key = self.STYLE_TO_PYATEM.get(resolved_style, "mix")
                self._hw_switcher.setTransitionStyle(0, hw_key)
                if resolved_style == "MIX":
                    self._hw_switcher.setTransitionMixRate(0, frames)
                elif resolved_style == "DIP":
                    self._hw_switcher.setTransitionDipRate(0, frames)
                elif resolved_style == "WIPE":
                    self._hw_switcher.setTransitionWipeRate(0, frames)
                elif resolved_style == "DVE":
                    self._hw_switcher.setTransitionDVERate(0, frames)
            except Exception:
                pass

        self.last_command = f"SET_TRANSITION style={self.transition_style} rate={self.transition_duration_sec:.1f}s ({frames}f)"
        return self.get_status()

    def set_pip_enabled(self, enabled: bool, fill_cam: Optional[int] = None) -> ATEMStatus:
        """Toggles ATEM Mini Pro Upstream Keyer 0 DVE Picture-in-Picture (PiP) overlay."""
        self.pip_enabled = bool(enabled)
        if fill_cam is not None:
            self.pip_fill_cam = int(fill_cam)
        else:
            self.pip_fill_cam = self.preview_input

        if self._hw_switcher is not None:
            try:
                self._hw_switcher.setKeyerType(0, 0, "dVE")
                self._hw_switcher.setKeyerFillSource(0, 0, self.pip_fill_cam)
                self._hw_switcher.setKeyerFlyEnabled(0, 0, True)
                self._hw_switcher.setKeyerOnAirEnabled(0, 0, self.pip_enabled)
            except Exception:
                pass

        self.last_command = f"ATEM_PIP {'ON' if self.pip_enabled else 'OFF'} (Sub Cam {self.pip_fill_cam})"
        self._append_cmd_log(self.last_command)
        return self.get_status()

    def toggle_fade_to_black(self, duration_sec: float = 1.0) -> ATEMStatus:
        """Executes ATEM Mini Pro Fade-to-Black (`execFadeToBlackME(0)`)."""
        self.ftb_active = not self.ftb_active
        frames = max(6, min(75, int(round(duration_sec * 30.0))))

        if self._hw_switcher is not None:
            try:
                if hasattr(self._hw_switcher, "setFadeToBlackRate"):
                    self._hw_switcher.setFadeToBlackRate(0, frames)
                self._hw_switcher.execFadeToBlackME(0)
            except Exception:
                pass

        self.last_command = f"ATEM_FTB {'ACTIVE (Fade Out)' if self.ftb_active else 'RELEASED (Fade In)'} ({frames}f)"
        self._append_cmd_log(self.last_command)
        return self.get_status()

    def set_afv_enabled(self, enabled: bool) -> ATEMStatus:
        """Toggles Audio-Follow-Video (`afv`) across ATEM HDMI inputs 1~4."""
        self.afv_enabled = bool(enabled)
        mix_opt = "afv" if self.afv_enabled else "on"

        if self._hw_switcher is not None:
            try:
                for cid in range(1, 5):
                    if hasattr(self._hw_switcher, "setAudioMixerInputMixOption"):
                        self._hw_switcher.setAudioMixerInputMixOption(cid, mix_opt)
            except Exception:
                pass

        self.last_command = f"ATEM_AUDIO_AFV {'ENABLED (afv)' if self.afv_enabled else 'MASTER_LOCK (on)'}"
        self._append_cmd_log(self.last_command)
        return self.get_status()

    def sync_switcher_bus(
        self,
        program_cam: int,
        preview_cam: int,
        switched: bool = False,
        reason: str = "",
        effective_style: Optional[str] = None,
    ) -> ATEMStatus:
        """Updates ATEM Preview/Program buses and executes Cut or Auto (`MIX`/`DIP`/`WIPE`/`DVE`) transition."""
        self.preview_input = preview_cam
        if self.pip_enabled:
            self.pip_fill_cam = preview_cam

        if self._hw_switcher is not None and not switched:
            if time.time() >= self._transition_end_time:
                try:
                    self._hw_switcher.setPreviewInputVideoSource(0, preview_cam)
                    if self.pip_enabled:
                        self._hw_switcher.setKeyerFillSource(0, 0, preview_cam)
                except Exception:
                    pass

        if switched or program_cam != self.program_input:
            prev_pgm = self.program_input
            self.program_input = program_cam
            self.total_switches += 1

            # Resolve effective transition style
            if self.transition_style == "AI_AUTO":
                exec_style = (effective_style or "MIX").upper()
            else:
                exec_style = self.transition_style

            if exec_style not in ("CUT", "MIX", "DIP", "WIPE", "DVE"):
                exec_style = "CUT"
            self.last_executed_style = exec_style

            frames = max(6, min(75, int(round(self.transition_duration_sec * 30.0))))
            cmd_str = (
                f"ATEM {exec_style} ({self.transition_duration_sec:.1f}s) ME0: "
                f"Cam {prev_pgm} -> Cam {program_cam} ({reason})"
                if exec_style != "CUT"
                else f"ATEM CUT ME0: Cam {prev_pgm} -> Cam {program_cam} ({reason})"
            )
            self.last_command = cmd_str
            self._append_cmd_log(cmd_str)

            if self._hw_switcher is not None:
                try:
                    if exec_style == "CUT":
                        self._hw_switcher.setPreviewInputVideoSource(0, program_cam)
                        self._hw_switcher.execCutME(0)
                        self._hw_switcher.setPreviewInputVideoSource(0, preview_cam)
                    else:
                        hw_key = self.STYLE_TO_PYATEM.get(exec_style, "mix")
                        self._hw_switcher.setTransitionStyle(0, hw_key)
                        if exec_style == "MIX":
                            self._hw_switcher.setTransitionMixRate(0, frames)
                        elif exec_style == "DIP":
                            self._hw_switcher.setTransitionDipRate(0, frames)
                        elif exec_style == "WIPE":
                            self._hw_switcher.setTransitionWipeRate(0, frames)
                        elif exec_style == "DVE":
                            self._hw_switcher.setTransitionDVERate(0, frames)
                        # Set Preview to target camera and trigger ATEM AUTO transition!
                        self._hw_switcher.setPreviewInputVideoSource(0, program_cam)
                        self._hw_switcher.execAutoME(0)
                        self._transition_end_time = time.time() + self.transition_duration_sec
                except Exception:
                    pass

        return self.get_status()

    def _append_cmd_log(self, cmd_str: str) -> None:
        self.command_log.append(
            {
                "wall_time": round(time.time(), 2),
                "command": cmd_str,
                "program": self.program_input,
                "preview": self.preview_input,
                "style": self.last_executed_style,
            }
        )
        if len(self.command_log) > 50:
            self.command_log = self.command_log[-50:]

    def get_status(self) -> ATEMStatus:
        return ATEMStatus(
            mode=self.mode,
            connected=self.connected,
            atem_ip=self.atem_ip,
            program_input=self.program_input,
            preview_input=self.preview_input,
            transition_style=self.transition_style,
            last_executed_style=self.last_executed_style,
            transition_duration_sec=self.transition_duration_sec,
            pip_enabled=self.pip_enabled,
            pip_fill_cam=self.pip_fill_cam,
            ftb_active=self.ftb_active,
            afv_enabled=self.afv_enabled,
            total_switches=self.total_switches,
            last_command=self.last_command,
        )
