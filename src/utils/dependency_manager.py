"""
Dependency & Pip Package Manager (`src/utils/dependency_manager.py`).

Checks whether required packages (especially `PyATEMMax` for real ATEM Mini Pro UDP control,
`opencv-python`, `numpy`, `PyQt5`, and `yt-dlp`) are installed, and provides one-click
automatic `pip install` execution from inside the GUI.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple


REQUIRED_PACKAGES: List[Tuple[str, str, str]] = [
    ("PyATEMMax", "PyATEMMax", "실제 ATEM Mini Pro 하드웨어 스위처(랜선 UDP) 제어 통신 모듈"),
    ("opencv-python", "cv2", "다중 카메라 실시간 영상 크롭 및 구도·블러 분석 엔진"),
    ("numpy", "numpy", "고속 행렬 및 프레임 메모리 연산 라이브러리"),
    ("PyQt5", "PyQt5", "네이티브 윈도우 데스크톱 스튜디오 GUI 프레임워크"),
    ("yt-dlp", "yt_dlp", "인터넷 실제 공연 멀티캠 테스트 영상 다운로더"),
]


@dataclass
class PackageStatus:
    pip_name: str
    import_name: str
    description_kr: str
    installed: bool
    version: str

    def to_dict(self) -> Dict:
        return asdict(self)


class DependencyManager:
    """Inspects and installs required Python packages without leaving the desktop application."""

    @classmethod
    def check_all_packages(cls) -> List[PackageStatus]:
        importlib.invalidate_caches()
        results: List[PackageStatus] = []

        for pip_name, mod_name, desc in REQUIRED_PACKAGES:
            installed = False
            version = "미설치"
            try:
                mod = importlib.import_module(mod_name)
                installed = True
                try:
                    version = importlib.metadata.version(pip_name)
                except Exception:
                    version = getattr(mod, "__version__", "설치됨")
            except Exception:
                installed = False

            results.append(
                PackageStatus(
                    pip_name=pip_name,
                    import_name=mod_name,
                    description_kr=desc,
                    installed=installed,
                    version=str(version),
                )
            )
        return results

    @classmethod
    def get_missing_packages(cls) -> List[PackageStatus]:
        return [p for p in cls.check_all_packages() if not p.installed]

    @classmethod
    def install_packages(cls, pip_names: List[str]) -> Tuple[bool, str]:
        """Runs `python -m pip install <pip_names>` and returns (success, log_output)."""
        if not pip_names:
            return True, "설치할 누락 패키지가 없습니다. 모든 필수 모듈이 이미 준비되어 있습니다."

        cmd = [sys.executable, "-m", "pip", "install", *pip_names]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90.0,
            )
            importlib.invalidate_caches()
            output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            ok = proc.returncode == 0
            return ok, output.strip()
        except Exception as exc:
            return False, f"pip 설치 실행 중 오류 발생: {exc}"
