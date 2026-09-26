# Implementation Plan — 스크롤바 정상화, 초 단위 템포 조절 바, ATEM 부드러운 화면전환(PyATEMMax) 및 JEV 결정 확률 도입

사용자가 요청하신 4가지 핵심 업데이트 사항을 설계 및 반영하는 실행 계획서입니다.

---

## 1. 목표 및 배경 (Goal Description)

1. **스크롤바 최상단 이상 작동(점프/숨김 현상) 완벽 수정**
   - **원인 분석**:
     1) PyQt5 다크 테마 스타일시트(`QWidget { background-color: ... }`) 적용 시 `QScrollBar::add-line:vertical` / `sub-line:vertical` 영역(`height: 0px; margin: 0px;`)과 `QScrollBar::handle:vertical`이 명시되지 않으면, Windows 기본 스크롤바 렌더러가 상/하단 화살표 여백(subcontrol) 계산을 오작동하여 스크롤바를 맨 위로 올렸을 때 핸들이 아래/위로 쏙 들어갔다 나오는 그래픽/좌표 왜곡이 발생합니다.
     2) 또한 `QTextEdit.append()` 호출 시 Qt 내부적으로 커서를 문서 맨 끝(`QTextCursor.End`)으로 강제 이동시킨 뒤 `ensureCursorVisible()`을 호출하므로, 사용자가 스크롤을 맨 위(`value == 0`)에 두고 있어도 실시간 로그가 추가될 때마다 스크롤바가 맨 아래로 끌려 내려갔다 다시 튀어 오르는 현상이 발생합니다.
   - **해결 방안**:
     - 모든 `QScrollBar:vertical` 및 `QScrollBar:horizontal`에 대해 `sub-line`, `add-line` 높이를 `0px`로 제거하고 `subcontrol-origin: margin`을 명시한 정식 다크모드 스크롤바 CSS를 적용합니다.
     - 메인 윈도우 전체를 `QScrollArea`로 감싸고 초기 위치를 항상 `verticalScrollBar().setValue(0)`(페이지 최상단)에 고정합니다.
     - 모든 `QTextEdit` 및 다이얼로그(`ConnectionGuideDialog`, `ATEMVerificationDialog`, `DependencyInstallDialog`, `txt_friendly_log`)에서 초기 텍스트 설정 시 `QTextCursor.Start` 및 `setValue(0)`을 강제하며, 실시간 로그 추가 시 **사용자의 현재 스크롤 위치를 보존하는 `_append_log_safe()`** 헬퍼를 적용하여 스크롤이 맨 위에 있을 때 화면도 정확히 맨 위에 고정되도록 수정합니다.

2. **화면전환 템포 설정: 기존 프리셋 버튼 유지 + 초(0.1초 단위) 직접 컨트롤 슬라이더 바 추가**
   - 기존의 3가지 프리셋 버튼(`☕ 차분하게 3.5~9.5초`, `🎸 표준 공연 2.5~7.5초`, `⚡ 역동적 1.8~5.5초`)을 그대로 유지합니다.
   - 바로 아래에 사용자가 마우스로 직접 끌어 **0.1초 단위**로 정밀 제어할 수 있는 2개의 슬라이더 바(`QSlider`)를 추가합니다:
     - **최소 유지 시간 바 (`min_hold_sec`)**: `0.5초 ~ 10.0초` (화면 깜빡임 방지 보호 시간)
     - **최대 순환 시간 바 (`max_hold_sec`)**: `2.0초 ~ 20.0초` (한 카메라 장기 고정 방지 자동 순환 시간)
   - 프리셋 버튼을 누르면 슬라이더 바가 해당 초 위치로 자동 이동하며, 슬라이더 바를 직접 움직이면 즉시 AI 엔진에 반영됩니다.

3. **ATEM Mini Pro 부드러운 장면 전환(`Mix` 디졸브, `Dip`, `Wipe`, `DVE`) 및 부가 기능(`PiP`, `FTB`, `AFV`) 자체 제어 (`PyATEMMax` + 실시간 화면 효과 합성)**
   - 현재 채택된 `PyATEMMax` 라이브러리의 하드웨어 프로토콜 명령어를 `ATEMController`([controller.py](file:///c:/Users/ddosu/antigravity_workspace/first/src/atem/controller.py))에 완전히 연결합니다:
     - **전환 스타일 선택**:
       - `CUT` (즉각 컷 전환: `execCutME(0)`)
       - `MIX` (부드러운 디졸브 겹침 전환: `setTransitionStyle(0, "mix")` + `setTransitionMixRate(0, frames)` + `execAutoME(0)`)
       - `DIP` (플래시 경유 부드러운 전환: `setTransitionStyle(0, "dip")` + `setTransitionDipRate(0, frames)` + `execAutoME(0)`)
       - `WIPE` (부드러운 경계 밀기 전환: `setTransitionStyle(0, "wipe")` + `setTransitionWipeRate(0, frames)` + `execAutoME(0)`)
       - `DVE` (화면 푸시/슬라이드 전환: `setTransitionStyle(0, "dVE")` + `setTransitionDVERate(0, frames)` + `execAutoME(0)`)
       - `AI 자동 선택 (JEV 적응형)`: 긴급 블러/강한 드럼 비트에서는 `CUT`, 완만한 점수 교차 구간에서는 부드러운 `MIX 디졸브`를 AI가 자동 선택
     - **전환 속도 조절 바 (`0.2초 ~ 2.0초`)**: 디졸브/와이프가 진행되는 시간(프레임 수)을 조절하며, 로컬 프로그램의 `방송 송출 화면(Program)` 모니터에서도 동일하게 30fps 부드러운 오버랩(Cross-Dissolve / Wipe / Slide) 애니메이션이 실시간 렌더링됩니다.
     - **ATEM 하드웨어 부가 기능 원클릭 토글**:
       - **`🖼️ PiP (화면 속 작은 화면)`**: `setKeyerType(0, 0, "dVE")` + `setKeyerFillSource` + `setKeyerOnAirEnabled(0, 0, True/False)` (메인 방송 화면 우측 하단에 다음 대기 카메라를 작은 창으로 동시 표시)
       - **`🌑 FTB (부드러운 암전 페이드)`**: `execFadeToBlackME(0)` (공연 시작/종료 시 화면을 부드럽게 암전/복귀)
       - **`🔊 AFV (카메라 전환 시 오디오 따라가기)`**: `setAudioMixerInputMixOption(cam, "afv" / "on")`

4. **JEV (Joint Extreme Value / Joint Entropy-Variance) 결정 확률 엔진 도입**
   - 기존에는 점수 차이가 고정 임계값(`0.09`)을 넘는지 여부만 따지는 이분법적(0% 아니면 100%) 구조였습니다.
   - 여기에 **JEV 결정 확률 모델(`JEVDecisionEngine`)**을 도입합니다:
     - 각 카메라의 종합 점수 $S_k$, 시간 경과에 따른 유지 보너스 $\mu_{\text{hold}}(t)$, 그리고 단기 점수 분산(흔들림/불안정도) $\sigma_k^2$을 결합하여 **JEV 카메라 선택 확률**을 산출합니다:
       $$P_{\text{JEV}}(\text{Cam } k) = \frac{\exp\left(\frac{U_k}{\tau \cdot (1 + \alpha \sigma_k^2)}\right)}{\sum_{j \in \mathcal{A}} \exp\left(\frac{U_j}{\tau \cdot (1 + \alpha \sigma_j^2)}\right)}$$
     - 분포의 **결합 엔트로피(Joint Entropy, $H_{\text{JEV}}$)**와 결합하여 최종 전환 결정 확률 $P_{\text{decision}} \in [0\%, 100\%]$을 실시간 계산합니다.
     - $P_{\text{decision}} \ge 80\%$ (확실한 우위 또는 비트 피크)일 때는 즉각적인 **`CUT`**, $55\% \le P_{\text{decision}} < 80\%$ (자연스러운 앵글 교차)일 때는 부드러운 **`MIX 디졸브`**를 자동 트리거합니다.

---

## 2. 컴포넌트별 변경 계획 (Proposed Changes)

### Component 1: ATEM 하드웨어 & 소프트웨어 전환 컨트롤러 확장
#### [MODIFY] [controller.py](file:///c:/Users/ddosu/antigravity_workspace/first/src/atem/controller.py)
- `ATEMController`에 다음 하드웨어/Mock 통합 제어 메서드를 추가합니다:
  - `set_transition_style_and_rate(style: str, duration_sec: float)`:
    - `style`: `"CUT" | "MIX" | "DIP" | "WIPE" | "DVE" | "AI_ADAPTIVE"`
    - `PyATEMMax` 연결 시 `setTransitionStyle(0, ...)`, `setTransitionMixRate(0, frames)`, `setTransitionDipRate(0, frames)`, `setTransitionWipeRate(0, frames)`, `setTransitionDVERate(0, frames)` 즉시 전송.
  - `sync_switcher_bus(..., effective_style: Optional[str] = None)`:
    - 전환 발생 시 `effective_style == "CUT"`이면 `setPreviewInputVideoSource(0, program_cam)` + `execCutME(0)` (또는 `setProgramInputVideoSource`), `"MIX" / "DIP" / "WIPE" / "DVE"`이면 `setPreviewInputVideoSource(0, program_cam)` 설정 후 `execAutoME(0)`을 호출하여 ATEM Mini Pro가 실제 부드러운 전환을 수행하도록 제어합니다.
  - `set_pip_enabled(enabled: bool, fill_cam: int)`: Upstream Keyer 0 DVE PiP 활성화/비활성화.
  - `toggle_fade_to_black(duration_sec: float = 1.0)`: `execFadeToBlackME(0)` 실행.
  - `set_afv_enabled(enabled: bool)`: 카메라 1~4번 오디오 믹서 `afv` / `on` 전환.

---

### Component 2: JEV 결정 확률 및 적응형 전환 스타일 엔진
#### [MODIFY] [state_machine.py](file:///c:/Users/ddosu/antigravity_workspace/first/src/engine/state_machine.py)
- `RuleEngineConfig` 확장:
  - `jev_enabled: bool = True`
  - `jev_temperature: float = 0.14` (JEV 확률 민감도 $\tau$)
  - `jev_cut_prob_threshold: float = 0.78` ($P_{\text{decision}} \ge 0.78 \implies \text{CUT}$)
  - `jev_mix_prob_threshold: float = 0.54` ($0.54 \le P_{\text{decision}} < 0.78 \implies \text{MIX Dissolve}$)
  - `transition_mode: str = "AI_ADAPTIVE"` (`"AI_ADAPTIVE" | "CUT" | "MIX" | "DIP" | "WIPE" | "DVE"`)
- `SwitchDecision` 확장:
  - `jev_probabilities: Dict[int, float]` (카메라별 JEV 선택 확률 `0.0 ~ 1.0`)
  - `jev_decision_confidence: float` (최종 전환 결정 확률 `0.0 ~ 1.0`)
  - `jev_entropy: float` (장면 불확실성 엔트로피)
  - `recommended_transition: str` (`"CUT"` 또는 `"MIX"` 등 이번 전환에 사용된 효과)

---

### Component 3: 데스크톱 UI (스크롤바 정상화, 초 단위 슬라이더, ATEM 전환/부가기능 패널, 로컬 부드러운 전환 렌더링)
#### [MODIFY] [desktop_app.py](file:///c:/Users/ddosu/antigravity_workspace/first/src/desktop_app.py)
1. **스크롤바 완벽 정상화**:
   - 글로벌 스타일시트에 `QScrollBar:vertical`, `QScrollBar::handle:vertical`, `QScrollBar::add-line:vertical`, `QScrollBar::sub-line:vertical` 정밀 정의 추가 (`height: 0px`, `margin: 0px`).
   - 메인 윈도우 중앙 위젯에 `QScrollArea` 적용 및 초기 `verticalScrollBar().setValue(0)` 고정.
   - `_append_log_safe(text_edit, msg)` 구현: 사용자가 스크롤을 맨 위(`value == 0`)나 중간에 두고 읽고 있을 때는 `QTextCursor`를 움직이지 않고 문서 끝에 텍스트만 조용히 추가한 뒤 `scrollbar.setValue(prev_val)`로 스크롤 위치를 완벽히 유지합니다.
   - 모든 다이얼로그(`ConnectionGuideDialog`, `ATEMVerificationDialog`, `DependencyInstallDialog`)가 열릴 때 `QTextCursor.Start` 및 `verticalScrollBar().setValue(0)`으로 무조건 페이지 최상단에서 시작하도록 수정합니다.
2. **초 단위 직접 컨트롤 바 (`min_hold_sec` & `max_hold_sec` 슬라이더)**:
   - `🎨 2. AI 화면 전환 템포 & JEV 확률 설정` 섹션에 기존 3개 프리셋 버튼 아래로:
     - `최소 유지 시간(초)` 슬라이더 바 (`0.5초 ~ 10.0초`, 0.1초 단위 실시간 표시)
     - `최대 순환 시간(초)` 슬라이더 바 (`2.0초 ~ 20.0초`, 0.1초 단위 실시간 표시)
     - `JEV 결정 확률 민감도` 슬라이더 바 및 체크박스 추가.
3. **ATEM 부드러운 화면전환 & 특수 기능 컨트롤 패널 + 로컬 30fps 트랜지션 렌더러**:
   - 전환 효과 선택 버튼/콤보(`⚡ 즉각 컷(CUT)`, `🌊 부드러운 디졸브(MIX)`, `✨ 플래시 디프(DIP)`, `🚪 화면 밀기(WIPE)`, `📐 3D 슬라이드(DVE)`, `🧠 AI JEV 자동(상황별 CUT/MIX)`).
   - 전환 효과 지속 시간 슬라이더(`0.2초 ~ 2.0초`).
   - `🖼️ PiP (화면 속 작은 화면 켜기/끄기)`, `🌑 FTB (부드러운 암전)`, `🔊 AFV (오디오 연동)` 버튼 추가.
   - 로컬 `방송 송출 화면(Program)` 모니터에서 전환 발생 시 이전 카메라 프레임과 새 카메라 프레임을 `transition_duration_sec` 동안 실제로 블렌딩(`MIX` 오버랩, `DIP` 화이트/블랙 디프, `WIPE` 경계 이동, `DVE` 푸시 슬라이드)하고 `PiP` 오버레이 및 `FTB` 페이드를 그려주어 눈으로 즉시 확인할 수 있게 합니다.

---

## 3. 검증 계획 (Verification Plan)

### Automated Tests
- `tests/test_jev_and_atem_features.py` 신규 작성 및 전체 테스트 실행:
  ```powershell
  pytest tests/ -v
  ```
  - JEV 결정 확률 합계($\sum_k P_{\text{JEV}}(k) = 1.0$), 단기 분산 페널티, `AI_ADAPTIVE` 모드에서의 `CUT` vs `MIX` 자동 선택 검증.
  - `ATEMController`의 `MIX`, `DIP`, `WIPE`, `DVE`, `PiP`, `FTB`, `AFV` 상태 전환 및 명령 로그 검증.
- 실행 파일 재빌드:
  ```powershell
  python scripts/build_exe.py
  ```
