"""
Clean, Uncluttered OBS Studio-Style Local AI Broadcast Director (`src/desktop_app.py`).

Key UX & Technical Features:
  1. Starts in calm STANDBY (OFF) mode (`system_running = False`):
     No video plays until the user clicks `[▶ 시스템 시작 (ON)]`.
  2. Fixed Qt Scrollbar Top-Alignment & Scroll-Preserving Log Engine:
     - Explicit `QScrollBar:vertical` / `QScrollBar:horizontal` stylesheet with zero-height
       `add-line`/`sub-line` subcontrols so the scrollbar thumb never sinks or wraps at the top.
     - `set_text_at_top()` and `append_log_preserving_scroll()` ensure that when a scrollbar is at
       the top (`value == 0`), the page stays strictly at the top and never jumps down and back up.
  3. Tempo Control with Presets + Direct 0.1s Slider Bars (`min_hold_sec` & `max_hold_sec`):
     - Keeps the 3 style preset buttons (`차분하게`, `표준 공연`, `역동적`) AND provides two interactive
       horizontal sliders to directly control minimum hold seconds (`0.5~10.0초`) and maximum
       rotation seconds (`2.0~20.0초`).
  4. ATEM Mini Pro Smooth Transitions (`MIX`, `DIP`, `WIPE`, `DVE`) & Hardware Functions (`PiP`, `FTB`, `AFV`):
     - Directly drives `PyATEMMax` hardware commands (`execAutoME`, `setTransitionStyle`, `setTransitionMixRate`,
       `setKeyerOnAirEnabled`, `execFadeToBlackME`, `setAudioMixerInputMixOption`) and simultaneously renders
       30fps smooth transitions (`MIX` dissolve, `DIP`, `WIPE`, `DVE` push), `PiP`, and `FTB` on the local
       Program monitor.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QImage, QPixmap, QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from scripts.download_real_concert_cams import prepare_real_concert_4cam_mp4s
from scripts.generate_synthetic_scene import FPS, generate_synthetic_dataset
from src.atem.controller import ATEMController
from src.atem.verifier import ATEMSignalVerifier
from src.capture.device_manager import VideoHardwareDevice, WindowsDeviceManager
from src.capture.ingestor import REAL_CONCERT_DEFAULT_FILES, MultiCamIngestor
from src.engine.state_machine import BroadcastStateMachine
from src.pipeline.audio_analyzer import AudioAnalyzer
from src.pipeline.vision_scorer import ChannelMetrics, VisionScorer
from src.server.app import run_full_scene_benchmark
from src.utils.dependency_manager import DependencyManager


# =============================================================================
# Global Scrollbar & UI Helpers (Fixes Windows Qt5 Scrollbar Top-Jump Glitch)
# =============================================================================

GLOBAL_SCROLLBAR_CSS = """
QScrollBar:vertical {
    background-color: #14161b;
    width: 12px;
    margin: 0px 0px 0px 0px;
    border: 1px solid #282c37;
    border-radius: 6px;
}
QScrollBar::handle:vertical {
    background-color: #475569;
    min-height: 32px;
    border-radius: 5px;
    margin: 1px;
}
QScrollBar::handle:vertical:hover {
    background-color: #60a5fa;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
    width: 0px;
    border: none;
    background: none;
    subcontrol-origin: margin;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QScrollBar:horizontal {
    background-color: #14161b;
    height: 12px;
    margin: 0px 0px 0px 0px;
    border: 1px solid #282c37;
    border-radius: 6px;
}
QScrollBar::handle:horizontal {
    background-color: #475569;
    min-width: 32px;
    border-radius: 5px;
    margin: 1px;
}
QScrollBar::handle:horizontal:hover {
    background-color: #60a5fa;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
    height: 0px;
    border: none;
    background: none;
    subcontrol-origin: margin;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}
QSlider::groove:horizontal {
    border: 1px solid #323746;
    height: 6px;
    background: #14161b;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #3b82f6;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #60a5fa;
    border: 1px solid #eff6ff;
    width: 15px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover {
    background: #93c5fd;
}
"""


def set_text_at_top(text_edit: QTextEdit, content: str) -> None:
    """Sets plain text on `text_edit` and guarantees cursor and scrollbar start strictly at the top (0)."""
    text_edit.setPlainText(content)
    cursor = text_edit.textCursor()
    cursor.movePosition(QTextCursor.Start)
    text_edit.setTextCursor(cursor)
    bar = text_edit.verticalScrollBar()
    bar.setValue(0)
    QTimer.singleShot(0, lambda: bar.setValue(0))


def append_log_preserving_scroll(text_edit: QTextEdit, line: str) -> None:
    """
    Inserts a new log line at the bottom of `text_edit` WITHOUT moving the active viewport
    cursor or hijacking the scrollbar when the user has scrolled to the top (`value == 0`).
    """
    bar = text_edit.verticalScrollBar()
    prev_val = bar.value()
    prev_max = bar.maximum()
    # Only auto-scroll if user was already near the very bottom AND not holding the top (0)
    was_at_bottom = (prev_max > 0) and (prev_val >= prev_max - 12)

    doc = text_edit.document()
    cursor = QTextCursor(doc)
    cursor.movePosition(QTextCursor.End)
    if doc.characterCount() > 1:
        cursor.insertText("\n" + line)
    else:
        cursor.insertText(line)

    if was_at_bottom:
        bar.setValue(bar.maximum())
    else:
        bar.setValue(prev_val)


def bgr_to_qpixmap(bgr: np.ndarray, width: int, height: int) -> QPixmap:
    resized = cv2.resize(bgr, (width, height), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg)


def make_standby_pixmap(width: int, height: int, title: str, subtitle: str) -> QPixmap:
    """Renders a calm, uncluttered dark slate Standby screen when the system is OFF."""
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:] = (22, 25, 33)
    cv2.rectangle(canvas, (2, 2), (width - 3, height - 3), (48, 54, 68), 2)

    cx, cy = width // 2, int(height * 0.40)
    cv2.circle(canvas, (cx, cy), 22, (45, 52, 68), -1, cv2.LINE_AA)
    cv2.circle(canvas, (cx, cy), 22, (85, 98, 125), 2, cv2.LINE_AA)
    cv2.rectangle(canvas, (cx - 7, cy - 9), (cx - 2, cy + 9), (148, 163, 184), -1)
    cv2.rectangle(canvas, (cx + 2, cy - 9), (cx + 7, cy + 9), (148, 163, 184), -1)

    t_size = cv2.getTextSize(title, cv2.FONT_HERSHEY_SIMPLEX, 0.58, 2)[0]
    cv2.putText(
        canvas,
        title,
        (max(10, (width - t_size[0]) // 2), int(height * 0.66)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (180, 192, 210),
        2,
        cv2.LINE_AA,
    )
    s_size = cv2.getTextSize(subtitle, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 1)[0]
    cv2.putText(
        canvas,
        subtitle,
        (max(10, (width - s_size[0]) // 2), int(height * 0.80)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.44,
        (115, 128, 150),
        1,
        cv2.LINE_AA,
    )
    return bgr_to_qpixmap(canvas, width, height)


def render_atem_program_composite(
    from_bgr: Optional[np.ndarray],
    to_bgr: np.ndarray,
    style: str,
    progress: float,
    pip_enabled: bool = False,
    pip_bgr: Optional[np.ndarray] = None,
    ftb_alpha: float = 0.0,
) -> np.ndarray:
    """
    Renders real-time ATEM Mini Pro transition effects (`MIX`, `DIP`, `WIPE`, `DVE`)
    plus Upstream Keyer Picture-in-Picture (`PiP`) and Fade-to-Black (`FTB`) at 30fps.
    """
    h, w = to_bgr.shape[:2]
    alpha = max(0.0, min(1.0, float(progress)))

    if from_bgr is None or alpha >= 0.999 or style == "CUT":
        out = to_bgr.copy()
    else:
        src_a = cv2.resize(from_bgr, (w, h)) if from_bgr.shape[:2] != (h, w) else from_bgr
        src_b = to_bgr
        st = style.upper()

        if st == "MIX":
            # Smooth cross-dissolve overlap
            out = cv2.addWeighted(src_a, 1.0 - alpha, src_b, alpha, 0.0)
        elif st == "DIP":
            # Dip through warm stage white flash at midpoint (alpha=0.5)
            dip_color = np.full_like(src_b, (235, 240, 250))
            if alpha < 0.5:
                local_a = alpha * 2.0
                out = cv2.addWeighted(src_a, 1.0 - local_a, dip_color, local_a, 0.0)
            else:
                local_a = (alpha - 0.5) * 2.0
                out = cv2.addWeighted(dip_color, 1.0 - local_a, src_b, local_a, 0.0)
        elif st == "WIPE":
            # Horizontal soft wipe with glowing border bar
            out = src_a.copy()
            split_x = max(1, min(w - 1, int(w * alpha)))
            out[:, :split_x] = src_b[:, :split_x]
            cv2.line(out, (split_x, 0), (split_x, h), (96, 165, 250), 3)
        elif st == "DVE":
            # 3D Push-Slide effect (new camera slides in from right pushing old camera left)
            out = np.zeros_like(src_b)
            shift_x = max(1, min(w - 1, int(w * alpha)))
            out[:, : w - shift_x] = src_a[:, shift_x:]
            out[:, w - shift_x :] = src_b[:, :shift_x]
            cv2.line(out, (w - shift_x, 0), (w - shift_x, h), (59, 130, 246), 3)
        else:
            out = cv2.addWeighted(src_a, 1.0 - alpha, src_b, alpha, 0.0)

        # Overlay subtle badge showing active smooth transition
        cv2.rectangle(out, (w - 225, 10), (w - 10, 38), (30, 58, 138), -1)
        cv2.putText(
            out,
            f"ATEM {st} ({int(alpha * 100)}%)",
            (w - 215, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    # Upstream Keyer 0 DVE Picture-in-Picture (PiP) overlay in bottom-right corner
    if pip_enabled and pip_bgr is not None:
        pw, ph = int(w * 0.30), int(h * 0.30)
        px, py = w - pw - 14, h - ph - 14
        pip_small = cv2.resize(pip_bgr, (pw, ph), interpolation=cv2.INTER_AREA)
        out[py : py + ph, px : px + pw] = pip_small
        cv2.rectangle(out, (px, py), (px + pw, py + ph), (96, 165, 250), 2)
        cv2.rectangle(out, (px, py), (px + 74, py + 20), (37, 99, 235), -1)
        cv2.putText(out, "ATEM PiP", (px + 6, py + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)

    # Fade-to-Black (FTB) overlay
    if ftb_alpha > 0.01:
        black = np.zeros_like(out)
        out = cv2.addWeighted(out, 1.0 - ftb_alpha, black, ftb_alpha, 0.0)
        if ftb_alpha > 0.4:
            cv2.putText(
                out,
                "ATEM FADE TO BLACK (FTB ON AIR)",
                (int(w * 0.22), int(h * 0.52)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (90, 100, 125),
                2,
                cv2.LINE_AA,
            )

    return out


def draw_friendly_camera_hud(
    frame_bgr: np.ndarray,
    metric: ChannelMetrics,
    is_program: bool,
    is_preview: bool,
    solo_active: bool,
) -> np.ndarray:
    vis = frame_bgr.copy()
    h, w = vis.shape[:2]

    if not getattr(metric, "enabled", True) or not getattr(metric, "has_signal", True):
        vis[:] = (22, 24, 30)
        cv2.rectangle(vis, (3, 3), (w - 4, h - 4), (55, 62, 78), 2)
        cv2.putText(
            vis,
            f"CAM {metric.cam_id} - OFF",
            (int(w * 0.34), int(h * 0.52)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.70,
            (130, 140, 160),
            2,
            cv2.LINE_AA,
        )
        return vis

    bx, by, bw, bh = metric.subject_bbox
    box_col = (50, 50, 235) if metric.is_blurry else ((30, 185, 255) if solo_active else (65, 220, 110))
    cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), box_col, 2)

    if metric.is_blurry:
        cv2.rectangle(vis, (10, 10), (235, 40), (35, 35, 215), -1)
        cv2.putText(vis, "BLUR / SHAKE (SKIP)", (18, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 255), 2, cv2.LINE_AA)
    elif solo_active:
        cv2.rectangle(vis, (10, 10), (210, 40), (195, 105, 20), -1)
        cv2.putText(vis, "SOLO HIGHLIGHT", (18, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 255), 2, cv2.LINE_AA)

    if is_program:
        cv2.rectangle(vis, (3, 3), (w - 4, h - 4), (45, 45, 240), 7)
    elif is_preview:
        cv2.rectangle(vis, (3, 3), (w - 4, h - 4), (55, 210, 85), 5)

    return vis


# =============================================================================
# Scrollable, Top-Aligned Dialogs
# =============================================================================


class DependencyInstallDialog(QDialog):
    """One-click Pip Package Status & Automatic Installer Dialog with Top-Aligned Scrollbar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("📦 필수 모듈(PyATEMMax 등) 점검 및 원클릭 자동 설치")
        self.resize(680, 480)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1d24; color: #eceff4; font-family: 'Malgun Gothic', sans-serif; }
            QLabel { font-size: 13px; }
            QTextEdit { background-color: #111318; border: 1px solid #2e3440; border-radius: 6px; padding: 8px; font-family: Consolas, 'Malgun Gothic', monospace; font-size: 12px; color: #e2e8f0; }
            QPushButton { background-color: #2563eb; color: white; border: none; border-radius: 6px; padding: 9px 18px; font-weight: bold; font-size: 13px; }
            QPushButton:hover { background-color: #1d4ed8; }
            """
            + GLOBAL_SCROLLBAR_CSS
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("📦 ATEM 하드웨어 제어 및 영상 분석 필수 모듈(pip) 점검")
        title.setFont(QFont("Malgun Gothic", 14, QFont.Bold))
        title.setStyleSheet("color: #60a5fa;")
        title.setWordWrap(True)
        layout.addWidget(title)

        desc = QLabel(
            "실제 ATEM Mini Pro 스위처를 랜선(UDP)으로 제어하려면 'PyATEMMax' 모듈이 필요합니다. "
            "아래에서 설치 여부를 확인하고, 미설치된 항목이 있다면 [⚡ 누락된 모듈 지금 자동 설치하기] 버튼만 누르세요."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #cbd5e1; line-height: 1.5;")
        layout.addWidget(desc)

        self.txt_status = QTextEdit()
        self.txt_status.setReadOnly(True)
        self.txt_status.setLineWrapMode(QTextEdit.WidgetWidth)
        layout.addWidget(self.txt_status, stretch=1)

        btn_row = QHBoxLayout()
        self.btn_install = QPushButton("⚡ 누락된 모듈(PyATEMMax 포함) 지금 자동 설치하기 (pip install)")
        self.btn_install.setStyleSheet("background-color: #16a34a;")
        self.btn_install.clicked.connect(self._run_auto_install)
        btn_row.addWidget(self.btn_install)

        btn_close = QPushButton("닫기")
        btn_close.setStyleSheet("background-color: #475569;")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self._refresh_status()

    def showEvent(self, event) -> None:  # type: ignore
        super().showEvent(event)
        self.txt_status.verticalScrollBar().setValue(0)

    def _refresh_status(self) -> None:
        pkgs = DependencyManager.check_all_packages()
        missing = [p.pip_name for p in pkgs if not p.installed]

        lines: List[str] = ["[필수 Python 패키지 설치 현황]\n"]
        for p in pkgs:
            mark = "✅ 설치 완료" if p.installed else "❌ 미설치 (자동 설치 대상)"
            lines.append(f"• {p.pip_name} ({mark} | 버전: {p.version})\n   ↳ 역할: {p.description_kr}\n")

        if not missing:
            lines.append("🎉 모든 필수 모듈(PyATEMMax 포함)이 완벽하게 설치되어 있습니다!")
            self.btn_install.setText("✅ 모든 필수 모듈 설치 완료 (재점검하기)")
            self.btn_install.setStyleSheet("background-color: #2563eb;")
        else:
            lines.append(f"⚠️ 미설치 항목 발견: {', '.join(missing)} — 아래 초록색 버튼을 누르면 자동으로 설치됩니다.")
            self.btn_install.setText(f"⚡ 미설치 모듈({', '.join(missing)}) 지금 자동 설치하기")
            self.btn_install.setStyleSheet("background-color: #16a34a;")

        set_text_at_top(self.txt_status, "\n".join(lines))

    def _run_auto_install(self) -> None:
        missing = [p.pip_name for p in DependencyManager.get_missing_packages()]
        target_list = missing if missing else ["PyATEMMax"]
        append_log_preserving_scroll(
            self.txt_status, f"\n⏳ 자동 설치 진행 중: pip install {' '.join(target_list)} ...\n잠시만 기다려 주세요..."
        )
        QApplication.processEvents()

        ok, out_log = DependencyManager.install_packages(target_list)
        self._refresh_status()
        append_log_preserving_scroll(self.txt_status, "\n[pip 실행 상세 로그]\n" + out_log)


class ConnectionGuideDialog(QDialog):
    """Scrollable, Top-Aligned Step-by-Step Connection Guide Dialog."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("❓ 초보자를 위한 카메라 & OBS Studio 연결 안내서")
        self.resize(740, 560)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1d24; color: #eceff4; font-family: 'Malgun Gothic', sans-serif; }
            QScrollArea { border: none; background-color: transparent; }
            QGroupBox { background-color: #242834; border: 1px solid #3b4254; border-radius: 8px; margin-top: 12px; padding: 16px; font-weight: bold; color: #60a5fa; font-size: 13.5px; }
            QLabel { font-size: 13px; color: #e2e8f0; }
            QPushButton { background-color: #2563eb; color: white; border: none; border-radius: 6px; padding: 9px 22px; font-weight: bold; font-size: 13px; }
            """
            + GLOBAL_SCROLLBAR_CSS
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        header = QLabel("🎥 OBS로 녹화·생방송하면서 AI 자동 화면전환 쓰는 방법")
        header.setFont(QFont("Malgun Gothic", 15, QFont.Bold))
        header.setStyleSheet("color: #60a5fa;")
        header.setWordWrap(True)
        outer.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(4, 4, 12, 4)
        c_layout.setSpacing(14)

        steps = [
            (
                "1단계: 필수 모듈(PyATEMMax) 원클릭 설치 확인",
                "상단 바의 [✅ 필수 모듈(PyATEMMax)] 버튼을 눌러 통신 모듈이 설치되어 있는지 확인합니다. "
                "설치되어 있지 않다면 버튼 한 번만 누르면 프로그램이 스스로 설치를 완료합니다.",
            ),
            (
                "2단계: 카메라(2대, 3대 또는 4대)를 ATEM 스위처에 연결",
                "가지고 계신 카메라(2대든 3대든 4대든 모두 가능)를 ATEM Mini Pro 뒷면 HDMI 1~4번 포트에 꽂으세요. "
                "카메라가 꽂히지 않은 빈 포트는 우리 프로그램이 자동으로 감지하여 전환 대상에서 제외하므로 검은 화면으로 넘어가지 않습니다.",
            ),
            (
                "3단계: OBS Studio에는 평소처럼 ATEM USB-C 선을 연결 (녹화/방송용)",
                "ATEM 뒷면의 USB-C 케이블을 컴퓨터에 꽂으면 OBS Studio에서 'Blackmagic Design 웹캠'으로 인식됩니다. "
                "OBS에서는 이 화면 하나만 띄워두고 평소처럼 녹화나 생방송을 진행하시면 됩니다.",
            ),
            (
                "4단계: 부드러운 디졸브(MIX) / 와이프 / PiP 및 초 단위 템포 조절",
                "하단 2번 패널에서 [최소 유지 시간 바]와 [최대 순환 시간 바]를 마우스로 움직여 원하는 초(0.1초 단위)로 템포를 직접 맞출 수 있으며, "
                "ATEM의 [부드러운 디졸브(MIX)], [화면 밀기(WIPE)], [3D 슬라이드(DVE)], [화면 속 작은 화면(PiP)], [부드러운 암전(FTB)] 기능도 자체 제어할 수 있습니다.",
            ),
        ]

        for step_title, step_body in steps:
            box = QGroupBox(step_title)
            bl = QVBoxLayout(box)
            lbl = QLabel(step_body)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("line-height: 1.6; padding-top: 4px;")
            bl.addWidget(lbl)
            c_layout.addWidget(box)

        c_layout.addStretch()
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, stretch=1)

        btn_close = QPushButton("확인했습니다 (창 닫기)")
        btn_close.clicked.connect(self.accept)
        outer.addWidget(btn_close, alignment=Qt.AlignRight)

    def showEvent(self, event) -> None:  # type: ignore
        super().showEvent(event)
        self.scroll.verticalScrollBar().setValue(0)


class ATEMVerificationDialog(QDialog):
    """Scrollable, Top-Aligned ATEM Signal & Hardware Verification Dialog."""

    def __init__(self, ingestor: MultiCamIngestor, active_channels: List[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ingestor = ingestor
        self.active_channels = active_channels
        self.setWindowTitle("🔬 ATEM 스위처 화면 수신 & 연결 장비 정식 진단")
        self.resize(780, 560)
        self.setStyleSheet(
            """
            QDialog { background-color: #1a1d24; color: #eceff4; font-family: 'Malgun Gothic', sans-serif; }
            QTextEdit { background-color: #111318; border: 1px solid #2e3440; border-radius: 6px; padding: 10px; font-family: Consolas, 'Malgun Gothic', monospace; font-size: 12.5px; color: #e2e8f0; }
            QPushButton { background-color: #2563eb; color: white; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            """
            + GLOBAL_SCROLLBAR_CSS
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        header = QLabel("🔬 ATEM 스위처 카메라 화면 수신 & 장비 연결 정식 진단")
        header.setFont(QFont("Malgun Gothic", 14, QFont.Bold))
        header.setStyleSheet("color: #60a5fa;")
        header.setWordWrap(True)
        layout.addWidget(header)

        desc = QLabel(
            "Windows DirectShow/WMI 정식 장치 탐색기로 연결된 캡처 장비의 실제 이름을 확인하고, "
            "ATEM 10분할 멀티뷰의 카메라 1~4번 영역 분리 및 탈리(Tally) 불빛 루프백 반응이 정상인지 검증합니다."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #cbd5e1;")
        layout.addWidget(desc)

        self.report_box = QTextEdit()
        self.report_box.setReadOnly(True)
        self.report_box.setLineWrapMode(QTextEdit.WidgetWidth)
        layout.addWidget(self.report_box, stretch=1)

        btn_row = QHBoxLayout()
        btn_rerun = QPushButton("🔄 정식 장비 탐색 & ATEM 수신 검증 다시 실행")
        btn_rerun.clicked.connect(self.run_diagnostics)
        btn_row.addWidget(btn_rerun)

        btn_close = QPushButton("닫기")
        btn_close.setStyleSheet("background-color: #475569;")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self.run_diagnostics()

    def showEvent(self, event) -> None:  # type: ignore
        super().showEvent(event)
        self.report_box.verticalScrollBar().setValue(0)

    def run_diagnostics(self) -> None:
        lines: List[str] = []
        lines.append("■ 1단계: Windows DirectShow / WMI 정식 비디오 캡처 장치 탐색")
        lines.append("-" * 68)
        devices = WindowsDeviceManager.discover_all_video_devices(max_probe=5)
        if not devices:
            lines.append("• 현재 물리적으로 연결된 외부 USB 캡처보드/웹캠이 없습니다.")
            lines.append("• 캡처보드를 꽂거나 OBS에서 [가상 카메라 시작]을 누르면 이곳에 정식 장비명이 표시됩니다.")
        else:
            for d in devices:
                avail = "사용 가능" if d.is_available else "OBS 등 다른 앱에서 사용 중"
                lines.append(f"• 장치 #{d.index}: {d.friendly_name} ({d.resolution}, {d.fps}fps) [{avail}]")
                lines.append(f"  ↳ 용도 안내: {d.role_label_kr}")

        lines.append("\n■ 2단계: ATEM 멀티뷰 카메라(1~4번) 분리 & 능동형 탈리 루프백 검증")
        lines.append("-" * 68)
        ver = ATEMSignalVerifier.run_full_verification(self.ingestor, self.active_channels)
        lines.append(f"• 기준 해상도: {ver['multiview_resolution']} | 활성 카메라: {ver['active_camera_count']}대 | 검증 시간: {ver['total_verification_ms']}ms\n")

        for s in ver["slots"]:
            pvw_ok = "통과(#00FF00 검출)" if s["green_tally_detected_on_pvw"] else "미검출"
            pgm_ok = "통과(#FF0000 검출)" if s["red_tally_detected_on_pgm"] else "미검출"
            lines.append(f"▶ [{s['cam_id']}번 카메라] 연결 소스: {s['source_desc']}")
            lines.append(f"   - 크롭 해상도: {s['resolution']} | Preview 탈리: {pvw_ok} | Program 탈리: {pgm_ok}")
            lines.append(f"   - 판정: {s['verdict_kr']}\n")

        set_text_at_top(self.report_box, "\n".join(lines))


# =============================================================================
# Main Decluttered OBS Studio-Style Window
# =============================================================================


class OBSDirectorWindow(QMainWindow):
    """Spacious, clean OBS Studio-inspired local AI Broadcast Director with Standby OFF default."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ATEM AI 방송 디렉터 — OBS 연동형 자동 카메라 스위처")
        self.resize(1520, 950)
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #181a20;
                color: #e5e9f0;
                font-family: 'Malgun Gothic', 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QScrollArea {
                border: none;
                background-color: #181a20;
            }
            QGroupBox {
                background-color: #22252e;
                border: 1px solid #323746;
                border-radius: 8px;
                margin-top: 10px;
                padding: 12px 12px 10px 12px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 2px 10px;
                background-color: #2d3240;
                border-radius: 4px;
                color: #d8dee9;
            }
            QPushButton {
                background-color: #2f3441;
                border: 1px solid #434959;
                border-radius: 6px;
                padding: 6px 12px;
                color: #eceff4;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3f4658;
                border-color: #60a5fa;
            }
            QPushButton:checked {
                background-color: #2563eb;
                border-color: #60a5fa;
                color: #ffffff;
            }
            QComboBox, QLineEdit {
                background-color: #14161b;
                border: 1px solid #3b4150;
                border-radius: 5px;
                padding: 5px 8px;
                color: #eceff4;
                font-size: 11.5px;
            }
            QProgressBar {
                background-color: #14161b;
                border: 1px solid #323746;
                border-radius: 4px;
                text-align: center;
                height: 14px;
                font-size: 10.5px;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #3b82f6;
                border-radius: 3px;
            }
            QTextEdit {
                background-color: #13151a;
                border: 1px solid #2d3240;
                border-radius: 6px;
                padding: 8px;
                color: #d8dee9;
                font-size: 12px;
            }
            """
            + GLOBAL_SCROLLBAR_CSS
        )

        data_dir = Path("data/synthetic")
        if not (data_dir / "cam1_wide.mp4").exists():
            generate_synthetic_dataset(data_dir)
        prepare_real_concert_4cam_mp4s("data/real_concert")

        self.discovered_devices: List[VideoHardwareDevice] = WindowsDeviceManager.discover_all_video_devices(max_probe=4)
        self.active_channels: List[int] = [1, 2, 3, 4]
        self.ingestor = MultiCamIngestor(mode="real_concert_mp4", data_dir=data_dir, active_channels=self.active_channels)
        self.scorer = VisionScorer()
        self.audio = AudioAnalyzer()
        self.state_machine = BroadcastStateMachine()
        self.atem = ATEMController()

        # Start in calm STANDBY (OFF) mode by default
        self.system_running: bool = False

        # Real-time local transition animation state
        self.trans_from_cam: Optional[int] = None
        self.trans_style: str = "CUT"
        self.trans_start_wall: float = 0.0
        self.ftb_visual_alpha: float = 0.0

        self._build_clean_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick_frame)
        self._render_standby_screens()
        self._refresh_pip_button_badge()

    def showEvent(self, event) -> None:  # type: ignore
        super().showEvent(event)
        # Ensure main window and log box scrollbars always start strictly at the top (0)
        self.main_scroll.verticalScrollBar().setValue(0)
        self.txt_friendly_log.verticalScrollBar().setValue(0)

    def _build_clean_ui(self) -> None:
        self.main_scroll = QScrollArea()
        self.main_scroll.setWidgetResizable(True)
        self.setCentralWidget(self.main_scroll)

        central = QWidget()
        self.main_scroll.setWidget(central)
        main_vbox = QVBoxLayout(central)
        main_vbox.setContentsMargins(16, 12, 16, 12)
        main_vbox.setSpacing(10)

        # =====================================================================
        # 1. SPACIOUS TOP MASTER HEADER BAR
        # =====================================================================
        top_frame = QFrame()
        top_frame.setStyleSheet(
            "QFrame { background-color: #21242d; border: 1px solid #323746; border-radius: 8px; }"
        )
        top_hbox = QHBoxLayout(top_frame)
        top_hbox.setContentsMargins(14, 9, 14, 9)
        top_hbox.setSpacing(12)

        self.btn_power = QPushButton("▶ 시스템 시작 (영상 수신 & AI 켜기)")
        self.btn_power.setCheckable(True)
        self.btn_power.setChecked(False)
        self.btn_power.setMinimumHeight(38)
        self.btn_power.setMinimumWidth(235)
        self.btn_power.setStyleSheet(
            """
            QPushButton { background-color: #16a34a; border: 2px solid #4ade80; color: white; font-size: 13px; border-radius: 6px; padding: 6px 14px; }
            QPushButton:hover { background-color: #15803d; }
            QPushButton:checked { background-color: #dc2626; border: 2px solid #f87171; color: white; }
            """
        )
        self.btn_power.clicked.connect(self._on_toggle_master_power)
        top_hbox.addWidget(self.btn_power)

        self.lbl_master_status = QLabel(
            "⏸ 현재 [대기 모드(OFF)]입니다. 카메라 대수나 전환 효과를 확인한 뒤 [▶ 시스템 시작]을 눌러주세요."
        )
        self.lbl_master_status.setWordWrap(True)
        self.lbl_master_status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.lbl_master_status.setStyleSheet("color: #cbd5e1; font-size: 12.5px;")
        top_hbox.addWidget(self.lbl_master_status, stretch=1)

        self.btn_pip = QPushButton("📦 필수 모듈(PyATEMMax) 점검 / 자동 설치")
        self.btn_pip.setMinimumHeight(34)
        self.btn_pip.clicked.connect(self._open_pip_installer)
        top_hbox.addWidget(self.btn_pip)

        btn_verify = QPushButton("🔬 ATEM 수신 진단")
        btn_verify.setMinimumHeight(34)
        btn_verify.clicked.connect(self._open_verification_wizard)
        top_hbox.addWidget(btn_verify)

        btn_help = QPushButton("❓ 연결 안내서")
        btn_help.setMinimumHeight(34)
        btn_help.setStyleSheet("background-color: #2563eb; color: white;")
        btn_help.clicked.connect(lambda: ConnectionGuideDialog(self).exec_())
        top_hbox.addWidget(btn_help)

        main_vbox.addWidget(top_frame)

        # =====================================================================
        # 2. OBS STUDIO MODE DUAL MONITORS + ATEM TRANSITION / HARDWARE CONTROLS
        # =====================================================================
        studio_hbox = QHBoxLayout()
        studio_hbox.setSpacing(12)

        # Left Monitor: PREVIEW
        pvw_box = QGroupBox("🟢 다음 대기 화면 (미리보기 / Preview)")
        pvw_box.setStyleSheet("QGroupBox { border: 2px solid #22c55e; }")
        pvw_layout = QVBoxLayout(pvw_box)
        pvw_layout.setSpacing(6)
        self.lbl_pvw_screen = QLabel()
        self.lbl_pvw_screen.setFixedSize(455, 225)
        self.lbl_pvw_screen.setAlignment(Qt.AlignCenter)
        self.lbl_pvw_screen.setStyleSheet("background-color: #12141a; border-radius: 6px;")
        pvw_layout.addWidget(self.lbl_pvw_screen, alignment=Qt.AlignCenter)

        self.lbl_pvw_desc = QLabel("대기 중 — 시스템을 시작하면 AI가 준비한 다음 카메라 화면이 표시됩니다.")
        self.lbl_pvw_desc.setWordWrap(True)
        self.lbl_pvw_desc.setAlignment(Qt.AlignCenter)
        self.lbl_pvw_desc.setStyleSheet("color: #4ade80; font-size: 12px; font-weight: bold;")
        pvw_layout.addWidget(self.lbl_pvw_desc)
        studio_hbox.addWidget(pvw_box, stretch=4)

        # Center Transition & ATEM Hardware Control Column
        center_frame = QFrame()
        center_frame.setFixedWidth(235)
        center_frame.setStyleSheet("QFrame { background-color: #22252e; border: 1px solid #323746; border-radius: 8px; }")
        center_vbox = QVBoxLayout(center_frame)
        center_vbox.setContentsMargins(10, 10, 10, 10)
        center_vbox.setSpacing(7)

        self.btn_ai_toggle = QPushButton("🤖 AI 자동 화면전환 [켜짐 ON]")
        self.btn_ai_toggle.setCheckable(True)
        self.btn_ai_toggle.setChecked(True)
        self.btn_ai_toggle.setMinimumHeight(36)
        self.btn_ai_toggle.setStyleSheet(
            """
            QPushButton { background-color: #1d4ed8; border: 1px solid #60a5fa; color: white; font-size: 12px; border-radius: 6px; }
            QPushButton:!checked { background-color: #3f4658; border: 1px solid #64748b; color: #cbd5e1; }
            """
        )
        self.btn_ai_toggle.clicked.connect(self._on_toggle_ai_director)
        center_vbox.addWidget(self.btn_ai_toggle)

        cut_auto_row = QHBoxLayout()
        cut_auto_row.setSpacing(6)
        btn_cut_now = QPushButton("⚡ 즉각 CUT")
        btn_cut_now.setToolTip("ATEM execCutME(0): 지연 없이 즉시 화면을 바꿉니다")
        btn_cut_now.setMinimumHeight(34)
        btn_cut_now.clicked.connect(lambda: self._manual_cut(self.state_machine.preview_cam, force_style="CUT"))
        cut_auto_row.addWidget(btn_cut_now)

        btn_auto_now = QPushButton("🌊 부드러운 AUTO")
        btn_auto_now.setToolTip("ATEM execAutoME(0): 설정된 디졸브/와이프/DVE 효과로 부드럽게 전환합니다")
        btn_auto_now.setMinimumHeight(34)
        btn_auto_now.setStyleSheet("background-color: #0284c7; color: white;")
        btn_auto_now.clicked.connect(lambda: self._manual_cut(self.state_machine.preview_cam, force_style="AUTO"))
        cut_auto_row.addWidget(btn_auto_now)
        center_vbox.addLayout(cut_auto_row)

        # ATEM Hardware Special Features (PiP, FTB, AFV)
        hw_lbl = QLabel("🎛️ ATEM 본체 부가기능 제어:")
        hw_lbl.setStyleSheet("color: #94a3b8; font-size: 11px;")
        center_vbox.addWidget(hw_lbl)

        hw_row = QHBoxLayout()
        hw_row.setSpacing(4)
        self.btn_atem_pip = QPushButton("🖼️ PiP")
        self.btn_atem_pip.setCheckable(True)
        self.btn_atem_pip.setToolTip("화면 속 작은 화면(Picture-in-Picture): 보조 카메라를 우측 하단에 동시 표시")
        self.btn_atem_pip.clicked.connect(self._on_toggle_atem_pip)
        hw_row.addWidget(self.btn_atem_pip)

        self.btn_atem_ftb = QPushButton("🌑 암전(FTB)")
        self.btn_atem_ftb.setCheckable(True)
        self.btn_atem_ftb.setToolTip("Fade-to-Black: 공연 시작/종료 시 방송 화면을 부드럽게 암전시킵니다")
        self.btn_atem_ftb.clicked.connect(self._on_toggle_atem_ftb)
        hw_row.addWidget(self.btn_atem_ftb)

        self.btn_atem_afv = QPushButton("🔊 AFV")
        self.btn_atem_afv.setCheckable(True)
        self.btn_atem_afv.setToolTip("Audio-Follow-Video: 송출되는 카메라의 마이크 소리가 자동으로 따라가게 설정")
        self.btn_atem_afv.clicked.connect(self._on_toggle_atem_afv)
        hw_row.addWidget(self.btn_atem_afv)
        center_vbox.addLayout(hw_row)

        self.lbl_hold_friendly = QLabel("⏱️ 화면 유지 타이머 (대기 모드)")
        self.lbl_hold_friendly.setWordWrap(True)
        self.lbl_hold_friendly.setAlignment(Qt.AlignCenter)
        self.lbl_hold_friendly.setStyleSheet("color: #93c5fd; font-size: 11px;")
        center_vbox.addWidget(self.lbl_hold_friendly)

        self.bar_hold = QProgressBar()
        self.bar_hold.setRange(0, 100)
        self.bar_hold.setValue(0)
        center_vbox.addWidget(self.bar_hold)

        studio_hbox.addWidget(center_frame)

        # Right Monitor: PROGRAM
        pgm_box = QGroupBox("🔴 현재 방송 송출 화면 (OBS Studio 녹화·생방송 출력 / Program)")
        pgm_box.setStyleSheet("QGroupBox { border: 2px solid #e5252a; }")
        pgm_layout = QVBoxLayout(pgm_box)
        pgm_layout.setSpacing(6)
        self.lbl_pgm_screen = QLabel()
        self.lbl_pgm_screen.setFixedSize(455, 225)
        self.lbl_pgm_screen.setAlignment(Qt.AlignCenter)
        self.lbl_pgm_screen.setStyleSheet("background-color: #12141a; border-radius: 6px;")
        pgm_layout.addWidget(self.lbl_pgm_screen, alignment=Qt.AlignCenter)

        self.lbl_pgm_desc = QLabel("대기 중 — 상단의 [▶ 시스템 시작] 버튼을 누르면 방송 송출 화면이 표시됩니다.")
        self.lbl_pgm_desc.setWordWrap(True)
        self.lbl_pgm_desc.setAlignment(Qt.AlignCenter)
        self.lbl_pgm_desc.setStyleSheet("color: #f87171; font-size: 12px; font-weight: bold;")
        pgm_layout.addWidget(self.lbl_pgm_desc)
        studio_hbox.addWidget(pgm_box, stretch=4)

        main_vbox.addLayout(studio_hbox)

        # =====================================================================
        # 3. DECLUTTERED CAMERA 1~4 CARDS
        # =====================================================================
        cams_hbox = QHBoxLayout()
        cams_hbox.setSpacing(10)

        self.cam_cards: Dict[int, QGroupBox] = {}
        self.cam_source_combos: Dict[int, QComboBox] = {}
        self.cam_img_labels: Dict[int, QLabel] = {}
        self.cam_badge_labels: Dict[int, QLabel] = {}

        friendly_names = {
            1: "1번 카메라 (전체 풀샷)",
            2: "2번 카메라 (보컬 클로즈업)",
            3: "3번 카메라 (악기/솔로)",
            4: "4번 카메라 (무빙/측면)",
        }

        for cid in range(1, 5):
            card = QGroupBox(f"📹 {friendly_names[cid]}")
            cvbox = QVBoxLayout(card)
            cvbox.setContentsMargins(10, 10, 10, 8)
            cvbox.setSpacing(6)

            status_row = QHBoxLayout()
            lbl_src_tag = QLabel("연결 소스:")
            lbl_src_tag.setStyleSheet("color: #94a3b8; font-size: 11px;")
            status_row.addWidget(lbl_src_tag)

            lbl_badge = QLabel("⏸ 대기 중 (OFF)")
            lbl_badge.setStyleSheet("color: #94a3b8; font-weight: bold; font-size: 11.5px;")
            self.cam_badge_labels[cid] = lbl_badge
            status_row.addWidget(lbl_badge, alignment=Qt.AlignRight)
            cvbox.addLayout(status_row)

            src_row = QHBoxLayout()
            src_row.setSpacing(6)
            cmb_src = QComboBox()
            self._populate_slot_combo(cmb_src, cid)
            cmb_src.currentIndexChanged.connect(lambda _, c=cid: self._on_slot_source_combo_changed(c))
            self.cam_source_combos[cid] = cmb_src
            src_row.addWidget(cmb_src, stretch=1)

            btn_pick_file = QPushButton("📂 파일")
            btn_pick_file.setToolTip(f"{cid}번 카메라에 원하는 MP4 동영상 파일을 직접 배치합니다")
            btn_pick_file.setFixedWidth(62)
            btn_pick_file.clicked.connect(lambda _, c=cid: self._pick_custom_mp4_for_slot(c))
            src_row.addWidget(btn_pick_file)
            cvbox.addLayout(src_row)

            img_lbl = QLabel()
            img_lbl.setFixedSize(330, 168)
            img_lbl.setAlignment(Qt.AlignCenter)
            img_lbl.setStyleSheet("background-color: #12141a; border-radius: 6px;")
            cvbox.addWidget(img_lbl, alignment=Qt.AlignCenter)

            btn_direct = QPushButton(f"👆 {cid}번 카메라로 전환")
            btn_direct.clicked.connect(lambda _, c=cid: self._manual_cut(c))
            cvbox.addWidget(btn_direct)

            self.cam_cards[cid] = card
            self.cam_img_labels[cid] = img_lbl
            cams_hbox.addWidget(card)

        main_vbox.addLayout(cams_hbox)

        # =====================================================================
        # 4. CLEAN 3-SECTION BOTTOM STUDIO DOCK
        # =====================================================================
        docks_hbox = QHBoxLayout()
        docks_hbox.setSpacing(12)

        # Section 1: 카메라 대수 & 모드 프리셋
        dock_conn = QGroupBox("🔌 1. 카메라 대수 (2대·3대·4대) & 일괄 연결")
        conn_vbox = QVBoxLayout(dock_conn)
        conn_vbox.setSpacing(7)

        preset_hbox = QHBoxLayout()
        self.btn_p2 = QPushButton("📹 카메라 2대\n(1·2번 사용)")
        self.btn_p3 = QPushButton("📹 카메라 3대\n(1·2·3번 사용)")
        self.btn_p4 = QPushButton("📹 카메라 4대\n(1~4번 모두)")
        for b in (self.btn_p2, self.btn_p3, self.btn_p4):
            b.setCheckable(True)
            b.setMinimumHeight(40)
        self.btn_p4.setChecked(True)
        self.btn_p2.clicked.connect(lambda: self._apply_cam_count_preset([1, 2], self.btn_p2))
        self.btn_p3.clicked.connect(lambda: self._apply_cam_count_preset([1, 2, 3], self.btn_p3))
        self.btn_p4.clicked.connect(lambda: self._apply_cam_count_preset([1, 2, 3, 4], self.btn_p4))
        preset_hbox.addWidget(self.btn_p2)
        preset_hbox.addWidget(self.btn_p3)
        preset_hbox.addWidget(self.btn_p4)
        conn_vbox.addLayout(preset_hbox)

        batch_hbox = QHBoxLayout()
        btn_real_mp4 = QPushButton("🎸 실제 공연 1분 영상(MP4) 4채널 배치")
        btn_real_mp4.clicked.connect(self._load_real_concert_mp4_preset)
        batch_hbox.addWidget(btn_real_mp4)

        btn_atem_mv = QPushButton("🖥️ ATEM 멀티뷰 캡처보드 크롭 모드")
        btn_atem_mv.clicked.connect(self._load_atem_multiview_preset)
        batch_hbox.addWidget(btn_atem_mv)
        conn_vbox.addLayout(batch_hbox)

        ip_hbox = QHBoxLayout()
        ip_hbox.addWidget(QLabel("ATEM 스위처 IP:"))
        self.txt_ip = QLineEdit("192.168.10.240")
        ip_hbox.addWidget(self.txt_ip)
        btn_atem_conn = QPushButton("스위처 연결")
        btn_atem_conn.clicked.connect(self._connect_atem_switcher)
        ip_hbox.addWidget(btn_atem_conn)
        conn_vbox.addLayout(ip_hbox)

        docks_hbox.addWidget(dock_conn, stretch=3)

        # Section 2: AI 화면 전환 템포 (기존 프리셋 + 초 직접 조절 슬라이더 바) & ATEM 부드러운 전환 효과
        dock_style = QGroupBox("🎨 2. 화면전환 템포 (프리셋 + 초 직접 조절 바) & ATEM 부드러운 전환")
        style_vbox = QVBoxLayout(dock_style)
        style_vbox.setSpacing(6)

        # Existing 3 Tempo Preset Buttons (Preserved!)
        style_btns = QHBoxLayout()
        btn_calm = QPushButton("☕ 차분하게 (3.5~9.5초)")
        btn_std = QPushButton("🎸 표준 공연 (2.5~7.5초)")
        btn_fast = QPushButton("⚡ 역동적 (1.8~5.5초)")
        for b in (btn_calm, btn_std, btn_fast):
            b.setMinimumHeight(30)
        btn_calm.clicked.connect(lambda: self._apply_style_preset(3.5, 9.5, "차분한 전환 (3.5~9.5초 유지)"))
        btn_std.clicked.connect(lambda: self._apply_style_preset(2.5, 7.5, "표준 공연 전환 (2.5~7.5초 유지)"))
        btn_fast.clicked.connect(lambda: self._apply_style_preset(1.8, 5.5, "빠른 역동적 전환 (1.8~5.5초 유지)"))
        style_btns.addWidget(btn_calm)
        style_btns.addWidget(btn_std)
        style_btns.addWidget(btn_fast)
        style_vbox.addLayout(style_btns)

        # Direct Second Control Slider 1: Minimum Hold Seconds (0.5s ~ 10.0s)
        min_row = QHBoxLayout()
        self.lbl_min_sec = QLabel("⏱️ 최소 화면 유지: 2.5초")
        self.lbl_min_sec.setFixedWidth(145)
        self.lbl_min_sec.setStyleSheet("color: #93c5fd; font-weight: bold;")
        min_row.addWidget(self.lbl_min_sec)

        self.slider_min_hold = QSlider(Qt.Horizontal)
        self.slider_min_hold.setRange(5, 100)  # 0.5s ~ 10.0s (0.1s resolution)
        self.slider_min_hold.setValue(25)
        self.slider_min_hold.valueChanged.connect(self._on_hold_sliders_changed)
        min_row.addWidget(self.slider_min_hold, stretch=1)
        style_vbox.addLayout(min_row)

        # Direct Second Control Slider 2: Maximum Rotation Seconds (2.0s ~ 20.0s)
        max_row = QHBoxLayout()
        self.lbl_max_sec = QLabel("🔄 최대 순환 시간: 7.5초")
        self.lbl_max_sec.setFixedWidth(145)
        self.lbl_max_sec.setStyleSheet("color: #60a5fa; font-weight: bold;")
        max_row.addWidget(self.lbl_max_sec)

        self.slider_max_hold = QSlider(Qt.Horizontal)
        self.slider_max_hold.setRange(20, 200)  # 2.0s ~ 20.0s (0.1s resolution)
        self.slider_max_hold.setValue(75)
        self.slider_max_hold.valueChanged.connect(self._on_hold_sliders_changed)
        max_row.addWidget(self.slider_max_hold, stretch=1)
        style_vbox.addLayout(max_row)

        # ATEM Smooth Transition Effect Style & Duration Bar
        trans_row = QHBoxLayout()
        trans_row.addWidget(QLabel("🌊 ATEM 전환 효과:"))
        self.cmb_trans_style = QComboBox()
        self.cmb_trans_style.addItem("🤖 AI 자동 (긴급·비트=CUT / 일반=부드러운 MIX 디졸브)", "AI_AUTO")
        self.cmb_trans_style.addItem("🌊 부드러운 디졸브 겹침 전환 (ATEM MIX)", "MIX")
        self.cmb_trans_style.addItem("✨ 플래시 디프 전환 (ATEM DIP)", "DIP")
        self.cmb_trans_style.addItem("🚪 화면 밀기 와이프 전환 (ATEM WIPE)", "WIPE")
        self.cmb_trans_style.addItem("📐 3D 슬라이드 밀기 전환 (ATEM DVE)", "DVE")
        self.cmb_trans_style.addItem("⚡ 즉각 컷 전환만 사용 (ATEM CUT)", "CUT")
        self.cmb_trans_style.currentIndexChanged.connect(self._on_atem_transition_settings_changed)
        trans_row.addWidget(self.cmb_trans_style, stretch=1)
        style_vbox.addLayout(trans_row)

        rate_row = QHBoxLayout()
        self.lbl_trans_rate = QLabel("✨ 부드러운 전환 속도: 0.7초")
        self.lbl_trans_rate.setFixedWidth(165)
        self.lbl_trans_rate.setStyleSheet("color: #4ade80;")
        rate_row.addWidget(self.lbl_trans_rate)

        self.slider_trans_rate = QSlider(Qt.Horizontal)
        self.slider_trans_rate.setRange(2, 20)  # 0.2s ~ 2.0s
        self.slider_trans_rate.setValue(7)
        self.slider_trans_rate.valueChanged.connect(self._on_atem_transition_settings_changed)
        rate_row.addWidget(self.slider_trans_rate, stretch=1)

        self.chk_beat_sync = QCheckBox("🥁 비트 동기화")
        self.chk_beat_sync.setChecked(True)
        self.chk_beat_sync.stateChanged.connect(
            lambda: setattr(self.state_machine.config, "beat_sync_enabled", self.chk_beat_sync.isChecked())
        )
        rate_row.addWidget(self.chk_beat_sync)
        style_vbox.addLayout(rate_row)

        docks_hbox.addWidget(dock_style, stretch=4)

        # Section 3: 과업 채점 & 스크롤 위치 완벽 고정 알림창
        dock_eval = QGroupBox("📊 3. AI 카메라 선택 채점 테스트 & 상태 알림창")
        eval_vbox = QVBoxLayout(dock_eval)
        eval_vbox.setSpacing(6)

        btn_test = QPushButton("🎯 동일 공연 장면(2대·3대·4대) AI 카메라 선택 정확도 채점하기")
        btn_test.setStyleSheet("background-color: #e5252a; color: white; padding: 7px; font-size: 12px;")
        btn_test.clicked.connect(self._run_friendly_benchmark)
        eval_vbox.addWidget(btn_test)

        self.txt_friendly_log = QTextEdit()
        self.txt_friendly_log.setReadOnly(True)
        self.txt_friendly_log.setLineWrapMode(QTextEdit.WidgetWidth)
        set_text_at_top(
            self.txt_friendly_log,
            "⏸ [대기 모드 (OFF)] 현재 모든 영상 재생이 멈춰 있는 깔끔한 대기 상태입니다.\n"
            "• 상단의 초록색 [▶ 시스템 시작 (영상 수신 & AI 켜기)] 버튼을 누르시면 실제 공연 1분 멀티캠 영상(또는 연결된 장비) 분석이 시작됩니다.\n"
            "• 좌측 패널의 [최소 화면 유지 / 최대 순환 시간] 슬라이더 바를 움직여 원하는 초(0.1초 단위)로 템포를 자유롭게 조절해 보세요.\n"
            "• ATEM 전환 효과를 [부드러운 디졸브(MIX)], [화면 밀기(WIPE)], [3D 슬라이드(DVE)] 또는 [PiP]로 설정하면 ATEM 본체 명령과 함께 방송 화면에도 부드러운 전환 효과가 실시간 적용됩니다.",
        )
        eval_vbox.addWidget(self.txt_friendly_log)

        docks_hbox.addWidget(dock_eval, stretch=4)
        main_vbox.addLayout(docks_hbox)

    # =========================================================================
    # Standby Screen & Master Power Control
    # =========================================================================

    def _render_standby_screens(self) -> None:
        """Draws calm dark Standby screens on all monitors when system_running is False."""
        self.lbl_pvw_screen.setPixmap(
            make_standby_pixmap(455, 225, "PREVIEW - STANDBY (OFF)", "Click [START SYSTEM] on top left to begin")
        )
        self.lbl_pgm_screen.setPixmap(
            make_standby_pixmap(455, 225, "PROGRAM - STANDBY (OFF)", "No video is playing in Standby Mode")
        )
        self.lbl_pvw_desc.setText("대기 모드 — [▶ 시스템 시작]을 누르면 다음 추천 카메라가 표시됩니다.")
        self.lbl_pgm_desc.setText("대기 모드 — [▶ 시스템 시작]을 누르면 방송 송출 화면이 표시됩니다.")
        self.lbl_hold_friendly.setText("⏱️ 화면 유지 타이머 (현재 대기 중)")
        self.bar_hold.setValue(0)

        for cid in range(1, 5):
            enabled = cid in self.active_channels
            sub = "Ready (Click Start to Play)" if enabled else "Disabled Port (OFF)"
            self.cam_img_labels[cid].setPixmap(
                make_standby_pixmap(330, 168, f"CAM {cid} - STANDBY", sub)
            )
            self.cam_badge_labels[cid].setText("⏸ 대기 중" if enabled else "🔌 꺼짐")
            self.cam_badge_labels[cid].setStyleSheet("color: #94a3b8; font-weight: bold;")
            self.cam_cards[cid].setStyleSheet("QGroupBox { border: 1px solid #323746; }")

    def _on_toggle_master_power(self) -> None:
        self.system_running = self.btn_power.isChecked()
        if self.system_running:
            self.btn_power.setText("⏹ 시스템 정지 (모든 영상 끄기 / 대기 모드)")
            self.lbl_master_status.setText(
                "🟢 [실시간 가동 중 (ON)] 카메라 영상을 실시간 분석하여 가장 좋은 화면으로 자동 전환하고 있습니다."
            )
            self.lbl_master_status.setStyleSheet("color: #4ade80; font-weight: bold; font-size: 12.5px;")
            self.timer.start(33)
            append_log_preserving_scroll(
                self.txt_friendly_log, "▶ 시스템을 시작했습니다! 카메라 영상 수신 및 AI 자동 스위칭이 작동합니다."
            )
        else:
            self.timer.stop()
            self.btn_power.setText("▶ 시스템 시작 (영상 수신 & AI 켜기)")
            self.lbl_master_status.setText(
                "⏸ 현재 [대기 모드(OFF)]입니다. 모든 영상 재생이 정지되었으며 [▶ 시스템 시작]을 누르면 다시 작동합니다."
            )
            self.lbl_master_status.setStyleSheet("color: #cbd5e1; font-size: 12.5px;")
            self._render_standby_screens()
            append_log_preserving_scroll(
                self.txt_friendly_log, "⏹ 시스템을 정지하여 깔끔한 대기 모드(OFF)로 전환했습니다."
            )

    def _refresh_pip_button_badge(self) -> None:
        missing = DependencyManager.get_missing_packages()
        if not missing:
            self.btn_pip.setText("✅ 필수 모듈(PyATEMMax) 준비 완료")
            self.btn_pip.setStyleSheet("background-color: #1e3a8a; border: 1px solid #3b82f6; color: #93c5fd;")
        else:
            names = ", ".join(m.pip_name for m in missing)
            self.btn_pip.setText(f"📦 필수 모듈({names}) 원클릭 설치하기")
            self.btn_pip.setStyleSheet("background-color: #d97706; border: 1px solid #fbbf24; color: white;")

    def _open_pip_installer(self) -> None:
        dlg = DependencyInstallDialog(self)
        dlg.exec_()
        self._refresh_pip_button_badge()

    def _open_verification_wizard(self) -> None:
        dlg = ATEMVerificationDialog(self.ingestor, self.active_channels, self)
        dlg.exec_()

    # =========================================================================
    # Direct Second Sliders & ATEM Smooth Transition / Hardware Controls
    # =========================================================================

    def _on_hold_sliders_changed(self) -> None:
        min_sec = round(self.slider_min_hold.value() / 10.0, 1)
        max_sec = round(self.slider_max_hold.value() / 10.0, 1)
        # Ensure max_hold_sec is always at least min_hold_sec + 0.5s
        if max_sec < min_sec + 0.5:
            max_sec = round(min_sec + 0.5, 1)
            self.slider_max_hold.blockSignals(True)
            self.slider_max_hold.setValue(int(round(max_sec * 10)))
            self.slider_max_hold.blockSignals(False)

        self.state_machine.config.min_hold_sec = min_sec
        self.state_machine.config.max_hold_sec = max_sec
        self.lbl_min_sec.setText(f"⏱️ 최소 화면 유지: {min_sec:.1f}초")
        self.lbl_max_sec.setText(f"🔄 최대 순환 시간: {max_sec:.1f}초")

    def _apply_style_preset(self, min_h: float, max_h: float, desc: str) -> None:
        self.slider_min_hold.blockSignals(True)
        self.slider_max_hold.blockSignals(True)
        self.slider_min_hold.setValue(int(round(min_h * 10)))
        self.slider_max_hold.setValue(int(round(max_h * 10)))
        self.slider_min_hold.blockSignals(False)
        self.slider_max_hold.blockSignals(False)
        self._on_hold_sliders_changed()
        append_log_preserving_scroll(self.txt_friendly_log, f"🎨 AI 전환 템포 프리셋 적용: {desc}")

    def _on_atem_transition_settings_changed(self) -> None:
        style_code = str(self.cmb_trans_style.currentData() or "AI_AUTO")
        dur_sec = round(self.slider_trans_rate.value() / 10.0, 1)
        self.lbl_trans_rate.setText(f"✨ 부드러운 전환 속도: {dur_sec:.1f}초")
        self.state_machine.config.transition_mode = style_code
        self.state_machine.config.transition_duration_sec = dur_sec
        self.atem.set_transition_style_and_rate(style_code, dur_sec)
        append_log_preserving_scroll(
            self.txt_friendly_log,
            f"🌊 ATEM 전환 효과 설정: [{style_code}] (속도 {dur_sec:.1f}초 / {int(dur_sec * 30)}프레임)",
        )

    def _on_toggle_atem_pip(self) -> None:
        enabled = self.btn_atem_pip.isChecked()
        self.atem.set_pip_enabled(enabled, fill_cam=self.state_machine.preview_cam)
        append_log_preserving_scroll(
            self.txt_friendly_log,
            f"🖼️ ATEM 화면 속 작은 화면(PiP): {'켜짐(ON) — 우측 하단에 대기 카메라 동시 표시' if enabled else '꺼짐(OFF)'}",
        )

    def _on_toggle_atem_ftb(self) -> None:
        self.atem.toggle_fade_to_black(self.state_machine.config.transition_duration_sec)
        self.btn_atem_ftb.setChecked(self.atem.ftb_active)
        append_log_preserving_scroll(
            self.txt_friendly_log,
            f"🌑 ATEM 부드러운 암전(Fade-to-Black): {'작동 중(암전)' if self.atem.ftb_active else '해제됨(정상 방송)'}",
        )

    def _on_toggle_atem_afv(self) -> None:
        enabled = self.btn_atem_afv.isChecked()
        self.atem.set_afv_enabled(enabled)
        append_log_preserving_scroll(
            self.txt_friendly_log,
            f"🔊 ATEM 오디오 따라가기(AFV): {'켜짐(카메라 전환 시 해당 마이크 소리 연동)' if enabled else '고정 오디오 모드'}",
        )

    # =========================================================================
    # Per-Slot Source Binding & Controls
    # =========================================================================

    def _populate_slot_combo(self, cmb: QComboBox, cid: int) -> None:
        cmb.blockSignals(True)
        cmb.clear()
        real_name = Path(REAL_CONCERT_DEFAULT_FILES[cid]).name
        cmb.addItem(f"🎸 실제 공연 1분 영상 ({real_name})", ("real_mp4", REAL_CONCERT_DEFAULT_FILES[cid]))
        cmb.addItem(f"🖥️ ATEM 멀티뷰 #{cid}번 구역 크롭", ("multiview_roi", cid))
        for dev in self.discovered_devices:
            cmb.addItem(f"🎥 장비 직결: {dev.friendly_name} (#{dev.index})", ("direct_device", dev.index, dev.friendly_name))
        cmb.addItem(f"🎭 가상 공연 시뮬레이터 #{cid}번", ("synthetic", cid))
        cmb.addItem("🚫 연결 안 함 (꺼짐 / 자동 제외)", ("disabled", 0))
        cmb.blockSignals(False)

    def _on_slot_source_combo_changed(self, cid: int) -> None:
        cmb = self.cam_source_combos[cid]
        data = cmb.currentData()
        if not data:
            return
        kind = data[0]
        if kind == "real_mp4":
            b = self.ingestor.bind_camera_slot(cid, "video_file", file_path=data[1])
        elif kind == "multiview_roi":
            b = self.ingestor.bind_camera_slot(cid, "multiview_roi", multiview_slot=int(data[1]))
        elif kind == "direct_device":
            b = self.ingestor.bind_camera_slot(cid, "direct_device", device_index=int(data[1]), device_name=str(data[2]))
        elif kind == "synthetic":
            b = self.ingestor.bind_camera_slot(cid, "synthetic")
        elif kind == "custom_mp4":
            b = self.ingestor.bind_camera_slot(cid, "video_file", file_path=data[1])
        else:
            b = self.ingestor.bind_camera_slot(cid, "disabled")

        self.active_channels = sorted(list(self.ingestor.active_channels))
        if not self.system_running:
            self._render_standby_screens()
        append_log_preserving_scroll(
            self.txt_friendly_log, f"🔧 [{cid}번 카메라] 연결 소스를 [{b.display_label}](으)로 설정했습니다."
        )

    def _pick_custom_mp4_for_slot(self, cid: int) -> None:
        fpath, _ = QFileDialog.getOpenFileName(
            self,
            f"{cid}번 카메라에 배치할 동영상(MP4) 파일 선택",
            str(Path("data/real_concert").resolve()),
            "Video Files (*.mp4 *.mov *.avi *.mkv *.webm)",
        )
        if not fpath:
            return
        cmb = self.cam_source_combos[cid]
        cmb.blockSignals(True)
        cmb.insertItem(0, f"🎬 지정 파일 ({Path(fpath).name})", ("custom_mp4", fpath))
        cmb.setCurrentIndex(0)
        cmb.blockSignals(False)
        self.ingestor.bind_camera_slot(cid, "video_file", file_path=fpath)
        self.active_channels = sorted(list(self.ingestor.active_channels))
        append_log_preserving_scroll(
            self.txt_friendly_log, f"📂 [{cid}번 카메라]에 [{Path(fpath).name}] 파일을 배치했습니다."
        )

    def _load_real_concert_mp4_preset(self) -> None:
        prepare_real_concert_4cam_mp4s("data/real_concert")
        self.ingestor.set_mode("real_concert_mp4")
        for cid in range(1, 5):
            cmb = self.cam_source_combos[cid]
            cmb.blockSignals(True)
            cmb.setCurrentIndex(0 if cid in self.active_channels else cmb.count() - 1)
            cmb.blockSignals(False)
        append_log_preserving_scroll(
            self.txt_friendly_log, "🎸 실제 공연 1분 멀티캠(cam1~4_real_*.mp4) 4개 파일을 각 카메라에 배치했습니다."
        )

    def _load_atem_multiview_preset(self) -> None:
        self.ingestor.set_mode("multiview_crop")
        for cid in range(1, 5):
            cmb = self.cam_source_combos[cid]
            cmb.blockSignals(True)
            cmb.setCurrentIndex(1 if cid in self.active_channels else cmb.count() - 1)
            cmb.blockSignals(False)
        append_log_preserving_scroll(
            self.txt_friendly_log, "🖥️ ATEM 10분할 멀티뷰 자동 크롭 모드로 설정했습니다."
        )

    def _apply_cam_count_preset(self, active_cams: List[int], clicked_btn: QPushButton) -> None:
        for b in (self.btn_p2, self.btn_p3, self.btn_p4):
            b.setChecked(b is clicked_btn)
        self.active_channels = active_cams
        self.ingestor.set_active_channels(self.active_channels)
        for cid in range(1, 5):
            cmb = self.cam_source_combos[cid]
            cmb.blockSignals(True)
            if cid not in active_cams:
                cmb.setCurrentIndex(cmb.count() - 1)
            elif cmb.currentIndex() == cmb.count() - 1:
                cmb.setCurrentIndex(0)
            cmb.blockSignals(False)
        if not self.system_running:
            self._render_standby_screens()
        append_log_preserving_scroll(
            self.txt_friendly_log, f"📹 카메라 대수 변경: 총 {len(active_cams)}대 ({active_cams}) 카메라만 사용합니다."
        )

    def _on_toggle_ai_director(self) -> None:
        enabled = self.btn_ai_toggle.isChecked()
        self.state_machine.auto_director_enabled = enabled
        self.btn_ai_toggle.setText("🤖 AI 자동 화면전환 [켜짐 ON]" if enabled else "✋ 수동 전환 모드 [AI 꺼짐]")

    def _manual_cut(self, target_cam: int, force_style: Optional[str] = None) -> None:
        prev_cam = self.state_machine.program_cam
        t = (self.ingestor.frame_idx % 1200) / float(FPS)
        if force_style == "AUTO":
            cfg_mode = self.state_machine.config.transition_mode
            eff_style = "MIX" if cfg_mode in ("AI_AUTO", "CUT") else cfg_mode
        elif force_style == "CUT":
            eff_style = "CUT"
        else:
            cfg_mode = self.state_machine.config.transition_mode
            eff_style = "MIX" if cfg_mode == "AI_AUTO" else cfg_mode

        dec = self.state_machine.manual_cut(target_cam, t, style=eff_style)
        self.atem.sync_switcher_bus(
            dec.program_cam,
            dec.preview_cam,
            switched=True,
            reason="사용자 수동 전환",
            effective_style=eff_style,
        )
        if prev_cam != dec.program_cam and eff_style != "CUT":
            self.trans_from_cam = prev_cam
            self.trans_style = eff_style
            self.trans_start_wall = time.perf_counter()

        append_log_preserving_scroll(
            self.txt_friendly_log,
            f"👆 [{target_cam}번 카메라]로 화면을 전환했습니다 (효과: {eff_style}).",
        )

    def _connect_atem_switcher(self) -> None:
        ip = self.txt_ip.text().strip()
        ok = self.atem.connect_hardware(ip)
        if ok:
            append_log_preserving_scroll(
                self.txt_friendly_log, f"🎉 실제 ATEM Mini Pro 스위처({ip})에 연결되었습니다!"
            )
        else:
            append_log_preserving_scroll(
                self.txt_friendly_log,
                f"ℹ️ 현재 네트워크에서 실제 ATEM({ip}) 응답이 없어 가상 스위처(Mock) 모드로 안전하게 동작합니다.",
            )

    def _run_friendly_benchmark(self) -> None:
        append_log_preserving_scroll(
            self.txt_friendly_log, "\n[🎯 동일 공연 장면(2대·3대·4대) AI 카메라 선택 정확도 채점 결과]"
        )
        for label, chans in [
            ("2대 연결(1·2번)", [1, 2]),
            ("3대 연결(1·2·3번)", [1, 2, 3]),
            ("4대 연결(1~4번)", [1, 2, 3, 4]),
        ]:
            rep = run_full_scene_benchmark(
                vision_cfg=self.scorer.config,
                rule_cfg=self.state_machine.config,
                mode="synthetic",
                active_channels=chans,
            )
            m = rep["metrics"]
            append_log_preserving_scroll(
                self.txt_friendly_log,
                f"• {label}: 베스트 샷 정확도 {m['gt_alignment_accuracy_pct']}% | "
                f"흔들림/무신호 차단율 {m['blur_avoidance_rate_pct']}% | 평균 {m['mean_shot_duration_sec']}초 유지",
            )

    def _translate_reason_to_friendly_korean(self, state: str, pgm: int, dur: float, style: str) -> str:
        if state == "HOLD_LOCKED":
            return f"현재 {pgm}번 카메라 방송 중 ({dur:.1f}초째 유지 — 깜빡임 방지 보호 중 | 효과: {style})"
        if state == "BEAT_ARMED":
            return f"다음 베스트 카메라 준비 완료! 음악 박자에 맞춰 전환합니다"
        if state == "FORCED_ROTATION":
            return f"최대 순환 시간 도달 ➔ {pgm}번 카메라로 부드럽게 순환 ({style})"
        if state == "EMERGENCY_CUT":
            return f"흔들림 감지 ➔ 선명한 {pgm}번 카메라로 즉각 보호 전환 (CUT)"
        return f"현재 {pgm}번 카메라가 가장 구도와 움직임이 좋아 방송 송출 중 ({dur:.1f}초째)"

    def _tick_frame(self) -> None:
        if not self.system_running:
            return

        t0 = time.perf_counter()
        t = (self.ingestor.frame_idx % 1200) / float(FPS)
        prev_pgm = self.state_machine.program_cam
        channels, meta, _ = self.ingestor.read_next()
        audio_feat = self.audio.analyze(t, meta)
        metrics = self.scorer.score_all_channels(
            channels,
            audio_bonuses=audio_feat.camera_audio_bonuses,
            active_channels=self.active_channels,
        )
        decision = self.state_machine.step(
            t,
            metrics,
            is_beat_peak=audio_feat.is_beat_peak,
            active_channels=self.active_channels,
        )
        self.atem.sync_switcher_bus(
            program_cam=decision.program_cam,
            preview_cam=decision.preview_cam,
            switched=decision.switched,
            reason=decision.switch_reason,
            effective_style=decision.recommended_transition,
        )

        if decision.switched and prev_pgm != decision.program_cam:
            self.trans_from_cam = prev_pgm
            self.trans_style = decision.recommended_transition
            self.trans_start_wall = time.perf_counter()

        # Smooth FTB alpha update
        target_ftb = 1.0 if self.atem.ftb_active else 0.0
        self.ftb_visual_alpha += (target_ftb - self.ftb_visual_alpha) * 0.18

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.lbl_master_status.setText(
            f"🟢 [실시간 가동 중 ({elapsed_ms:.1f}ms)] 활성 카메라 {decision.active_cam_count}대 중 "
            f"{decision.program_cam}번 카메라 송출 중 (ATEM 효과: {self.atem.last_executed_style})"
        )

        hud_frames: Dict[int, np.ndarray] = {}
        for cid in range(1, 5):
            m = metrics[cid]
            hud = draw_friendly_camera_hud(
                channels[cid],
                m,
                is_program=(cid == decision.program_cam),
                is_preview=(cid == decision.preview_cam),
                solo_active=(audio_feat.solo_cam_id == cid),
            )
            hud_frames[cid] = hud
            self.cam_img_labels[cid].setPixmap(bgr_to_qpixmap(hud, 330, 168))

            if not m.enabled or not m.has_signal:
                self.cam_badge_labels[cid].setText("🔌 꺼짐 (자동 제외)")
                self.cam_badge_labels[cid].setStyleSheet("color: #64748b;")
                self.cam_cards[cid].setStyleSheet("QGroupBox { border: 1px dashed #323746; }")
            else:
                score_100 = int(round(m.composite_score * 100))
                if m.is_blurry:
                    self.cam_badge_labels[cid].setText("⚠️ 흔들림 제외")
                    self.cam_badge_labels[cid].setStyleSheet("color: #f87171; font-weight: bold;")
                elif score_100 >= 65:
                    self.cam_badge_labels[cid].setText(f"✨ 베스트 ({score_100}점)")
                    self.cam_badge_labels[cid].setStyleSheet("color: #4ade80; font-weight: bold;")
                else:
                    self.cam_badge_labels[cid].setText(f"👌 양호 ({score_100}점)")
                    self.cam_badge_labels[cid].setStyleSheet("color: #93c5fd; font-weight: bold;")

                if cid == decision.program_cam:
                    self.cam_cards[cid].setStyleSheet("QGroupBox { border: 2px solid #e5252a; }")
                elif cid == decision.preview_cam:
                    self.cam_cards[cid].setStyleSheet("QGroupBox { border: 2px solid #22c55e; }")
                else:
                    self.cam_cards[cid].setStyleSheet("QGroupBox { border: 1px solid #323746; }")

        # Render Preview Monitor
        self.lbl_pvw_screen.setPixmap(bgr_to_qpixmap(hud_frames[decision.preview_cam], 455, 225))

        # Render Program Monitor with Smooth ATEM Transition (MIX / DIP / WIPE / DVE), PiP, and FTB
        dur_sec = max(0.15, self.state_machine.config.transition_duration_sec)
        trans_progress = (
            (time.perf_counter() - self.trans_start_wall) / dur_sec
            if self.trans_from_cam is not None
            else 1.0
        )
        from_frame = hud_frames.get(self.trans_from_cam) if self.trans_from_cam is not None else None
        pgm_composite = render_atem_program_composite(
            from_bgr=from_frame,
            to_bgr=hud_frames[decision.program_cam],
            style=self.trans_style,
            progress=trans_progress,
            pip_enabled=self.atem.pip_enabled,
            pip_bgr=channels.get(decision.preview_cam),
            ftb_alpha=self.ftb_visual_alpha,
        )
        self.lbl_pgm_screen.setPixmap(bgr_to_qpixmap(pgm_composite, 455, 225))

        self.lbl_pvw_desc.setText(f"🟢 다음 대기 화면: {decision.preview_cam}번 카메라")
        friendly_pgm_msg = self._translate_reason_to_friendly_korean(
            decision.state,
            decision.program_cam,
            decision.current_shot_duration,
            self.atem.last_executed_style,
        )
        self.lbl_pgm_desc.setText(f"🔴 {friendly_pgm_msg}")

        max_h = max(1.0, self.state_machine.config.max_hold_sec)
        pct = min(100, int((decision.current_shot_duration / max_h) * 100))
        self.bar_hold.setValue(pct)
        self.lbl_hold_friendly.setText(
            f"⏱️ 유지: {decision.current_shot_duration:.1f}초 (최소 {self.state_machine.config.min_hold_sec:.1f}s ~ 최대 {max_h:.1f}s)"
        )

        if decision.switched:
            append_log_preserving_scroll(
                self.txt_friendly_log,
                f"🎬 [{t:04.1f}초] AI가 [{decision.program_cam}번 카메라]로 전환했습니다 (ATEM 효과: {decision.recommended_transition}).",
            )


def main() -> None:
    app = QApplication(sys.argv)
    win = OBSDirectorWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
