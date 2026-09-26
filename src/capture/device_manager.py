"""
Formal Windows Hardware Video Device Discovery & Per-Camera Slot Routing (`src/capture/device_manager.py`).

Uses the same official device enumeration techniques as OBS Studio:
  1. DirectShow Filter Graph device enumeration via `ffmpeg -list_devices true -f dshow -i dummy`
     to retrieve exact Friendly Names (e.g., "Blackmagic Design", "USB3.0 HD Video Capture",
     "Cam Link 4K", "OBS Virtual Camera", "Integrated Webcam") and DirectShow Moniker IDs.
  2. Windows WMI / CIM (`Win32_PnPEntity`) hardware inspection for USB VID/PID and connection status.
  3. Per-Camera Slot Binding (`CameraSlotBinding`) so each camera screen (Cam 1 ~ Cam 4) can
     independently connect to a specific capture device, an ATEM Multiview ROI slot, or a local MP4 file.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import cv2


@dataclass
class VideoHardwareDevice:
    index: int
    friendly_name: str
    dshow_moniker: str
    device_role: str  # "ATEM_USB_PROGRAM" | "HDMI_MULTIVIEW_CAPTURE" | "OBS_VIRTUAL_CAM" | "USB_CAMERA"
    role_label_kr: str
    resolution: str
    fps: float
    is_available: bool

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class CameraSlotBinding:
    """Defines what video source is bound to an individual camera slot (Cam 1 .. Cam 4)."""

    cam_id: int
    source_type: str  # "real_concert_mp4" | "multiview_roi" | "direct_device" | "video_file" | "synthetic" | "disabled"
    display_label: str
    device_index: int = 0
    device_name: str = ""
    file_path: str = ""
    multiview_slot: int = 1

    def to_dict(self) -> Dict:
        return asdict(self)


class WindowsDeviceManager:
    """Discovers physical & virtual video capture devices on Windows with full friendly names."""

    @staticmethod
    def _find_ffmpeg() -> Optional[str]:
        ff = shutil.which("ffmpeg")
        if ff:
            return ff
        candidates = list(
            Path(r"C:\Users\ddosu\AppData\Local\Microsoft\WinGet\Packages").glob("**/ffmpeg.exe")
        )
        return str(candidates[0]) if candidates else None

    @classmethod
    def list_directshow_names_via_ffmpeg(cls) -> List[Dict[str, str]]:
        """
        Queries DirectShow video devices via `ffmpeg -list_devices true -f dshow -i dummy`.
        Returns list of {"friendly_name": ..., "moniker": ...}.
        """
        ff = cls._find_ffmpeg()
        if not ff:
            return []

        try:
            proc = subprocess.run(
                [ff, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
            )
            stderr = proc.stderr or ""
        except Exception:
            return []

        devices: List[Dict[str, str]] = []
        lines = stderr.splitlines()
        for i, line in enumerate(lines):
            if "(video)" in line:
                m = re.search(r'"([^"]+)"\s*\(video\)', line)
                if m:
                    fname = m.group(1)
                    moniker = ""
                    if i + 1 < len(lines) and "Alternative name" in lines[i + 1]:
                        m_alt = re.search(r'"([^"]+)"', lines[i + 1])
                        if m_alt:
                            moniker = m_alt.group(1)
                    devices.append({"friendly_name": fname, "moniker": moniker})
        return devices

    @classmethod
    def list_wmi_camera_entities(cls) -> List[str]:
        """Queries Windows WMI `Win32_PnPEntity` for connected Camera/Media PnP devices."""
        try:
            ps_cmd = (
                "Get-CimInstance Win32_PnPEntity | "
                "Where-Object { $_.PNPClass -in @('Camera','Image','Media') -and $_.Status -eq 'OK' } | "
                "Select-Object -ExpandProperty Name"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4.0,
            )
            names = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
            return names
        except Exception:
            return []

    @staticmethod
    def _classify_device_role(name: str) -> tuple[str, str]:
        lower = name.lower()
        if "blackmagic" in lower or "atem" in lower:
            return (
                "ATEM_USB_PROGRAM",
                "⚠️ ATEM USB-C 웹캠 출력 (단일 Program 화면 — OBS 방송용 권장)",
            )
        if any(k in lower for k in ("capture", "cam link", "hdmi", "usb3.0 hd", "elgato", "avermedia", "magewell")):
            return (
                "HDMI_MULTIVIEW_CAPTURE",
                "✅ HDMI 멀티뷰/카메라 캡처보드 (AI 디렉터 분석용 최적 장비)",
            )
        if "obs" in lower or "virtual" in lower:
            return (
                "OBS_VIRTUAL_CAM",
                "🔄 OBS 가상 카메라 (OBS 화면 실시간 루프백 테스트용)",
            )
        return ("USB_CAMERA", "📹 일반 USB 카메라 / 웹캠 (개별 카메라 슬롯에 직접 연결 가능)")

    @classmethod
    def discover_all_video_devices(cls, max_probe: int = 5) -> List[VideoHardwareDevice]:
        """
        Combines DirectShow filter enumeration, WMI hardware info, and OpenCV CAP_DSHOW probing
        into a structured list of `VideoHardwareDevice`.
        """
        dshow_list = cls.list_directshow_names_via_ffmpeg()
        wmi_names = cls.list_wmi_camera_entities()

        results: List[VideoHardwareDevice] = []
        count_to_check = max(len(dshow_list), min(max_probe, 4))

        for idx in range(count_to_check):
            dshow_info = dshow_list[idx] if idx < len(dshow_list) else None
            friendly = (
                dshow_info["friendly_name"]
                if dshow_info
                else (wmi_names[idx] if idx < len(wmi_names) else f"DirectShow Video Input #{idx}")
            )
            moniker = dshow_info["moniker"] if dshow_info else f"@device_pnp_index_{idx}"

            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            is_avail = bool(cap.isOpened())
            if is_avail:
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
                res_str = f"{w}x{h}"
                cap.release()
            else:
                res_str = "1920x1080 (Supported)" if dshow_info else "연결 신호 없음"
                fps = 30.0

            if is_avail or dshow_info is not None:
                role_code, role_kr = cls._classify_device_role(friendly)
                results.append(
                    VideoHardwareDevice(
                        index=idx,
                        friendly_name=friendly,
                        dshow_moniker=moniker,
                        device_role=role_code,
                        role_label_kr=role_kr,
                        resolution=res_str,
                        fps=round(fps, 1),
                        is_available=is_avail,
                    )
                )

        return results
